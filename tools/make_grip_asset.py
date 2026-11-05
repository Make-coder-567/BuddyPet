"""把生成的抓边姿态处理成可用素材：去背景、去竖线、裁边。

用法：python tools/make_grip_asset.py <生成图> [输出名]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent


def background_mask(rgb: np.ndarray) -> np.ndarray:
    """高亮低饱和 = 棋盘格背景。"""
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    return (mx > 200) & ((mx - mn) < 32)


def character_mask(rgb: np.ndarray) -> np.ndarray:
    """最大连通域 + 填洞：把白底/棋盘格剔掉，同时保住角色内部的白色。"""
    mx = rgb.max(axis=2)
    mn = rgb.min(axis=2)
    bg = (mx > 200) & ((mx - mn) < 32)
    char = ~bg
    lab, n = ndimage.label(char, structure=np.ones((3, 3)))
    if n == 0:
        return char
    sizes = ndimage.sum(char, lab, range(1, n + 1))
    main = lab == (int(np.argmax(sizes)) + 1)
    return ndimage.binary_fill_holes(main)


def remove_guide_line(rgb: np.ndarray, alpha: np.ndarray) -> int:
    """清掉模型自己画的那条垂直辅助线：只看左侧窄带里的细暗列。"""
    h, w = alpha.shape
    dark = (rgb.max(axis=2) < 80) & (alpha > 0)
    band = dark[:80, :240]
    cols = np.nonzero(band.any(axis=0))[0]
    if len(cols) == 0:
        return 0
    x0, x1 = int(cols.min()), int(cols.max())
    x_lo, x_hi = max(0, x0 - 2), min(w, x1 + 3)
    removed = 0
    for y in range(h):
        for x in range(x_lo, x_hi):
            if not dark[y, x]:
                continue
            # 右侧 12px 是透明背景 → 说明是那条孤立的线，而不是手
            if x + 12 < w and alpha[y, x + 12] == 0:
                alpha[y, x] = 0
                removed += 1
    return removed


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if src is None or not src.exists():
        raise SystemExit("需要传入生成图路径")
    name = sys.argv[2] if len(sys.argv) > 2 else "buddy_grip.png"

    im = Image.open(src).convert("RGB")
    rgb = np.array(im).astype(int)
    alpha = np.where(character_mask(rgb), 255, 0).astype(np.uint8)

    removed = remove_guide_line(rgb, alpha)

    # 轻微羽化，避免锯齿
    a_img = Image.fromarray(alpha, "L").filter(ImageFilter.GaussianBlur(0.6))
    out = im.convert("RGBA")
    out.putalpha(a_img)

    bbox = out.getbbox()
    out = out.crop(bbox)
    target = ROOT / "assets" / "frames" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    out.save(target)
    print(f"saved {target} size={out.size} line_rows={removed} bbox={bbox}")


if __name__ == "__main__":
    main()
