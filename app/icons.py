"""菜单用的矢量小图标（用 QPainter 现画，避免额外的图片依赖）。"""
from __future__ import annotations

from PyQt5.QtCore import QPointF, QRectF, Qt
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

SIZE = 17


def _base(color: str) -> tuple[QPixmap, QPainter]:
    pm = QPixmap(SIZE, SIZE)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    pen = QPen(QColor(color))
    pen.setWidthF(1.5)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    return pm, p


def _finish(pm: QPixmap, p: QPainter) -> QIcon:
    p.end()
    return QIcon(pm)


def icon_open(color: str = "#F3F4F6") -> QIcon:
    """方框 + 右上箭头：打开程序。"""
    pm, p = _base(color)
    p.drawRoundedRect(QRectF(2.6, 5.4, 9.0, 9.0), 2.0, 2.0)
    p.drawLine(QPointF(8.4, 8.6), QPointF(14.2, 2.8))
    p.drawLine(QPointF(10.4, 2.8), QPointF(14.2, 2.8))
    p.drawLine(QPointF(14.2, 2.8), QPointF(14.2, 6.6))
    return _finish(pm, p)


def icon_sparkle(color: str = "#FF7A3D") -> QIcon:
    pm, p = _base(color)
    path = QPainterPath()
    path.moveTo(8.5, 1.6)
    path.quadTo(9.6, 6.6, 15.4, 8.5)
    path.quadTo(9.6, 10.4, 8.5, 15.4)
    path.quadTo(7.4, 10.4, 1.6, 8.5)
    path.quadTo(7.4, 6.6, 8.5, 1.6)
    p.drawPath(path)
    return _finish(pm, p)


def icon_gear(color: str = "#F3F4F6") -> QIcon:
    pm, p = _base(color)
    p.drawEllipse(QRectF(5.6, 5.6, 5.8, 5.8))
    for i in range(8):
        import math

        a = math.pi * i / 4
        p.drawLine(
            QPointF(8.5 + math.cos(a) * 5.0, 8.5 + math.sin(a) * 5.0),
            QPointF(8.5 + math.cos(a) * 7.2, 8.5 + math.sin(a) * 7.2),
        )
    return _finish(pm, p)


def icon_pin(color: str = "#F3F4F6") -> QIcon:
    pm, p = _base(color)
    p.drawEllipse(QRectF(4.8, 2.2, 7.4, 7.4))
    p.drawLine(QPointF(8.5, 9.9), QPointF(8.5, 14.8))
    p.drawLine(QPointF(5.6, 14.8), QPointF(11.4, 14.8))
    return _finish(pm, p)


def icon_lock(color: str = "#F3F4F6") -> QIcon:
    pm, p = _base(color)
    p.drawRoundedRect(QRectF(3.4, 7.4, 10.2, 7.6), 2.0, 2.0)
    path = QPainterPath()
    path.moveTo(5.6, 7.4)
    path.lineTo(5.6, 5.6)
    path.arcTo(QRectF(5.6, 2.2, 5.8, 5.8), 180, -180)
    path.lineTo(11.4, 7.4)
    p.drawPath(path)
    return _finish(pm, p)


def icon_target(color: str = "#F3F4F6") -> QIcon:
    pm, p = _base(color)
    p.drawEllipse(QRectF(3.2, 3.2, 10.6, 10.6))
    p.drawEllipse(QRectF(6.4, 6.4, 4.2, 4.2))
    p.drawLine(QPointF(8.5, 0.9), QPointF(8.5, 3.0))
    p.drawLine(QPointF(8.5, 14.0), QPointF(8.5, 16.1))
    return _finish(pm, p)


def icon_info(color: str = "#9CA1AB") -> QIcon:
    pm, p = _base(color)
    p.drawEllipse(QRectF(2.4, 2.4, 12.2, 12.2))
    p.drawPoint(QPointF(8.5, 5.4))
    p.drawLine(QPointF(8.5, 8.3), QPointF(8.5, 12.2))
    return _finish(pm, p)


def icon_power(color: str = "#9CA1AB") -> QIcon:
    pm, p = _base(color)
    path = QPainterPath()
    path.arcMoveTo(QRectF(3.2, 3.6, 10.6, 10.6), 65)
    path.arcTo(QRectF(3.2, 3.6, 10.6, 10.6), 65, 230)
    p.drawPath(path)
    p.drawLine(QPointF(8.5, 1.8), QPointF(8.5, 7.6))
    return _finish(pm, p)


def icon_eye(color: str = "#F3F4F6") -> QIcon:
    pm, p = _base(color)
    path = QPainterPath()
    path.moveTo(1.8, 8.5)
    path.quadTo(8.5, 1.4, 15.2, 8.5)
    path.quadTo(8.5, 15.6, 1.8, 8.5)
    p.drawPath(path)
    p.drawEllipse(QRectF(6.9, 6.9, 3.2, 3.2))
    return _finish(pm, p)


def icon_hidden(color: str = "#F3F4F6") -> QIcon:
    pm, p = _base(color)
    p.drawEllipse(QRectF(3.0, 4.6, 11.0, 8.2))
    p.drawLine(QPointF(2.6, 2.6), QPointF(14.6, 14.6))
    return _finish(pm, p)


def icon_check(color: str = "#FF7A3D") -> QIcon:
    pm, p = _base(color)
    pen = p.pen()
    pen.setWidthF(1.9)
    p.setPen(pen)
    p.drawPolyline(QPointF(3.2, 8.8), QPointF(7.0, 12.4), QPointF(14.0, 4.6))
    return _finish(pm, p)


