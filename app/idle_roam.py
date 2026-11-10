"""「空闲自动漫游」：人离开一会儿，Buddy 自己下地溜达；人一回来就归位。

分成两层，互不耦合：

* :class:`IdleWatcher` 只负责回答「现在用户是不是没在碰电脑」。Windows 用
  ``GetLastInputInfo`` 读全系统最后一次键鼠输入的时间（**不是**本进程的输入），
  非 Windows / 调用失败时退化为「光标位置 + 应用内活动」的近似判断并标记
  ``degraded=True``；离屏测试环境直接判定「不可用」，绝不误报空闲。
  只在状态翻转时发 :attr:`idle_started` / :attr:`idle_ended`。
* :class:`RoamController` 是漫游状态机（``off → walk ⇄ pause → return → off``），
  由 :meth:`RoamController.step` 在宠物 60 FPS 主循环里推进：改窗口坐标、
  返回本帧要叠加到 ``Pose`` 上的步态增量，并在恰当时机说台词 / 放小特效。

设计约束
--------
1. **不打扰**：锁位置、隐藏、菜单 / 弹窗打开时不启动；用户一动键鼠，
   500ms 内结束漫游并回家；点宠物本身会立刻 ``interrupt()``。
2. **不改用户位置**：漫游期间从不写 ``cfg.pos``，也不调用 ``save()``。
   只有「不归位」模式结束时才把落点记为新位置。这样意外退出 / 崩溃后
   重启，宠物仍在你熟悉的右下角。
3. **不打架**：不新增特效、不改 ``EffectEngine``，姿态只在 ``_tick`` 末尾
   以少量 ``dy / rot / sx`` 叠加；随机互动要求 ``engine`` 空闲且限频 10~22s。
4. **不越界**：目标点始终落在当前屏幕可用区域内（去掉任务栏），窗口整框
   留 8~16px 边距；速度有上限，靠近目标减速，永远不会瞬移。
"""
from __future__ import annotations

import ctypes
import math
import os
import random
import sys
import time
from dataclasses import dataclass

from PyQt5.QtCore import QObject, QPoint, QTimer, pyqtSignal
from PyQt5.QtGui import QCursor

from . import lines
from .config import save

#: 空闲轮询间隔（毫秒）。唤醒延迟 = 采样相位 0~500ms，够快；探测成本约 0.01ms。
POLL_MS = 500
#: 连续多少次采样都超阈值才认定「真的离开了」，防止采样相位抖动误触发。
IDLE_CONFIRM = 2
#: 强制状态（命令通道 / QA）默认保持多久，期间跳过自动纠正。
FORCE_HOLD_S = 60.0

#: 漫游时的「小节目」：只在 pause 里、限频、且引擎空闲时挑一个。
FUN_EFFECTS = ("blink", "hearts", "sparkle", "hop", "coffee", "sleep")


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint32), ("dwTime", ctypes.c_uint32)]


def _idle_seconds_win32() -> float | None:
    """Windows：全系统最后一次键鼠输入距今多少秒。失败返回 ``None``。

    ``dwTime`` 是 ``GetTickCount`` 的 32 位快照；用无符号 32 位减法抵消
    49.7 天回绕，不用 ``GetTickCount64``（其 restype 默认是 c_int，容易被截断）。
    """
    try:
        lii = _LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        if not user32.GetLastInputInfo(ctypes.byref(lii)):
            return None
        now32 = int(kernel32.GetTickCount()) & 0xFFFFFFFF
        delta = (now32 - int(lii.dwTime)) & 0xFFFFFFFF
        return delta / 1000.0
    except Exception:
        return None


