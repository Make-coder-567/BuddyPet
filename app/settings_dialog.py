"""设置面板与关于窗口：暖色深灰卡片 + 橙色强调。"""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    pyqtProperty,
    pyqtSignal,
)
from PyQt5.QtGui import QColor, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import autostart, icons, theme
from .config import GALLERY_OWNED_FIELDS, LEFT_CLICK_CHOICES, STATS_OWNED_FIELDS, Settings, save
from .gallery.ui_section import GallerySection

APP_VERSION = "1.4.0"


class Switch(QWidget):
    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self._checked = bool(checked)
        self._k = 1.0 if checked else 0.0
        self.setFixedSize(42, 23)
        self.setCursor(Qt.PointingHandCursor)
        self._anim = QPropertyAnimation(self, b"k", self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, value: bool) -> None:
        value = bool(value)
        if value == self._checked:
            return
        self._checked = value
        self._anim.stop()
        self._anim.setStartValue(self._k)
        self._anim.setEndValue(1.0 if value else 0.0)
        self._anim.start()

    def _get_k(self) -> float:
        return self._k

    def _set_k(self, value: float) -> None:
        self._k = value
        self.update()

    k = pyqtProperty(float, _get_k, _set_k)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        off = QColor(56, 60, 70)
        on = QColor(theme.ORANGE)
        col = QColor(
            int(off.red() + (on.red() - off.red()) * self._k),
            int(off.green() + (on.green() - off.green()) * self._k),
            int(off.blue() + (on.blue() - off.blue()) * self._k),
        )
        p.setPen(Qt.NoPen)
        p.setBrush(col)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        d = self.height() - 6
        x = 3 + (self.width() - d - 6) * self._k
        p.setBrush(QColor("#FFFFFF"))
        p.drawEllipse(QRectF(x, 3, d, d))
        p.end()


class CardDialog(QDialog):
    """无边框圆角卡片，标题栏可拖动。"""

    def __init__(self, parent: QWidget | None = None, width: int = 460):
        super().__init__(
            parent,
            Qt.FramelessWindowHint | Qt.Dialog | Qt.NoDropShadowWindowHint | Qt.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self._drag_from: QPoint | None = None
        self._placed = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame(self)
        self.card.setObjectName("Card")
        self.card.setFixedWidth(width)
        outer.addWidget(self.card)
        self.body = QVBoxLayout(self.card)
        self.body.setContentsMargins(24, 18, 24, 18)
        self.body.setSpacing(9)

    # -- 位置：贴着宠物，且始终完整落在屏幕内
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._placed:
            self._placed = True
            self.adjustSize()
            self._place()

    def _place(self) -> None:
        parent = self.parentWidget()
        screen = QApplication.screenAt(parent.geometry().center()) if parent else None
        screen = screen or QApplication.primaryScreen()
        area = screen.availableGeometry()
        size = self.size()
        anchor = parent.geometry().center() if parent else area.center()
        x = anchor.x() - size.width() // 2
        y = anchor.y() - size.height() // 2
        x = max(area.left() + 16, min(x, area.right() - size.width() - 15))
        y = max(area.top() + 16, min(y, area.bottom() - size.height() - 15))
        self.move(x, y)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and event.pos().y() < 58:
            self._drag_from = event.globalPos() - self.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_from is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPos() - self._drag_from)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_from = None
        super().mouseReleaseEvent(event)


def _label(text: str, name: str = "") -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    return lb


