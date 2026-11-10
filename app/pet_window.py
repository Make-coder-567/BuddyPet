"""桌面宠物主窗口：透明无边框、置顶、可拖拽，左键开程序、右键出菜单。"""
from __future__ import annotations

import math
import random
import time
from pathlib import Path

from PyQt5.QtCore import QElapsedTimer, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QCursor, QPainter, QPen, QRadialGradient, QRegion
from PyQt5.QtWidgets import QAction, QApplication, QMenu, QWidget

from . import icons, launcher, lines, theme
from .assets import BuddySprites
from .config import Settings, save
from .effects import EFFECT_ORDER, EFFECTS, EffectEngine, Pose
from .gaze import GazeTracker
from .overlay import EffectOverlay
from .sound import SoundManager

TICK_MS = 16
DRAG_THRESHOLD = 5
CLICK_MAX_MS = 900
BLINK_RATIO = (1.00, 0.62, 0.28, 0.12, 0.28, 0.62)
EDGE_SNAP = 28          # 距离屏幕侧边多少像素内松手会贴上去
EDGE_PIVOT_Y = 0.76     # 抓握点高度（相对身高，1.0 为头顶）
EDGE_TILT = 16.0        # 侧边悬挂的基础倾角

# ---------------------------------------------------------------- 拖放分类
#: 扩展名集合（小写、不带点）。分类只影响「反馈用哪个特效 + 哪句台词」，
#: 不影响任何文件本身 —— 拖进来只是「看一眼」，绝不复制/移动/打开文件。
DROP_IMAGE = frozenset("png jpg jpeg gif webp bmp svg ico".split())
DROP_AUDIO = frozenset("mp3 wav flac m4a ogg aac wma".split())
DROP_CODE = frozenset(
    "py js ts tsx jsx java c cpp h cs go rs json yaml yml html css sh bat".split()
)
DROP_DOC = frozenset("doc docx pdf txt md ppt pptx xls xlsx csv".split())
DROP_ARCHIVE = frozenset("zip rar 7z tar gz".split())

#: 分类 -> 落地后的互动特效
DROP_EFFECT: dict[str, str] = {
    "image": "hearts",
    "audio": "dance",
    "code": "sparkle",
    "doc": "sparkle",
    "archive": "box",
    "other": "box",
}