def icon_heart(color: str = "#FF6E8A") -> QIcon:
    pm, p = _base(color)
    path = QPainterPath()
    path.moveTo(8.5, 14.6)
    path.cubicTo(1.0, 9.4, 2.0, 3.4, 8.5, 6.2)
    path.cubicTo(15.0, 3.4, 16.0, 9.4, 8.5, 14.6)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(path)
    return _finish(pm, p)


def icon_up(color: str = "#FF7A3D") -> QIcon:
    pm, p = _base(color)
    p.drawPolyline(QPointF(8.5, 2.8), QPointF(8.5, 13.6))
    p.drawPolyline(QPointF(4.4, 6.6), QPointF(8.5, 2.6), QPointF(12.6, 6.6))
    p.drawLine(QPointF(3.6, 15.0), QPointF(13.4, 15.0))
    return _finish(pm, p)


def icon_gift(color: str = "#FFB27A") -> QIcon:
    pm, p = _base(color)
    p.drawRoundedRect(QRectF(2.8, 6.2, 11.4, 8.8), 1.6, 1.6)
    p.drawLine(QPointF(2.8, 9.0), QPointF(14.2, 9.0))
    p.drawLine(QPointF(8.5, 6.2), QPointF(8.5, 15.0))
    p.drawArc(QRectF(5.4, 2.6, 6.2, 4.2), 0, 180 * 16)
    return _finish(pm, p)


def icon_star(color: str = "#FFD36E") -> QIcon:
    pm, p = _base(color)
    import math

    path = QPainterPath()
    for i in range(10):
        ang = -math.pi / 2 + math.pi * i / 5
        r = 7.0 if i % 2 == 0 else 2.9
        pt = QPointF(8.5 + math.cos(ang) * r, 8.5 + math.sin(ang) * r)
        if i == 0:
            path.moveTo(pt)
        else:
            path.lineTo(pt)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(path)
    return _finish(pm, p)


def icon_music(color: str = "#26DFBB") -> QIcon:
    pm, p = _base(color)
    p.drawLine(QPointF(6.4, 12.2), QPointF(6.4, 3.4))
    p.drawLine(QPointF(6.4, 3.4), QPointF(12.6, 2.0))
    p.drawLine(QPointF(12.6, 2.0), QPointF(12.6, 10.2))
    p.drawEllipse(QRectF(3.6, 10.6, 4.4, 3.6))
    p.drawEllipse(QRectF(9.8, 8.6, 4.4, 3.6))
    return _finish(pm, p)


def icon_cup(color: str = "#FFD8B0") -> QIcon:
    pm, p = _base(color)
    p.drawRoundedRect(QRectF(3.0, 6.4, 8.4, 7.2), 1.6, 1.6)
    p.drawArc(QRectF(10.6, 7.6, 4.4, 4.4), -80 * 16, 180 * 16)
    p.drawLine(QPointF(4.8, 3.4), QPointF(4.8, 4.8))
    p.drawLine(QPointF(9.6, 3.4), QPointF(9.6, 4.8))
    return _finish(pm, p)


def icon_moon(color: str = "#B9C0CC") -> QIcon:
    pm, p = _base(color)
    path = QPainterPath()
    path.moveTo(10.6, 2.4)
    path.arcTo(QRectF(3.2, 2.4, 11.0, 12.2), 62, 250)
    path.arcTo(QRectF(6.0, 1.4, 9.0, 12.0), 250, -230)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color))
    p.drawPath(path)
    return _finish(pm, p)


def icon_rotate(color: str = "#8AB4FF") -> QIcon:
    pm, p = _base(color)
    p.drawArc(QRectF(3.0, 3.0, 11.0, 11.0), 40 * 16, 280 * 16)
    p.drawPolyline(QPointF(12.4, 1.8), QPointF(14.2, 4.2), QPointF(11.6, 5.4))
    return _finish(pm, p)


def icon_confetti(color: str = "#FF7A3D") -> QIcon:
    pm, p = _base(color)
    pen = p.pen()
    pen.setWidthF(2.0)
    p.setPen(pen)
    p.drawLine(QPointF(3.4, 4.0), QPointF(6.2, 1.2))
    p.drawLine(QPointF(9.6, 10.4), QPointF(13.6, 9.0))
    p.drawLine(QPointF(5.0, 12.6), QPointF(3.2, 15.2))
    p.drawLine(QPointF(11.6, 3.0), QPointF(14.6, 5.4))
    return _finish(pm, p)


def effect_icon(effect_id: str) -> QIcon:
    return {
        "box": icon_gift("#FFB27A"),
        "hearts": icon_heart(),
        "hop": icon_up(),
        "sparkle": icon_star(),
        "confetti": icon_confetti(),
        "dance": icon_music(),
        "blink": icon_eye("#F3F4F6"),
        "coffee": icon_cup(),
        "sleep": icon_moon(),
        "spin": icon_rotate(),
    }.get(effect_id, icon_sparkle())


def avatar(pixmap: QPixmap, size: int = 68) -> QPixmap:
    """从整身立绘裁出头部，做成正方形头像。"""
    w, h = pixmap.width(), pixmap.height()
    side = int(w * 0.80)
    x = (w - side) // 2
    y = int(h * 0.02)
    return pixmap.copy(x, y, side, side).scaled(
        size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation
    )


def app_icon(pixmap: QPixmap) -> QIcon:
    """托盘图标：取宠物半身像，缩放到 32px。"""
    return QIcon(avatar(pixmap, 64))