def _section(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("Section")
    return lb


def _hint(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("Sub")
    lb.setWordWrap(True)
    return lb


#: 滚动区样式：透明底 + 8px 暖色圆角滚动条（与卡片配色一致）
_SCROLL_QSS = """
QScrollArea { background: transparent; border: none; }
QScrollBar:vertical {
    width: 8px; background: transparent; margin: 0px;
}
QScrollBar::handle:vertical {
    background: rgba(255,122,61,0.32);
    border-radius: 4px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: rgba(255,122,61,0.62);
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""


class SettingsDialog(CardDialog):
    """设置面板：头部固定 + 中间滚动区 + 底部固定操作栏。

    为什么要滚动区（架构 D4）：加入图鉴卡片后内容高度会超过 1080p 可用高度，
    不重构则「保存 / 取消」会被顶出屏幕。因此：
        * 头部标题栏留在 ``self.body``；
        * 所有分组挂到滚动内容 ``self.col``（**不是** ``self.body``）；
        * footer（恢复默认 / 取消 / 保存）留在 ``self.body``，永远可见。
    面板总高硬上限 = 屏幕可用高度 × 0.88。
    """

    preview = pyqtSignal()

    #: 除滚动区之外的固定开销（头部 + divider + footer + 边距 + 间距）
    _NON_SCROLL_H = 172

    def __init__(
        self,
        cfg: Settings,
        root: Path,
        pet_pixmap: QPixmap,
        parent: QWidget | None = None,
        gallery=None,
        focus_section: str = "",
        switcher=None,
    ):
        super().__init__(parent, width=516)
        self.cfg = cfg
        self.root = root
        self.gallery = gallery
        self.switcher = switcher
        self._focus_section = focus_section
        self._snapshot = cfg.to_dict()
        self.setWindowTitle("桌面宠物设置")

        # ---------- 头部（固定，不随滚动） ----------
        header = QHBoxLayout()
        header.setSpacing(10)
        avatar = QLabel()
        avatar.setFixedSize(38, 38)
        avatar.setPixmap(icons.avatar(pet_pixmap, 76))
        header.addWidget(avatar)
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(_label("桌面宠物设置", "Title"))
        # 副标题不换行：``_hint`` 默认 wordWrap=True，在标题栏这种窄列里会把
        # 「Buddy · 常驻桌面，随时待命」折成两行，留下一个孤零零的「随时待命」。
        subtitle = _hint("Buddy · 常驻桌面，随时待命")
        subtitle.setWordWrap(False)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        close = QPushButton("×")
        close.setObjectName("Ghost")
        close.setFixedSize(26, 26)
        close.setStyleSheet("font-size: 15pt; padding: 0px; margin-bottom: 4px;")
        close.clicked.connect(self.reject)
        header.addWidget(close, 0, Qt.AlignTop)
        self.body.addLayout(header)
        self.body.addWidget(self._divider())

        # ---------- 滚动区 ----------
        self.scroll = QScrollArea(self.card)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.setStyleSheet(_SCROLL_QSS)
        self.content = QWidget()
        self.content.setObjectName("ScrollContent")
        # 必须带 #ScrollContent 选择器！写成裸声明 ``background: transparent`` 时，
        # Qt 会把这条规则扩散到**所有后代**，把滚动区里 QPushButton 的
        # background-color 一起抹成透明 —— 图鉴里「应用 / 取消 / 恢复默认」三个按钮
        # 会变成「只剩文字、没有按钮底」的隐形按钮（T04 像素级自审抓到）。
        self.content.setStyleSheet("#ScrollContent { background: transparent; }")
        # 防溢出硬保险：横向滚动条是关掉的，内容一旦比视口宽，右侧就被裁掉。
        self.content.setMinimumWidth(0)
        self.col = QVBoxLayout(self.content)
        self.col.setContentsMargins(0, 0, 8, 0)     # 右侧留给滚动条
        self.col.setSpacing(9)
        self.scroll.setWidget(self.content)
        # 高度：总高不超过屏幕可用高度的 88%，超出才滚动
        avail = QApplication.primaryScreen().availableGeometry().height()
        self._max_total_h = max(420, int(avail * 0.88))
        scroll_h = max(240, min(760, self._max_total_h - self._NON_SCROLL_H))
        self.scroll.setFixedHeight(scroll_h)
        self.setMaximumHeight(self._max_total_h)
        self.body.addWidget(self.scroll, 1)

        # ---------- 图鉴（本次主角，放最前，避免用户找） ----------
        self.gallery_section = GallerySection(
            self.cfg, self.root, service=gallery, switcher=switcher, parent=self.content
        )
        self.gallery_section.set_scroll_parent(self.scroll)
        self.col.addWidget(self.gallery_section)
        self.col.addWidget(self._divider())

        # ---------- 外观 ----------
        self.col.addWidget(_section("外观"))
        self.size_slider, self.size_value = self._slider_row("宠物大小", 120, 360, cfg.size, " px", 150)
        self.size_slider.valueChanged.connect(self._on_size)
        self.opacity_slider, self.opacity_value = self._slider_row(
            "不透明度", 35, 100, int(round(cfg.opacity * 100)), " %", 150
        )
        self.opacity_slider.valueChanged.connect(self._on_opacity)
        self.lively_slider, self.lively_value = self._slider_row(
            "活泼程度", 20, 180, int(round(cfg.liveliness * 100)), " %", 150
        )
        self.lively_slider.valueChanged.connect(self._on_lively)

        # ---------- 交互 ----------
        self.col.addWidget(self._divider())
        self.col.addWidget(_section("交互"))
        self.click_combo = QComboBox()
        for key, text in LEFT_CLICK_CHOICES.items():
            self.click_combo.addItem(text, key)
        self.click_combo.setCurrentIndex(max(0, self.click_combo.findData(cfg.left_click)))
        self.click_combo.currentIndexChanged.connect(self._on_click_mode)
        self._row("左键单击", self.click_combo)

        self.idle_switch = Switch(cfg.idle_anim)
        self.idle_switch.toggled.connect(self._on_idle)
        self._row("待机呼吸动画", self.idle_switch)

        self.auto_switch = Switch(cfg.auto_effect)
        self.auto_switch.toggled.connect(self._on_auto)
        self._row("定时随机互动", self.auto_switch)

        self.interval_slider, self.interval_value = self._slider_row(
            "互动间隔", 1, 60, cfg.auto_effect_min, " 分钟", 128
        )
        self.interval_slider.valueChanged.connect(self._on_interval)
        self.interval_slider.setEnabled(cfg.auto_effect)

        # 空闲自动漫游：人离开一会儿，Buddy 自己下地溜达
        self.roam_switch = Switch(cfg.roam_enabled)
        self.roam_switch.toggled.connect(self._on_roam)
        self._row("空闲自动漫游", self.roam_switch)

        self.roam_idle_slider, self.roam_idle_value = self._slider_row(
            "触发空闲时长", 1, 30, cfg.roam_idle_min, " 分钟", 128
        )
        self.roam_idle_slider.valueChanged.connect(self._on_roam_idle)

        self.roam_speed_slider, self.roam_speed_value = self._slider_row(
            "漫游速度", 30, 180, cfg.roam_speed, " px/s", 128
        )
        self.roam_speed_slider.valueChanged.connect(self._on_roam_speed)

        self.roam_home_switch = Switch(cfg.roam_return_home)
        self.roam_home_switch.toggled.connect(self._on_roam_home)
        self._row("回来时归位", self.roam_home_switch)

        self.roam_fun_switch = Switch(cfg.roam_effects)
        self.roam_fun_switch.toggled.connect(self._on_roam_fun)
        self._row("漫游时随机互动", self.roam_fun_switch)

        roam_hint = _hint("无人操作一会儿，Buddy 会在屏幕里自己溜达；你一动键鼠，它就小跑回来。")
        self.col.addWidget(roam_hint)
        self._sync_roam_enabled()

        self.hint_switch = Switch(cfg.bubble_hint)
        self.hint_switch.toggled.connect(self._on_hint)
        self._row("悬停提示气泡", self.hint_switch)

        self.edge_switch = Switch(cfg.edge_cling)
        self.edge_switch.toggled.connect(self._on_edge_cling)
        self._row("贴边依附", self.edge_switch)

        self.gaze_switch = Switch(cfg.gaze_follow)
        self.gaze_switch.toggled.connect(self._on_gaze)
        self._row("视线跟随鼠标", self.gaze_switch)

        # 全局快捷键（仅 Windows 生效；关掉后不影响拖放/双击/音乐感知）
        self.hotkey_switch = Switch(cfg.hotkey_enabled)
        self.hotkey_switch.toggled.connect(self._on_hotkey)
        self._row("全局快捷键（Ctrl+Alt+B）", self.hotkey_switch)

        # 音乐感知（有声音时跳舞/眨眼；完全可关）
        self.music_switch = Switch(cfg.music_aware)
        self.music_switch.toggled.connect(self._on_music)
        self._row("音乐感知", self.music_switch)

        self.sound_switch = Switch(cfg.sound_enabled)
        self.sound_switch.toggled.connect(self._on_sound)
        self._row("音效", self.sound_switch)

        self.volume_slider, self.volume_value = self._slider_row(
            "音效音量", 0, 100, int(round(cfg.sound_volume * 100)), " %", 150
        )
        self.volume_slider.valueChanged.connect(self._on_volume)

        # ---------- 系统 ----------
        self.col.addWidget(self._divider())
        self.col.addWidget(_section("系统"))
        self.top_switch = Switch(cfg.always_on_top)
        self.top_switch.toggled.connect(self._on_top)
        self._row("始终置顶", self.top_switch)

        self.lock_switch = Switch(cfg.lock_pos)
        self.lock_switch.toggled.connect(self._on_lock)
        self._row("锁定位置", self.lock_switch)

        self.start_switch = Switch(autostart.is_enabled())
        self._row("开机自动启动", self.start_switch)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        path_row.addWidget(_label("程序路径"))
        self.path_edit = QLineEdit(cfg.workbuddy_path)
        self.path_edit.setPlaceholderText("留空自动检测 WorkBuddy.exe")
        self.path_edit.setStyleSheet(
            f"QLineEdit{{background:rgba(255,255,255,0.05);border:1px solid rgba(255,255,255,0.09);"
            f"border-radius:8px;padding:5px 9px;color:{theme.TEXT};font-size:8.5pt;}}"
        )
        self.path_edit.editingFinished.connect(self._on_path)
        browse = QPushButton("浏览")
        browse.setObjectName("Ghost")
        browse.clicked.connect(self._browse)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse)
        self.col.addLayout(path_row)
        self.col.addStretch(1)

        # ---------- 底部（固定，不随滚动；三个按钮必须始终可见） ----------
        self.body.addSpacing(2)
        footer = QHBoxLayout()
        footer.setSpacing(8)
        reset = QPushButton("恢复默认")
        reset.setObjectName("Ghost")
        reset.clicked.connect(self._reset)
        footer.addWidget(reset)
        footer.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        ok = QPushButton("保存")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        footer.addWidget(ok)
        self.footer_reset = reset
        self.footer_cancel = cancel
        self.footer_ok = ok
        self.footer_layout = footer
        self.body.addLayout(footer)

        self.setStyleSheet(theme.DIALOG_QSS)

    # ---------- 定位分组 ----------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._focus_section == "gallery":
            self._focus_section = ""
            QTimer.singleShot(0, self.gallery_section.scroll_into_view)

    # ---------------- 小部件工厂
    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet("background: rgba(255,255,255,0.07);")
        return line

    def _row(self, text: str, widget: QWidget) -> None:
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(_label(text))
        row.addStretch(1)
        row.addWidget(widget, 0, Qt.AlignVCenter)
        self.col.addLayout(row)

    def _slider_row(self, text: str, lo: int, hi: int, value: int, suffix: str, width: int):
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(_label(text))
        row.addStretch(1)
        val = _label(f"{value}{suffix}", "Value")
        val.setMinimumWidth(50)
        val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(lo, hi)
        slider.setValue(value)
        slider.setFixedWidth(width)
        slider.setCursor(Qt.PointingHandCursor)
        row.addWidget(val)
        row.addWidget(slider, 0, Qt.AlignVCenter)

        def _update(v: int) -> None:
            val.setText(f"{v}{suffix}")

        slider.valueChanged.connect(_update)
        self.col.addLayout(row)
        return slider, val

    # ---------------- 事件
    def _changed(self) -> None:
        self.cfg.clamp()
        save(self.cfg)
        self.preview.emit()

    def _on_size(self, v: int) -> None:
        self.cfg.size = v
        self._changed()

    def _on_opacity(self, v: int) -> None:
        self.cfg.opacity = v / 100.0
        self._changed()

    def _on_lively(self, v: int) -> None:
        self.cfg.liveliness = v / 100.0
        self._changed()

    def _on_click_mode(self, _idx: int) -> None:
        self.cfg.left_click = self.click_combo.currentData()
        self._changed()

    def _on_idle(self, checked: bool) -> None:
        self.cfg.idle_anim = checked
        self._changed()

    def _on_auto(self, checked: bool) -> None:
        self.cfg.auto_effect = checked
        self.interval_slider.setEnabled(checked)
        self._changed()

    def _on_interval(self, v: int) -> None:
        self.cfg.auto_effect_min = v
        self._changed()

    def _on_roam(self, checked: bool) -> None:
        self.cfg.roam_enabled = bool(checked)
        self._sync_roam_enabled()
        self._changed()

    def _on_roam_idle(self, v: int) -> None:
        self.cfg.roam_idle_min = v
        self._changed()

    def _on_roam_speed(self, v: int) -> None:
        self.cfg.roam_speed = v
        self._changed()

    def _on_roam_home(self, checked: bool) -> None:
        self.cfg.roam_return_home = bool(checked)
        self._changed()

    def _on_roam_fun(self, checked: bool) -> None:
        self.cfg.roam_effects = bool(checked)
        self._changed()

    def _sync_roam_enabled(self) -> None:
        """总开关关掉时把子项一起置灰，避免「改了却不生效」的错觉。"""
        on = bool(self.cfg.roam_enabled)
        for widget in (
            self.roam_idle_slider,
            self.roam_speed_slider,
            self.roam_home_switch,
            self.roam_fun_switch,
        ):
            widget.setEnabled(on)

    def _on_hint(self, checked: bool) -> None:
        self.cfg.bubble_hint = checked
        self._changed()

    def _on_edge_cling(self, checked: bool) -> None:
        self.cfg.edge_cling = checked
        self._changed()

    def _on_gaze(self, checked: bool) -> None:
        self.cfg.gaze_follow = checked
        self._changed()

    def _on_hotkey(self, checked: bool) -> None:
        self.cfg.hotkey_enabled = checked
        self._changed()

    def _on_music(self, checked: bool) -> None:
        self.cfg.music_aware = checked
        self._changed()

    def _on_sound(self, checked: bool) -> None:
        self.cfg.sound_enabled = checked
        self._changed()

    def _on_volume(self, v: int) -> None:
        self.cfg.sound_volume = v / 100.0
        self._changed()

    def _on_top(self, checked: bool) -> None:
        self.cfg.always_on_top = checked
        self._changed()

    def _on_lock(self, checked: bool) -> None:
        self.cfg.lock_pos = checked
        self._changed()

    def _on_path(self) -> None:
        self.cfg.workbuddy_path = self.path_edit.text().strip()
        self._changed()

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择 WorkBuddy.exe", "", "可执行文件 (*.exe)")
        if path:
            self.path_edit.setText(path)
            self._on_path()

    def _reset(self) -> None:
        """恢复默认：只重置「外观 / 交互 / 系统」三段。

        跳过 ``GALLERY_OWNED_FIELDS``（架构 D5）：图鉴是「立即提交」语义，
        选中皮肤与同步状态一旦点下就已落盘并已换装，若在这里重置会把用户
        刚选的皮肤悄悄改回去，属于数据一致性破坏。
        """
        defaults = Settings()
        for key, value in defaults.to_dict().items():
            if key in GALLERY_OWNED_FIELDS or key in STATS_OWNED_FIELDS:
                continue
            setattr(self.cfg, key, value)
        self._sync_widgets()
        self.gallery_section.refresh_from_service()
        self._changed()

    def _sync_widgets(self) -> None:
        sliders = (
            self.size_slider,
            self.opacity_slider,
            self.lively_slider,
            self.interval_slider,
            self.roam_idle_slider,
            self.roam_speed_slider,
            self.volume_slider,
        )
        for widget in sliders:
            widget.blockSignals(True)
        self.size_slider.setValue(self.cfg.size)
        self.opacity_slider.setValue(int(round(self.cfg.opacity * 100)))
        self.lively_slider.setValue(int(round(self.cfg.liveliness * 100)))
        self.interval_slider.setValue(self.cfg.auto_effect_min)
        self.roam_idle_slider.setValue(self.cfg.roam_idle_min)
        self.roam_speed_slider.setValue(self.cfg.roam_speed)
        vol_pct = int(round(self.cfg.sound_volume * 100))
        self.volume_slider.setValue(vol_pct)
        for widget in sliders:
            widget.blockSignals(False)
        self.size_value.setText(f"{self.cfg.size} px")
        self.opacity_value.setText(f"{int(round(self.cfg.opacity * 100))} %")
        self.lively_value.setText(f"{int(round(self.cfg.liveliness * 100))} %")
        self.interval_value.setText(f"{self.cfg.auto_effect_min} 分钟")
        self.roam_idle_value.setText(f"{self.cfg.roam_idle_min} 分钟")
        self.roam_speed_value.setText(f"{self.cfg.roam_speed} px/s")
        self.volume_value.setText(f"{vol_pct} %")
        self.click_combo.setCurrentIndex(max(0, self.click_combo.findData(self.cfg.left_click)))
        self.idle_switch.setChecked(self.cfg.idle_anim)
        self.auto_switch.setChecked(self.cfg.auto_effect)
        self.interval_slider.setEnabled(self.cfg.auto_effect)
        self.roam_switch.setChecked(self.cfg.roam_enabled)
        self.roam_home_switch.setChecked(self.cfg.roam_return_home)
        self.roam_fun_switch.setChecked(self.cfg.roam_effects)
        self._sync_roam_enabled()
        self.hint_switch.setChecked(self.cfg.bubble_hint)
        self.edge_switch.setChecked(self.cfg.edge_cling)
        self.gaze_switch.setChecked(self.cfg.gaze_follow)
        self.hotkey_switch.setChecked(self.cfg.hotkey_enabled)
        self.music_switch.setChecked(self.cfg.music_aware)
        self.sound_switch.setChecked(self.cfg.sound_enabled)
        self.top_switch.setChecked(self.cfg.always_on_top)
        self.lock_switch.setChecked(self.cfg.lock_pos)
        self.path_edit.setText(self.cfg.workbuddy_path)

    def accept(self) -> None:
        autostart.set_enabled(self.start_switch.isChecked(), self.root)
        self.cfg.clamp()
        save(self.cfg)
        super().accept()

    def reject(self) -> None:
        """取消：回滚快照中「非图鉴、非统计」字段。

        ``GALLERY_OWNED_FIELDS`` 不进回滚 —— 图鉴是立即提交，取消设置面板
        不应该把已经切换好的皮肤退回去（否则界面显示与磁盘状态不一致）。
        ``STATS_OWNED_FIELDS``（拖放计数 / 战绩）同样不回滚 —— 那是使用数据，
        不是设置项，回滚设置不该把战绩清掉。
        """
        for key, value in self._snapshot.items():
            if key in GALLERY_OWNED_FIELDS or key in STATS_OWNED_FIELDS:
                continue
            setattr(self.cfg, key, value)
        save(self.cfg)
        self.preview.emit()
        super().reject()


class AboutDialog(CardDialog):
    def __init__(self, pet_pixmap: QPixmap, parent: QWidget | None = None):
        super().__init__(parent, width=372)
        self.setWindowTitle("关于 Buddy")
        self.body.setContentsMargins(26, 22, 26, 20)
        self.body.setSpacing(8)

        avatar = QLabel()
        avatar.setAlignment(Qt.AlignCenter)
        avatar.setPixmap(pet_pixmap.scaledToHeight(132, Qt.SmoothTransformation))
        self.body.addWidget(avatar)

        name = QLabel("Buddy 桌面宠物")
        name.setObjectName("Title")
        name.setAlignment(Qt.AlignCenter)
        self.body.addWidget(name)

        version = QLabel(f"v{APP_VERSION} · 为 WorkBuddy 打造")
        version.setObjectName("Sub")
        version.setAlignment(Qt.AlignCenter)
        self.body.addWidget(version)

        desc = QLabel(
            "左键单击：打开 WorkBuddy\n"
            "右键单击：设置与互动效果\n"
            "按住拖动：挪到桌面任意位置\n\n"
            "形象取自 WorkBuddy 成长中心的「盲盒 Buddy」"
        )
        desc.setObjectName("Sub")
        desc.setAlignment(Qt.AlignCenter)
        self.body.addWidget(desc)

        ok = QPushButton("知道啦")
        ok.setObjectName("Primary")
        ok.clicked.connect(self.accept)
        self.body.addSpacing(6)
        self.body.addWidget(ok, 0, Qt.AlignHCenter)
        self.setStyleSheet(theme.DIALOG_QSS)