def platform_available() -> bool:
    """离屏 / 无 GUI 平台不提供可信的空闲信息（测试用 force 显式驱动）。"""
    return not (os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen")


class IdleWatcher(QObject):
    """轮询系统空闲时长，把「离开 / 回来」翻译成两个信号。

    Signals:
        idle_started: 连续确认用户已空闲超过阈值。
        idle_ended: 用户恢复操作（或功能被关闭 / 强制结束）。
        state: 每次翻转都发一次 ``(bool)``，便于状态落盘与调试。
    """

    idle_started = pyqtSignal()
    idle_ended = pyqtSignal()
    state = pyqtSignal(bool)

    def __init__(self, cfg, parent: QObject | None = None):
        super().__init__(parent)
        self._cfg = cfg
        self._enabled = False
        self._idle = False
        self._primed = False            # 首次采样只建档，不发信号
        self._pending = 0               # 连续超阈值次数
        self._hold_until = 0.0          # 强制状态保持到这个 monotonic 时刻
        self._forced = False
        self._idle_sec: float | None = None
        self._source = "none"
        self._degraded = False
        self._fallback_last = time.monotonic()
        self._fallback_pos = QPoint(-1, -1)

        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._on_tick)

    # ------------------------------------------------------------ 采样
    def _probe(self) -> tuple[float | None, str, bool]:
        """返回 ``(空闲秒数, 来源, 是否降级)``；不可用时秒数为 ``None``。"""
        if not platform_available():
            return None, "unavailable", True
        if sys.platform == "win32" or os.name == "nt":
            sec = _idle_seconds_win32()
            if sec is not None:
                return sec, "GetLastInputInfo", False
        # 降级：光标位置变化就算有操作。桌宠窗口是 Tool + 鼠标穿透，
        # 收不到全局键鼠事件；这里只能做近似，并在状态里如实标记 degraded。
        try:
            pos = QCursor.pos()
            now = time.monotonic()
            if pos != self._fallback_pos:
                self._fallback_pos = QPoint(pos)
                self._fallback_last = now
            return max(0.0, now - self._fallback_last), "cursor-fallback", True
        except Exception:
            return None, "unavailable", True

    def poll_now(self) -> dict:
        """立即采样并返回明细（不改状态机、不发信号），供 ``idle:status`` 落盘。"""
        sec, source, degraded = self._probe()
        return {
            "enabled": self._enabled,
            "idle": self._idle,
            "idle_sec": round(sec, 2) if sec is not None else None,
            "threshold_sec": max(1, int(self._cfg.roam_idle_min) * 60),
            "source": source,
            "degraded": degraded,
            "forced": self._forced,
            "hold_left_sec": round(max(0.0, self._hold_until - time.monotonic()), 1),
        }

    # ------------------------------------------------------------ 生命周期
    def set_enabled(self, value: bool) -> None:
        """开关轮询。关闭时若正处于空闲态，补发一次 ``idle_ended`` 让宠物归位。"""
        value = bool(value)
        self._enabled = value
        if not value:
            was_idle = self._idle
            self.stop()
            if was_idle:
                self._idle = False
                self.idle_ended.emit()
                self.state.emit(False)
            return
        self._primed = False
        self._pending = 0
        self._hold_until = 0.0
        self._forced = False
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        """停轮询并复位内部进度（幂等）；是否补发结束信号由调用方决定。"""
        try:
            self._timer.stop()
        except Exception:
            pass
        self._enabled = False
        self._primed = False
        self._pending = 0
        self._hold_until = 0.0
        self._forced = False

    def force(self, idle: bool, hold_s: float = 0.0) -> None:
        """QA / 命令通道：立即注入一次空闲状态（幂等，可保持一段时间）。

        ``hold_s > 0`` 时该状态在墙钟时间内不被自动轮询纠正 —— 这样
        ``idle:on`` 可以不用真的等 3 分钟就预览漫游，演示完 ``idle:off``
        立刻恢复。同状态重复下发只刷新保持时间，不会重复发信号。
        """
        idle = bool(idle)
        self._primed = True
        self._pending = 0
        self._forced = idle
        self._hold_until = time.monotonic() + max(0.0, float(hold_s)) if (idle and hold_s) else 0.0
        # _apply 自带等值守卫：同状态重复下发只刷新保持时间，不重复广播
        self._apply(idle)

    def _apply(self, idle: bool) -> None:
        if idle == self._idle:
            return
        self._idle = idle
        if idle:
            self.idle_started.emit()
        else:
            self.idle_ended.emit()
        self.state.emit(bool(idle))

    # ------------------------------------------------------------ 轮询
    def _on_tick(self) -> None:
        if not self._enabled:
            return
        now = time.monotonic()
        if self._hold_until and now < self._hold_until:
            return
        if self._hold_until:
            self._hold_until = 0.0
            self._forced = False
        sec, source, degraded = self._probe()
        self._idle_sec, self._source, self._degraded = sec, source, degraded
        if sec is None:
            return
        threshold = max(1, int(self._cfg.roam_idle_min) * 60)
        if not self._primed:
            # 首采只建档不发信号（避免刚启动就自己跑起来）。若建档时人
            # 确实已经离开，预置一次确认进度：下一 tick 仍空闲就会正常触发，
            # 不会出现「开着功能时用户早已离开，却永远等不到 idle_started」。
            self._primed = True
            self._idle = False
            self._pending = IDLE_CONFIRM - 1 if sec >= threshold else 0
            return
        if not self._idle:
            if sec >= threshold:
                self._pending += 1
            else:
                self._pending = 0
            if self._pending >= IDLE_CONFIRM:
                self._pending = 0
                self._apply(True)
        elif sec < threshold:
            # 唤醒要快：只要有一次输入就结束，不做确认窗口
            self._pending = 0
            self._apply(False)


