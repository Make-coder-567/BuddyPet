"""图鉴 UI 原子控件：**只画控件，不含业务**（架构 §8.1）。

四个类：
    * :class:`Badge`       —— 小圆角标签（已获得 / 默认 / 可用 / 可降级 / 不可用）
    * :class:`BuddyTile`   —— 形象缩略方块（可选中、可置灰）
    * :class:`SkinRow`     —— 皮肤行（缩略图 + 名称 + 状态 + 缺失/降级明细）
    * :class:`SkinPreview` —— 立绘预览（能播一次眨眼）

约定：所有"业务判断"（皮肤能不能用、缺什么）都由调用方算好后传进来
（``ValidationResult``），本模块不读配置、不碰 ``switcher``。
"""
from __future__ import annotations

import random
from pathlib import Path

from PyQt5.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .. import theme
from .models import Buddy, Skin, SkinStatus, ValidationResult

__all__ = ["Badge", "BuddyTile", "SkinRow", "SkinPreview", "TONE_COLORS"]

#: Badge 的四档色调（与 theme 保持一致）
TONE_COLORS: dict[str, str] = {
    "ok": theme.TEAL,
    "warn": theme.ORANGE,
    "bad": "#FF6B6B",
    "muted": theme.MUTED,
}

_STATUS_TONE: dict[SkinStatus, str] = {
    SkinStatus.READY: "ok",
    SkinStatus.DEGRADED: "warn",
    SkinStatus.BROKEN: "bad",
}

_STATUS_TEXT: dict[SkinStatus, str] = {
    SkinStatus.READY: "可用",
    SkinStatus.DEGRADED: "可降级",
    SkinStatus.BROKEN: "不可用",
}

#: 降级项里的"能力 token"（与 switcher.DEGRADED_TOKENS 同口径）
_CAP_TOKENS = ("视线跟随", "抓边姿态")


def _rgba(color: str, alpha: int) -> QColor:
    c = QColor(color)
    c.setAlpha(alpha)
    return c