class PetWindow(QWidget):
    settings_requested = pyqtSignal()
    about_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    hidden_requested = pyqtSignal()
    #: 换肤完成（配置已写盘、画面已换）—— 托盘图标/关于窗口需要跟着刷新
    sprites_changed = pyqtSignal()
    #: 请求打开图鉴分组（右键「切换 Buddy…」/ 托盘同名项）
    gallery_requested = pyqtSignal()
    #: 请求开一局石头剪刀布（双击宠物 / 菜单 / 托盘 / 命令通道）
    rps_requested = pyqtSignal()
    #: 请求切换「音乐感知」开关（右键菜单勾选项；真正的落盘由 main 负责）
    music_toggle_requested = pyqtSignal(bool)
    #: 请求切换「空闲自动漫游」开关（落盘与检测器开关由 main 统一负责）
    roam_toggle_requested = pyqtSignal(bool)

    def __init__(self, cfg: Settings, sprites: BuddySprites, engine: EffectEngine, overlay: EffectOverlay, root: Path):
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if cfg.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.cfg = cfg
        self.sprites = sprites
        self.engine = engine
        self.overlay = overlay
        self.root = root

        # 互动音效管理器：QSoundEffect 必须在 QApplication 之后构造（本类在 GUI 线程构造，安全）
        self.sfx = SoundManager(self.cfg, root)
        # 悬停音节流时间戳（避免反复进/出时刷屏）
        self._last_hover_sfx = 0.0
        # 完成音节流时间戳
        self._last_complete_sfx = 0.0

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setWindowTitle("Buddy 桌面宠物")

        self._rng = random.Random()
        self._elapsed = QElapsedTimer()
        self._elapsed.start()
        self._t = 0.0
        self._swap_fade = 1.0  # 1.0 = 完全可见；换肤时降到 0 后在 _tick 里 180ms 淡入
        self._pose = Pose()
        self._hover = 0.0
        self._hover_target = 0.0
        self._tilt = 0.0
        self._blink_in = self._rng.uniform(1.6, 3.4)
        self._dragging = False
        self._press_global = QPoint()
        self._press_offset = QPoint()
        self._press_ms = 0.0
        self._last_move_x = 0.0
        self._last_move_t = 0.0
        self._airborne_mask = False
        self._mask_idle = None
        self._mask_air = None
        self._hint_shown = False
        self._overlay_suppressed = False
        self._applied_on_top = cfg.always_on_top
        self.gaze = GazeTracker()
        self._edge: str | None = None      # None / "left" / "right"
        self._edge_hinted = False
        self._grip_hop = 0.0
        self._grab_settle = 0.0
        self._regrip_at = 0.0

        # ---------------- 拖放 / 音乐律动 / 单击-双击区分（本次新增） ----------------
        # 接收文件拖放：拖入时整窗可绘制（虚线框），落下后按类型给反应
        self.setAcceptDrops(True)
        self._drag_over = False      # 是否有文件正悬在宠物上方
        self._drag_phase = 0.0       # 拖入脉冲的相位（_tick 里推进）
        self._music_on = False       # 音乐感知：当前是否「在响」
        self._music_groove = 0.0     # 律动权重 0~1，向目标缓动
        # 单击延迟定时器：单击必须等一个双击间隔再执行，否则双击会先触发一次单击
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_click)
        self._ignore_click = False   # 双击后抑制紧随其后的那次单击
        # 空闲自动漫游控制器：由 main 在装配阶段注入；_tick 每帧向它要步态增量
        self._roam = None

        self._apply_geometry(initial=True)
        self._detect_edge_from_pos()
        self._bind_timers()
        self._schedule_auto_effect()
        # 特效自然播放完成时触发「完成操作」音效
        self.engine.effect_finished.connect(self._on_effect_finished)

    # ------------------------------------------------------------ 布局
    def _metrics(self) -> tuple[int, int, int, int, float, float]:
        h = self.cfg.size
        w = self.sprites.width_for(h)
        pad = 12
        win_w = round(w * 1.32) + pad * 2
        win_h = round(h * 1.40) + pad * 2
        feet_x = win_w / 2.0
        feet_y = win_h - pad
        return w, h, win_w, win_h, feet_x, feet_y

    def _apply_geometry(self, initial: bool = False) -> None:
        w, h, win_w, win_h, feet_x, feet_y = self._metrics()
        self._sprite_w, self._sprite_h = w, h
        self._feet = QPointF(feet_x, feet_y)
        self.setFixedSize(QSize(win_w, win_h))

        pos = self._initial_position() if initial else self.pos()
        if self.cfg.pos and initial:
            pos = QPoint(int(self.cfg.pos[0]), int(self.cfg.pos[1]))
        pos = self._clamp(pos)
        self.move(pos)
        if self._edge:
            self._snap_edge_position(silent=True)

        self._mask_idle = self._build_mask(airborne=False)
        self._mask_air = self._build_mask(airborne=True)
        self._apply_mask(False)
        self._sync_overlay()

    def set_sprites(self, sprites: BuddySprites) -> None:
        """换肤唯一入口（架构 §9.2）：整体替换素材并按新比例重建窗口/遮罩。

        顺序不能改，少一步就会出 bug：
            a. 换 ``self.sprites``；
            b. 清空 **``self._mask_cache``** —— 这是 ``PetWindow`` 自己的贴边轮廓缓存
               （按 ``(sprite_h, side)`` 做键），**不属于 ``assets.py`` 五级缓存**，
               不清就会残留旧皮肤的轮廓；
            c. 按新 ``width_for()`` 重算窗口尺寸（跨宽高比皮肤宽度会变）；
            d. 贴边状态下重建轮廓遮罩并重新吸附；
            e. 夹回屏幕内（尺寸变小后可能留在屏幕外）；
            f. 重绘 + 发 ``sprites_changed``。
        """
        old = self.sprites
        # ---- a) 换素材 ----
        self.sprites = sprites
        # 仅在「真正换了一套餐装」时触发淡入；初次构造（old 为 None）不闪一下
        if old is not None and old is not sprites:
            self._swap_fade = 0.0
        # ---- b) 清空第六处缓存 + 两块从素材派生的遮罩 ----
        self._mask_cache = {}
        self._mask_idle = None
        self._mask_air = None
        # 新皮肤眼位不同，视线归零防跳变
        self.gaze.reset()

        # ---- c) 重算窗口尺寸（width_for 现在按实例 canvas_w/canvas_h 算）----
        w, h, win_w, win_h, feet_x, feet_y = self._metrics()
        self._sprite_w, self._sprite_h = w, h
        self._feet = QPointF(feet_x, feet_y)
        self.setFixedSize(QSize(win_w, win_h))

        # 先夹一次：吸附公式依赖窗口尺寸，尺寸变化后旧坐标可能已在屏幕外
        self.move(self._clamp(self.pos()))

        # 底板遮罩按新素材重建
        self._mask_idle = self._build_mask(airborne=False)
        self._mask_air = self._build_mask(airborne=True)

        # ---- d) 贴边：先吸附到新位置，再按新立绘重建轮廓遮罩 ----
        # 顺序关键：_build_edge_mask 里抓边立绘要按"当前窗口 x"换算屏幕边线，
        # 所以必须在吸附之后建。这里刻意不走 _edge_mask() 缓存，
        # 让 _mask_cache 保持为空 —— 之后任何一次懒加载都会用新素材重算。
        if self._edge is not None and self.cfg.edge_cling:
            self._snap_edge_position(silent=True)
            self.setMask(self._build_edge_mask(self._edge))
        else:
            self._apply_mask(self._airborne_mask)

        # ---- e) 兜底再夹一次（实测四边吸附位置均落在 _clamp 允许区间内，等同于空操作）----
        self.move(self._clamp(self.pos()))

        # ---- f) 重绘 + 通知 ----
        self._sync_overlay()
        self.update()
        if old is not None and old is not sprites:
            old.clear_caches()      # 释放旧皮肤的显存
        self.sprites_changed.emit()

    def _initial_position(self) -> QPoint:
        screen = QApplication.primaryScreen().availableGeometry()
        _, _, win_w, win_h, _, _ = self._metrics()
        return QPoint(screen.right() - win_w - 48, screen.bottom() - win_h - 24)

    def _clamp(self, pos: QPoint) -> QPoint:
        screen = QApplication.screenAt(pos + QPoint(self.width() // 2, self.height() // 2))
        if screen is None:
            screen = QApplication.primaryScreen()
        area = screen.availableGeometry()
        # 上边缘倒挂时窗口大半要探出屏幕，纵向留更多余量
        x = max(area.left() - self.width() // 3, min(pos.x(), area.right() - self.width() * 2 // 3))
        y = max(area.top() - int(self.height() * 0.75), min(pos.y(), area.bottom() - 24))
        return QPoint(x, y)

    # ------------------------------------------------------------ 贴边抓握
    EDGE_LABEL = {"left": "左边缘", "right": "右边缘", "top": "上边缘", "bottom": "下边缘"}

    def _screen_area(self) -> QRect:
        screen = QApplication.screenAt(self.geometry().center()) or QApplication.primaryScreen()
        return screen.availableGeometry()

    def _sprite_offset(self) -> int:
        """立绘左边缘相对窗口左边缘的偏移（窗口比立绘宽）。"""
        return (self.width() - self._sprite_w) // 2

    def _sprite_bounds(self) -> tuple[int, int]:
        left = self.x() + self._sprite_offset()
        return left, left + self._sprite_w

    def _sprite_rect(self) -> QRect:
        return QRect(
            self.x() + self._sprite_offset(),
            self.y() + int(self._feet.y()) - self._sprite_h,
            self._sprite_w,
            self._sprite_h,
        )

    def _edge_gaps(self) -> dict[str, int]:
        """窗口外框到屏幕可用区域四边的距离；负数表示已经越出该边。"""
        area = self._screen_area()
        return {
            "left": self.x() - area.left(),
            "right": area.right() + 1 - (self.x() + self.width()),
            "top": self.y() - area.top(),
            "bottom": area.bottom() + 1 - (self.y() + self.height()),
        }

    def _snap_edge_position(self, silent: bool = False) -> bool:
        """把宠物真正贴到当前边缘（立绘压到屏幕上，而不是窗口外框）。"""
        if self._edge is None or not self.cfg.edge_cling:
            return False
        area = self._screen_area()
        off = self._sprite_offset()
        w, h = self._sprite_w, self._sprite_h
        feet_y = self._feet.y()
        x, y = self.x(), self.y()
        grip = self.sprites.has_grip
        if self._edge in ("left", "right") and grip:
            gw = self.sprites.grip_width(h)
            pad = (self.width() - gw) // 2
            if self._edge == "left":
                x = area.left() - pad
            else:
                x = area.right() + 1 - pad - gw
            y = max(area.top(), min(y, area.bottom() - h * 0.55))
        # 立绘轮廓贴住屏幕边（不用抓边素材时按轮廓对齐）
        elif self._edge == "left":
            x = area.left() - off - 2
            y = max(area.top() - h * 0.30, min(y, area.bottom() - h * 0.55))
        elif self._edge == "right":
            x = area.right() + 3 - off - w
            y = max(area.top() - h * 0.30, min(y, area.bottom() - h * 0.55))
        elif self._edge == "top":
            # 留 3px 让脚爪压在屏幕内侧，不然像被上边缘切掉
            y = area.top() + 3 - (feet_y - h)
            x = max(area.left() - off + 6, min(x, area.right() - off - w - 6))
        else:  # bottom
            y = area.bottom() + 1 - feet_y
            x = max(area.left() - off + 6, min(x, area.right() - off - w - 6))
        self.move(int(x), int(y))
        self.cfg.pos = [self.x(), self.y()]
        if not silent:
            save(self.cfg)
        self._sync_overlay()
        return True

    def _try_grab_edge(self) -> None:
        """拖拽松手后调用：离哪条边最近就吸附过去并进入抓握状态。"""
        if not self.cfg.edge_cling or self.cfg.lock_pos:
            self._edge = None
            return
        gaps = self._edge_gaps()
        side = min(gaps, key=lambda k: gaps[k])
        if gaps[side] > EDGE_SNAP:
            if self._edge is not None:
                self._edge = None
                self._apply_mask(self._airborne_mask)
                self.engine.say("松开边缘", 1.1)
            return
        self._edge = side
        self._snap_edge_position()
        self._apply_mask(self._airborne_mask)
        self._grab_settle = 1.0
        self._regrip_at = self._t + self._rng.uniform(9.0, 18.0)
        self._contact_puff(9)
        self.sfx.play("grab")  # 抓边成功：轻微低频「咔/嗒」
        if not self._edge_hinted:
            self._edge_hinted = True
            self.engine.say(f"我扒住{self.EDGE_LABEL[side]}啦", 2.0)
        else:
            self.engine.say("抓稳了", 1.2)

    def _detect_edge_from_pos(self) -> None:
        """启动时按实际位置恢复抓握状态（吸附后立绘是压着屏幕边的）。"""
        if not self.cfg.edge_cling:
            return
        area = self._screen_area()
        h = self._sprite_h
        pad = (self.width() - self.sprites.grip_width(h)) // 2 if self.sprites.has_grip else 0
        r = self._sprite_rect()
        side = None
        if self.x() <= area.left() - pad + 3 or r.left() <= area.left() + 3:
            side = "left"
        elif self.x() + self.width() >= area.right() + pad - 3 or r.right() >= area.right() - 2:
            side = "right"
        elif r.top() <= area.top() + 3:
            side = "top"
        elif r.bottom() >= area.bottom() - 2:
            side = "bottom"
        if side:
            self._edge = side
            self._snap_edge_position(silent=True)
            self._apply_mask(False)
            self._regrip_at = self._rng.uniform(9.0, 18.0)

    def release_edge(self, speak: bool = True) -> None:
        if self._edge is None:
            return
        self._edge = None
        self._apply_mask(self._airborne_mask)
        if speak:
            self.engine.say("松手啦", 1.3)

    def _edge_sign(self) -> int:
        return -1 if self._edge in ("left", "top") else 1

    def _paint_pivot(self) -> QPointF | None:
        """左右悬挂时的抓握点（肩部），倒挂与停靠不需要。"""
        if self._edge == "left":
            return QPointF(-self._sprite_w * 0.30, -self._sprite_h * EDGE_PIVOT_Y)
        if self._edge == "right":
            return QPointF(self._sprite_w * 0.30, -self._sprite_h * EDGE_PIVOT_Y)
        return None

    def _apply_pet_transform(
        self, p: QPainter, side, rot: float, dy: float, sx: float, sy: float, dx: float = 0.0
    ) -> None:
        """统一绘制变换：绘制与遮罩必须共用，否则遮罩会跟姿态对不上。"""
        w, h = self._sprite_w, self._sprite_h
        p.translate(self._feet.x() + dx, self._feet.y() + dy)
        if side == "top":
            # 倒挂：以腿脚为抓握点，整体翻上来，头朝下悬在屏幕顶边
            p.translate(0, -h)
            p.scale(1, -1)
            p.rotate(-rot)
        elif side in ("left", "right"):
            pivot = QPointF(-w * 0.30 if side == "left" else w * 0.30, -h * EDGE_PIVOT_Y)
            p.translate(pivot)
            p.rotate(rot)
            p.translate(-pivot)
        else:
            p.rotate(rot)
        p.scale(sx, sy)

    # ------------------------------------------------------------ 抓握的手
    def _edge_local_x(self, side: str) -> float:
        """屏幕边缘在窗口坐标系里的 x。"""
        area = self._screen_area()
        if side == "left":
            return area.left() - self.x()
        return area.right() + 1 - self.x()

    def _paint_pet(self, p: QPainter, side, rot: float, dy: float, sx: float, sy: float) -> None:
        """画立绘；左右贴边时换用专用的抓边姿态，手正好压在屏幕边线上。"""
        w, h = self._sprite_w, self._sprite_h
        blinking = self._pose.eyes is not None
        blink_ratio = BLINK_RATIO[int(round(self._pose.eyes))] if blinking else 1.0
        squeeze = 1.0 - 0.18 * min(1.0, abs(self.gaze.nx))

        if side in ("left", "right") and self.sprites.has_grip:
            gw = self.sprites.grip_width(h)
            ex = self._edge_local_x(side)
            left = ex if side == "left" else ex - gw
            top = self._feet.y() - h + dy
            mirrored = side == "right"
            # 抓边立绘有眼睛分层就走「底板 + 可动眼睛」，否则退回整张静态图
            layered = self.sprites.has_grip_gaze and self.cfg.gaze_follow
            pm = (self.sprites.grip_base(h, mirrored=mirrored) if layered
                  else self.sprites.grip(h, mirrored=mirrored))
            # 抓边时手压在屏幕边线上：以抓握点为支点做小幅随视线倾斜，
            # 让头部明显转向光标，而手基本不离边。rot 含拖拽/装饰角，gaze_tilt 为视线角。
            p.save()
            pivot_x = left if side == "left" else left + gw
            pivot_y = top + h * (1.0 - EDGE_PIVOT_Y)
            gaze_tilt = self.gaze.head_tilt() if self.cfg.gaze_follow else 0.0
            p.translate(pivot_x, pivot_y)
            # 抓边以抓握点为支点：光标在内侧（nx 与 side 同向）时头部朝光标转，
            # 故取 rot - gaze_tilt（左/右对称，pixmap 镜像已处理左右，painter 同向）。
            p.rotate(rot - gaze_tilt)
            p.translate(-pivot_x, -pivot_y)
            p.drawPixmap(QRectF(left, top, gw, h), pm, QRectF(0, 0, pm.width(), pm.height()))
            if not layered:
                p.restore()
                return
            # 抓边时手已固定压在边线上，这里只动眼睛（眼球位移量由眼窝安全余量约束）
            ox, oy = self.gaze.eye_offset(h)
            for i in (0, 1):
                gx, gy, gew, geh = self.sprites.grip_eye_rect(i, h)
                cx = left + gx + gew / 2
                if mirrored:
                    cx = left + gw - (gx + gew / 2)
                cy = top + gy + geh / 2
                eye_pm = self.sprites.grip_eye(i, h)
                dw, dh = gew * squeeze, max(2.0, geh * blink_ratio)
                rect = QRectF(cx + ox - dw / 2, cy + oy - dh / 2, dw, dh)
                p.drawPixmap(rect, eye_pm, QRectF(0, 0, eye_pm.width(), eye_pm.height()))
            p.restore()
            return

        # 中心 / 上边缘倒挂 / 下边缘停靠共用普通立绘的眼睛分层。
        # 这里只看素材是否齐备、**不看 gaze_follow 开关**：关掉跟随只是让眼睛停在原位，
        # 绘制路径保持原样，避免开关切换时立绘在「分层重绘」与「整帧」之间跳变。
        gaze_ok = self.sprites.has_gaze
        # 头部联动：中心与上下边缘都随视线倾斜（增强边缘跟随时「转头看向光标」的辨识度）；
        # 左右抓边走上面的专用分支（以抓握点为支点单独倾斜）。
        # 倒挂（top）整体 scale(1,-1) 后再 rotate(-rot)，水平方向需反向才朝光标转。
        hsign = -1.0 if side == "top" else 1.0
        head_rot = (hsign * self.gaze.head_rotation()) if (gaze_ok and side in (None, "top", "bottom")) else 0.0
        head_dx = (hsign * self.gaze.head_shift(h)) if (gaze_ok and side in (None, "top", "bottom")) else 0.0
        self._apply_pet_transform(p, side, rot + head_rot, dy, sx, sy, dx=head_dx)

        if gaze_ok:
            # 底板（无眼）+ 可位移的眼睛
            pm = self.sprites.base(h)
            p.drawPixmap(QRectF(-w / 2, -h, w, h), pm, QRectF(0, 0, pm.width(), pm.height()))
            ox, oy = self.gaze.eye_offset(h)
            if side == "top":
                # 倒挂时 painter 已 scale(1,-1)，局部 +y 在屏幕上是向上，故纵向偏移反号
                oy = -oy
            for i in (0, 1):
                ex, ey, ew, eh = self.sprites.eye_rect(i, h)
                cx = -w / 2 + ex + ew / 2 + ox
                cy = -h + ey + eh / 2 + oy
                eye_pm = self.sprites.eye(i, h)
                dw, dh = ew * squeeze, max(2.0, eh * blink_ratio)
                rect = QRectF(cx - dw / 2, cy - dh / 2, dw, dh)
                p.drawPixmap(rect, eye_pm, QRectF(0, 0, eye_pm.width(), eye_pm.height()))
        else:
            frame = self._pose.eyes if blinking else 0
            pm = self.sprites.pixmap(frame, h)
            p.drawPixmap(QRectF(-w / 2, -h, w, h), pm, QRectF(0, 0, pm.width(), pm.height()))

    def _contact_puff(self, count: int) -> None:
        """抓握接触点扬尘，按四条边给不同方向。"""
        if self._edge is None:
            return
        h = self._sprite_h
        if self._edge in ("left", "right"):
            sign = -1 if self._edge == "left" else 1
            dx, dy = sign * (self._sprite_w * 0.5), -h * 0.62
            angle = (math.pi * 0.55, math.pi * 1.45) if sign < 0 else (-math.pi * 0.45, math.pi * 0.45)
        elif self._edge == "top":
            dx, dy = 0.0, -h * 0.98
            angle = (0.15 * math.pi, 0.85 * math.pi)
        else:
            dx, dy = 0.0, -4.0
            angle = (1.15 * math.pi, 1.85 * math.pi)
        self.engine.burst(
            "dust", dx, dy, count, (18, 46), angle=angle,
            ttl=0.42, g=30, color=QColor("#D6DAE2"), size=4, drag=3.0,
        )

    def _regrip(self) -> None:
        """贴边时偶尔「再抓一把」：轻微挪动 + 接触点扬尘。"""
        self._grip_hop = 1.0
        self._regrip_at = self._t + self._rng.uniform(10.0, 20.0)
        self._contact_puff(4)

    def _build_mask(self, airborne: bool):
        from PyQt5.QtGui import QRegion

        h, w = self._sprite_h, self._sprite_w
        left = int(self._feet.x() - w / 2)
        top = int(self._feet.y() - h)
        region = QRegion(self.sprites.mask(h)).translated(left, top)
        lifts = [0.10, 0.20, 0.30, 0.40] if airborne else [0.05]
        for frac in lifts:
            region = region.united(
                QRegion(self.sprites.mask(h)).translated(left, top - int(h * frac))
            )
        rect = self.sprites.foot_shadow_rect(h).translated(int(self._feet.x()), int(self._feet.y()))
        return region.united(QRegion(rect, QRegion.Ellipse))

    def _build_edge_mask(self, side: str):
        """贴边时宠物会倾斜/翻转，遮罩按同一套变换逐角度重建，否则身体会被裁掉。"""
        from PyQt5.QtGui import QBitmap, QImage, QPainter as QP, QRegion

        img = QImage(self.size(), QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        p = QP(img)
        p.setRenderHint(QP.Antialiasing, True)
        p.setRenderHint(QP.SmoothPixmapTransform, True)
        w, h = self._sprite_w, self._sprite_h
        pm = self.sprites.pixmap(0, h)
        if side in ("left", "right"):
            angles = (-24, -19, -14, -9, -4, 1, 6, 11, 16, 21)
        elif side == "top":
            angles = (-10, -5, 0, 5, 10)
        else:
            angles = (0,)
        for angle in angles:
            p.save()
            self._paint_pet(p, side, angle, 0.0, 1.0, 1.0)
            p.restore()
        p.end()
        region = QRegion(QBitmap.fromImage(img.createAlphaMask()))
        for dy in (-0.34, -0.22, -0.10, 0.08):
            region = region.united(region.translated(0, int(h * dy)))
        if side != "top":
            rect = self.sprites.foot_shadow_rect(h).translated(int(self._feet.x()), int(self._feet.y()))
            region = region.united(QRegion(rect, QRegion.Ellipse))
        return region

    def _edge_mask(self, side: str):
        key = (self._sprite_h, side)
        cache = getattr(self, "_mask_cache", None)
        if cache is None:
            cache = self._mask_cache = {}
        if key not in cache:
            cache[key] = self._build_edge_mask(side)
        return cache[key]

    def refresh_mask(self) -> None:
        self._apply_mask(self._airborne_mask)

    def _apply_mask(self, airborne: bool) -> None:
        self._airborne_mask = airborne
        if self._edge is not None:
            region = self._edge_mask(self._edge)
        else:
            region = self._mask_air if airborne else self._mask_idle
        if region is not None:
            self.setMask(region)

    # ------------------------------------------------------------ 定时
    def _bind_timers(self) -> None:
        self._tick_timer = QTimer(self)
        self._tick_timer.setTimerType(Qt.PreciseTimer)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(TICK_MS)

        self._hint_timer = QTimer(self)
        self._hint_timer.setSingleShot(True)
        self._hint_timer.timeout.connect(self._show_hover_hint)

        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._auto_effect)

    def _schedule_auto_effect(self) -> None:
        self._auto_timer.stop()
        if not self.cfg.auto_effect:
            return
        base = self.cfg.auto_effect_min * 60_000
        delay = int(base * self._rng.uniform(0.75, 1.25))
        self._auto_timer.start(max(20_000, delay))

    def _auto_effect(self) -> None:
        self._schedule_auto_effect()
        if self._dragging or self.engine.is_busy() or not self.isVisible():
            return
        pool = ["hearts", "sparkle", "hop", "dance", "blink", "coffee", "confetti"]
        self.play(self._rng.choice(pool), quiet=True)

    # ------------------------------------------------------------ 主循环
    def _tick(self) -> None:
        dt = min(0.05, self._elapsed.restart() / 1000.0)
        self._t += dt
        if self._swap_fade < 1.0:
            # 换肤淡入：180ms 内从 0 升到 1；不依赖任何外部状态，必然收敛
            self._swap_fade = min(1.0, self._swap_fade + dt / 0.18)
        liveliness = self.cfg.liveliness

        pose = self.engine.tick(dt)

        # 待机呼吸 + 漂浮
        if self.cfg.idle_anim and not self._dragging:
            breathe = math.sin(self._t * math.tau / 3.8)
            float_y = -2.4 * liveliness * (0.5 + 0.5 * math.sin(self._t * math.tau / 3.8 + 1.1))
            pose.sx *= 1 - 0.007 * liveliness * breathe
            pose.sy *= 1 + 0.011 * liveliness * breathe
            pose.dy += float_y
        elif self._dragging:
            pose.dy += -4.0

        # 悬停放大
        self._hover += (self._hover_target - self._hover) * min(1.0, dt * 12)
        pose.sx *= 1 + 0.028 * self._hover
        pose.sy *= 1 + 0.028 * self._hover
        pose.dy -= 3.0 * self._hover

        # 拖入态「接住」脉冲：轻微放大 + 呼吸光晕，表示「我准备好了」
        if self._drag_over:
            self._drag_phase += dt * 5.0
            pulse = 0.5 + 0.5 * math.sin(self._drag_phase)
            pose.sx *= 1 + 0.035 * pulse
            pose.sy *= 1 + 0.035 * pulse
            pose.glow = max(pose.glow, 0.35 + 0.2 * pulse)

        # 拖拽倾斜
        if self._dragging:
            now = time.monotonic()
            dx = self._last_move_x
            tilt_target = max(-9.0, min(9.0, dx * 0.6))
            self._tilt += (tilt_target - self._tilt) * min(1.0, dt * 8)
            self._last_move_x *= 0.72
        else:
            self._tilt += (0.0 - self._tilt) * min(1.0, dt * 6)
            if time.monotonic() - self._last_move_t > 0.12:
                self._last_move_x = 0.0
        pose.rot += self._tilt

        # 贴边抓握：四条边各有姿态，都在抓握点附近轻摆
        if self._edge is not None and not self._dragging:
            phase = self._t * 1.55
            if self._edge in ("left", "right"):
                if self.sprites.has_grip:
                    # 专用抓边立绘已经把姿态画好，这里只做呼吸式起伏
                    pose.dy += 1.6 + 1.4 * math.sin(phase * 0.8)
                else:
                    pose.rot += self._edge_sign() * (EDGE_TILT + 2.6 * math.sin(phase))
                    pose.dy += 2.0 + 2.2 * math.sin(phase * 0.85)
                    pose.sx *= 0.985
            elif self._edge == "top":
                # 上边缘倒挂：小幅摆动，身体向下悬垂
                pose.rot += 2.6 * math.sin(phase * 1.1)
                pose.dy += 1.8 + 1.6 * math.sin(phase * 0.9)
            else:
                # 下边缘停靠：屈膝坐下，轻微起伏
                pose.sy *= 0.945
                pose.sx *= 1.028
                pose.dy += 2.0 + 1.2 * math.sin(phase * 0.8)
            if self._grip_hop > 0:
                self._grip_hop = max(0.0, self._grip_hop - dt * 2.6)
                pose.dy -= 4.0 * self._grip_hop
            if self._grab_settle > 0:
                self._grab_settle = max(0.0, self._grab_settle - dt * 3.4)
                pose.dy += 7.0 * math.sin(math.pi * self._grab_settle)
            if self._t >= self._regrip_at and not self.engine.is_busy():
                self._regrip()

        # 音乐律动：权重缓动到目标后再叠加极小幅度摆动，
        # 关掉音乐（或拖动中）时 grooves 会自然衰减回 0，不会突然「定格」
        groove_target = 1.0 if (self._music_on and not self._dragging) else 0.0
        self._music_groove += (groove_target - self._music_groove) * min(1.0, dt * 2.5)
        if self._music_groove > 0.001:
            groove = self._music_groove * liveliness
            pose.rot += 1.8 * groove * math.sin(math.tau * self._t / 2.6)
            pose.dy += 1.0 * groove * math.sin(math.tau * self._t / 1.3)

        # 空闲自动漫游：位置由控制器直接写到窗口上，这里只叠加步态。
        # 幅度刻意压在 ±2.2px 内，避免每帧触发 airborne 轮廓遮罩切换。
        if self._roam is not None:
            roam = self._roam.step(dt)
            if roam is not None:
                pose.dy += roam.bob
                pose.rot += roam.rot
                pose.sx *= roam.sx
                pose.sy *= roam.sy

        # 随机眨眼
        if not self.engine.is_busy() and pose.eyes is None:
            self._blink_in -= dt
            if self._blink_in <= 0:
                self.engine.start("blink", supersede=False)
                self._blink_in = self._rng.uniform(2.4, 6.2)

        # 先落定本帧姿态再算视线：贴边时眼位依赖 pose.dy / sx / sy，
        # 用上一帧的 pose 会让眼睛中心慢一拍，快速移动时能看出方向滞后。
        self._pose = pose
        self._update_gaze(dt)
        airborne = abs(pose.dy) > 3.0
        if self._roam is not None and self._roam.active:
            # 漫游的步态会反复穿过 ±3px 阈值；固定用「带抬升余量」的遮罩，
            # 避免每帧 setMask 来回切换（立绘缩小/侧身也绝不会越出这个轮廓）
            airborne = True
        # 拖入态用的是全窗遮罩（要画出宠物外的虚线框），此时不要去切轮廓遮罩
        if not self._drag_over and airborne != self._airborne_mask:
            self._apply_mask(airborne)

        self.update()
        self._sync_overlay()

    def set_roam_controller(self, controller) -> None:
        """由 ``PetApp`` 注入漫游控制器；传入 ``None`` 可随时卸载。"""
        self._roam = controller

    def _gaze_available(self) -> bool:
        """当前姿态是否有可动眼睛。

        左右贴边走的是**另一张立绘**（抓边图），普通立绘的 ``eyes.json``
        眼位对它完全不适用，必须要求抓边自己的眼睛分层齐备；
        中心 / 上边缘倒挂 / 下边缘停靠用的是同一套普通立绘，看 ``has_gaze``。
        """
        if not self.cfg.gaze_follow:
            return False
        if self._edge in ("left", "right") and self.sprites.has_grip:
            return self.sprites.has_grip_gaze
        return self.sprites.has_gaze

    def _eye_center_global(self) -> QPointF:
        """当前姿态下眼睛中心在**屏幕**上的位置。

        贴边姿态会倒挂（``scale(1,-1)``）或压缩（``sy``），眼睛不再位于
        「立绘上部 72%」那个固定比例处，必须按各自的几何变换反算，
        否则「鼠标在眼睛的哪一侧」会算错，表现为视线朝反方向或不动。
        """
        side = self._edge
        w, h = self._sprite_w, self._sprite_h
        pose = self._pose
        if side in ("left", "right") and self.sprites.has_grip and self.sprites.has_grip_gaze:
            # 抓边立绘不做 painter 变换，直接画在 (left, top) 矩形里
            gw = self.sprites.grip_width(h)
            ex = self._edge_local_x(side)
            left = ex if side == "left" else ex - gw
            top = self._feet.y() - h + pose.dy
            gx, gy, gw_eye, gh_eye = self.sprites.grip_eye_rect(0, h)
            cx = left + gx + gw_eye / 2
            if side == "right":
                cx = left + gw - (gx + gw_eye / 2)      # 立绘整体镜像
            cy = top + gy + gh_eye / 2
        else:
            ex, ey, ew, eh = self.sprites.eye_rect(0, h)
            local_x = -w / 2 + ex + ew / 2
            local_y = -h + ey + eh / 2
            if side == "top":
                # 倒挂：translate(0,-h) + scale(1,-1)，局部 y 在屏幕上反向
                cx = self._feet.x() + local_x
                cy = self._feet.y() - h - local_y
            else:
                cx = self._feet.x() + local_x * pose.sx
                cy = self._feet.y() + local_y * pose.sy
        return QPointF(self.x() + cx, self.y() + cy)

    def _update_gaze(self, dt: float) -> None:
        """以眼睛中心为原点，把光标位置平滑映射成视线方向。"""
        if not self._gaze_available() or not self.isVisible():
            self.gaze.reset()
            return
        self.gaze.update(dt, self._eye_center_global(), QCursor.pos(), enabled=True)

    def set_overlay_suppressed(self, value: bool) -> None:
        """菜单 / 弹窗打开时，隐藏始终置顶的粒子层，避免盖住它们。"""
        self._overlay_suppressed = value
        if value and self.overlay is not None and self.overlay.isVisible():
            self.overlay.hide()

    def _sync_overlay(self) -> None:
        if self.overlay is None:
            return
        if self._overlay_suppressed:
            if self.overlay.isVisible():
                self.overlay.hide()
            return
        feet_global = QPointF(self.x() + self._feet.x(), self.y() + self._feet.y())
        if not self.overlay.has_content():
            if self.overlay.isVisible():
                self.overlay.hide()
            self.overlay.sync(self.geometry(), feet_global, self._sprite_h)
            return
        if not self.overlay.isVisible():
            self.overlay.show()
        moved = self.overlay.sync(self.geometry(), feet_global, self._sprite_h)
        if moved:
            self.overlay.raise_()
        self.overlay.update()

    # ------------------------------------------------------------ 绘制
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform
        )

        # 拖入态：先在窗口坐标系画一圈橙色虚线框（宠物画在它上面），
        # 让「松手就会接住」这件事一眼可见
        if self._drag_over:
            painter.setPen(QPen(QColor(255, 122, 61, 190), 2, Qt.DashLine))
            painter.setBrush(QColor(255, 122, 61, 18))
            painter.drawRoundedRect(QRectF(self.rect().adjusted(8, 8, -8, -8)), 16, 16)
            painter.setBrush(Qt.NoBrush)

        pose = self._pose
        w, h = self._sprite_w, self._sprite_h
        cx, feet_y = self._feet.x(), self._feet.y()
        lift = max(0.0, min(1.0, -pose.dy / (h * 0.30)))
        if self._edge is not None:
            lift = max(lift, 0.35)

        # 倒挂时不画地面投影，其它贴边姿态保留淡投影
        if self._edge != "top":
            self._paint_shadow(painter, cx, feet_y, h, lift, fade=self._swap_fade)

        painter.save()
        painter.setOpacity(self.cfg.opacity * max(0.0, min(1.0, pose.alpha)) * self._swap_fade)
        self._paint_pet(painter, self._edge, pose.rot, pose.dy, pose.sx, pose.sy)

        if pose.glow > 0.01:
            glow = QRadialGradient(QPointF(0, -h * 0.62), w * 0.62)
            glow.setColorAt(0, QColor(255, 236, 210, int(70 * pose.glow)))
            glow.setColorAt(1, QColor(255, 214, 170, 0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(glow)
            painter.drawEllipse(QPointF(0, -h * 0.62), w * 0.62, h * 0.42)
        painter.restore()

        painter.end()

    def _paint_shadow(self, painter: QPainter, cx: float, feet_y: float, h: int, lift: float, fade: float = 1.0) -> None:
        w = h * 0.56 * (1 - 0.22 * lift)
        alpha = int(78 * (1 - 0.55 * lift) * self.cfg.opacity * fade)
        grad = QRadialGradient(QPointF(cx, feet_y), w * 0.5)
        grad.setColorAt(0.0, QColor(12, 14, 20, alpha))
        grad.setColorAt(0.55, QColor(12, 14, 20, int(alpha * 0.55)))
        grad.setColorAt(1.0, QColor(12, 14, 20, 0))
        painter.setPen(Qt.NoPen)
        painter.setBrush(grad)
        painter.drawEllipse(QPointF(cx, feet_y), w * 0.5, h * 0.052 * (1 - 0.2 * lift))

    # ------------------------------------------------------------ 鼠标
    def mousePressEvent(self, event) -> None:
        # 人已经亲手点进来了：不必等 500ms 的空闲轮询，立刻收住漫游并归位
        if self._roam is not None and self._roam.active:
            self._roam.interrupt()
        if event.button() == Qt.LeftButton:
            # 新的一次按下：取消上一次还没落地的单击（也顺带清掉双击抑制）
            self._click_timer.stop()
            self._ignore_click = False
            self._press_global = event.globalPos()
            self._press_offset = event.globalPos() - self.pos()
            self._press_ms = time.monotonic()
            self._dragging = False
            self._hover_target = 0.0
        elif event.button() == Qt.RightButton:
            self._show_menu(event.globalPos())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if not (event.buttons() & Qt.LeftButton):
            return
        delta = event.globalPos() - self._press_global
        if not self._dragging and delta.manhattanLength() <= DRAG_THRESHOLD:
            return
        if self.cfg.lock_pos:
            if not self._dragging:
                self.engine.say("位置已锁定", 1.2)
            self._dragging = True
            return
        if not self._dragging:
            self._dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            self.engine.clear_bubble()
            if self._edge is not None:
                self._edge = None      # 拖动即松手
                self._grip_hop = 0.0
                self._apply_mask(self._airborne_mask)
        new_pos = event.globalPos() - self._press_offset
        now = time.monotonic()
        dt = max(1e-3, now - self._last_move_t)
        self._last_move_x = (new_pos.x() - self.x()) / dt * 0.02
        self._last_move_t = now
        self.move(new_pos)
        self._sync_overlay()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.LeftButton:
            return super().mouseReleaseEvent(event)
        was_dragging = self._dragging
        self._dragging = False
        self.setCursor(Qt.PointingHandCursor)
        if was_dragging:
            self.move(self._clamp(self.pos()))
            self._try_grab_edge()
            self.sfx.play("release")  # 拖拽松手：柔和低频「噗」
            self.cfg.pos = [self.x(), self.y()]
            save(self.cfg)
            return
        elapsed_ms = (time.monotonic() - self._press_ms) * 1000
        if elapsed_ms <= CLICK_MAX_MS:
            if self._ignore_click:
                # 刚刚是双击的第二下松手：已经把「双击」交给 mouseDoubleClickEvent 了
                self._ignore_click = False
            else:
                # 延后一个双击间隔再执行单击，给双击留出识别时间
                delay = min(260, int(QApplication.doubleClickInterval()))
                self._click_timer.start(max(120, delay))
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        """双击宠物 → 石头剪刀布。单击被推迟，所以这里要先取消它。"""
        if event.button() == Qt.LeftButton:
            self._click_timer.stop()
            self._ignore_click = True
            self._dragging = False
            self.sfx.play("click")
            self.rps_requested.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _on_click(self) -> None:
        # 无论哪种左键模式，都先响一声点击音
        self.sfx.play("click")
        mode = self.cfg.left_click
        if mode == "open":
            self.play("hop", quiet=True)
            self.engine.say("打开 WorkBuddy…", 1.5)
            QTimer.singleShot(200, self.open_workbuddy)
        elif mode == "effect":
            self.play(self._rng.choice(EFFECT_ORDER), quiet=True)
        else:
            self.play("blink", quiet=True)

    def enterEvent(self, event) -> None:
        self._hover_target = 1.0
        # 悬停提示音：节流避免反复进/出时刷屏
        now = time.monotonic()
        if now - self._last_hover_sfx > 0.6:
            self._last_hover_sfx = now
            self.sfx.play("hover", cooldown_ms=600)
        if self.cfg.bubble_hint and not self._hint_shown and not self.engine.is_busy():
            self._hint_timer.start(750)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_target = 0.0
        self._hint_timer.stop()
        super().leaveEvent(event)

    def _show_hover_hint(self) -> None:
        if self._hover < 0.4:
            return
        self._hint_shown = True
        self.engine.say("左键打开 WorkBuddy · 右键看菜单", 2.6)

    # ------------------------------------------------------------ 拖放文件
    def dragEnterEvent(self, event) -> None:
        """有文件悬到宠物身上：接住 + 视觉/听觉/台词三重反馈。"""
        try:
            data = event.mimeData()
        except Exception:
            data = None
        if data is not None and data.hasUrls():
            event.acceptProposedAction()
            self._drag_enter()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:
        """悬停移动：持续接住事件，否则 Windows 会认为拖放取消。"""
        try:
            data = event.mimeData()
        except Exception:
            data = None
        if data is not None and data.hasUrls():
            event.acceptProposedAction()
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._drag_exit()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        """松手：先退出拖入态（还原遮罩），再按内容给反馈。"""
        self._drag_exit()
        urls: list = []
        try:
            data = event.mimeData()
            if data is not None:
                urls = list(data.urls() or [])
        except Exception:
            urls = []
        self._handle_drop_urls(urls)
        try:
            event.acceptProposedAction()
        except Exception:
            pass
        self.update()

    def _drag_enter(self) -> None:
        """进入拖入态：整窗可绘制（虚线框画在宠物外面也得看得到）。"""
        if self._drag_over:
            return
        self._drag_over = True
        self._drag_phase = 0.0
        self.setMask(QRegion(self.rect()))
        self.sfx.play("hover")
        self.engine.say(lines.pick("drag_enter"), 2.0)
        self.update()

    def _drag_exit(self) -> None:
        """退出拖入态：恢复宠物轮廓遮罩（贴边时走贴边轮廓）。"""
        if not self._drag_over:
            return
        self._drag_over = False
        self._apply_mask(self._airborne_mask)
        self.update()

    def _handle_drop_urls(self, urls) -> None:
        """把 QUrl 列表拆成 本地文件 / 本地文件夹 / 远程链接 三桶。"""
        files: list[str] = []
        folders: list[str] = []
        remote: list[str] = []
        for url in list(urls or []):
            try:
                local = bool(url.isLocalFile())
                text = url.toLocalFile() if local else url.toString()
            except Exception:
                continue
            text = str(text or "").strip()
            if not text:
                continue
            if not local:
                remote.append(text)
                continue
            try:
                is_dir = Path(text).is_dir()
            except OSError:
                is_dir = False
            (folders if is_dir else files).append(text)
        self._apply_drop(files, folders, remote)

    def _apply_drop(self, files: list[str], folders: list[str], remote: list[str]) -> None:
        """拖放落地的统一处理入口（``dropEvent`` 与 ``simulate_drop`` 共用）。"""
        count = len(files) + len(folders) + len(remote)
        if count <= 0:
            return
        self.sfx.play("complete")

        # 注意顺序：**先 play() 再 say()**。
        # box 这类特效在自己的 on_start() 里会 say 一句（如「开个盲盒…」），
        # 若先 say 文件名再 play，就会同一帧被特效台词覆盖，用户看不到名字。
        # 所以统一「特效先起、气泡后写」，保证文件名/大小是最终显示的那句。

        # ---- 多份：一次性庆祝，只报总数 ----
        if count > 1:
            self.play("confetti", quiet=True)
            self.engine.say(f"收到 {count} 个文件！", 2.0)
            self._record_drop((files or folders or remote)[0], count)
            self._say_later(lines.pick("drop_multi", n=count))
            return

        # ---- 单个远程链接 ----
        if remote:
            link = remote[0]
            self.play("sparkle", quiet=True)
            self.engine.say(f"收到「{self._short_name(link, 18)}」", 2.0)
            self._record_drop(link, 1)
            self._say_later(lines.pick("drop_url"))
            return

        # ---- 单个文件夹 ----
        if folders:
            path = folders[0]
            name = self._short_name(Path(path).name or path)
            self.play("box", quiet=True)
            self.engine.say(f"收到文件夹「{name}」", 2.0)
            self._record_drop(path, 1)
            self._say_later(lines.pick("drop_folder"))
            return

        # ---- 单个文件：按扩展名分类 ----
        path = files[0]
        category = self._classify(path)
        name = self._short_name(Path(path).name or path)
        size = self._fmt_size(path)
        suffix = f" {size}" if size else ""
        self.play(DROP_EFFECT.get(category, "box"), quiet=True)
        self.engine.say(f"收到「{name}」{suffix}", 2.0)
        self._record_drop(path, 1)
        self._say_later(lines.pick(f"drop_{category}"))

    @staticmethod
    def _classify(path: str) -> str:
        """按扩展名把文件归到 image/audio/code/doc/archive/other。"""
        try:
            ext = Path(str(path or "")).suffix.lstrip(".").lower()
        except Exception:
            return "other"
        if not ext:
            return "other"
        if ext in DROP_IMAGE:
            return "image"
        if ext in DROP_AUDIO:
            return "audio"
        if ext in DROP_CODE:
            return "code"
        if ext in DROP_DOC:
            return "doc"
        if ext in DROP_ARCHIVE:
            return "archive"
        return "other"

    @staticmethod
    def _fmt_size(path: str) -> str:
        """把文件大小格式化成 ``12 KB`` / ``3.4 MB``；读不到就返回空串。"""
        try:
            num = float(Path(str(path or "")).stat().st_size)
        except (OSError, ValueError):
            return ""
        if num < 1024:
            return f"{int(num)} B"
        if num < 1024 * 1024:
            return f"{num / 1024:.0f} KB"
        return f"{num / (1024 * 1024):.1f} MB"

    @staticmethod
    def _short_name(text: str, limit: int = 14) -> str:
        """文件名太长会把气泡撑爆，超长截断加省略号。"""
        base = str(text or "").strip()
        if not base:
            return ""
        if len(base) <= limit:
            return base
        return base[: max(1, limit - 1)] + "…"

    def _record_drop(self, first_path: str, count: int) -> None:
        """记录拖放统计（只记录，不复制/移动任何文件）。"""
        try:
            self.cfg.drop_count = int(self.cfg.drop_count) + int(count)
            if first_path:
                self.cfg.last_drop_path = str(first_path)
            save(self.cfg)
        except Exception:
            pass

    def _say_later(self, text: str, delay_ms: int = 1900, duration: float = 1.8) -> None:
        """首句报完名字/大小之后，再补一句分类台词（气泡一次只显示一句）。"""
        if not text:
            return

        def _emit() -> None:
            if self.isVisible():
                self.engine.say(text, duration)

        QTimer.singleShot(delay_ms, _emit)

    def simulate_drop(self, paths: list[str]) -> None:
        """QA/命令通道专用：不依赖 ``QDropEvent``，直接走同一套拖放处理。"""
        files: list[str] = []
        folders: list[str] = []
        remote: list[str] = []
        for raw in list(paths or []):
            text = str(raw or "").strip()
            if not text:
                continue
            if text.startswith(("http://", "https://")):
                remote.append(text)
                continue
            try:
                is_dir = Path(text).is_dir()
            except OSError:
                is_dir = False
            if is_dir:
                folders.append(text)
            else:
                files.append(text)
        self._apply_drop(files, folders, remote)

    # ------------------------------------------------------------ 音乐感知反馈
    def on_music_state(self, playing: bool, kind: str = "music", title: str = "") -> None:
        """音乐感知状态变化：开/关律动 + 说一句。

        Args:
            playing: 现在是否「在响」。
            kind: ``"music"``（识别到音乐播放器）或 ``"audio"``（只是有声音）。
            title: 音乐窗口标题，可能为空。
        """
        self._music_on = bool(playing)
        if not playing:
            self.engine.say(lines.pick("music_stop"), 2.0)
            return
        if self._drag_over or self._dragging or not self.isVisible():
            return
        # 音乐 → 跳舞；只是有声音（视频/通话）→ 轻轻眨个眼，不喧宾夺主
        self.play("dance" if kind == "music" else "blink", quiet=True)
        if kind == "music" and str(title or "").strip():
            self.engine.say(lines.pick("music_start_track", title=self._short_name(title, 12)), 2.4)
        else:
            self.engine.say(lines.pick("music_start"), 2.2)

    # ------------------------------------------------------------ 动作
    def play(self, effect_id: str, quiet: bool = False) -> None:
        if effect_id not in EFFECTS:
            return
        if effect_id == "spin" and self._edge is not None:
            self.release_edge(speak=False)
        if self.engine.start(effect_id):
            self.cfg.last_effect = effect_id
            if not quiet:
                fx = EFFECTS[effect_id]
                self.engine.say(fx.name, 1.3)
            save(self.cfg)

    def open_workbuddy(self) -> None:
        ok, message = launcher.open_workbuddy(self.cfg.workbuddy_path)
        self.engine.say(message, 2.4)
        if not ok:
            QTimer.singleShot(300, self.settings_requested.emit)

    def reset_position(self) -> None:
        self._edge = None
        pos = self._initial_position()
        self.move(pos)
        self.cfg.pos = [pos.x(), pos.y()]
        save(self.cfg)
        self._sync_overlay()
        self.engine.say("回到右下角啦", 1.6)

    def apply_settings(self) -> None:
        if not self.cfg.edge_cling:
            self._edge = None
        self._apply_geometry(initial=False)
        if self.cfg.always_on_top != self._applied_on_top:
            self._apply_window_flags()
        self._schedule_auto_effect()
        # 设置面板保存后即时生效：音效开关与音量
        self.sfx.set_enabled(self.cfg.sound_enabled)
        self.sfx.set_volume(self.cfg.sound_volume)
        self.update()

    def _apply_window_flags(self) -> None:
        self._applied_on_top = self.cfg.always_on_top
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if self.cfg.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        pos = self.pos()
        self.setWindowFlags(flags)
        self.move(pos)
        self.show()
        self._apply_mask(self._airborne_mask)
        self._sync_overlay()

    def show_intro(self) -> None:
        if self.cfg.seen_intro:
            return
        self.cfg.seen_intro = True
        save(self.cfg)
        QTimer.singleShot(700, lambda: self.engine.say("嗨，我是 Buddy，点我打开 WorkBuddy ～", 5.0))

    # ------------------------------------------------------------ 菜单
    def _show_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.setAttribute(Qt.WA_TranslucentBackground, True)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        menu.setStyleSheet(theme.MENU_QSS)

        act_open = QAction(icons.icon_open(theme.ORANGE), "打开 WorkBuddy", menu)
        act_open.triggered.connect(self.open_workbuddy)
        menu.addAction(act_open)
        menu.addSeparator()

        fx_menu = QMenu("互动效果", menu)
        fx_menu.setStyleSheet(theme.MENU_QSS)
        fx_menu.setAttribute(Qt.WA_TranslucentBackground, True)
        fx_random = QAction(icons.icon_sparkle(), "随机来一个", fx_menu)
        fx_random.triggered.connect(lambda: self.play(self._rng.choice(EFFECT_ORDER)))
        fx_menu.addAction(fx_random)
        fx_menu.addSeparator()
        for eid in EFFECT_ORDER:
            fx = EFFECTS[eid]
            action = QAction(icons.effect_icon(eid), fx.name, fx_menu)
            action.setToolTip(fx.hint)
            action.triggered.connect(lambda _=False, e=eid: self.play(e))
            fx_menu.addAction(action)
        fx_menu.addSeparator()
        act_auto = QAction("待机随机互动", fx_menu)
        act_auto.setCheckable(True)
        act_auto.setChecked(self.cfg.auto_effect)
        act_auto.toggled.connect(self._toggle_auto_effect)
        fx_menu.addAction(act_auto)

        # 空闲自动漫游：只发信号，落盘与检测器开关由 main 统一负责
        act_roam = QAction(icons.icon_moon(), "空闲自动漫游", fx_menu)
        act_roam.setCheckable(True)
        act_roam.setChecked(self.cfg.roam_enabled)
        act_roam.toggled.connect(self.roam_toggle_requested.emit)
        fx_menu.addAction(act_roam)

        act_sound = QAction("音效", fx_menu)
        act_sound.setCheckable(True)
        act_sound.setChecked(self.cfg.sound_enabled)
        act_sound.toggled.connect(self._toggle_sound)
        fx_menu.addAction(act_sound)

        # 音乐感知开关：只发信号，落盘与检测器开关由 main 统一负责（避免两处写配置）
        act_music = QAction(icons.icon_music(), "音乐感知", fx_menu)
        act_music.setCheckable(True)
        act_music.setChecked(self.cfg.music_aware)
        act_music.toggled.connect(self.music_toggle_requested.emit)
        fx_menu.addAction(act_music)
        menu.addMenu(fx_menu).setIcon(icons.icon_sparkle())

        act_rps = QAction(icons.icon_gift(), "石头剪刀布…", menu)
        act_rps.triggered.connect(self.rps_requested.emit)
        menu.addAction(act_rps)

        act_settings = QAction(icons.icon_gear(), "设置…", menu)
        act_settings.setShortcut("Ctrl+,")
        act_settings.triggered.connect(self.settings_requested.emit)
        menu.addAction(act_settings)

        act_gallery = QAction(icons.icon_star(), "切换 Buddy…", menu)
        act_gallery.triggered.connect(self.gallery_requested.emit)
        menu.addAction(act_gallery)
        menu.addSeparator()

        act_top = QAction(
            icons.icon_check() if self.cfg.always_on_top else icons.icon_pin(), "始终置顶", menu
        )
        act_top.setCheckable(True)
        act_top.setChecked(self.cfg.always_on_top)
        act_top.toggled.connect(self._toggle_always_on_top)
        menu.addAction(act_top)

        act_lock = QAction(
            icons.icon_check() if self.cfg.lock_pos else icons.icon_lock(), "锁定位置", menu
        )
        act_lock.setCheckable(True)
        act_lock.setChecked(self.cfg.lock_pos)
        act_lock.toggled.connect(self._toggle_lock)
        menu.addAction(act_lock)

        act_reset = QAction(icons.icon_target(), "回到右下角", menu)
        act_reset.triggered.connect(self.reset_position)
        menu.addAction(act_reset)

        if self._edge is not None:
            act_release = QAction(icons.icon_up(), "松开边缘", menu)
            act_release.triggered.connect(lambda: self.release_edge())
            menu.addAction(act_release)
        menu.addSeparator()

        act_about = QAction(icons.icon_info(), "关于 Buddy", menu)
        act_about.triggered.connect(self.about_requested.emit)
        menu.addAction(act_about)

        act_hide = QAction(icons.icon_hidden(), "隐藏宠物", menu)
        act_hide.triggered.connect(self.hidden_requested.emit)
        menu.addAction(act_hide)

        act_quit = QAction(icons.icon_power(), "退出", menu)
        act_quit.triggered.connect(self.quit_requested.emit)
        menu.addAction(act_quit)

        self.set_overlay_suppressed(True)
        try:
            menu.exec_(pos)
        finally:
            self.set_overlay_suppressed(False)

    def _toggle_auto_effect(self, checked: bool) -> None:
        self.cfg.auto_effect = checked
        save(self.cfg)
        self._schedule_auto_effect()
        self.engine.say("待机互动已开启" if checked else "待机互动已关闭", 1.5)

    def _toggle_always_on_top(self, checked: bool) -> None:
        self.cfg.always_on_top = checked
        save(self.cfg)
        self._apply_window_flags()
        self.engine.say("已置顶" if checked else "取消置顶", 1.4)

    def _toggle_lock(self, checked: bool) -> None:
        self.cfg.lock_pos = checked
        save(self.cfg)
        self.engine.say("位置已锁定" if checked else "位置已解锁", 1.5)

    def _on_effect_finished(self, eid: str) -> None:
        """某互动特效自然播放完成 → 触发柔和的「完成操作」音效（带节流）。"""
        now = time.monotonic()
        if now - self._last_complete_sfx < 0.12:
            return
        self._last_complete_sfx = now
        self.sfx.play("complete", cooldown_ms=120)

    def _toggle_sound(self, checked: bool) -> None:
        """右键菜单 / 托盘菜单的「音效」勾选项逻辑。"""
        self.cfg.sound_enabled = bool(checked)
        save(self.cfg)
        self.sfx.set_enabled(checked)

    # ------------------------------------------------------------ 其它
    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._sync_overlay()

    def closeEvent(self, event) -> None:
        event.ignore()
        self.hide()
