"""视线跟随：把鼠标位置平滑映射成眼睛与头部的偏移量。

设计要点
- 触发范围：以眼睛中心为圆心，`RANGE` 内开始跟随，超出即回正；
- 死区：`DEAD` 内不产生偏移，避免鼠标停在眼前时眼睛抖动；
- 映射：距离在 DEAD→RANGE 之间用 smoothstep 归一到 0..1，方向由单位向量给出；
- 平滑：指数插值 + 最大角速度限制，帧率变化时表现一致；
- 输出：归一化方向 (nx, ny)，由绘制层换成像素位移、压缩比与头部角度。
"""
from __future__ import annotations

import math

from PyQt5.QtCore import QPointF

RANGE = 460.0        # 触发距离（px）
DEAD = 40.0          # 死区半径（px）
RESPONSE = 9.0       # 指数平滑系数，越大越跟手
MAX_STEP = 4.2       # 单帧最大位移（归一化单位/秒）
GAZE_TILT = 6.5      # 边缘抓握姿态的头部随视线倾斜（度）；中心用 head_rotation


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


class GazeTracker:
    def __init__(self) -> None:
        self.nx = 0.0          # -1..1，右为正
        self.ny = 0.0          # -1..1，下为正
        self.tx = 0.0
        self.ty = 0.0
        self.active = False
        self.distance = 0.0

    def reset(self) -> None:
        self.nx = self.ny = self.tx = self.ty = 0.0
        self.active = False

    def update(self, dt: float, eye_center: QPointF, cursor: QPointF, enabled: bool = True) -> None:
        dx = cursor.x() - eye_center.x()
        dy = cursor.y() - eye_center.y()
        dist = math.hypot(dx, dy)
        self.distance = dist

        if not enabled or dist > RANGE or dist < 1e-3:
            self.tx = self.ty = 0.0
            self.active = False
        else:
            self.active = True
            weight = _smoothstep((dist - DEAD) / max(1.0, RANGE - DEAD))
            self.tx = dx / dist * weight
            self.ty = dy / dist * weight

        # 指数平滑：dt 变化时收敛速度一致
        k = 1.0 - math.exp(-dt * RESPONSE)
        step_x = (self.tx - self.nx) * k
        step_y = (self.ty - self.ny) * k
        limit = MAX_STEP * dt
        if limit > 0:
            step_x = max(-limit, min(limit, step_x))
            step_y = max(-limit, min(limit, step_y))
        self.nx += step_x
        self.ny += step_y

    # 取整前先限幅，避免瞬间大跳
    def eye_offset(self, height: int) -> tuple[float, float]:
        # 略增幅度以用满眼窝安全行程（正常眼窝横向余量 ~7px@212h，抓边余量同量级）
        return self.nx * height * 0.028, self.ny * height * 0.022

    def head_rotation(self) -> float:
        return self.nx * 3.0

    def head_tilt(self) -> float:
        """左右抓边时头部随视线倾斜的角度（度），以抓握点为支点。"""
        return self.nx * GAZE_TILT

    def head_shift(self, height: int) -> float:
        return self.nx * height * 0.012
