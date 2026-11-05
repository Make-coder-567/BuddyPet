"""``GallerySection``：设置面板里的「Buddy 图鉴」分组卡片（架构 §8.1）。

控件树：
    状态条（来源 + 时间 + 刷新） → 形象网格 + 皮肤列表 → 预览区 → 操作区

语义要点：
    * 打开面板即渲染**内存快照**，绝不触发网络（架构 §8.3）；
    * 换肤是**立即提交**语义（D5）：点「应用」立刻落盘并换肤，不受面板
      「保存 / 取消」管辖；
    * 「取消」只把面板内的选中态还原成"当前生效组合"，不碰配置。
"""
from __future__ import annotations

import datetime as _dt
import logging
from pathlib import Path
from typing import Any

from PyQt5.QtCore import QPoint, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from .models import Buddy, GallerySnapshot, Skin, SkinStatus, SourceKind
from .ui_widgets import BuddyTile, SkinRow, SkinPreview
from .validator import validate_skin

__all__ = ["GallerySection"]

logger = logging.getLogger(__name__)

_SOURCE_KIND_TEXT = {
    SourceKind.CLOUD: "云端",
    SourceKind.CACHE: "离线缓存",
    SourceKind.BUILTIN: "内置清单",
    SourceKind.DEFAULT: "默认 Buddy",
}

#: 右侧预览栏宽度：立绘框 132 + 左右各 24 的余量。
#: 余量不能省 —— 「视线跟随 ✓ · 抓边姿态 ✓」实测需要 165px（fonts 有字库时），
#: 若按 132 定宽会被裁字（T04 截图自审 + fontMetrics 断言共同发现）。
PREVIEW_COL_W = SkinPreview.BOX_W + 48


def _fmt_time(fetched_at: float | str, fallback: str = "") -> str:
    """epoch 秒 → ``MM-DD HH:MM``；字符串原样；0/非法 → fallback / 空串。

    注意：默认返回**空串**而不是 ``"--"``。状态条是 ``f"{layer} · {time}"``，
    没有时间时若返回 ``"--"`` 就会出现「内置清单 · --」这种残缺文案（T04 截图自审发现）。
    """
    if isinstance(fetched_at, str) and fetched_at.strip():
        return fetched_at.strip()
    try:
        ts = float(fetched_at)
    except (TypeError, ValueError):
        return fallback
    if ts <= 0:
        return fallback
    try:
        return _dt.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return fallback


