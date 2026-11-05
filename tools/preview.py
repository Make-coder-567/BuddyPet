"""离屏效果预览：不依赖真实桌面，逐帧驱动动画并合成对照图。

用法：
    python tools/preview.py                 # 全部特效 × 1 张关键帧
    python tools/preview.py box 6           # 只做盲盒，取 6 个时间点
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtCore import QPointF, QRect, Qt  # noqa: E402
from PyQt5.QtGui import QColor, QImage, QLinearGradient, QPainter, QPixmap, QRadialGradient  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app.assets import BuddySprites  # noqa: E402
from app.config import Settings  # noqa: E402
from app.effects import EFFECTS, EffectEngine  # noqa: E402
from app.overlay import EffectOverlay  # noqa: E402
from app.pet_window import PetWindow  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "preview"

KEY_SHOTS = [
    ("box", 1.60),
    ("hearts", 1.20),
    ("hop", 0.36),
    ("sparkle", 0.90),
    ("confetti", 0.32),
    ("dance", 0.95),
    ("blink", 0.19),
    ("coffee", 1.10),
    ("sleep", 2.30),
    ("spin", 0.60),
]


class FakeClock:
    def __init__(self, step_ms: float = 16.0):
        self.step = step_ms

    def restart(self) -> int:
        return int(self.step)


def build_pet(size: int = 260) -> tuple[PetWindow, EffectOverlay, EffectEngine]:
    cfg = Settings(size=size, seen_intro=True)
    sprites = BuddySprites(ROOT)
    engine = EffectEngine()
    engine.rng.seed(20261104)
    overlay = EffectOverlay(engine)
    pet = PetWindow(cfg, sprites, engine, overlay, ROOT)
    pet._rng.seed(7)
    pet._blink_in = 999.0
    pet._elapsed = FakeClock()
    pet.move(0, 0)

    def _sync_only() -> None:
        feet = QPointF(pet.x() + pet._feet.x(), pet.y() + pet._feet.y())
        overlay.sync(pet.geometry(), feet, pet._sprite_h)
        overlay.update()

    pet._sync_overlay = _sync_only
    pet._sync_overlay()
    for w in (pet, overlay):
        w.setAttribute(Qt.WA_DontShowOnScreen, True)
        w.show()
    pet._sync_overlay()
    return pet, overlay, engine


def compose(pet: PetWindow, overlay: EffectOverlay) -> QImage:
    pet_rect = pet.geometry()
    ov_rect = overlay.geometry()
    union: QRect = pet_rect.united(ov_rect)
    img = QImage(union.size(), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)

    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    grad = QLinearGradient(0, 0, union.width(), union.height())
    grad.setColorAt(0.0, QColor("#1E2027"))
    grad.setColorAt(0.55, QColor("#252830"))
    grad.setColorAt(1.0, QColor("#191B20"))
    p.fillRect(QRect(0, 0, union.width(), union.height()), grad)
    glow = QRadialGradient(QPointF(union.width() * 0.5, union.height() * 0.2), union.width() * 0.9)
    glow.setColorAt(0, QColor(90, 96, 112, 60))
    glow.setColorAt(1, QColor(20, 22, 26, 0))
    p.fillRect(QRect(0, 0, union.width(), union.height()), glow)
    p.end()

    ov_img = overlay.grab().toImage()
    pet_img = pet.grab().toImage()
    p = QPainter(img)
    # 模拟真实屏幕：窗口可能探出屏幕外，超出的部分应被"屏幕边界"裁掉
    screen = QApplication.primaryScreen().geometry()
    p.setClipRect(QRect(0, 0, screen.width(), screen.height()).translated(-union.topLeft()))
    p.drawImage(pet_rect.topLeft() - union.topLeft(), pet_img)
    p.drawImage(ov_rect.topLeft() - union.topLeft(), ov_img)
    p.setClipping(False)
    p.end()
    return img


def qimage_to_pil(img: QImage) -> Image.Image:
    img = img.convertToFormat(QImage.Format_RGBA8888)
    w, h = img.width(), img.height()
    ptr = img.bits()
    ptr.setsize(img.byteCount())
    return Image.frombytes("RGBA", (w, h), bytes(ptr)).transpose(Image.FLIP_TOP_BOTTOM) if False else Image.frombytes(
        "RGBA", (w, h), bytes(ptr)
    )


def font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("msyh.ttc", "msyhbd.ttc", "simhei.ttf"):
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def shoot_one(pet, overlay, engine, effect: str | None, at: float) -> Image.Image:
    engine.stop_all()
    if effect:
        engine.start(effect)
    steps = max(1, int(at / 0.016))
    for _ in range(steps):
        pet._tick()
    return qimage_to_pil(compose(pet, overlay))


def main() -> None:
    app = QApplication([])
    OUT.mkdir(parents=True, exist_ok=True)

    if len(sys.argv) > 2 and sys.argv[1] == "edge":
        side = sys.argv[2]
        pet, overlay, engine = build_pet(260)
        pet.move(-pet._sprite_offset(), 0)
        pet._edge = side
        pet._snap_edge_position(silent=True)
        pet._regrip_at = 999.0
        pet._t = 0.3
        shots = []
        for i in range(4):
            for _ in range(22):
                pet._tick()
            shots.append(qimage_to_pil(compose(pet, overlay)))
        tw, th = shots[0].size
        stamp = Image.new("RGB", (tw * len(shots), th), (20, 22, 26))
        for i, im in enumerate(shots):
            stamp.paste(im.convert("RGB"), (i * tw, 0))
        stamp.save(OUT / f"edge_{side}.png")
        print("saved", OUT / f"edge_{side}.png", stamp.size)
        return

    if len(sys.argv) > 2:
        effect = sys.argv[1]
        count = int(sys.argv[2])
        pet, overlay, engine = build_pet(260)
        total = EFFECTS[effect].duration
        for i in range(count):
            at = total * (i + 0.5) / count
            img = shoot_one(pet, overlay, engine, effect, at)
            img.save(OUT / f"{effect}_{i}.png")
            print("saved", effect, round(at, 2))
        return

    pet, overlay, engine = build_pet(240)
    tiles: list[tuple[str, Image.Image]] = []
    for eid, at in KEY_SHOTS:
        tiles.append((eid, shoot_one(pet, overlay, engine, eid, at)))

    tw, th = tiles[0][1].size
    cols = 5
    rows = (len(tiles) + cols - 1) // cols
    pad = 10
    label_h = 26
    sheet = Image.new("RGBA", (cols * tw + pad, rows * (th + label_h) + pad), (18, 20, 24, 255))
    dr = ImageDraw.Draw(sheet)
    f = font(15)
    for i, (eid, im) in enumerate(tiles):
        x = pad // 2 + (i % cols) * tw
        y = pad // 2 + (i // cols) * (th + label_h)
        sheet.alpha_composite(im, (x, y + label_h))
        dr.text((x + 6, y + 4), f"{EFFECTS[eid].name}  ·  {eid}", font=f, fill=(255, 170, 120, 255))
    sheet.convert("RGB").save(OUT / "effects_sheet.png")
    print("sheet ->", OUT / "effects_sheet.png", sheet.size)


if __name__ == "__main__":
    main()
