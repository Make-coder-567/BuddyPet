"""效果总览：逐个触发特效，在指定时刻抓一帧，拼成对照图。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image, ImageDraw
from PyQt5.QtNetwork import QLocalSocket
from PyQt5.QtWidgets import QApplication

from shoot import find_pet_rect, send

OUT = Path(__file__).resolve().parent.parent / "assets" / "shots"
SHOTS = [
    ("hop", 0.34),
    ("hearts", 1.30),
    ("sparkle", 0.85),
    ("confetti", 0.30),
    ("dance", 1.00),
    ("coffee", 1.40),
    ("sleep", 2.20),
    ("spin", 0.62),
]


def grab(screen, rect, name: str) -> Image.Image:
    pm = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
    path = OUT / f"g_{name}.png"
    pm.save(str(path))
    return Image.open(path)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    screen = app.primaryScreen()
    rect = find_pet_rect().adjusted(-170, -230, 170, 70)
    rect = rect.intersected(screen.geometry())

    tiles = []
    for eid, at in SHOTS:
        send(f"effect:{eid}")
        time.sleep(at)
        tiles.append((eid, grab(screen, rect, eid)))
        time.sleep(3.2)

    tw, th = tiles[0][1].size
    scale = 0.62
    tw, th = int(tw * scale), int(th * scale)
    cols = 4
    rows = (len(tiles) + cols - 1) // cols
    sheet = Image.new("RGB", (tw * cols, th * rows), (26, 28, 32))
    dr = ImageDraw.Draw(sheet)
    for i, (eid, im) in enumerate(tiles):
        x, y = (i % cols) * tw, (i // cols) * th
        sheet.paste(im.resize((tw, th), Image.LANCZOS), (x, y))
        dr.rectangle([x, y, x + tw - 1, y + th - 1], outline=(60, 64, 72))
        dr.text((x + 8, y + 6), eid, fill=(255, 190, 150))
    sheet.save(OUT.parent / "ref" / "effects_sheet.png")
    print("sheet ->", OUT.parent / "ref" / "effects_sheet.png", sheet.size)


if __name__ == "__main__":
    main()