class GallerySection(QWidget):
    """图鉴分组。

    Args:
        cfg: 配置对象。
        root: 项目根目录。
        service: :class:`~app.gallery.service.GalleryService`（缺省自建，不联网）。
        switcher: :class:`~app.gallery.switcher.SkinSwitcher`（缺省则「应用」不可用）。
        parent: 父控件。

    Signals:
        applied (object): 换肤成功后的 :class:`Skin`。
        dirty_changed (bool): "有未提交变更"状态变化。
    """

    applied = pyqtSignal(object)
    dirty_changed = pyqtSignal(bool)

    def __init__(
        self,
        cfg,
        root: Path | str,
        service: Any | None = None,
        switcher: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.root = Path(root)
        self._service = service
        self._switcher = switcher
        self._scroll_parent: QWidget | None = None
        self._busy = False
        self._refreshing = False

        self._buddy_id: str = str(getattr(cfg, "selected_buddy_id", "") or "default")
        self._skin_id: str = str(getattr(cfg, "selected_skin_id", "") or "default")
        self._snapshot: GallerySnapshot | None = None
        self._buddy_tiles: dict[str, BuddyTile] = {}
        self._skin_rows: dict[str, SkinRow] = {}

        self._build_ui()
        self._wire()
        self.refresh_from_service()

    # ---------------------------------------------------------------- 构建
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.setSpacing(7)

        title = QLabel("Buddy 图鉴", self)
        title.setObjectName("Section")
        outer.addWidget(title)

        # ---------- 状态条 ----------
        status = QHBoxLayout()
        status.setSpacing(8)
        self.source_label = QLabel("内置清单", self)
        self.source_label.setObjectName("GallerySource")
        self.source_label.setStyleSheet(
            f"color:{theme.ORANGE};font-size:8.5pt;font-weight:600;background:transparent;"
        )
        self.source_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        status.addWidget(self.source_label, 0, Qt.AlignVCenter)
        self.status_sub = QLabel("", self)
        self.status_sub.setObjectName("Sub")
        self.status_sub.setStyleSheet(f"color:{theme.MUTED};font-size:8pt;background:transparent;")
        # 状态条里的长文本绝不能撑宽整块（否则 404px 视口会被撑到 521px，
        # 右下角预览被挤出可视区且横向滚动条被关掉 → 用户永远看不到）。
        self.status_sub.setWordWrap(False)
        self.status_sub.setMinimumWidth(0)
        self.status_sub.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        status.addWidget(self.status_sub, 1)
        self.refresh_btn = QPushButton("刷新图鉴", self)
        self.refresh_btn.setObjectName("Ghost")
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.setFixedHeight(24)
        self.refresh_btn.setStyleSheet(
            f"QPushButton{{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.09);"
            f"border-radius:7px;padding:1px 10px;color:{theme.TEXT};font-size:8.5pt;}}"
            f"QPushButton:hover{{background:rgba(255,122,61,0.16);color:{theme.ORANGE};}}"
            f"QPushButton:disabled{{color:rgba(243,244,246,0.32);}}"
        )
        status.addWidget(self.refresh_btn, 0, Qt.AlignVCenter)
        outer.addLayout(status)

        # ---------- 主体：左（形象+皮肤） / 右（预览） ----------
        body = QHBoxLayout()
        body.setSpacing(10)
        body.setContentsMargins(0, 0, 0, 0)

        left = QVBoxLayout()
        left.setSpacing(6)
        left.setContentsMargins(0, 0, 0, 0)
        self.buddy_grid = QGridLayout()
        self.buddy_grid.setSpacing(8)
        self.buddy_grid.setContentsMargins(0, 0, 0, 0)
        left.addLayout(self.buddy_grid)

        self.skin_list = QVBoxLayout()
        self.skin_list.setSpacing(5)
        self.skin_list.setContentsMargins(0, 0, 0, 0)
        left.addLayout(self.skin_list)
        left.addStretch(1)
        body.addLayout(left, 1)

        right = QVBoxLayout()
        right.setSpacing(4)
        right.setContentsMargins(0, 0, 0, 0)
        self.preview = SkinPreview(168, self)
        right.addWidget(self.preview, 0, Qt.AlignHCenter)
        self.preview_title = QLabel("—", self)
        self.preview_title.setStyleSheet(
            f"color:{theme.TEXT};font-size:8.5pt;font-weight:600;background:transparent;"
        )
        self.preview_title.setAlignment(Qt.AlignCenter)
        right.addWidget(self.preview_title)
        self.preview_caps = QLabel("—", self)
        self.preview_caps.setObjectName("Sub")
        self.preview_caps.setAlignment(Qt.AlignCenter)
        self.preview_caps.setStyleSheet(f"color:{theme.MUTED};font-size:8pt;background:transparent;")
        right.addWidget(self.preview_caps)
        self.preview_meta = QLabel("—", self)
        self.preview_meta.setAlignment(Qt.AlignCenter)
        self.preview_meta.setStyleSheet(f"color:{theme.MUTED};font-size:8pt;background:transparent;")
        self.preview_meta.setWordWrap(True)
        right.addWidget(self.preview_meta)
        right.addStretch(1)
        # 右栏定宽：三个文字标签的「最小宽度」不能反过来撑宽整块（T04 截图自审的
        # 真实缺陷：preview_meta 的最小宽 165px 把内容撑到 521px，预览被裁掉）。
        for label in (self.preview_title, self.preview_caps, self.preview_meta):
            label.setFixedWidth(PREVIEW_COL_W)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        body.addLayout(right, 0)
        outer.addLayout(body)

        # ---------- 操作区 ----------
        action = QHBoxLayout()
        action.setSpacing(8)
        self.dirty_label = QLabel("", self)
        self.dirty_label.setStyleSheet(f"color:{theme.ORANGE};font-size:8pt;background:transparent;")
        action.addWidget(self.dirty_label, 1)
        self.reset_btn = QPushButton("恢复默认形象", self)
        self.reset_btn.setObjectName("Ghost")
        self.reset_btn.setCursor(Qt.PointingHandCursor)
        action.addWidget(self.reset_btn)
        self.cancel_btn = QPushButton("放弃更改", self)
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        action.addWidget(self.cancel_btn)
        self.apply_btn = QPushButton("应用此皮肤", self)
        self.apply_btn.setObjectName("Primary")
        self.apply_btn.setCursor(Qt.PointingHandCursor)
        action.addWidget(self.apply_btn)
        outer.addLayout(action)

    def _wire(self) -> None:
        self.refresh_btn.clicked.connect(self._on_refresh)
        self.apply_btn.clicked.connect(self._on_apply)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.reset_btn.clicked.connect(self._on_reset)
        if self._service is not None:
            signal = getattr(self._service, "updated", None)
            if signal is not None and hasattr(signal, "connect"):
                signal.connect(self._on_service_updated)
        if self._switcher is not None:
            for name, handler in (
                ("busy_changed", self._on_busy),
                ("swapped", self._on_swapped),
                ("failed", self._on_switcher_failed),
            ):
                signal = getattr(self._switcher, name, None)
                if signal is not None and hasattr(signal, "connect"):
                    signal.connect(handler)

    # ---------------------------------------------------------------- 滚动
    def set_scroll_parent(self, scroll: QWidget | None) -> None:
        self._scroll_parent = scroll

    def scroll_into_view(self) -> None:
        """把本分组的**顶边**对齐视口顶部（菜鸟用户不用找）。

        不能用 ``QScrollArea.ensureWidgetVisible``：当分组本身比视口还高时，
        Qt 会"尽量居中"，实测落到 value=20 而不是 0，顶部那行状态条被切掉。
        这里直接换算内容坐标后写滚动条，结果确定可断言。
        """
        scroll = self._scroll_parent
        if scroll is None:
            return
        try:
            bar = scroll.verticalScrollBar()
            content = scroll.widget()
            top = self.mapTo(content, QPoint(0, 0)).y() if content is not None else 0
            bar.setValue(max(0, min(bar.maximum(), top - 8)))
        except AttributeError:
            pass

    # ---------------------------------------------------------------- 生命周期
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # 面板打开期间让立绘自己眨眼（与桌宠同款节奏），关闭立刻停（P8）。
        # 之前 set_auto_blink 从未被调用 → 预览只有点击时才眨，不会"活"起来。
        if not self.preview.has_frames():
            self.preview.set_skin(self._current_skin())
        self.preview.set_auto_blink(True)

    def hideEvent(self, event) -> None:  # noqa: N802
        self.preview.set_auto_blink(False)
        super().hideEvent(event)

    # ---------------------------------------------------------------- 数据
    def refresh_from_service(self) -> None:
        """从 service 取快照并整体重绘（不联网）。"""
        snapshot = None
        if self._service is not None:
            try:
                snapshot = self._service.snapshot()
            except Exception:  # noqa: BLE001 —— 门面异常不该炸面板
                logger.warning("gallery section: snapshot failed", exc_info=True)
        if snapshot is None:
            from .builtin import builtin_snapshot

            snapshot = builtin_snapshot(self.root)
        self._render(snapshot)

    def _render(self, snapshot: GallerySnapshot) -> None:
        self._snapshot = snapshot
        self._render_status(snapshot)
        self._render_buddies(snapshot)
        self._render_skins()
        self._sync_preview()
        self._sync_buttons()

    def _render_status(self, snapshot: GallerySnapshot) -> None:
        time_text = _fmt_time(
            snapshot.fetched_at, str(getattr(self.cfg, "gallery_last_sync", "") or "")
        )
        layer = _SOURCE_KIND_TEXT.get(snapshot.source, "内置清单")
        self.source_label.setText(f"{layer} · {time_text}" if time_text else layer)
        text = snapshot.reason_text(time_text)
        base_url = str(getattr(self.cfg, "gallery_base_url", "") or "").strip()
        if not base_url:
            sub = "未配置云端连接，仅使用本地素材"
        elif snapshot.source is SourceKind.CLOUD:
            sub = "已从云端同步"
        elif snapshot.cache_stale:
            sub = "云端暂不可达，以下为离线缓存（可能不是最新）"
        else:
            sub = text if text != layer else ""
        self.status_sub.setText(sub)
        self.status_sub.setToolTip(text)
        self.refresh_btn.setEnabled(not self._busy and not self._refreshing)
        self.refresh_btn.setText("刷新中…" if self._refreshing else "刷新图鉴")

    def _render_buddies(self, snapshot: GallerySnapshot) -> None:
        while self.buddy_grid.count():
            item = self.buddy_grid.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._buddy_tiles.clear()

        ids = [b.id for b in snapshot.buddies]
        if self._buddy_id not in ids:
            current = str(getattr(self.cfg, "selected_buddy_id", "") or "")
            self._buddy_id = current if current in ids else (ids[0] if ids else "")

        columns = 3
        for index, buddy in enumerate(snapshot.buddies):
            tile = BuddyTile(
                buddy,
                selected=(buddy.id == self._buddy_id),
                on_click=self._on_buddy_clicked,
                root=self.root,
                parent=self,
            )
            self.buddy_grid.addWidget(tile, index // columns, index % columns, Qt.AlignLeft | Qt.AlignTop)
            self._buddy_tiles[buddy.id] = tile
        for column in range(columns):
            self.buddy_grid.setColumnStretch(column, 0)
        self.buddy_grid.setColumnStretch(columns, 1)

    def _current_buddy(self) -> Buddy | None:
        if self._snapshot is None:
            return None
        return self._snapshot.buddy(self._buddy_id)

    def _render_skins(self) -> None:
        while self.skin_list.count():
            item = self.skin_list.takeAt(0)
            widget = item.widget() if item is not None else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._skin_rows.clear()

        buddy = self._current_buddy()
        if buddy is None:
            self._skin_id = ""
            return

        ids = [s.id for s in buddy.skins]
        if self._skin_id not in ids:
            default_skin = buddy.default_skin
            self._skin_id = default_skin.id if default_skin is not None else (ids[0] if ids else "")

        for skin in buddy.skins:
            # 每次都重算校验（不缓存错误结论）
            vr = validate_skin(skin, use_cache=False)
            row = SkinRow(
                skin,
                vr,
                selected=(skin.id == self._skin_id),
                on_click=self._on_skin_clicked,
                parent=self,
            )
            self.skin_list.addWidget(row)
            self._skin_rows[skin.id] = row
        self.skin_list.addStretch(1)

    def _current_skin(self) -> Skin | None:
        buddy = self._current_buddy()
        return buddy.find_skin(self._skin_id) if buddy is not None else None

    # ---------------------------------------------------------------- 预览
    def _sync_preview(self) -> None:
        buddy = self._current_buddy()
        skin = self._current_skin()
        if skin is None:
            self.preview.set_skin(None)
            self.preview_title.setText("—")
            self.preview_caps.setText("—")
            self.preview_meta.setText("—")
            return
        vr = validate_skin(skin, use_cache=False)
        self.preview.set_skin(skin if vr.can_apply else None)
        buddy_name = buddy.name if buddy is not None else skin.buddy_id
        self.preview_title.setText(f"{buddy_name} · {skin.name}")
        gaze = "✓" if vr.has_gaze else "✕"
        grip = "✓" if vr.has_grip else "✕"
        self.preview_caps.setText(f"视线跟随 {gaze} · 抓边姿态 {grip}")
        if vr.canvas:
            self.preview_meta.setText(f"{vr.canvas[0]}×{vr.canvas[1]} · {vr.frames} 帧")
        else:
            self.preview_meta.setText(vr.message or "—")
        if vr.status is not SkinStatus.READY:
            extra = "；".join(vr.missing) or next((d for d in vr.degraded if d not in ("视线跟随", "抓边姿态")), "")
            if extra:
                self.preview_meta.setText(f"{vr.message}\n{extra}")
        self.preview_meta.setToolTip(vr.message)

    # ---------------------------------------------------------------- 按钮态
    def _selected_pair(self) -> tuple[Buddy | None, Skin | None]:
        return self._current_buddy(), self._current_skin()

    def _is_dirty(self) -> bool:
        current = (
            str(getattr(self.cfg, "selected_buddy_id", "") or ""),
            str(getattr(self.cfg, "selected_skin_id", "") or ""),
        )
        return (self._buddy_id, self._skin_id) != current

    def _sync_buttons(self) -> None:
        buddy, skin = self._selected_pair()
        dirty = self._is_dirty()
        can_apply = False
        if skin is not None and self._switcher is not None and not self._busy:
            can_apply = dirty and validate_skin(skin, use_cache=False).can_apply
        self.apply_btn.setEnabled(bool(can_apply))
        self.cancel_btn.setEnabled(bool(dirty) and not self._busy)
        self.reset_btn.setEnabled(
            not self._busy
            and self._switcher is not None
            and (self._buddy_id, self._skin_id) != ("default", "default")
        )
        self.dirty_label.setText("有未提交的变更" if dirty else "")
        self.dirty_changed.emit(dirty)

    # ---------------------------------------------------------------- 交互
    def _on_buddy_clicked(self, buddy: Buddy) -> None:
        if self._busy:
            return
        self._buddy_id = buddy.id
        default_skin = buddy.default_skin
        self._skin_id = default_skin.id if default_skin is not None else ""
        for bid, tile in self._buddy_tiles.items():
            tile.set_selected(bid == self._buddy_id)
            tile.update()
        self._render_skins()
        self._sync_preview()
        self.preview.blink()
        self._sync_buttons()

    def _on_skin_clicked(self, skin: Skin) -> None:
        if self._busy:
            return
        self._skin_id = skin.id
        for sid, row in self._skin_rows.items():
            row.set_selected(sid == self._skin_id)
            row.update()
        self._sync_preview()
        self.preview.blink()
        self._sync_buttons()

    def _on_apply(self) -> None:
        buddy, skin = self._selected_pair()
        if buddy is None or skin is None or self._switcher is None:
            return
        ok = self._switcher.apply(buddy, skin)
        if ok:
            self.dirty_label.setText("")
            self.applied.emit(skin)
            self._sync_buttons()
        else:
            self.dirty_label.setText("应用失败，已恢复上一形象")
            self._sync_buttons()

    def _on_cancel(self) -> None:
        self._buddy_id = str(getattr(self.cfg, "selected_buddy_id", "") or "default")
        self._skin_id = str(getattr(self.cfg, "selected_skin_id", "") or "default")
        self._render_selection_only()

    def _on_reset(self) -> None:
        if self._switcher is None:
            return
        if self._switcher.restore_default():
            self._buddy_id = "default"
            self._skin_id = "default"
            self._render_selection_only()

    def _render_selection_only(self) -> None:
        """只刷新选中态/预览/按钮，不重建列表（避免闪）。"""
        for bid, tile in self._buddy_tiles.items():
            tile.set_selected(bid == self._buddy_id)
            tile.update()
        self._render_skins()
        self._sync_preview()
        self._sync_buttons()

    def _on_refresh(self) -> None:
        if self._service is None or self._refreshing:
            return
        self._refreshing = True
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("刷新中…")
        try:
            self._service.refresh()
        finally:
            if self._snapshot is not None:
                self._render_status(self._snapshot)

    def _on_service_updated(self, snapshot: object) -> None:
        if not isinstance(snapshot, GallerySnapshot):
            return
        self._refreshing = False
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("刷新图鉴")
        self._render(snapshot)

    def _on_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.apply_btn.setText("应用中…" if self._busy else "应用")
        self._sync_buttons()

    def _on_swapped(self, skin: object) -> None:
        if isinstance(skin, Skin):
            self._buddy_id = skin.buddy_id or self._buddy_id
            self._skin_id = skin.id
        self._render_selection_only()

    def _on_switcher_failed(self, name: str, reason: str) -> None:
        self.dirty_label.setText(f"应用失败（{reason}）")
        self._sync_buttons()

    # ---------------------------------------------------------------- 测试辅助
    def select_skin(self, skin_id: str) -> bool:
        """按 id 选中皮肤（自动化验收用），返回是否成功。"""
        buddy = self._current_buddy()
        if buddy is None:
            return False
        skin = buddy.find_skin(skin_id)
        if skin is None:
            return False
        self._on_skin_clicked(skin)
        return True

    def state(self) -> dict:
        """当前面板状态摘要（断言用）。"""
        buddy, skin = self._selected_pair()
        return {
            "buddy_id": self._buddy_id,
            "skin_id": self._skin_id,
            "dirty": self._is_dirty(),
            "apply_enabled": self.apply_btn.isEnabled(),
            "cancel_enabled": self.cancel_btn.isEnabled(),
            "reset_enabled": self.reset_btn.isEnabled(),
            "preview_frames": len(self.preview._frames),  # noqa: SLF001
            "source_label": self.source_label.text(),
            "skin_rows": list(self._skin_rows.keys()),
            "skin": skin.id if skin is not None else None,
            "buddy": buddy.id if buddy is not None else None,
        }