@dataclass
class RoamPose:
    """一帧里叠加到 ``Pose`` 上的步态增量（不改变特效自身的姿态曲线）。"""

    bob: float = 0.0     # 垂直起伏（px，负值向上）
    rot: float = 0.0     # 摆角（度）
    sx: float = 1.0
    sy: float = 1.0


class RoamController(QObject):
    """把「空闲中」变成屏幕上的一段自主活动。

    由 ``PetWindow._tick`` 每帧调用 :meth:`step`；位置直接写到宠物窗口上，
    步态以 :class:`RoamPose` 返回，避免和呼吸 / 悬停 / 音乐律动抢同一份状态。
    """

    #: 状态变化（调试 / 状态落盘）：``off / walk / pause / return``
    state_changed = pyqtSignal(str)

    def __init__(self, cfg, pet, parent: QObject | None = None):
        super().__init__(parent)
        self._cfg = cfg
        self._pet = pet
        self._rng = random.Random()
        self._state = "off"
        self._clock = 0.0
        self._phase = 0.0
        self._turn = 0.0
        self._vx = 0.0
        self._vy = 0.0
        self._dir = 1
        self._px = 0.0
        self._py = 0.0
        self._target = (0.0, 0.0)
        self._pause_left = 0.0
        self._fun_at = 0.0
        self._home = QPoint()
        self._home_edge: str | None = None
        self._acted = False           # 本次漫游是否真的动过（状态落盘用）

    # ------------------------------------------------------------ 查询
    @property
    def active(self) -> bool:
        return self._state != "off"

    @property
    def state(self) -> str:
        return self._state

    def status(self) -> dict:
        return {
            "enabled": bool(self._cfg.roam_enabled),
            "active": self.active,
            "state": self._state,
            "pos": [self._pet.x(), self._pet.y()],
            "home": [self._home.x(), self._home.y()],
            "target": [round(self._target[0]), round(self._target[1])],
            "return_home": bool(self._cfg.roam_return_home),
            "speed": int(self._cfg.roam_speed),
            "moved": self._acted,
        }

    # ------------------------------------------------------------ 生命周期
    def set_enabled(self, value: bool) -> None:
        """功能被关掉：立刻收场并归位，不留一个停在半空的宠物。"""
        if not value:
            self.finish(immediate=True)

    def start(self) -> bool:
        """空闲开始：记下老家、松开贴边、说一句，然后选第一个目标。"""
        if not self._cfg.roam_enabled:
            return False
        if self.active:
            # 「回家路上又睡着了」：取消归位，直接继续逛，home 保持不变
            if self._state == "return":
                self._pick_target()
                self._set_state("walk")
            return True
        pet = self._pet
        if (
            self._cfg.lock_pos
            or not pet.isVisible()
            or getattr(pet, "_overlay_suppressed", False)
            or getattr(pet, "_dragging", False)
        ):
            return False
        self._home = QPoint(pet.x(), pet.y())
        self._home_edge = getattr(pet, "_edge", None)
        if self._home_edge is not None:
            pet.release_edge(speak=False)
        self._px, self._py = float(pet.x()), float(pet.y())
        self._vx = self._vy = 0.0
        self._clock = 0.0
        self._phase = 0.0
        self._acted = False
        self._fun_at = self._rng.uniform(9.0, 17.0)
        pet.engine.say(lines.pick("roam_start"), 2.4)
        self._pick_target()
        self._set_state("walk")
        return True

    def finish(self, immediate: bool = False) -> None:
        """用户回来了：小跑回原位（或直接定住），然后恢复贴边状态。"""
        if not self.active:
            return
        pet = self._pet
        if not immediate and self._cfg.roam_return_home and self._acted:
            pet.engine.say(lines.pick("roam_back"), 2.2)
            self._target = (float(self._home.x()), float(self._home.y()))
            self._set_state("return")
            return
        self._settle()

    def interrupt(self) -> None:
        """用户直接点了宠物：不等 500ms 轮询，就地停住。

        刻意**不**归位：点击的下一行就要用 ``globalPos - pos()`` 算拖拽
        偏移，此刻把窗口瞬移回家会让这次拖拽错位。用户主动上手，就当他
        想把 Buddy 留在这儿 —— 落点记为新家，贴边状态也不再强行恢复；
        随后 watcher 补发的 ``idle_ended`` 遇到 ``off`` 状态是幂等的。
        """
        if self.active:
            self._settle(keep_current=True)

    def _settle(self, keep_current: bool = False) -> None:
        pet = self._pet
        if self._cfg.roam_return_home and not keep_current:
            pet.move(self._home)
            self._cfg.pos = [self._home.x(), self._home.y()]
        else:
            # 不归位 / 被点击打断：把落点记成新家，下次启动就在这儿
            self._cfg.pos = [pet.x(), pet.y()]
            save(self._cfg)
        if self._home_edge is not None and self._cfg.edge_cling and not keep_current:
            pet._edge = self._home_edge
            pet._snap_edge_position(silent=True)
            pet.refresh_mask()
        self._home_edge = None
        self._vx = self._vy = 0.0
        self._turn = 0.0
        self._set_state("off")

    def _set_state(self, value: str) -> None:
        if self._state != value:
            self._state = value
            self.state_changed.emit(value)

    # ------------------------------------------------------------ 目标
    def _bounds(self) -> tuple[int, int, int, int]:
        """窗口左上角允许的矩形；整框留在当前屏幕可用区域内。"""
        pet = self._pet
        area = pet._screen_area()
        x0 = area.left() + 16
        x1 = area.right() - pet.width() - 16
        y0 = area.top() + max(12, int(pet.height() * 0.10))
        y1 = area.bottom() - pet.height() - 8
        if x1 < x0:                       # 极端小屏 / 大宠物：退化为居中
            x0 = x1 = area.left() + max(0, (area.width() - pet.width()) // 2)
        if y1 < y0:
            y0 = y1 = area.top() + max(0, (area.height() - pet.height()) // 2)
        return x0, x1, y0, y1

    def _pick_target(self) -> None:
        x0, x1, y0, y1 = self._bounds()
        span_x = max(1.0, x1 - x0)
        # 距离当前位置 120px ~ 屏幕宽 75%，避免原地蹭或长时间横穿
        lo, hi = 120.0, max(150.0, span_x * 0.75)
        for _ in range(12):
            tx = self._rng.uniform(x0, x1)
            if lo <= abs(tx - self._px) <= hi:
                break
        else:
            tx = x0 if self._px > (x0 + x1) / 2 else x1
        # 六成落在下半「地面带」，四成允许满屏跑，站立姿态不至于一直悬空
        ground = y0 + (y1 - y0) * 0.55
        ty = self._rng.uniform(ground, y1) if self._rng.random() < 0.6 else self._rng.uniform(y0, y1)
        self._target = (tx, float(ty))
        self._dir = 1 if tx >= self._px else -1
        self._turn = 1.0                 # 起步时轻微侧身，像转身

    # ------------------------------------------------------------ 每帧推进
    def step(self, dt: float) -> RoamPose | None:
        """推进一帧；返回要叠加的步态，未激活时返回 ``None``。

        拖拽 / 隐藏 / 弹窗期间只维持状态、不移动窗口，等下一帧再继续。
        """
        if not self.active:
            return None
        pet = self._pet
        self._clock += dt
        if self._turn > 0:
            self._turn = max(0.0, self._turn - dt * 2.2)
        busy = (
            getattr(pet, "_dragging", False)
            or not pet.isVisible()
            or getattr(pet, "_overlay_suppressed", False)
        )
        if busy:
            return RoamPose(sx=1.0 - 0.08 * self._turn)

        if self._state == "pause":
            self._pause_left -= dt
            self._vx *= max(0.0, 1.0 - dt * 6)
            self._vy *= max(0.0, 1.0 - dt * 6)
            self._maybe_fun()
            if self._pause_left <= 0:
                self._pick_target()
                self._set_state("walk")
            # 停顿时只有很轻的呼吸起伏，交给 _tick 原有的待机动画
            return RoamPose(sx=1.0 - 0.08 * self._turn)

        speed = max(20.0, float(self._cfg.roam_speed)) * (0.65 + 0.35 * self._cfg.liveliness)
        dx = self._target[0] - self._px
        dy = self._target[1] - self._py
        dist = math.hypot(dx, dy)
        ease = min(1.0, dist / 90.0)      # 接近目标自然减速
        if dist > 1e-3:
            want_x = dx / dist * speed * ease
            want_y = dy / dist * speed * ease
        else:
            want_x = want_y = 0.0
        k = min(1.0, dt * 5.0)
        self._vx += (want_x - self._vx) * k
        self._vy += (want_y - self._vy) * k
        self._px += self._vx * dt
        self._py += self._vy * dt
        nx, ny = int(round(self._px)), int(round(self._py))
        if (nx, ny) != (pet.x(), pet.y()):
            pet.move(nx, ny)
            self._acted = True

        self._phase += dt * (5.5 + speed / 45.0)
        lively = self._cfg.liveliness
        bob = -abs(math.sin(self._phase)) * 2.0 * lively
        rot = math.sin(self._phase) * 1.8 + self._dir * 2.2
        sx = 1.0 - 0.08 * self._turn - 0.018 * abs(math.cos(self._phase))
        sy = 1.0 + 0.012 * abs(math.cos(self._phase))
        pose = RoamPose(bob=bob, rot=rot, sx=sx, sy=sy)

        if dist < 9.0 and math.hypot(self._vx, self._vy) < speed * 0.35:
            self._vx = self._vy = 0.0
            if self._state == "return":
                self._settle()
            else:
                self._pause_left = self._rng.uniform(1.2, 4.0)
                self._set_state("pause")
        else:
            self._maybe_fun()
        return pose

    def _maybe_fun(self) -> None:
        """漫游中的小节目：限频、引擎空闲、可关，且不与台词同时刷屏。"""
        if not self._cfg.roam_effects or self._clock < self._fun_at:
            return
        self._fun_at = self._clock + self._rng.uniform(10.0, 22.0)
        engine = self._pet.engine
        if engine.is_busy():
            return
        if self._rng.random() < 0.5:
            engine.say(lines.pick("roam_wander"), 2.2)
        else:
            self._pet.play(self._rng.choice(FUN_EFFECTS), quiet=True)
