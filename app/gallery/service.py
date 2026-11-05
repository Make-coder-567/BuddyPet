"""图鉴门面：UI 只跟它打交道（架构 §5.3）。

职责刻意保持"薄"：
    * 持 ``cfg`` / ``provider`` / ``cache``；
    * :meth:`GalleryService.snapshot` 返回内存快照（首帧纯本地，绝不联网）；
    * :meth:`GalleryService.startup_sync` 启动同步：先离线出货，再按需异步刷新；
    * :meth:`GalleryService.refresh` 异步走云端，结果回写缓存并发 :attr:`updated`。

UI 侧只读 ``snapshot().source`` 与 ``snapshot().buddies``。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QObject, pyqtSignal

from ..config import Settings
from .cache import GalleryCache
from .models import Buddy, GallerySnapshot, Skin, SourceKind
from .validator import validate_skin

__all__ = ["GalleryService"]

logger = logging.getLogger(__name__)


class GalleryService(QObject):
    """图鉴门面。

    Args:
        cfg: 配置对象。
        root: 项目根目录。
        provider: 注入的 :class:`~app.gallery.provider.GalleryProvider`（测试用）。
        cache: 注入的 :class:`~app.gallery.cache.GalleryCache`（测试用）。
        parent: Qt 父对象。

    Signals:
        updated (object): 快照有更新（离线首帧 / 云端返回 / 云端失败降级都发）。
    """

    updated = pyqtSignal(object)

    def __init__(
        self,
        cfg: Settings,
        root: Path | str,
        provider: Any | None = None,
        cache: GalleryCache | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        # 延迟 import：provider → `from . import builtin` → 包 __init__，
        # 模块级 import 会把包初始化顺序搅乱（见 T02 报告 ④-13）。
        from .provider import GalleryProvider

        self.cfg = cfg
        self.root = Path(root)
        self.cache = cache if cache is not None else GalleryCache(cfg)
        self.provider = (
            provider if provider is not None else GalleryProvider(cfg, self.root, cache=self.cache, parent=self)
        )
        signal = getattr(self.provider, "resolved", None)
        if signal is not None and hasattr(signal, "connect"):
            signal.connect(self._on_resolved)
        self._snapshot: GallerySnapshot | None = None
        self._started = False

    # ---------------- 读 ----------------
    def snapshot(self) -> GallerySnapshot:
        """当前内存快照；没有就现算一次（纯本地，< 10ms）。"""
        if self._snapshot is None:
            snap = self.provider.snapshot()
            self._snapshot = snap
        return self._snapshot

    #: 架构文档 §8.3 的旧名
    current = snapshot

    def find(self, buddy_id: str, skin_id: str) -> tuple[Buddy | None, Skin | None]:
        """按 id 查 (形象, 皮肤)；查不到会自动重新扫一次内置清单（磁盘可能刚变）。"""
        snap = self.snapshot()
        buddy = snap.buddy(buddy_id)
        skin = buddy.find_skin(skin_id) if buddy is not None else None
        if skin is not None:
            return buddy, skin
        snap = self.reload()
        buddy = snap.buddy(buddy_id)
        return buddy, (buddy.find_skin(skin_id) if buddy is not None else None)

    # ---------------- 生命周期 ----------------
    def startup_sync(self) -> None:
        """启动同步（架构 §5.4）：先离线出货，再按需异步刷新。**绝不阻塞**。"""
        self._started = True
        snapped = self.provider.resolve()
        self._on_resolved(snapped)

        if not (getattr(self.cfg, "gallery_base_url", "") or "").strip():
            return                       # 未配置：不进网络
        if not bool(getattr(self.cfg, "gallery_auto_sync", True)):
            return                       # 用户关掉了启动同步
        if snapped.source is SourceKind.CACHE and not snapped.cache_stale:
            return                       # TTL 内命中缓存：不联网
        self.refresh()

    def refresh(self, force: bool = False) -> None:
        """异步刷新（走 provider）。``force`` 目前只影响语义，不改变实现。"""
        self.provider.refresh()

    def reload(self) -> GallerySnapshot:
        """丢弃 provider 的内存快照并重新走一次离线链（磁盘变了时用）。"""
        try:
            self.provider._current = None      # noqa: SLF001 —— 同包内协作，接受
        except Exception:  # noqa: BLE001
            pass
        snapped = self.provider.resolve()
        self._on_resolved(snapped)
        return snapped

    def _on_resolved(self, snapshot: object) -> None:
        if not isinstance(snapshot, GallerySnapshot):
            return
        self._snapshot = snapshot
        if snapshot.source is SourceKind.CLOUD and self.cache is not None:
            self.cache.save(snapshot)          # provider 已写过一次，这里幂等兜底
        self.updated.emit(snapshot)

    # ---------------- 给 QA / 命令通道用的摘要 ----------------
    def dump_summary(self) -> dict:
        """把当前快照压成可断言的纯 JSON 结构（``gallery:dump`` 用）。"""
        snap = self.snapshot()
        buddies: list[dict] = []
        for buddy in snap.buddies:
            skins: list[dict] = []
            for skin in buddy.skins:
                vr = validate_skin(skin, use_cache=False)
                skins.append(
                    {
                        "id": skin.id,
                        "name": skin.name,
                        "is_default": bool(skin.is_default),
                        "packaged": bool(skin.packaged),
                        "asset_dir": skin.asset_dir,
                        "is_current": (
                            buddy.id == getattr(self.cfg, "selected_buddy_id", "")
                            and skin.id == getattr(self.cfg, "selected_skin_id", "")
                        ),
                        "status": vr.status.value,
                        "can_apply": vr.can_apply,
                        "message": vr.message,
                        "missing": list(vr.missing),
                        "degraded": list(vr.degraded),
                        "canvas": list(vr.canvas) if vr.canvas else None,
                        "frames": vr.frames,
                        "has_gaze": vr.has_gaze,
                        "has_grip": vr.has_grip,
                    }
                )
            buddies.append(
                {
                    "id": buddy.id,
                    "name": buddy.name,
                    "owned": bool(buddy.owned),
                    "order": int(buddy.order),
                    "thumbnail": buddy.thumbnail,
                    "skins": skins,
                }
            )
        fetched_at: float | str = snap.fetched_at
        return {
            "source": snap.source.value if isinstance(snap.source, SourceKind) else str(snap.source),
            "reason": snap.reason,
            "reason_text": snap.reason_text(),
            "fetched_at": fetched_at,
            "cache_stale": bool(snap.cache_stale),
            "detail": snap.detail,
            "buddy_count": len(buddies),
            "buddies": buddies,
            "selected": {
                "buddy_id": getattr(self.cfg, "selected_buddy_id", ""),
                "skin_id": getattr(self.cfg, "selected_skin_id", ""),
            },
        }
