"""四层降级链编排：云端 → 离线缓存 → 内置清单 → 默认兜底（架构 §4）。

关键不变量：
    * :meth:`GalleryProvider._finalize` 是**唯一出口**，任一层产出的
      ``GallerySnapshot`` 都同构（UI 只读 ``source`` + ``buddies``）。
    * :meth:`GalleryProvider.resolve` **纯离线、同步、永不抛、绝不发网络请求**。
    * 某层产出的 buddies 为空 → 视为该层失败，继续降一层。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QObject, pyqtSignal

from ..config import Settings
from . import builtin as builtin_mod
from .adapter import adapt_buddies
from .cache import GalleryCache
from .cloud import (
    R_NETWORK,
    R_NOT_CONFIGURED,
    R_PARSE,
    R_TIMEOUT,
    CloudClient,
)
from .models import Buddy, GallerySnapshot, Skin, SourceKind

__all__ = ["GalleryProvider", "CloudPayloadError"]

logger = logging.getLogger(__name__)


class CloudPayloadError(Exception):
    """云端响应无法归一成有效快照（``reason`` 为 models 层的原因码）。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail


def _dedupe_skins(skins: tuple[Skin, ...]) -> tuple[Skin, ...]:
    seen: set[str] = set()
    out: list[Skin] = []
    for skin in skins:
        if skin.id in seen:
            continue
        seen.add(skin.id)
        out.append(skin)
    return tuple(out)


def _normalize_buddy(buddy: Buddy) -> Buddy:
    """每个 buddy 必须有且仅有 1 个 ``is_default=True`` 的 skin（§4.3-3）。"""
    skins = _dedupe_skins(buddy.skins)
    if not skins:
        skins = (
            Skin(
                id=builtin_mod.DEFAULT_SKIN_ID,
                name="默认",
                buddy_id=buddy.id,
                asset_dir=None,
                manifest={},
                remote_ref="",
                is_default=True,
                packaged=False,
            ),
        )
    keep = next((s for s in skins if s.is_default), skins[0])
    fixed: list[Skin] = []
    for skin in skins:
        want_default = skin is keep
        if skin.is_default == want_default:
            fixed.append(skin)
            continue
        fixed.append(
            Skin(
                id=skin.id,
                name=skin.name,
                buddy_id=skin.buddy_id,
                asset_dir=skin.asset_dir,
                manifest=skin.manifest,
                remote_ref=skin.remote_ref,
                is_default=want_default,
                packaged=skin.packaged,
            )
        )
    return Buddy(
        id=buddy.id,
        name=buddy.name,
        owned=buddy.owned,
        thumbnail=buddy.thumbnail if isinstance(buddy.thumbnail, str) else "",
        skins=tuple(fixed),
        order=buddy.order,
    )


