"""素材加载：眨眼帧、缩放缓存、点击命中区域。"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QBitmap, QPixmap, QRegion, QTransform

BASE_W, BASE_H = 268, 392
FRAME_COUNT = 6


def _eye_meta_valid(meta: dict) -> bool:
    """校验眼位元数据的结构是否足以支撑 ``eye_rect`` / ``eye``。

    ``size`` 必须是两个正整数，``eyes.left`` / ``eyes.right`` 的
    ``x`` / ``y`` / ``w`` / ``h`` 必须都能转 ``int`` 且 ``w``、``h`` 为正。
    普通立绘的 ``eyes.json`` 与抓边立绘的 ``eyes_grip.json`` 共用这套判据。
    任何异常一律视为不可用（不抛）。
    """
    try:
        if not isinstance(meta, dict):
            return False
        size = meta.get("size")
        if not isinstance(size, (list, tuple)) or len(size) != 2:
            return False
        sw, sh = int(size[0]), int(size[1])
        if sw <= 0 or sh <= 0:
            return False
        eyes = meta.get("eyes")
        if not isinstance(eyes, dict):
            return False
        for name in ("left", "right"):
            item = eyes.get(name)
            if not isinstance(item, dict):
                return False
            geometry = [int(item[key]) for key in ("x", "y", "w", "h")]
            if geometry[2] <= 0 or geometry[3] <= 0:
                return False
        return True
    except (KeyError, TypeError, ValueError, IndexError):
        return False


class BuddySprites:
    def __init__(self, root: Path, frames_dir: Path | None = None):
        """加载一个皮肤目录下的全部素材。

        Args:
            root: 项目根目录（默认素材位于 ``root/assets/frames``）。
            frames_dir: 皮肤素材目录；为 ``None`` 时落到 ``root/assets/frames``，
                保证 ``BuddySprites(ROOT)`` 的默认行为与改造前完全一致。

        Raises:
            FileNotFoundError: 6 张眨眼帧中任意一张缺失或无法加载。
        """
        frame_dir = Path(frames_dir) if frames_dir is not None else Path(root) / "assets" / "frames"
        self.frames_dir: Path = frame_dir
        self.raw: list[QPixmap] = []
        for i in range(FRAME_COUNT):
            pm = QPixmap(str(frame_dir / f"buddy_{i}.png"))
            if pm.isNull():
                raise FileNotFoundError(f"缺少素材：{frame_dir / f'buddy_{i}.png'}")
            self.raw.append(pm)
        # 画布尺寸由帧 0 实测得出；跨尺寸皮肤必须按实例字段算宽高比（架构 D2）。
        self.canvas_w: int = self.raw[0].width() or BASE_W
        self.canvas_h: int = self.raw[0].height() or BASE_H
        base_path = frame_dir / "buddy_base.png"
        self.base_raw = QPixmap(str(base_path)) if base_path.exists() else None
        self.eye_raw: list[QPixmap] = []
        self.eye_meta: dict = {}
        meta_path = frame_dir / "eyes.json"
        if meta_path.exists():
            self.eye_meta = json.loads(meta_path.read_text(encoding="utf-8"))
            for name in ("eye_l.png", "eye_r.png"):
                pm = QPixmap(str(frame_dir / name))
                if not pm.isNull():
                    self.eye_raw.append(pm)
        self._base_cache: dict[int, QPixmap] = {}
        self._eye_cache: dict[tuple[int, int], QPixmap] = {}

        grip_path = frame_dir / "buddy_grip.png"
        self.grip_raw = QPixmap(str(grip_path)) if grip_path.exists() else None
        # 抓边立绘的眼睛分层（左右贴边时的视线跟随用）；缺任一素材则该姿态不跟随
        grip_base_path = frame_dir / "buddy_grip_base.png"
        self.grip_base_raw = QPixmap(str(grip_base_path)) if grip_base_path.exists() else None
        self.grip_eye_raw: list[QPixmap] = []
        self.grip_eye_meta: dict = {}
        grip_meta_path = frame_dir / "eyes_grip.json"
        if grip_meta_path.exists():
            try:
                self.grip_eye_meta = json.loads(grip_meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self.grip_eye_meta = {}
            for name in ("grip_eye_l.png", "grip_eye_r.png"):
                pm = QPixmap(str(frame_dir / name))
                if not pm.isNull():
                    self.grip_eye_raw.append(pm)
        self._grip_base_cache: dict[tuple[int, bool], QPixmap] = {}
        self._grip_eye_cache: dict[tuple[int, int], QPixmap] = {}
        self._cache: dict[tuple[int, int], QPixmap] = {}
        self._grip_cache: dict[tuple[int, bool], QPixmap] = {}
        self._mask_cache: dict[tuple[int, int], QRegion] = {}

    def width_for(self, height: int) -> int:
        return round(height * self.canvas_w / self.canvas_h)

    def pixmap(self, frame: int, height: int, dpr: float = 2.0) -> QPixmap:
        frame = max(0, min(FRAME_COUNT - 1, int(round(frame))))
        height = int(height)
        key = (frame, height)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        w = self.width_for(height)
        pm = self.raw[frame].scaled(
            max(1, round(w * dpr)),
            max(1, round(height * dpr)),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation,
        )
        pm.setDevicePixelRatio(dpr)
        self._cache[key] = pm
        return pm

    def _eye_meta_ok(self) -> bool:
        """眼位元数据是否完整可用（判据见模块级 :func:`_eye_meta_valid`）。

        改造前 ``has_gaze`` 只看 ``eye_meta`` 非空，字段残缺时会崩在
        ``eye_rect()`` 的 ``KeyError`` 上（架构坑位 P5），这里把判据前移。
        """
        return _eye_meta_valid(self.eye_meta)

    @property
    def has_gaze(self) -> bool:
        return (
            self.base_raw is not None
            and len(self.eye_raw) == 2
            and bool(self.eye_meta)
            and self._eye_meta_ok()
        )

    def clear_caches(self) -> None:
        """清空全部缩放缓存：帧 / 底板 / 眼睛 / 抓边 / 抓边底板 / 抓边眼睛 / 轮廓。

        换肤时由 ``SkinSwitcher`` 对新旧实例各调一次（主策略见架构 §3.3）。
        """
        self._cache.clear()
        self._base_cache.clear()
        self._eye_cache.clear()
        self._grip_cache.clear()
        self._grip_base_cache.clear()
        self._grip_eye_cache.clear()
        self._mask_cache.clear()

    def base(self, height: int, dpr: float = 2.0) -> QPixmap:
        """去掉眼睛的面罩底板。"""
        key = int(height)
        hit = self._base_cache.get(key)
        if hit is not None:
            return hit
        w = self.width_for(height)
        pm = self.base_raw.scaled(
            max(1, round(w * dpr)), max(1, round(height * dpr)), Qt.IgnoreAspectRatio, Qt.SmoothTransformation
        )
        pm.setDevicePixelRatio(dpr)
        self._base_cache[key] = pm
        return pm

    def eye(self, index: int, height: int, dpr: float = 2.0) -> QPixmap:
        key = (index, int(height))
        hit = self._eye_cache.get(key)
        if hit is not None:
            return hit
        sw, sh = self.eye_meta["size"]
        scale = height / sh
        src = self.eye_raw[index]
        pm = src.scaled(
            max(1, round(src.width() * scale * dpr)),
            max(1, round(src.height() * scale * dpr)),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation,
        )
        pm.setDevicePixelRatio(dpr)
        self._eye_cache[key] = pm
        return pm

    def eye_rect(self, index: int, height: int) -> tuple[float, float, float, float]:
        """眼睛相对立绘左上角的位置与尺寸（显示像素）。"""
        sw, sh = self.eye_meta["size"]
        name = "left" if index == 0 else "right"
        meta = self.eye_meta["eyes"][name]
        scale = height / sh
        return (meta["x"] * scale, meta["y"] * scale, meta["w"] * scale, meta["h"] * scale)

    @property
    def has_grip(self) -> bool:
        return self.grip_raw is not None and not self.grip_raw.isNull()

    def grip(self, height: int, mirrored: bool = False, dpr: float = 2.0) -> QPixmap:
        """抓边姿态：按显示高度缩放，右边缘用镜像版本。"""
        key = (int(height), mirrored)
        hit = self._grip_cache.get(key)
        if hit is not None:
            return hit
        src = self.grip_raw
        w = round(height * src.width() / src.height())
        pm = src.scaled(
            max(1, round(w * dpr)), max(1, round(height * dpr)), Qt.IgnoreAspectRatio, Qt.SmoothTransformation
        )
        pm.setDevicePixelRatio(dpr)
        if mirrored:
            pm = pm.transformed(QTransform().scale(-1, 1))
            pm.setDevicePixelRatio(dpr)
        self._grip_cache[key] = pm
        return pm

    def grip_width(self, height: int) -> int:
        """抓边姿态在给定高度下的显示宽度；素材缺失或尺寸为零时返回 0。"""
        grip_raw = self.grip_raw
        if grip_raw is None or grip_raw.isNull() or height == 0:
            return 0
        src_h = grip_raw.height()
        if src_h == 0:
            return 0
        return round(height * grip_raw.width() / src_h)

    # -------------------------------------------------------- 抓边姿态的视线
    @property
    def has_grip_gaze(self) -> bool:
        """抓边立绘是否具备可动眼睛（无眼底板 + 左右眼 + 眼位元数据齐备）。"""
        return (
            self.grip_base_raw is not None
            and not self.grip_base_raw.isNull()
            and len(self.grip_eye_raw) == 2
            and _eye_meta_valid(self.grip_eye_meta)
        )

    def grip_scale(self, height: int) -> float:
        """抓边立绘从原图到显示高度的缩放比；元数据缺失时为 0。"""
        size = self.grip_eye_meta.get("size") if self.grip_eye_meta else None
        if not size or len(size) != 2:
            return 0.0
        return height / size[1] if size[1] else 0.0

    def grip_base(self, height: int, mirrored: bool = False, dpr: float = 2.0) -> QPixmap:
        """抓边立绘的无眼底板，右边缘用镜像版本。"""
        key = (int(height), mirrored)
        hit = self._grip_base_cache.get(key)
        if hit is not None:
            return hit
        src = self.grip_base_raw
        w = round(height * src.width() / src.height())
        pm = src.scaled(
            max(1, round(w * dpr)), max(1, round(height * dpr)), Qt.IgnoreAspectRatio, Qt.SmoothTransformation
        )
        pm.setDevicePixelRatio(dpr)
        if mirrored:
            pm = pm.transformed(QTransform().scale(-1, 1))
            pm.setDevicePixelRatio(dpr)
        self._grip_base_cache[key] = pm
        return pm

    def grip_eye(self, index: int, height: int, dpr: float = 2.0) -> QPixmap:
        """抓边立绘的单只眼睛。眼睛是左右对称的胶囊，无需随姿态镜像。"""
        key = (index, int(height))
        hit = self._grip_eye_cache.get(key)
        if hit is not None:
            return hit
        scale = self.grip_scale(height)
        src = self.grip_eye_raw[index]
        pm = src.scaled(
            max(1, round(src.width() * scale * dpr)),
            max(1, round(src.height() * scale * dpr)),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation,
        )
        pm.setDevicePixelRatio(dpr)
        self._grip_eye_cache[key] = pm
        return pm

    def grip_eye_rect(self, index: int, height: int) -> tuple[float, float, float, float]:
        """眼睛相对抓边立绘左上角的位置与尺寸（显示像素，**未镜像**坐标系）。"""
        name = "left" if index == 0 else "right"
        meta = self.grip_eye_meta["eyes"][name]
        scale = self.grip_scale(height)
        return (meta["x"] * scale, meta["y"] * scale, meta["w"] * scale, meta["h"] * scale)

    def mask(self, height: int) -> QRegion:
        """宠物轮廓区域（不含阴影），用于点击命中。"""
        key = (0, height)
        hit = self._mask_cache.get(key)
        if hit is not None:
            return hit
        pm = self.pixmap(0, height, dpr=1.0)
        bmp: QBitmap = pm.mask()
        region = QRegion(bmp)
        self._mask_cache[key] = region
        return region

    def foot_shadow_rect(self, height: int) -> QRect:
        w = self.width_for(height)
        sh_w = int(w * 0.62)
        sh_h = max(8, int(sh_w * 0.17))
        return QRect(-sh_w // 2, -sh_h // 2, sh_w, sh_h)
