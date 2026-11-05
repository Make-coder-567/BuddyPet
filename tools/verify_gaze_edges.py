"""验证四种姿态下的视线跟随：中心 / 左右贴边 / 上边缘倒挂 / 下边缘停靠。

验证分三层
----------
A. 可用性：各姿态下 ``_gaze_available()`` 是否为真（过去贴边时被一刀切关掉）
B. 方向：以 ``_eye_center_global()`` 为原点，鼠标在右 → nx>0、在左 → nx<0、
   在上 → ny<0、在下 → ny>0；距离超 RANGE 回正、进 DEAD 死区归零
C. **渲染方向**（最关键）：在眼睛邻域内做亮度加权质心，鼠标在左/右分别渲染，
   质心位移方向必须与鼠标一致。倒挂姿态若忘了把 oy 反号，这一条会立刻反向。

用法：``QT_QPA_PLATFORM=offscreen python tools/verify_gaze_edges.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtGui import QGuiApplication, QImage
from PyQt5.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.assets import BuddySprites  # noqa: E402
from app.config import Settings  # noqa: E402
from app.effects import EffectEngine  # noqa: E402
from app.gaze import DEAD, RANGE  # noqa: E402
from app.pet_window import PetWindow  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), label + (f"  {detail}" if detail else "")))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  {detail}" if detail else ""))


def eye_centroid(img: QImage, box: tuple[float, float, float, float]) -> tuple[float, float] | None:
    """在给定窗口内求**饱和度**加权质心。

    这只 Buddy 的眼睛是青/橙色，比白色头盔更暗，纯亮度阈值会把眼睛过滤掉；
    但眼睛是高饱和色、头盔与描线是中性色，用饱和度即可干净地只锁定眼睛。
    """
    x, y, w, h = (int(round(v)) for v in box)
    if w <= 2 or h <= 2:
        return None
    sub = img.copy(x, y, w, h).convertToFormat(QImage.Format_RGBA8888)
    ptr = sub.bits()
    ptr.setsize(sub.sizeInBytes())
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(sub.height(), sub.width(), 4).astype(np.float32)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mx = np.maximum(r, np.maximum(g, b))
    mn = np.minimum(r, np.minimum(g, b))
    sat = np.where(mx > 10.0, (mx - mn) / (mx + 1e-6), 0.0)
    med = float(np.median(sat))
    weight = np.clip(sat - med - 0.12, 0.0, None)
    if weight.sum() < 1.0:
        return None
    ys, xs = np.mgrid[0:sub.height(), 0:sub.width()]
    return (float((xs * weight).sum() / weight.sum()) + x,
            float((ys * weight).sum() / weight.sum()) + y)


def render(pet: PetWindow, nx: float, ny: float) -> QImage:
    """离屏渲染当前帧到 QImage（offscreen 平台下 ``grab()`` 不可靠，用 ``render``）。"""
    pet.gaze.nx, pet.gaze.ny = nx, ny
    pet.gaze.tx, pet.gaze.ty = nx, ny
    pet.clearMask()  # 排除窗口遮罩在离屏渲染时裁切眼睛
    img = QImage(pet.size(), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    pet.render(img)
    return img


def settle_gaze(pet: PetWindow, cursor: QPointF) -> None:
    """把鼠标放到指定屏幕坐标，跑足够帧让指数平滑收敛。"""
    center = pet._eye_center_global()
    for _ in range(90):
        pet.gaze.update(1 / 60.0, center, cursor, enabled=True)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    QGuiApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    cfg = Settings()
    cfg.gaze_follow = True
    cfg.edge_cling = True
    pet = PetWindow(cfg, BuddySprites(ROOT), EffectEngine(), None, ROOT)
    pet.show()
    app.processEvents()

    print(f"窗口 {pet.width()}x{pet.height()}  立绘 {pet._sprite_w}x{pet._sprite_h}")
    print(f"has_gaze={pet.sprites.has_gaze}  has_grip={pet.sprites.has_grip}  "
          f"has_grip_gaze={pet.sprites.has_grip_gaze}")
    check(pet.sprites.has_grip_gaze, "抓边立绘眼睛分层已就绪")

    for side in (None, "left", "right", "top", "bottom"):
        name = side or "center"
        print(f"\n=== 姿态 {name} ===")
        pet._edge = side
        if side is not None:
            pet._snap_edge_position(silent=True)
        app.processEvents()
        pet._pose.dy = 0.0
        pet._pose.sx = 1.0
        pet._pose.sy = 0.945 if side == "bottom" else 1.0

        check(pet._gaze_available(), f"[{name}] 视线跟随可用")
        center = pet._eye_center_global()
        print(f"  窗口位置 ({pet.x()},{pet.y()})  眼睛屏幕中心 ({center.x():.1f},{center.y():.1f})")
        area = pet._screen_area()
        check(area.top() - pet.height() < center.y() < area.bottom() + pet.height(),
              f"[{name}] 眼睛中心在屏幕纵向合理范围")

        # ---- B 方向与阈值 ----
        settle_gaze(pet, QPointF(center.x() + 200, center.y()))
        check(pet.gaze.nx > 0.2, f"[{name}] 鼠标在右 → nx>0", f"nx={pet.gaze.nx:+.3f}")
        settle_gaze(pet, QPointF(center.x() - 200, center.y()))
        check(pet.gaze.nx < -0.2, f"[{name}] 鼠标在左 → nx<0", f"nx={pet.gaze.nx:+.3f}")
        settle_gaze(pet, QPointF(center.x(), center.y() + 200))
        check(pet.gaze.ny > 0.2, f"[{name}] 鼠标在下 → ny>0", f"ny={pet.gaze.ny:+.3f}")
        settle_gaze(pet, QPointF(center.x(), center.y() - 200))
        check(pet.gaze.ny < -0.2, f"[{name}] 鼠标在上 → ny<0", f"ny={pet.gaze.ny:+.3f}")
        settle_gaze(pet, QPointF(center.x() + RANGE + 300, center.y()))
        check(abs(pet.gaze.nx) < 1e-6 and not pet.gaze.active,
              f"[{name}] 超出 {RANGE:.0f}px 回正", f"nx={pet.gaze.nx:+.4f}")
        settle_gaze(pet, QPointF(center.x() + DEAD * 0.4, center.y()))
        check(abs(pet.gaze.nx) < 1e-6, f"[{name}] 死区 {DEAD:.0f}px 内不抖动", f"nx={pet.gaze.nx:+.4f}")

        # ---- C 渲染方向：眼睛像素是否真的随鼠标移动 ----
        if side in ("left", "right") and pet.sprites.has_grip_gaze:
            gw = pet.sprites.grip_width(pet._sprite_h)
            ex = pet._edge_local_x(side)
            left = ex if side == "left" else ex - gw
            top = pet._feet.y() - pet._sprite_h
            gx, gy, gew, geh = pet.sprites.grip_eye_rect(0, pet._sprite_h)
            ecx = left + gx + gew / 2
            if side == "right":
                ecx = left + gw - (gx + gew / 2)
            ecy = top + gy + geh / 2
            bw, bh = gew, geh
        else:
            ex0, ey0, ew0, eh0 = pet.sprites.eye_rect(0, pet._sprite_h)
            local_x = -pet._sprite_w / 2 + ex0 + ew0 / 2
            local_y = -pet._sprite_h + ey0 + eh0 / 2
            if side == "top":
                ecx = pet._feet.x() + local_x
                ecy = pet._feet.y() - pet._sprite_h - local_y
            else:
                ecx = pet._feet.x() + local_x * pet._pose.sx
                ecy = pet._feet.y() + local_y * pet._pose.sy
            bw, bh = ew0, eh0
        # 头部随视线倾斜后，整只眼睛会随头部位移最多 ~25px（212h·sin6.5°）；
        # 探测窗要先在「回正帧」里定位眼睛真实中心，再以它为圆心放宽到足以
        # 容纳最大倾角位移的窗口，否则眼睛会被甩出 10px 余量之外导致误判。
        rest = render(pet, 0.0, 0.0)
        rc = eye_centroid(rest, (ecx - 40, ecy - 40, 80, 80))
        if rc is None:
            rc = (ecx, ecy)
        half = 40.0 + max(bw, bh) / 2.0 + 30.0
        probe = (rc[0] - half, rc[1] - half, half * 2, half * 2)
        img_left = render(pet, -0.85, 0.0)
        img_right = render(pet, 0.85, 0.0)
        check(not img_left.isNull() and not img_right.isNull(), f"[{name}] 离屏渲染成功")
        c_left = eye_centroid(img_left, probe)
        c_right = eye_centroid(img_right, probe)
        if c_left is None or c_right is None:
            check(False, f"[{name}] 眼睛邻域内取不到饱和亮源", f"probe={probe}")
        else:
            dx = c_right[0] - c_left[0]
            check(dx > 0.5, f"[{name}] 眼睛随鼠标向右移动",
                  f"质心x {c_left[0]:.2f} → {c_right[0]:.2f}  Δ={dx:+.2f}px")

        img_up = render(pet, 0.0, -0.85)
        img_down = render(pet, 0.0, 0.85)
        c_up = eye_centroid(img_up, probe)
        c_down = eye_centroid(img_down, probe)
        if c_up is None or c_down is None:
            check(False, f"[{name}] 纵向探测取不到饱和亮源")
        else:
            dyv = c_down[1] - c_up[1]
            check(dyv > 0.3, f"[{name}] 眼睛随鼠标向下移动（倒挂需反号）",
                  f"质心y {c_up[1]:.2f} → {c_down[1]:.2f}  Δ={dyv:+.2f}px")

        # 存图供人工复核
        out = ROOT / "docs" / "img"
        out.mkdir(parents=True, exist_ok=True)
        render(pet, -0.85, 0.0).save(str(out / f"gaze_{name}_left.png"))
        render(pet, 0.85, 0.0).save(str(out / f"gaze_{name}_right.png"))

    pet.close()
    passed = sum(1 for ok, _ in RESULTS if ok)
    print(f"\n{'=' * 56}\n{passed}/{len(RESULTS)} 通过")
    for ok, label in RESULTS:
        if not ok:
            print(f"  失败：{label}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
