"""互动特效引擎：粒子 + 宠物姿态时间线。

引擎不关心窗口，只负责：
- 维护激活中的特效，逐帧汇总出宠物姿态（缩放 / 位移 / 旋转 / 透明度 / 眼睛帧）
- 维护粒子、盲盒与提示气泡
- 把上述内容画到传入的 QPainter 上
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from PyQt5.QtCore import QObject, QPointF, QRectF, Qt, pyqtSignal
from PyQt5.QtGui import (
    QColor,
    QFontMetrics,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)

from . import theme

# ---------------------------------------------------------------- 数学小工具


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ease_out_cubic(t: float) -> float:
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_in_out(t: float) -> float:
    t = clamp(t)
    return 3 * t * t - 2 * t * t * t


def ease_out_back(t: float, s: float = 1.7) -> float:
    t = clamp(t)
    return 1 + (s + 1) * (t - 1) ** 3 + s * (t - 1) ** 2


def ease_out_bounce(t: float) -> float:
    t = clamp(t)
    n1, d1 = 7.5625, 2.75
    if t < 1 / d1:
        return n1 * t * t
    if t < 2 / d1:
        t -= 1.5 / d1
        return n1 * t * t + 0.75
    if t < 2.5 / d1:
        t -= 2.25 / d1
        return n1 * t * t + 0.9375
    t -= 2.625 / d1
    return n1 * t * t + 0.984375


# ---------------------------------------------------------------- 数据结构


@dataclass
class Pose:
    sx: float = 1.0
    sy: float = 1.0
    dy: float = 0.0
    rot: float = 0.0
    alpha: float = 1.0
    eyes: int | None = None      # 0..5 眨眼帧
    glow: float = 0.0


@dataclass
class Particle:
    kind: str
    x: float
    y: float
    vx: float = 0.0
    vy: float = 0.0
    g: float = 0.0
    ttl: float = 1.0
    life: float = 0.0
    size: float = 8.0
    rot: float = 0.0
    spin: float = 0.0
    color: QColor = field(default_factory=lambda: QColor("#FF7A3D"))
    text: str = ""
    wobble: float = 0.0
    wobble_amp: float = 0.0
    drag: float = 0.0
    alpha: float = 1.0

    def ratio(self) -> float:
        return clamp(self.life / self.ttl if self.ttl else 1.0)

    def fade(self) -> float:
        r = self.ratio()
        fade_in = clamp(self.life / 0.12)
        fade_out = 1.0 if r < 0.55 else (1.0 - (r - 0.55) / 0.45) ** 1.3
        return clamp(fade_in * fade_out) * self.alpha


CONFETTI_COLORS = ["#FF7A3D", "#26DFBB", "#FFD166", "#FF6E8A", "#8AB4FF", "#FFFFFF"]


class EffectEngine(QObject):
    #: 某个非眨眼特效「自然播放完成」时发出（str 为该特效的 id）。
    #: 被 supersede 清掉的特效不会走到 done 分支，因此不会触发本信号。
    effect_finished = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.rng = random.Random()
        self.particles: list[Particle] = []
        self.effects: list["BaseEffect"] = []
        self.anchor = QPointF(0, 0)      # 宠物脚底中点（窗口内坐标）
        self.pet_h = 210
        self.box: dict | None = None
        self.card: dict | None = None
        self.bubble: dict | None = None

    # -- 生命周期 -------------------------------------------------
    def set_anchor(self, point: QPointF, pet_h: int) -> None:
        self.anchor = point
        self.pet_h = pet_h

    def start(self, effect_id: str, supersede: bool = True) -> bool:
        cls = EFFECTS.get(effect_id)
        if cls is None:
            return False
        if supersede:
            self.effects = [e for e in self.effects if isinstance(e, BlinkEffect)]
            self.box = None
            self.card = None
        eff = cls(self)
        self.effects.append(eff)
        eff.on_start()
        return True

    def stop_all(self) -> None:
        self.effects.clear()
        self.particles.clear()
        self.box = None
        self.card = None
        self.bubble = None

    def say(self, text: str, duration: float = 2.2) -> None:
        self.bubble = {"text": text, "t": 0.0, "dur": duration}

    def clear_bubble(self) -> None:
        self.bubble = None

    def is_busy(self) -> bool:
        return any(not isinstance(e, BlinkEffect) for e in self.effects)

    # -- 每帧 -----------------------------------------------------
    def tick(self, dt: float) -> Pose:
        pose = Pose()
        alive: list[BaseEffect] = []
        for eff in self.effects:
            p = eff.update(dt)
            if not eff.done:
                alive.append(eff)
            else:
                # 非眨眼特效「自然播放完成」时发信号（供 PetWindow 触发完成音效）。
                # 被 supersede 清掉的特效不会进入 done 分支，无需处理。
                if not isinstance(eff, BlinkEffect):
                    self.effect_finished.emit(eff.id)
            pose.sx *= p.sx
            pose.sy *= p.sy
            pose.dy += p.dy
            pose.rot += p.rot
            pose.alpha *= p.alpha
            pose.glow = max(pose.glow, p.glow)
            if p.eyes is not None:
                pose.eyes = p.eyes
        self.effects = alive

        for p in self.particles:
            p.life += dt
            if p.drag:
                p.vx *= max(0.0, 1.0 - p.drag * dt)
                p.vy *= max(0.0, 1.0 - p.drag * dt)
            p.vy += p.g * dt
            p.x += p.vx * dt
            p.y += p.vy * dt
            if p.wobble_amp:
                p.wobble += dt * 3.2
                p.x += math.sin(p.wobble) * p.wobble_amp * dt * 4.0
            p.rot += p.spin * dt
        self.particles = [p for p in self.particles if p.life < p.ttl]

        if self.bubble:
            self.bubble["t"] += dt
            if self.bubble["t"] > self.bubble["dur"]:
                self.bubble = None
        if self.card:
            self.card["t"] += dt
            if self.card["t"] > self.card["dur"]:
                self.card = None
        return pose

    # -- 生成粒子 -------------------------------------------------
    def spawn(self, kind: str, dx: float, dy: float, **kw) -> Particle:
        p = Particle(kind=kind, x=self.anchor.x() + dx, y=self.anchor.y() + dy, **kw)
        if len(self.particles) < 280:
            self.particles.append(p)
        return p

    def burst(
        self,
        kind: str,
        dx: float,
        dy: float,
        count: int,
        speed: tuple[float, float],
        angle: tuple[float, float] = (0, math.tau),
        **kw,
    ) -> None:
        size = kw.pop("size", 8)
        jitter = kw.pop("size_jitter", 0.4)
        for _ in range(count):
            a = self.rng.uniform(*angle)
            v = self.rng.uniform(*speed)
            self.spawn(
                kind,
                dx,
                dy,
                vx=math.cos(a) * v,
                vy=math.sin(a) * v,
                size=size * self.rng.uniform(1 - jitter, 1 + jitter),
                **kw,
            )

    # -- 绘制 -----------------------------------------------------
    def paint(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self.box:
            self._paint_box(painter)
        for p in self.particles:
            if p.kind in ("ring", "ray"):
                _paint_particle(painter, p)
        for p in self.particles:
            if p.kind not in ("ring", "ray", "lid"):
                _paint_particle(painter, p)
        for p in self.particles:
            if p.kind == "lid":
                _paint_particle(painter, p)
        if self.card:
            self._paint_card(painter)
        if self.bubble:
            self._paint_bubble(painter)

    def _paint_box(self, painter: QPainter) -> None:
        b = self.box or {}
        h = self.pet_h
        size = h * 0.46 * b.get("scale", 1.0)
        if size < 2:
            return
        cx = self.anchor.x() + b.get("dx", 0.0)
        cy = self.anchor.y() + b.get("dy", 0.0)
        alpha = b.get("alpha", 1.0)
        if alpha <= 0.01:
            return
        painter.save()
        painter.setOpacity(alpha)
        painter.translate(cx, cy)
        painter.rotate(b.get("rot", 0.0))
        painter.translate(0, -size / 2)

        shadow = QRadialGradient(QPointF(0, size / 2 + 4), size * 0.58)
        shadow.setColorAt(0, QColor(0, 0, 0, 95))
        shadow.setColorAt(1, QColor(0, 0, 0, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(shadow)
        painter.drawEllipse(QPointF(0, size / 2 + 2), size * 0.56, size * 0.15)

        body = QRectF(-size / 2, -size / 2, size, size)
        grad = QLinearGradient(body.topLeft(), body.bottomRight())
        grad.setColorAt(0.0, QColor("#FF9A62"))
        grad.setColorAt(0.55, QColor("#F0671F"))
        grad.setColorAt(1.0, QColor("#C4470F"))
        painter.setBrush(grad)
        painter.setPen(QPen(QColor(255, 255, 255, 55), 1))
        painter.drawRoundedRect(body, size * 0.10, size * 0.10)

        lid = QRectF(-size / 2 - size * 0.02, -size / 2 - size * 0.11, size * 1.04, size * 0.21)
        lid_grad = QLinearGradient(lid.topLeft(), lid.bottomLeft())
        lid_grad.setColorAt(0.0, QColor("#FFAF80"))
        lid_grad.setColorAt(1.0, QColor("#E85E22"))
        painter.setBrush(lid_grad)
        painter.drawRoundedRect(lid, size * 0.07, size * 0.07)

        painter.setBrush(QColor(38, 223, 187, 230))
        painter.drawRect(QRectF(-size * 0.032, -size / 2, size * 0.064, size))
        painter.drawRect(QRectF(-size / 2, size * 0.12, size, size * 0.055))

        painter.setBrush(QColor(35, 20, 12, 215))
        ears = QPainterPath()
        for sign in (-1, 1):
            ears.moveTo(sign * size * 0.02, -size * 0.16)
            ears.lineTo(sign * size * 0.13, -size * 0.27)
            ears.lineTo(sign * size * 0.17, -size * 0.14)
            ears.closeSubpath()
        painter.drawPath(ears)

        painter.setPen(QPen(QColor(255, 244, 232, 235), max(1.6, size * 0.035), Qt.SolidLine, Qt.RoundCap))
        painter.setFont(theme.font(int(max(9, size * 0.24)), bold=True))
        painter.drawText(QRectF(-size / 2, -size * 0.03, size, size * 0.40), Qt.AlignCenter, "?")

        painter.setPen(QPen(QColor(255, 255, 255, 55), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(body, size * 0.10, size * 0.10)
        painter.restore()

    def _paint_card(self, painter: QPainter) -> None:
        c = self.card or {}
        t = c.get("t", 0.0)
        dur = c.get("dur", 1.0)
        rise = ease_out_cubic(clamp(t / 0.45))
        fade_out = 1.0 if t < dur - 0.4 else clamp((dur - t) / 0.4)
        alpha = clamp(rise) * fade_out
        if alpha <= 0.01:
            return
        w, h = 168.0, 76.0
        cx = self.anchor.x()
        cy = self.anchor.y() - self.pet_h - 12 - rise * 10
        painter.save()
        painter.setOpacity(alpha)
        rect = QRectF(cx - w / 2, cy - h, w, h)

        border = QLinearGradient(rect.topLeft(), rect.bottomRight())
        border.setColorAt(0.0, QColor("#FFB27A"))
        border.setColorAt(0.5, QColor("#FF7A3D"))
        border.setColorAt(1.0, QColor("#26DFBB"))
        painter.setPen(QPen(border, 1.6))
        painter.setBrush(QColor(24, 26, 31, 244))
        painter.drawRoundedRect(rect, 13, 13)

        painter.save()
        clip = QPainterPath()
        clip.addRoundedRect(rect, 13, 13)
        painter.setClipPath(clip)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 8))
        for i in range(-2, 9):
            x = rect.left() + i * 22
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(x, rect.bottom()),
                        QPointF(x + 9, rect.bottom()),
                        QPointF(x + 34, rect.top()),
                        QPointF(x + 25, rect.top()),
                    ]
                )
            )
        painter.restore()

        painter.setPen(QColor("#FF9A63"))
        painter.setFont(theme.font(7, bold=True, spacing=2.2))
        painter.drawText(QRectF(rect.left() + 14, rect.top() + 8, w - 28, 13), Qt.AlignLeft, "BUDDY")

        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(theme.font(15, bold=True))
        painter.drawText(QRectF(rect.left() + 14, rect.top() + 21, w - 28, 28), Qt.AlignLeft | Qt.AlignVCenter, c.get("title", "SR"))

        painter.setPen(QColor("#A9AFBA"))
        painter.setFont(theme.font(8))
        painter.drawText(
            QRectF(rect.left() + 14, rect.top() + 51, w - 28, 16),
            Qt.AlignLeft | Qt.AlignVCenter,
            c.get("sub", "专属 Buddy"),
        )
        painter.restore()

    def _paint_bubble(self, painter: QPainter) -> None:
        b = self.bubble or {}
        t, dur = b.get("t", 0.0), b.get("dur", 2.0)
        appear = ease_out_back(clamp(t / 0.22))
        alpha = appear * (1.0 if t < dur - 0.3 else clamp((dur - t) / 0.3))
        if alpha <= 0.02:
            return
        text = b.get("text", "")
        fm = QFontMetrics(theme.font(9))
        tw = min(280, fm.horizontalAdvance(text) + 26)
        th = fm.height() + 15
        cx = self.anchor.x()
        cy = self.anchor.y() - self.pet_h - 12

        painter.save()
        painter.setOpacity(clamp(alpha))
        painter.translate(cx, cy - (1 - appear) * 7)
        rect = QRectF(-tw / 2, -th, tw, th)
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1))
        painter.setBrush(QColor(26, 28, 34, 242))
        painter.drawRoundedRect(rect, 11, 11)
        tail = QPainterPath()
        tail.moveTo(-5.5, 0.5)
        tail.lineTo(5.5, 0.5)
        tail.lineTo(0, 7)
        tail.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.drawPath(tail)
        painter.setPen(QColor("#F1F2F5"))
        painter.setFont(theme.font(9))
        painter.drawText(rect.adjusted(13, 0, -13, 0), Qt.AlignCenter, text)
        painter.restore()


# ---------------------------------------------------------------- 粒子绘制


def _paint_particle(painter: QPainter, p: Particle) -> None:
    a = p.fade()
    if a <= 0.01:
        return
    painter.save()
    painter.setOpacity(a)
    painter.translate(p.x, p.y)
    painter.rotate(p.rot)
    kind = p.kind

    if kind == "heart":
        s = p.size
        path = QPainterPath()
        path.moveTo(0, s * 0.42)
        path.cubicTo(-s * 0.62, -s * 0.05, -s * 0.46, -s * 0.66, 0, -s * 0.28)
        path.cubicTo(s * 0.46, -s * 0.66, s * 0.62, -s * 0.05, 0, s * 0.42)
        painter.setPen(Qt.NoPen)
        painter.setBrush(p.color)
        painter.drawPath(path)

    elif kind == "star":
        s = p.size
        path = QPainterPath()
        for i in range(8):
            ang = math.pi * i / 4 - math.pi / 2
            r = s if i % 2 == 0 else s * 0.26
            pt = QPointF(math.cos(ang) * r, math.sin(ang) * r)
            if i == 0:
                path.moveTo(pt)
            else:
                path.lineTo(pt)
        path.closeSubpath()
        painter.setPen(Qt.NoPen)
        painter.setBrush(p.color)
        painter.drawPath(path)

    elif kind == "spark":
        s = p.size
        c = QColor(p.color)
        grad = QRadialGradient(QPointF(0, 0), s)
        grad.setColorAt(0, QColor(c.red(), c.green(), c.blue(), 255))
        grad.setColorAt(0.45, QColor(c.red(), c.green(), c.blue(), 120))
        grad.setColorAt(1, QColor(c.red(), c.green(), c.blue(), 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(grad)
        painter.drawEllipse(QPointF(0, 0), s, s)

    elif kind == "confetti":
        painter.setPen(Qt.NoPen)
        painter.setBrush(p.color)
        painter.drawRoundedRect(QRectF(-p.size / 2, -p.size * 0.32, p.size, p.size * 0.64), 1.2, 1.2)

    elif kind == "ring":
        r = p.size * (0.55 + 0.95 * p.ratio())
        painter.setPen(QPen(QColor(p.color.red(), p.color.green(), p.color.blue(), int(170 * a)), 2.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(QPointF(0, 0), r, r * 0.40)

    elif kind == "ray":
        painter.setPen(QPen(QColor(p.color.red(), p.color.green(), p.color.blue(), int(190 * a)), 2.6, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(0, 0), QPointF(p.size, 0))

    elif kind == "note":
        s = p.size
        painter.setPen(QPen(p.color, max(1.6, s * 0.16), Qt.SolidLine, Qt.RoundCap))
        painter.setBrush(p.color)
        painter.drawEllipse(QPointF(-s * 0.26, s * 0.42), s * 0.30, s * 0.23)
        painter.drawLine(QPointF(s * 0.03, s * 0.38), QPointF(s * 0.03, -s * 0.66))
        flag = QPainterPath()
        flag.moveTo(s * 0.03, -s * 0.66)
        flag.cubicTo(s * 0.58, -s * 0.50, s * 0.60, -s * 0.12, s * 0.26, s * 0.02)
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(flag)

    elif kind == "zzz":
        painter.setPen(QColor(p.color))
        painter.setFont(theme.font(int(max(8, p.size)), bold=True))
        painter.drawText(QRectF(-p.size, -p.size, p.size * 2, p.size * 2), Qt.AlignCenter, p.text or "Z")

    elif kind == "dust":
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(p.color.red(), p.color.green(), p.color.blue(), int(70 * a)))
        painter.drawEllipse(QPointF(0, 0), p.size, p.size)

    elif kind == "cup":
        _paint_cup(painter, p)

    elif kind == "lid":
        s = p.size
        lid = QRectF(-s / 2, -s * 0.10, s, s * 0.20)
        grad = QLinearGradient(lid.topLeft(), lid.bottomLeft())
        grad.setColorAt(0, QColor("#FFAF80"))
        grad.setColorAt(1, QColor("#E85E22"))
        painter.setBrush(grad)
        painter.setPen(QPen(QColor(255, 255, 255, 70), 1))
        painter.drawRoundedRect(lid, s * 0.06, s * 0.06)

    painter.restore()


def _paint_cup(painter: QPainter, p: Particle) -> None:
    s = p.size
    body = QRectF(-s * 0.46, -s * 0.42, s * 0.92, s * 0.9)
    painter.setPen(QPen(QColor(255, 255, 255, 80), 1))
    painter.setBrush(QColor(250, 249, 247, 246))
    path = QPainterPath()
    path.moveTo(body.left(), body.top())
    path.lineTo(body.right(), body.top())
    path.lineTo(body.right() - s * 0.11, body.bottom())
    path.lineTo(body.left() + s * 0.11, body.bottom())
    path.closeSubpath()
    painter.drawPath(path)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(255, 122, 61, 225))
    painter.drawRect(QRectF(body.left() + 0.5, body.top() + s * 0.24, s * 0.91, s * 0.15))
    painter.setPen(QPen(QColor(250, 249, 247, 235), max(1.6, s * 0.07)))
    painter.setBrush(Qt.NoBrush)
    painter.drawArc(QRectF(body.right() - s * 0.05, body.top() + s * 0.20, s * 0.32, s * 0.32), -80 * 16, 160 * 16)


# ---------------------------------------------------------------- 特效基类


class BaseEffect:
    id = ""
    name = ""
    hint = ""
    duration = 1.0

    def __init__(self, engine: EffectEngine):
        self.e = engine
        self.age = 0.0
        self.done = False
        self.p = Pose()

    def on_start(self) -> None:
        pass

    def update(self, dt: float) -> Pose:
        self.age += dt
        if self.age >= self.duration:
            self.done = True
        self.p = Pose()
        self.step(self.age, dt)
        return self.p

    def step(self, t: float, dt: float) -> None:
        pass

    @property
    def h(self) -> int:
        return self.e.pet_h


# ---------------------------------------------------------------- 具体特效


class BlinkEffect(BaseEffect):
    id, name, hint = "blink", "眨眼卖萌", "轻轻眨下眼，卖个萌"
    duration = 0.62
    SEQ = [(0.00, 0), (0.09, 1), (0.17, 2), (0.25, 3), (0.34, 2), (0.44, 1)]

    def step(self, t: float, dt: float) -> None:
        frame = 0
        for start, value in self.SEQ:
            if t >= start:
                frame = value
        self.p.eyes = frame
        k = math.sin(math.pi * clamp(t / self.duration))
        self.p.dy = -2.5 * k
        self.p.sy = 1 - 0.012 * k


class HopEffect(BaseEffect):
    id, name, hint = "hop", "元气弹跳", "蹦一下，脚下冒烟"
    duration = 1.05

    def __init__(self, engine):
        super().__init__(engine)
        self._puff_up = False
        self._puff_down = False

    def _puff(self, count: int, spread: float) -> None:
        rng = self.e.rng
        self.e.burst(
            "dust",
            rng.uniform(-spread, spread),
            -2,
            count,
            (30, 82),
            angle=(math.pi * 1.03, math.pi * 1.97),
            ttl=0.5,
            g=45,
            color=QColor("#D6DAE2"),
            size=6,
            drag=3.0,
        )

    def step(self, t: float, dt: float) -> None:
        h = self.h
        peak = -h * 0.30
        if t < 0.10:
            k = t / 0.10
            self.p.sy = 1 - 0.11 * k
            self.p.sx = 1 + 0.09 * k
        elif t < 0.62:
            k = (t - 0.10) / 0.52
            self.p.dy = 4 * peak * k * (1 - k)
            self.p.sy = 1.045
            self.p.sx = 0.962
        elif t < 0.78:
            k = (t - 0.62) / 0.16
            squash = 1 - 0.13 * math.sin(math.pi * k)
            self.p.sy = squash
            self.p.sx = 1 + (1 - squash) * 0.7
        else:
            k = clamp((t - 0.78) / 0.27)
            self.p.sy = lerp(0.96, 1.0, ease_out_back(k))
            self.p.sx = lerp(1.03, 1.0, ease_out_back(k))

        if not self._puff_up and t >= 0.10:
            self._puff_up = True
            self._puff(7, h * 0.26)
        if not self._puff_down and t >= 0.62:
            self._puff_down = True
            self._puff(9, h * 0.32)


class SpinEffect(BaseEffect):
    id, name, hint = "spin", "旋转登场", "原地转一圈，星光环绕"
    duration = 1.30

    def step(self, t: float, dt: float) -> None:
        k = clamp((t - 0.10) / 0.92)
        self.p.rot = 360 * ease_out_cubic(k)
        breathe = math.sin(math.pi * clamp(t / self.duration))
        shrink = 1 - 0.30 * math.sin(math.pi * clamp(t / self.duration))
        self.p.sy = shrink
        self.p.sx = shrink
        self.p.dy = -6 * breathe
        if int(self.age / 0.045) != int((self.age - dt) / 0.045):
            self._spark()

    def _spark(self) -> None:
        rng = self.e.rng
        r = self.h * 0.36
        ang = rng.uniform(0, math.tau)
        self.e.spawn(
            "star",
            math.cos(ang) * r,
            -self.h * 0.55 + math.sin(ang) * r * 1.3,
            vy=-22,
            ttl=0.72,
            size=rng.uniform(4, 8),
            color=QColor("#FFD36E"),
            spin=rng.uniform(-110, 110),
            drag=1.0,
        )


class HeartsEffect(BaseEffect):
    id, name, hint = "hearts", "爱心暴击", "头顶冒出一串小爱心"
    duration = 2.30

    def step(self, t: float, dt: float) -> None:
        pulse = math.sin(t * 11) * math.exp(-t * 0.7)
        self.p.sy = 1 + 0.045 * pulse
        self.p.sx = 1 - 0.03 * pulse
        self.p.dy = -math.exp(-t * 0.5) * 4
        if int(self.age / 0.13) != int((self.age - dt) / 0.13):
            self._spawn_heart()

    def _spawn_heart(self) -> None:
        rng = self.e.rng
        self.e.spawn(
            "heart",
            rng.uniform(-self.h * 0.30, self.h * 0.24),
            -self.h * rng.uniform(0.94, 1.02),
            vx=rng.uniform(-26, 26),
            vy=rng.uniform(-84, -58),
            ttl=rng.uniform(1.2, 1.8),
            size=rng.uniform(11, 18),
            color=QColor(rng.choice(["#FF6E8A", "#FF4D6D", "#FF92A8"])),
            wobble_amp=rng.uniform(6, 14),
            drag=0.6,
        )


class SparkleEffect(BaseEffect):
    id, name, hint = "sparkle", "星光闪烁", "身上闪起金色星芒"
    duration = 1.90

    def step(self, t: float, dt: float) -> None:
        self.p.glow = math.sin(math.pi * clamp(t / self.duration))
        self.p.sy = 1 + 0.025 * math.sin(t * 8.5)
        self.p.sx = self.p.sy
        if int(self.age / 0.55) != int((self.age - dt) / 0.55):
            self._ring()
        if int(self.age / 0.07) != int((self.age - dt) / 0.07):
            self._star()

    def _ring(self) -> None:
        self.e.spawn("ring", 0, -self.h * 0.5, ttl=0.9, size=self.h * 0.34, color=QColor("#FFB27A"))

    def _star(self) -> None:
        rng = self.e.rng
        side = rng.choice((-1, 1))
        self.e.spawn(
            "star",
            side * rng.uniform(self.h * 0.26, self.h * 0.42),
            -rng.uniform(self.h * 0.15, self.h * 0.95),
            vy=rng.uniform(-26, -8),
            ttl=rng.uniform(0.5, 0.95),
            size=rng.uniform(3.5, 8.5),
            color=QColor(rng.choice(["#FFD36E", "#FFE9A8", "#FFFFFF"])),
            spin=rng.uniform(-160, 160),
            drag=0.8,
        )


class ConfettiEffect(BaseEffect):
    id, name, hint = "confetti", "彩带礼花", "礼花炸开，庆祝一下"
    duration = 2.50

    def __init__(self, engine):
        super().__init__(engine)
        self._second = False

    def on_start(self) -> None:
        self._burst()

    def _burst(self) -> None:
        rng = self.e.rng
        for _ in range(24):
            side = rng.choice((-1, 1))
            self.e.spawn(
                "confetti",
                side * self.h * rng.uniform(0.30, 0.40),
                -self.h * rng.uniform(0.45, 0.62),
                vx=side * rng.uniform(140, 300),
                vy=rng.uniform(-280, -140),
                g=520,
                ttl=rng.uniform(1.2, 2.0),
                size=rng.uniform(5, 10),
                color=QColor(rng.choice(CONFETTI_COLORS)),
                rot=rng.uniform(0, 360),
                spin=rng.uniform(-420, 420),
                drag=0.5,
            )
        for i in range(10):
            ang = math.tau * i / 10 + 0.2
            r0 = self.h * 0.46
            self.e.spawn(
                "ray",
                math.cos(ang) * r0,
                -self.h * 0.62 + math.sin(ang) * r0 * 0.85,
                ttl=0.32,
                size=self.h * 0.12,
                rot=math.degrees(ang),
                color=QColor("#FFF0D6"),
            )

    def step(self, t: float, dt: float) -> None:
        if t < 0.55:
            k = t / 0.55
            self.p.dy = 4 * (-self.h * 0.14) * k * (1 - k)
            self.p.sy = 1.03
            self.p.sx = 0.97
        elif 1.15 <= t < 1.65:
            k = (t - 1.15) / 0.5
            self.p.dy = 4 * (-self.h * 0.09) * k * (1 - k)
        if not self._second and t >= 1.15:
            self._second = True
            self._burst()


class CoffeeEffect(BaseEffect):
    id, name, hint = "coffee", "咖啡时间", "端一杯咖啡，冒点热气"
    duration = 2.90

    def __init__(self, engine):
        super().__init__(engine)
        self._steam_at = 0.0

    def on_start(self) -> None:
        self.e.spawn(
            "cup",
            self.h * 0.27,
            -self.h * 0.30,
            ttl=2.55,
            size=self.h * 0.175,
            color=QColor("#FAF9F7"),
            vy=5,
            drag=1.6,
        )

    def step(self, t: float, dt: float) -> None:
        self.p.dy = -3 * math.sin(math.pi * clamp(t / self.duration)) - 2
        self.p.sy = 1 + 0.012 * math.sin(t * 3.6)
        self.p.sx = 1 - 0.012 * math.sin(t * 3.6)
        if 0.35 < t < 0.80:
            self.p.eyes = 3
        if t - self._steam_at >= 0.30 and t < 2.4:
            self._steam_at = t
            rng = self.e.rng
            self.e.spawn(
                "dust",
                self.h * 0.27 + rng.uniform(-4, 4),
                -self.h * 0.38,
                vy=rng.uniform(-42, -28),
                vx=rng.uniform(-8, 8),
                ttl=rng.uniform(0.8, 1.15),
                size=rng.uniform(4.5, 7.5),
                color=QColor("#F0EDE7"),
                drag=0.4,
            )


class SleepEffect(BaseEffect):
    id, name, hint = "sleep", "摸鱼打盹", "闭眼小憩，飘出 Zzz"
    duration = 4.20

    def __init__(self, engine):
        super().__init__(engine)
        self._z_at = 0.2

    def step(self, t: float, dt: float) -> None:
        self.p.eyes = 3
        self.p.rot = -3.2
        self.p.dy = 2 + math.sin(t * 1.6) * 2.0
        self.p.sy = 1 + 0.018 * math.sin(t * 1.6)
        self.p.sx = 1 - 0.012 * math.sin(t * 1.6)
        if t - self._z_at >= 0.72:
            self._z_at = t
            rng = self.e.rng
            self.e.spawn(
                "zzz",
                self.h * 0.22 + rng.uniform(-3, 3),
                -self.h * rng.uniform(0.90, 0.98),
                vx=rng.uniform(12, 24),
                vy=rng.uniform(-40, -28),
                ttl=1.8,
                size=rng.uniform(15, 22),
                color=QColor("#D8DEE9"),
                text=rng.choice(["Z", "z"]),
                drag=0.3,
            )


class DanceEffect(BaseEffect):
    id, name, hint = "dance", "摇摆舞步", "左右摇摆，哼个小调"
    duration = 3.00

    def __init__(self, engine):
        super().__init__(engine)
        self._note_at = 0.1

    def step(self, t: float, dt: float) -> None:
        w = 5.6 * math.exp(-t * 0.10)
        self.p.rot = 11.0 * math.sin(t * w)
        self.p.dy = -9 * abs(math.sin(t * w))
        self.p.sy = 1 + 0.025 * math.sin(t * w * 2)
        self.p.sx = 1 - 0.02 * math.sin(t * w * 2)
        if t - self._note_at >= 0.42:
            self._note_at = t
            rng = self.e.rng
            self.e.spawn(
                "note",
                rng.uniform(-self.h * 0.3, self.h * 0.3),
                -self.h * rng.uniform(0.85, 0.98),
                vx=rng.uniform(-16, 16),
                vy=rng.uniform(-46, -30),
                ttl=1.6,
                size=rng.uniform(13, 19),
                color=QColor(rng.choice(["#FF9E6B", "#FFD36E", "#26DFBB"])),
                text=rng.choice(["♪", "♫", "♩"]),
                wobble_amp=rng.uniform(8, 16),
                drag=0.5,
            )


class BoxEffect(BaseEffect):
    """盲盒特效：掉盒 → 抖动蓄力 → 爆开 → 抽到卡面 → 开心小跳。"""

    id, name, hint = "box", "盲盒开箱", "拆一个盲盒，看看抽到谁"
    duration = 3.90

    T_DROP = 0.42
    T_BURST = 1.35
    T_CARD_END = 3.10

    def __init__(self, engine):
        super().__init__(engine)
        self._opened = False
        self._charge_at = 0.0

    def on_start(self) -> None:
        self.e.box = {"scale": 0.82, "rot": 0.0, "dx": 0.0, "dy": -self.h * 1.1, "alpha": 1.0}
        self.e.say("开个盲盒…", 1.2)

    def step(self, t: float, dt: float) -> None:
        if not self._opened:
            box = self.e.box
            if t < self.T_DROP:
                k = ease_out_bounce(t / self.T_DROP)
                if box:
                    box["dy"] = lerp(-self.h * 1.1, 0, k)
                    box["scale"] = lerp(0.82, 1.0, ease_out_cubic(t / self.T_DROP))
                    box["rot"] = 4 * math.sin(t * 26) * (1 - t / self.T_DROP)
                self.p.alpha = 0.0
            else:
                k = clamp((t - self.T_DROP) / (self.T_BURST - self.T_DROP))
                if box:
                    box["rot"] = math.sin(t * 52) * (2.5 + 6.0 * k)
                    box["dx"] = math.sin(t * 40) * 2.4 * k
                    box["scale"] = 1 + 0.05 * math.sin(t * 26) * k
                    box["dy"] = -abs(math.sin(t * 18)) * 5 * k
                self.p.alpha = 0.0
                if t - self._charge_at >= 0.15:
                    self._charge_at = t
                    self._charge()
            if t >= self.T_BURST:
                self._open()
                self._opened = True
            return

        # 开箱后：宠物弹入 → 卡面 → 小跳
        if t < 2.10:
            k = clamp((t - self.T_BURST) / 0.55)
            self.p.alpha = clamp(k * 2.4)
            self.p.sy = lerp(0.32, 1.0, ease_out_back(k, 2.4))
            self.p.sx = self.p.sy
            self.p.dy = -15 * math.sin(math.pi * k)
        else:
            k = clamp((t - 2.10) / 0.34)
            self.p.sy = 1 + 0.045 * math.sin(math.pi * k)
            self.p.sx = 1 - 0.035 * math.sin(math.pi * k)
            self.p.dy = -9 * math.sin(math.pi * k)
        if int(self.age / 0.09) != int((self.age - dt) / 0.09):
            self._spark()

    def _charge(self) -> None:
        rng = self.e.rng
        self.e.spawn(
            "spark",
            rng.uniform(-self.h * 0.3, self.h * 0.3),
            -self.h * 0.22 - rng.uniform(0, self.h * 0.18),
            vy=-32,
            ttl=0.5,
            size=rng.uniform(5, 10),
            color=QColor("#FFD8A8"),
            drag=1.5,
        )

    def _open(self) -> None:
        rng = self.e.rng
        self.e.box = None
        self.e.card = {"title": "SR", "sub": "命中注定 · 专属 Buddy", "t": 0.0, "dur": 1.85}
        for _ in range(26):
            side = rng.choice((-1, 1))
            self.e.spawn(
                "confetti",
                side * self.h * rng.uniform(0.26, 0.36),
                -self.h * rng.uniform(0.40, 0.58),
                vx=side * rng.uniform(150, 290),
                vy=rng.uniform(-250, -120),
                g=560,
                ttl=rng.uniform(1.0, 1.9),
                size=rng.uniform(5, 10),
                color=QColor(rng.choice(CONFETTI_COLORS)),
                rot=rng.uniform(0, 360),
                spin=rng.uniform(-480, 480),
                drag=0.6,
            )
        for _ in range(14):
            self.e.spawn(
                "spark",
                rng.uniform(-20, 20),
                -self.h * 0.28,
                vx=rng.uniform(-125, 125),
                vy=rng.uniform(-195, -60),
                ttl=0.6,
                size=rng.uniform(8, 16),
                color=QColor("#FFE2BE"),
                drag=2.0,
            )
        for i in range(12):
            ang = math.tau * i / 12 + 0.26
            if math.sin(ang) > 0.55:
                continue
            r0 = self.h * 0.50
            self.e.spawn(
                "ray",
                math.cos(ang) * r0,
                -self.h * 0.42 + math.sin(ang) * r0 * 0.80,
                ttl=0.34,
                size=self.h * 0.13,
                rot=math.degrees(ang),
                color=QColor("#FFF3E2"),
            )
        self.e.spawn(
            "lid",
            -self.h * 0.04,
            -self.h * 0.98,
            vx=rng.uniform(-26, 26),
            vy=-420,
            g=780,
            ttl=0.62,
            size=self.h * 0.34,
            rot=-14,
            spin=rng.uniform(-190, 190),
            color=QColor("#F0671F"),
        )

    def _spark(self) -> None:
        rng = self.e.rng
        side = rng.choice((-1, 1))
        self.e.spawn(
            "star",
            side * rng.uniform(self.h * 0.24, self.h * 0.44),
            -rng.uniform(self.h * 0.10, self.h * 0.95),
            vy=rng.uniform(-42, -12),
            ttl=rng.uniform(0.6, 1.0),
            size=rng.uniform(4, 9),
            color=QColor(rng.choice(["#FFD36E", "#FFFFFF", "#26DFBB"])),
            spin=rng.uniform(-180, 180),
            drag=1.0,
        )


EFFECTS: dict[str, type[BaseEffect]] = {
    e.id: e
    for e in [
        BoxEffect,
        HeartsEffect,
        HopEffect,
        SparkleEffect,
        ConfettiEffect,
        DanceEffect,
        BlinkEffect,
        CoffeeEffect,
        SleepEffect,
        SpinEffect,
    ]
}

EFFECT_ORDER = ["box", "hearts", "hop", "sparkle", "confetti", "dance", "blink", "coffee", "sleep", "spin"]
