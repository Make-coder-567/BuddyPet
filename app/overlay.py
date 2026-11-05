"""特效叠加层：一个点击穿透、始终置顶的透明窗口，专门承载粒子和气泡。

拆成独立窗口的原因：宠物主窗口用 setMask 只保留轮廓区域，
这样桌面上大片透明区不会挡住鼠标；而粒子需要飘到轮廓之外，
只能画在另一个「不吃鼠标事件」的窗口里。
"""
from __future__ import annotations

import ctypes

from PyQt5.QtCore import QPointF, QRect, Qt
from PyQt5.QtGui import QPainter
from PyQt5.QtWidgets import QWidget

from .effects import EffectEngine

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_LAYERED = 0x00080000


class EffectOverlay(QWidget):
    MARGIN = 200

    def __init__(self, engine: EffectEngine):
        flags = (
            Qt.FramelessWindowHint
            | Qt.Tool
            | Qt.WindowStaysOnTopHint
            | Qt.NoDropShadowWindowHint
        )
        transparent_input = getattr(Qt, "WindowTransparentForInput", None)
        if transparent_input is not None:
            flags |= transparent_input
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.engine = engine
        self._pet_rect = QRect()

    # -- 窗口位置跟随宠物 -----------------------------------------
    def sync(self, pet_rect: QRect, feet: QPointF, pet_h: int) -> bool:
        m = self.MARGIN
        geo = QRect(pet_rect.x() - m, pet_rect.y() - m, pet_rect.width() + 2 * m, pet_rect.height() + 2 * m)
        moved = geo != self.geometry()
        if moved:
            self.setGeometry(geo)
        self.engine.set_anchor(QPointF(feet.x() - geo.x(), feet.y() - geo.y()), pet_h)
        return moved

    def has_content(self) -> bool:
        e = self.engine
        return bool(e.particles or e.box or e.card or e.bubble)

    # -- 绘制 -----------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform
        )
        self.engine.paint(painter)
        painter.end()

    # -- Windows 层面确保不吃鼠标、不抢焦点 -----------------------
    def showEvent(self, event) -> None:
        super().showEvent(event)
        _make_click_through(int(self.winId()))


def _make_click_through(hwnd: int) -> None:
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        if hasattr(user32, "GetWindowLongPtrW"):
            get_long = user32.GetWindowLongPtrW
            set_long = user32.SetWindowLongPtrW
            get_long.restype = ctypes.c_ssize_t
            set_long.restype = ctypes.c_ssize_t
        else:
            get_long = user32.GetWindowLongW
            set_long = user32.SetWindowLongW
        style = get_long(hwnd, GWL_EXSTYLE)
        set_long(hwnd, GWL_EXSTYLE, style | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_LAYERED)
    except Exception:
        pass