class GalleryProvider(QObject):
    """四层降级链。

    Args:
        cfg: 配置（读 ``gallery_base_url`` / ``gallery_auth_ref`` / ``gallery_cache_ttl_h``）。
        root: 项目根目录（内置清单的基准）。
        cloud: 注入的 :class:`CloudClient`（测试用）。
        cache: 注入的 :class:`GalleryCache`（测试用）。
        builtin: 注入的内置清单模块/对象，需提供 ``builtin_snapshot(root)``
            与 ``DEFAULT_BUDDY``（测试用）。
        parent: Qt 父对象。

    Signals:
        resolved (object): 一次新的 :class:`GallerySnapshot`（``refresh`` 完成时发）。
    """

    resolved = pyqtSignal(object)

    def __init__(
        self,
        cfg: Settings,
        root: Path | str,
        cloud: CloudClient | None = None,
        cache: GalleryCache | None = None,
        builtin: Any | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.root = Path(root)
        self._builtin = builtin if builtin is not None else builtin_mod
        self.cache = cache if cache is not None else GalleryCache(cfg)
        self.cloud = cloud if cloud is not None else CloudClient(cfg, self)
        # 容忍注入的最小 stub（测试替身可以只有 fetch()，没有 finished 信号）
        signal = getattr(self.cloud, "finished", None)
        if signal is not None and hasattr(signal, "connect"):
            signal.connect(self._on_cloud_finished)
        self._current: GallerySnapshot | None = None
        #: 最近一次 L1 失败的原因码（供 UI/气泡使用）
        self.last_cloud_reason: str = ""

    # ---------------- 对外 ----------------
    def resolve(self) -> GallerySnapshot:
        """L2 → L3 → L4：纯本地、同步、必然返回、永不抛、**不发网络请求**。"""
        return self._offline("")

    def offline_snapshot(self, reason: str = "", force_refresh: bool = False) -> GallerySnapshot:
        """:meth:`resolve` 的兼容名（架构 §4.1）。``force_refresh`` 目前不影响离线层。"""
        return self._offline(reason)

    def current(self) -> GallerySnapshot | None:
        """最近一次产出的快照（尚未计算则返回 ``None``）。"""
        return self._current

    def snapshot(self) -> GallerySnapshot:
        """取内存快照；没有就现算一次（UI 首帧用，纯本地）。"""
        if self._current is None:
            return self.resolve()
        return self._current

    def refresh(self) -> None:
        """L1：异步走云端，完成后发 :attr:`resolved`。

        ``gallery_base_url`` 为空时**不发网络请求**，直接发离线快照。
        """
        if not (getattr(self.cfg, "gallery_base_url", "") or "").strip():
            self.last_cloud_reason = "not_configured"
            snapshot = self._offline("not_configured")
            self.resolved.emit(snapshot)
            return
        self.cloud.fetch()

    # ---------------- L1 ----------------
    def from_cloud(self, payload: bytes | dict | list) -> GallerySnapshot:
        """把云端原始响应归一成快照。

        Raises:
            CloudPayloadError: 结构无法识别 / 归一后 0 个形象。
        """
        buddies = adapt_buddies(payload, only_owned=True)
        if not buddies:
            raise CloudPayloadError("empty_payload", "云端未返回任何可用形象")
        snapshot = self._finalize(buddies, SourceKind.CLOUD, "ok")
        if self.cache is not None:
            self.cache.save(snapshot)
        return snapshot

    def _on_cloud_finished(self, ok: bool, payload: object, reason: str) -> None:
        if ok:
            try:
                snapshot = self.from_cloud(payload)  # type: ignore[arg-type]
            except CloudPayloadError as exc:
                self.last_cloud_reason = exc.reason
                snapshot = self._offline(exc.reason)
            except Exception as exc:  # noqa: BLE001 —— 云端任何异常都不许外泄
                logger.warning("gallery: cloud normalize failed: %r", exc)
                self.last_cloud_reason = "parse_error"
                snapshot = self._offline("parse_error")
            self.resolved.emit(snapshot)
            return

        mapped = self._map_cloud_reason(reason)
        self.last_cloud_reason = mapped
        snapshot = self._offline(mapped)
        self.resolved.emit(snapshot)

    def _map_cloud_reason(self, reason: str) -> str:
        """cloud.py 的大写错误码 → models.REASON_TEXT 的小写原因码。"""
        code = (reason or "").strip().upper()
        if code == R_NOT_CONFIGURED:
            return "not_configured"
        if code == R_TIMEOUT:
            timeout = getattr(self.cloud, "last_timeout", "") or "connect_timeout"
            return "read_timeout" if timeout == "read_timeout" else "connect_timeout"
        if code == R_NETWORK:
            return "network_error"
        if code == R_PARSE:
            return "parse_error"
        if code.startswith("HTTP_"):
            digits = code[5:]
            if digits in ("401", "403"):
                return f"http_{digits}"
            if digits.startswith("5"):
                return "http_5xx"
            return f"http_{digits}" if digits else "network_error"
        return "network_error"

    # ---------------- L2 → L3 → L4 ----------------
    def _offline(self, cloud_reason: str) -> GallerySnapshot:
        """离线三层。``cloud_reason`` 非空时表示"云端刚失败"，用它做 reason。"""
        snapshot = self._from_cache()
        if snapshot is not None:
            if cloud_reason:
                # 云端失败 + 缓存可用：source 仍是 CACHE，但 reason 说清失败原因
                snapshot = GallerySnapshot(
                    source=SourceKind.CACHE,
                    reason=cloud_reason,
                    buddies=snapshot.buddies,
                    fetched_at=snapshot.fetched_at,
                    cache_stale=snapshot.cache_stale,
                    detail=snapshot.detail,
                )
            self._current = snapshot
            return snapshot

        snapshot = self._from_builtin()
        if snapshot is not None:
            if cloud_reason:
                snapshot = GallerySnapshot(
                    source=snapshot.source,
                    reason=cloud_reason,
                    buddies=snapshot.buddies,
                    fetched_at=snapshot.fetched_at,
                    cache_stale=False,
                    detail=snapshot.detail,
                )
            self._current = snapshot
            return snapshot

        # L4 默认兜底：仍返回同一结构
        logger.warning("gallery: builtin layer failed, falling back to DEFAULT buddy")
        snapshot = self._finalize([self._default_buddy()], SourceKind.DEFAULT, "assets_missing")
        if cloud_reason:
            snapshot.reason = cloud_reason
        self._current = snapshot
        return snapshot

    def _from_cache(self) -> GallerySnapshot | None:
        if self.cache is None:
            return None
        try:
            cached = self.cache.load()
        except Exception:  # noqa: BLE001
            return None
        if cached is None or not cached.buddies:
            return None
        ttl = int(getattr(self.cfg, "gallery_cache_ttl_h", 24) or 24)
        try:
            fresh = self.cache.is_fresh(ttl)
        except Exception:  # noqa: BLE001
            fresh = False
        reason = "cache_hit" if fresh else "cache_stale"
        return GallerySnapshot(
            source=SourceKind.CACHE,
            reason=reason,
            buddies=cached.buddies,
            fetched_at=cached.fetched_at,
            cache_stale=not fresh,
            detail=cached.detail,
        )

    def _from_builtin(self) -> GallerySnapshot | None:
        try:
            raw = self._builtin.builtin_snapshot(self.root)
        except Exception as exc:  # noqa: BLE001 —— L3 抛异常就是要走 L4
            logger.warning("gallery: builtin snapshot failed: %r", exc)
            return None
        buddies = getattr(raw, "buddies", ())
        if not buddies:
            return None
        return self._finalize(buddies, SourceKind.BUILTIN, "builtin")

    def _default_buddy(self) -> Buddy:
        """L4 用的默认形象（优先取注入模块的常量，保证测试可替换）。"""
        buddy = getattr(self._builtin, "DEFAULT_BUDDY", None)
        if isinstance(buddy, Buddy):
            return buddy
        return builtin_mod.default_buddy(self.root)

    # ---------------- 结构统一闸门 ----------------
    def _finalize(
        self,
        buddies: Any,
        source: SourceKind,
        reason: str,
        fetched_at: float | str = 0.0,
        detail: str = "",
    ) -> GallerySnapshot:
        """所有层的唯一出口：做 5 件强制归一化（架构 §4.3）。"""
        normalized: list[Buddy] = []
        seen: set[str] = set()
        for buddy in buddies or ():
            if not isinstance(buddy, Buddy):
                continue
            bid = str(buddy.id or "").strip()
            if not bid or bid in seen:
                continue
            seen.add(bid)
            normalized.append(_normalize_buddy(buddy))

        normalized.sort(key=lambda b: (int(getattr(b, "order", 0) or 0), b.name))
        # 排序后重排 order，保证 UI 顺序稳定
        ordered: list[Buddy] = []
        for index, buddy in enumerate(normalized):
            if buddy.order == index:
                ordered.append(buddy)
            else:
                ordered.append(
                    Buddy(
                        id=buddy.id,
                        name=buddy.name,
                        owned=buddy.owned,
                        thumbnail=buddy.thumbnail,
                        skins=buddy.skins,
                        order=index,
                    )
                )

        if not ordered:
            # 至少 1 个 buddy：回填默认形象，source 保持不变
            ordered = [_normalize_buddy(self._default_buddy())]
            detail = detail or "空清单已回填默认形象"

        return GallerySnapshot(
            source=source,
            reason=reason or "builtin",
            buddies=tuple(ordered),
            fetched_at=fetched_at,
            cache_stale=False,
            detail=detail,
        )