def load_frame_pixmap(asset_dir: str | None, index: int, height: int) -> QPixmap | None:
    """按目录约定读第 ``index`` 帧并按高度缩放；任何失败返回 ``None``（不抛）。"""
    if not asset_dir:
        return None
    try:
        path = Path(asset_dir) / f"buddy_{max(0, min(5, int(index)))}.png"
        if not path.is_file():
            return None
        img = QImage(str(path))
        if img.isNull():
            return None
        pm = QPixmap.fromImage(img)
        if pm.isNull():
            return None
        target_h = max(8, int(height))
        target_w = max(4, round(target_h * pm.width() / max(1, pm.height())))
        return pm.scaled(target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    except Exception:  # noqa: BLE001 —— 预览/缩略图绝不能把面板拖崩
        return None


class Badge(QLabel):
    """小圆角标签。

    Args:
        text: 文案。
        tone: ``ok`` / ``warn`` / ``bad`` / ``muted``。
        parent: 父控件。
    """

    def __init__(self, text: str = "", tone: str = "muted", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Badge")
        self.setAlignment(Qt.AlignCenter)
        self._tone = "muted"
        self.set_tone(tone)

    def tone(self) -> str:
        return self._tone

    def set_tone(self, tone: str) -> None:
        self._tone = tone if tone in TONE_COLORS else "muted"
        color = TONE_COLORS[self._tone]
        self.setStyleSheet(
            f"QLabel#Badge{{color:{color};background:{_rgba(color, 34).name(QColor.HexArgb)};"
            f"border:1px solid {_rgba(color, 90).name(QColor.HexArgb)};"
            f"border-radius:7px;padding:0px 6px;font-size:7.5pt;font-weight:600;}}"
        )
        self.adjustSize()

    def set_state(self, text: str, tone: str) -> None:
        self.setText(text)
        self.set_tone(tone)


class _ClickCard(QWidget):
    """带"选中描边 + 悬停底色"的卡片基类（两个 tile/row 共用）。"""

    clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._selected = False
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

    # ---------------- 状态 ----------------
    def is_selected(self) -> bool:
        return self._selected

    def set_selected(self, value: bool) -> None:
        value = bool(value)
        if value != self._selected:
            self._selected = value
            self.update()

    def set_disabled_look(self, disabled: bool) -> None:
        self.setEnabled(not disabled)
        self.setCursor(Qt.ArrowCursor if disabled else Qt.PointingHandCursor)
        self.update()

    # ---------------- 交互 ----------------
    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.isEnabled():
            self.clicked.emit()
        super().mousePressEvent(event)

    # ---------------- 绘制 ----------------
    def _paint_card(self, p: QPainter, *, radius: float = 10.0) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.75, 0.75, self.width() - 1.5, self.height() - 1.5)
        if self._selected:
            bg = _rgba(theme.ORANGE, 40)
            border = QColor(theme.ORANGE)
        elif self._hover and self.isEnabled():
            bg = QColor(255, 255, 255, 20)
            border = QColor(255, 255, 255, 40)
        else:
            bg = QColor(255, 255, 255, 10)
            border = QColor(255, 255, 255, 24)
        p.setPen(border)
        p.setBrush(bg)
        p.drawRoundedRect(rect, radius, radius)


class BuddyTile(_ClickCard):
    """形象方块：缩略图 + 名称 + 已获得标记。

    Args:
        buddy: 形象数据。
        selected: 是否选中。
        on_click: 点击回调（传 buddy）。
        root: 项目根目录，用于 ``assets/frames`` 兜底缩略图。
        parent: 父控件。
    """

    TILE_W = 78
    TILE_H = 94
    THUMB = 60

    def __init__(
        self,
        buddy: Buddy,
        selected: bool = False,
        on_click=None,
        root: Path | str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.buddy = buddy
        self._on_click = on_click
        self._root = Path(root) if root is not None else None
        self.setFixedSize(QSize(self.TILE_W, self.TILE_H))
        self._selected = bool(selected)
        self._pm = self._load_thumb()

        self.name_label = QLabel(buddy.name, self)
        self.name_label.setObjectName("TileName")
        self.name_label.setAlignment(Qt.AlignCenter)
        self.name_label.setGeometry(2, self.TILE_H - 21, self.TILE_W - 4, 17)
        self.name_label.setStyleSheet(
            f"color:{theme.TEXT};font-size:8.5pt;background:transparent;"
        )
        self.setToolTip(f"{buddy.name}（{len(buddy.skins)} 套皮肤）")
        if not (buddy.owned and buddy.skins):
            self.set_disabled_look(True)
            self.setToolTip("未获得" if not buddy.owned else "该形象暂无可用皮肤")
            self.name_label.setStyleSheet(f"color:{theme.MUTED};font-size:8.5pt;background:transparent;")
        self.clicked.connect(self._emit_click)

    # ---------------- 内部 ----------------
    def _emit_click(self) -> None:
        if self._on_click is not None:
            self._on_click(self.buddy)

    def _thumb_source(self) -> str | None:
        """缩略图来源优先级：``buddy.thumbnail``（本地路径）→ 默认皮肤 → ``assets/frames``。"""
        thumb = (self.buddy.thumbnail or "").strip()
        if thumb and not thumb.lower().startswith(("http://", "https://")):
            if Path(thumb).is_file():
                return thumb
        default_skin = self.buddy.default_skin or (self.buddy.skins[0] if self.buddy.skins else None)
        if default_skin is not None and default_skin.asset_dir:
            candidate = Path(default_skin.asset_dir) / "buddy_0.png"
            if candidate.is_file():
                return str(candidate)
        if self._root is not None:
            fallback = self._root / "assets" / "frames" / "buddy_0.png"
            if fallback.is_file():
                return str(fallback)
        return None

    def _load_thumb(self) -> QPixmap | None:
        source = self._thumb_source()
        if source is None:
            return None
        try:
            img = QImage(source)
            if img.isNull():
                return None
            pm = QPixmap.fromImage(img)
            return pm.scaled(self.THUMB, self.THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except Exception:  # noqa: BLE001
            return None

    # ---------------- 绘制 ----------------
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        self._paint_card(p, radius=11.0)
        alpha = 255 if self.isEnabled() else 110
        area = QRectF((self.TILE_W - self.THUMB) / 2, 7, self.THUMB, self.THUMB)
        if self._pm is not None and not self._pm.isNull():
            p.setOpacity(alpha / 255.0)
            scaled = self._pm.scaled(
                int(area.width()), int(area.height()), Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
            x = area.x() + (area.width() - scaled.width()) / 2
            y = area.y() + (area.height() - scaled.height()) / 2
            p.drawPixmap(int(x), int(y), scaled)
            p.setOpacity(1.0)
        else:
            p.setPen(QColor(255, 255, 255, 40))
            p.setBrush(QColor(255, 255, 255, 12))
            p.drawRoundedRect(area, 8, 8)
            p.setPen(QColor(theme.MUTED))
            p.drawText(area, Qt.AlignCenter, "无图")
        if self.buddy.owned:
            # 右上角已获得小勾
            cx, cy, r = self.TILE_W - 13, 13, 6.0
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(theme.TEAL))
            p.drawEllipse(QRectF(cx - r, cy - r, r * 2, r * 2))
            p.setPen(QColor("#0C211D"))
            f = p.font()
            f.setPointSizeF(6.5)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(cx - r, cy - r, r * 2, r * 2), Qt.AlignCenter, "✓")
        p.end()


class SkinRow(_ClickCard):
    """皮肤行：缩略图 + 名称 + 状态 Badge + 明细行。

    Args:
        skin: 皮肤数据。
        validation: 该校验结果（``BROKEN`` 时置灰禁用）。
        selected: 是否选中。
        on_click: 点击回调（传 skeleton）。
        parent: 父控件。
    """

    ROW_H = 48

    def __init__(
        self,
        skin: Skin,
        validation: ValidationResult,
        selected: bool = False,
        on_click=None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.skin = skin
        self.validation = validation
        self._on_click = on_click
        self.setObjectName("SkinRow")
        self.setMinimumHeight(self.ROW_H)
        self.setMaximumHeight(self.ROW_H)
        self._selected = bool(selected)
        self._pm = load_frame_pixmap(skin.asset_dir, 0, 34)

        row = QHBoxLayout(self)
        row.setContentsMargins(7, 5, 9, 5)
        row.setSpacing(9)

        self.thumb = QLabel(self)
        self.thumb.setFixedSize(QSize(24, 34))
        self.thumb.setAlignment(Qt.AlignCenter)
        if self._pm is not None and not self._pm.isNull():
            self.thumb.setPixmap(self._pm)
        else:
            self.thumb.setText("·")
            self.thumb.setStyleSheet(f"color:{theme.MUTED};font-size:11pt;")
        row.addWidget(self.thumb, 0, Qt.AlignVCenter)

        texts = QVBoxLayout()
        texts.setSpacing(1)
        texts.setContentsMargins(0, 0, 0, 0)
        self.name_label = QLabel(skin.name, self)
        self.name_label.setObjectName("SkinName")
        self.name_label.setStyleSheet(
            f"color:{theme.TEXT if validation.can_apply else theme.MUTED};"
            f"font-size:9.5pt;background:transparent;"
        )
        texts.addWidget(self.name_label)
        self.detail_label = QLabel(self._detail_text(), self)
        self.detail_label.setStyleSheet(
            f"color:{theme.MUTED};font-size:8pt;background:transparent;"
        )
        texts.addWidget(self.detail_label)
        # 两行文本的最小宽度**不能**反向决定整行/整栏的最小宽度：QScrollArea 关了
        # 横向滚动条，一旦撑宽就会把右侧预览挤出可见区（T04 截图自审的真实缺陷）。
        for label in (self.name_label, self.detail_label):
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addLayout(texts, 1)

        tone = _STATUS_TONE.get(validation.status, "muted")
        badge_text = _STATUS_TEXT.get(validation.status, "未知")
        if skin.is_default:
            badge_text = "默认"
            tone = "warn"
        self.badge = Badge(badge_text, tone, self)
        row.addWidget(self.badge, 0, Qt.AlignVCenter)

        tip = [validation.message or _STATUS_TEXT.get(validation.status, "")]
        if validation.missing:
            tip.append("缺失：" + "、".join(validation.missing))
        self.setToolTip("\n".join(t for t in tip if t))

        if not validation.can_apply:
            self.set_disabled_look(True)
        self.clicked.connect(self._emit_click)

    def _detail_text(self) -> str:
        """一行明细：优先缺失项，其次降级明细，最后画布/帧数。"""
        vr = self.validation
        if vr.missing:
            return "缺失：" + "、".join(vr.missing[:2])
        details = [d for d in vr.degraded if d not in _CAP_TOKENS]
        if details:
            return details[0]
        if vr.canvas:
            return f"{vr.canvas[0]}×{vr.canvas[1]} · {vr.frames} 帧"
        return vr.message or ""

    def _emit_click(self) -> None:
        if self._on_click is not None:
            self._on_click(self.skin)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        self._paint_card(p, radius=9.0)
        p.end()


class SkinPreview(QWidget):
    """立绘预览：按需加载某个皮肤的第 0 帧，可播一次眨眼。

    Args:
        height: 立绘显示高度（宽度按画布比例算）。
        parent: 父控件。

    Signals:
        blink_finished: 一次眨眼播放完毕（已回到第 0 帧）。
    """

    blink_finished = pyqtSignal()
    STEP_MS = 110
    BOX_W = 132
    BOX_H = 192

    def __init__(self, height: int = 168, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._height = int(height)
        self._asset_dir: str | None = None
        self._frames: list[QPixmap] = []
        self._index = 0
        self._placeholder = "未选择皮肤"
        self.setFixedSize(QSize(self.BOX_W, self.BOX_H))

        self._tick = QTimer(self)
        self._tick.setInterval(self.STEP_MS)
        self._tick.timeout.connect(self._next_frame)

        self._auto = QTimer(self)
        self._auto.setSingleShot(True)
        self._auto.timeout.connect(self.blink)
        self._auto_enabled = False

    # ---------------- 对外 ----------------
    def set_skin(self, skin: Skin | None) -> None:
        """换预览目标；``None`` 显示占位。"""
        asset_dir = getattr(skin, "asset_dir", None) if skin is not None else None
        if asset_dir == self._asset_dir and self._frames:
            self._index = 0
            self.update()
            return
        self._stop_timers()
        self._asset_dir = asset_dir
        self._index = 0
        self._placeholder = "素材不可用" if skin is not None else "未选择皮肤"
        self._frames = self._load_frames()
        self.update()
        if self._auto_enabled and self._frames:
            self._schedule_auto()

    def set_auto_blink(self, enabled: bool) -> None:
        """是否自动眨眼（每次随机 2.6~5.2s 一次）。"""
        self._auto_enabled = bool(enabled)
        if self._auto_enabled and self._frames:
            self._schedule_auto()
        else:
            self._auto.stop()

    def blink(self) -> None:
        """播一次眨眼：0→1→…→5→0（110ms 一步）。"""
        if not self._frames:
            self.blink_finished.emit()
            return
        self._auto.stop()
        self._index = 1
        self.update()
        self._tick.start()

    def stop_animation(self) -> None:
        """停止一切定时器并停在第 0 帧（截图/关闭面板时用）。"""
        self._stop_timers()
        self._index = 0
        self.update()

    def has_frames(self) -> bool:
        return bool(self._frames)

    # ---------------- 内部 ----------------
    def _load_frames(self) -> list[QPixmap]:
        frames: list[QPixmap] = []
        for i in range(6):
            pm = load_frame_pixmap(self._asset_dir, i, self._height)
            if pm is None:
                return []
            frames.append(pm)
        return frames

    def _schedule_auto(self) -> None:
        if not self._auto_enabled:
            return
        self._auto.start(random.randint(2600, 5200))

    def _next_frame(self) -> None:
        if not self._frames:
            self._tick.stop()
            return
        self._index += 1
        if self._index >= len(self._frames):
            self._tick.stop()
            self._index = 0
            self.update()
            self.blink_finished.emit()
            self._schedule_auto()
            return
        self.update()

    def _stop_timers(self) -> None:
        self._tick.stop()
        self._auto.stop()

    def hideEvent(self, event) -> None:  # noqa: N802
        self.stop_animation()          # P8：面板关掉后不许后台空转
        super().hideEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.75, 0.75, self.width() - 1.5, self.height() - 1.5)
        p.setPen(QColor(255, 255, 255, 30))
        p.setBrush(QColor(255, 255, 255, 10))
        p.drawRoundedRect(rect, 12, 12)
        if not self._frames:
            p.setPen(QColor(theme.MUTED))
            f = p.font()
            f.setPointSizeF(8.5)
            p.setFont(f)
            p.drawText(rect, Qt.AlignCenter, self._placeholder)
            p.end()
            return
        pm = self._frames[max(0, min(len(self._frames) - 1, self._index))]
        x = (self.width() - pm.width()) / 2
        y = (self.height() - pm.height()) / 2 - 2
        p.drawPixmap(int(x), int(y), pm)
        p.end()
