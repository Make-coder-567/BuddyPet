"""离线缓存：``%APPDATA%/WorkBuddyPet/gallery_cache.json`` 的读写、TTL、损坏容错。

设计约束（架构 §6 / Q9）：
    * 唯一碰缓存文件的模块。
    * **只落盘元数据，云端图片一律不落盘**；``IMAGE_POLICY`` 是这条策略的单点开关。
    * 写盘原子（``.tmp`` → ``os.replace``）；任何异常都被吞掉，缓存失败永不影响主流程。

``load()`` 的失败原因会记录到 :attr:`GalleryCache.last_reason`，取值：
``"no_cache"`` / ``"cache_corrupt"`` / ``"cache_scope_mismatch"`` / ``"version_mismatch"``。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from ..config import Settings, config_dir
from .models import Buddy, GallerySnapshot, Skin, SourceKind

__all__ = [
    "IMAGE_POLICY",
    "CACHE_VERSION",
    "MAX_CACHE_BYTES",
    "cache_path",
    "credential_path",
    "GalleryCache",
]

#: Q9 版权占位：本期只落盘元数据 JSON。放开图片落盘时改这一处 + 打开下方分支。
IMAGE_POLICY = "memory_only"

CACHE_VERSION = 1

#: 快照体积上限（Q7）：超出只警告不写
MAX_CACHE_BYTES = 512 * 1024


def cache_path() -> Path:
    """缓存文件路径（与 ``config.json`` 同目录）。"""
    return config_dir() / "gallery_cache.json"


def credential_path() -> Path:
    """凭据引用文件路径（只存引用名 → token 的映射，见 §12-Q2）。"""
    return config_dir() / "credentials.json"


def make_scope(base_url: str, auth_ref: str) -> str:
    """缓存作用域：``sha1(base_url|auth_ref)[:12]``（Q6 多账号/换地址隔离）。"""
    raw = f"{(base_url or '').strip()}|{(auth_ref or '').strip()}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


# --------------------------------------------------------------------------- #
# 序列化
# --------------------------------------------------------------------------- #
def _skin_to_dict(skin: Skin) -> dict[str, Any]:
    return {
        "id": skin.id,
        "name": skin.name,
        "buddy_id": skin.buddy_id,
        "asset_dir": skin.asset_dir,
        "manifest": skin.manifest if isinstance(skin.manifest, dict) else {},
        "remote_ref": skin.remote_ref,
        "is_default": bool(skin.is_default),
        "packaged": bool(skin.packaged),
    }


def _buddy_to_dict(buddy: Buddy) -> dict[str, Any]:
    return {
        "id": buddy.id,
        "name": buddy.name,
        "owned": bool(buddy.owned),
        "thumbnail": buddy.thumbnail,
        "order": int(buddy.order),
        "skins": [_skin_to_dict(s) for s in buddy.skins],
    }


def _skin_from_dict(raw: Any, buddy_id: str) -> Skin | None:
    if not isinstance(raw, dict):
        return None
    sid = raw.get("id")
    if not isinstance(sid, str) or not sid.strip():
        return None
    name = raw.get("name")
    asset_dir = raw.get("asset_dir")
    manifest = raw.get("manifest")
    remote_ref = raw.get("remote_ref")
    return Skin(
        id=sid.strip(),
        name=name if isinstance(name, str) and name.strip() else sid.strip(),
        buddy_id=buddy_id,
        asset_dir=asset_dir if isinstance(asset_dir, str) and asset_dir else None,
        manifest=manifest if isinstance(manifest, dict) else {},
        remote_ref=remote_ref if isinstance(remote_ref, str) else "",
        is_default=bool(raw.get("is_default", False)),
        packaged=bool(raw.get("packaged", True)),
    )


def _buddy_from_dict(raw: Any) -> Buddy | None:
    """逐条容错：结构不对的单条直接丢弃（返回 ``None``）。"""
    if not isinstance(raw, dict):
        return None
    bid = raw.get("id")
    if not isinstance(bid, str) or not bid.strip():
        return None
    bid = bid.strip()
    skins_raw = raw.get("skins")
    skins: list[Skin] = []
    if isinstance(skins_raw, list):
        for item in skins_raw:
            skin = _skin_from_dict(item, bid)
            if skin is not None:
                skins.append(skin)
    name = raw.get("name")
    thumbnail = raw.get("thumbnail")
    order = raw.get("order")
    try:
        order_int = int(order) if order is not None else 0
    except (TypeError, ValueError):
        order_int = 0
    return Buddy(
        id=bid,
        name=name if isinstance(name, str) and name.strip() else bid,
        owned=bool(raw.get("owned", True)),
        thumbnail=thumbnail if isinstance(thumbnail, str) else "",
        skins=tuple(skins),
        order=order_int,
    )


# --------------------------------------------------------------------------- #
# GalleryCache
# --------------------------------------------------------------------------- #
class GalleryCache:
    """缓存读写门面。

    Args:
        cfg: 用于计算 scope 与 TTL；传 ``None`` 表示**不做 scope 校验**
            （单测/离线脚本友好）。
        path: 覆盖缓存文件路径（测试用临时目录）。
    """

    def __init__(self, cfg: Settings | None = None, path: Path | str | None = None) -> None:
        self.cfg = cfg
        self._path = Path(path) if path is not None else cache_path()
        #: 最近一次 ``load()`` 的结论，供 provider 区分 no_cache / corrupt / scope
        self.last_reason: str = "no_cache"
        #: 最近一次成功 ``load()`` 得到的时间戳
        self.last_fetched_at: float = 0.0

    # ---------------- 基础属性 ----------------
    @property
    def path(self) -> Path:
        return self._path

    def scope(self) -> str | None:
        """当前配置的缓存作用域；``cfg is None`` 时返回 ``None``（不校验）。"""
        if self.cfg is None:
            return None
        return make_scope(getattr(self.cfg, "gallery_base_url", ""), getattr(self.cfg, "gallery_auth_ref", ""))

    # ---------------- 读 ----------------
    def load(self) -> GallerySnapshot | None:
        """读缓存。

        Returns:
            合法快照；文件不存在 / JSON 损坏 / 版本不符 / scope 不匹配 / 结构非法
            一律返回 ``None``（不抛）。具体原因见 :attr:`last_reason`。
        """
        path = self._path
        if not path.is_file():
            self.last_reason = "no_cache"
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            self._quarantine()
            self.last_reason = "cache_corrupt"
            return None
        if not isinstance(raw, dict):
            self.last_reason = "cache_corrupt"
            return None
        if raw.get("version") != CACHE_VERSION:
            self.last_reason = "version_mismatch"
            return None
        buddies_raw = raw.get("buddies")
        if not isinstance(buddies_raw, list):
            self.last_reason = "cache_corrupt"
            return None

        scope = self.scope()
        if scope is not None:
            cached_scope = raw.get("scope")
            if isinstance(cached_scope, str) and cached_scope and cached_scope != scope:
                self.last_reason = "cache_scope_mismatch"
                return None

        buddies: list[Buddy] = []
        for item in buddies_raw:
            buddy = _buddy_from_dict(item)
            if buddy is not None:
                buddies.append(buddy)
        if not buddies:
            self.last_reason = "cache_corrupt"
            return None

        fetched_at = raw.get("fetched_at")
        try:
            fetched = float(fetched_at) if fetched_at is not None else 0.0
        except (TypeError, ValueError):
            fetched = 0.0

        source_raw = raw.get("source")
        try:
            source = SourceKind(str(source_raw)) if source_raw else SourceKind.CLOUD
        except ValueError:
            source = SourceKind.CLOUD
        reason_raw = raw.get("reason")

        self.last_fetched_at = fetched
        self.last_reason = "cache_hit"
        return GallerySnapshot(
            source=source,
            reason=reason_raw if isinstance(reason_raw, str) and reason_raw else "cache_hit",
            buddies=tuple(buddies),
            fetched_at=fetched,
            cache_stale=False,
            detail=raw.get("detail") if isinstance(raw.get("detail"), str) else "",
        )

    #: 架构文档 §6.4 的旧名，保留别名
    read = load

    # ---------------- 写 ----------------
    def save(self, snapshot: GallerySnapshot) -> bool:
        """原子写缓存；失败返回 ``False``（不抛）。"""
        if snapshot is None:
            return False
        try:
            payload: dict[str, Any] = {
                "version": CACHE_VERSION,
                "image_policy": IMAGE_POLICY,
                "fetched_at": float(snapshot.fetched_at) if isinstance(snapshot.fetched_at, (int, float)) else 0.0,
                "source": snapshot.source.value if isinstance(snapshot.source, SourceKind) else str(snapshot.source),
                "reason": snapshot.reason,
                "scope": self.scope() or "",
                "detail": snapshot.detail,
                "buddies": [_buddy_to_dict(b) for b in snapshot.buddies],
            }
            text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
            if len(text.encode("utf-8")) > MAX_CACHE_BYTES:
                return False
            path = self._path
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(str(tmp), str(path))
            self.last_fetched_at = float(payload["fetched_at"])
            return True
        except Exception:  # noqa: BLE001
            return False

    #: 架构文档 §6.4 的旧名，保留别名
    write = save

    # ---------------- TTL / 维护 ----------------
    def _fresh_fetched_at(self) -> float | None:
        """实时读取磁盘上的 ``fetched_at``（手改文件后也能立刻反映）。"""
        snapshot = self.load()
        if snapshot is None:
            return None
        try:
            return float(snapshot.fetched_at)
        except (TypeError, ValueError):
            return None

    def age_hours(self) -> float:
        """缓存年龄（小时）；无缓存返回 ``float("inf")``。"""
        fetched = self._fresh_fetched_at()
        if fetched is None or fetched <= 0:
            return float("inf")
        return max(0.0, (time.time() - fetched) / 3600.0)

    def is_fresh(self, ttl_hours: int | float) -> bool:
        """缓存是否在 TTL 内。``ttl_hours <= 0`` 视为 1 小时（与 §6.3 一致）。"""
        try:
            ttl = max(1.0, float(ttl_hours))
        except (TypeError, ValueError):
            ttl = 24.0
        fetched = self._fresh_fetched_at()
        if fetched is None or fetched <= 0:
            return False
        return (time.time() - fetched) <= ttl * 3600.0

    def clear(self) -> bool:
        """删除缓存文件；成功或本就不存在都返回 ``True``。"""
        try:
            self._path.unlink()
            return True
        except FileNotFoundError:
            return True
        except OSError:
            return False

    def _quarantine(self) -> None:
        """把损坏的缓存改名 ``.corrupt``（失败忽略）。"""
        try:
            self._path.replace(self._path.with_suffix(self._path.suffix + ".corrupt"))
        except OSError:
            pass
