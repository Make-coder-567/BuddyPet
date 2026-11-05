"""从官方 Buddy 形象生成眨眼序列帧。

思路：
1. 用左右相邻像素对眼睛区域做水平插值修补，恢复"无眼"的面罩底板；
2. 把原始眼睛按不同纵向比例压扁，居中贴回原位，形成 开-半闭-闭 的过渡。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "ref" / "silhouette.png"
OUT = ROOT / "assets" / "frames"

EYES = [(81, 110, 94, 148), (163, 110, 177, 148)]
PAD = 5
RATIOS = [1.0, 0.62, 0.28, 0.12, 0.28, 0.62]


def inpaint_region(arr: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = box
    X0, Y0, X1, Y1 = x0 - PAD, y0 - PAD, x1 + PAD, y1 + PAD
    X0, Y0 = max(0, X0), max(0, Y0)
    X1, Y1 = min(arr.shape[1] - 1, X1), min(arr.shape[0] - 1, Y1)
    for y in range(Y0, Y1 + 1):
        left = arr[y, max(0, X0 - 5):X0].mean(axis=0)
        right = arr[y, X1 + 1:min(arr.shape[1], X1 + 6)].mean(axis=0)
        span = X1 - X0 + 1
        t = (np.arange(span) + 0.5) / span
        arr[y, X0:X1 + 1] = left[None, :] * (1 - t)[:, None] + right[None, :] * t[:, None]
    return arr


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base = Image.open(SRC).convert("RGBA")
    arr = np.array(base).astype(np.float32)

    clean = arr.copy()
    for box in EYES:
        clean = inpaint_region(clean, box)
    clean_img = Image.fromarray(np.clip(clean, 0, 255).astype(np.uint8), "RGBA")

    meta = {"size": list(base.size), "eyes": EYES, "frames": len(RATIOS)}

    # 眼睛软遮罩：只用"青色像素 + 抗锯齿边缘"参与合成，避免把面罩底色整块搬动而留下方框。
    base_rgb = np.array(base).astype(np.int16)
    g, r = base_rgb[:, :, 1], base_rgb[:, :, 0]
    soft = np.clip((g - 45) / 60.0, 0, 1) * (g > r + 12)
    alpha_mask = Image.fromarray((soft * 255).astype(np.uint8), "L").filter(
        ImageFilter.GaussianBlur(1.1)
    )

    for idx, ratio in enumerate(RATIOS):
        frame = base.copy() if ratio >= 0.999 else clean_img.copy()
        if ratio < 0.999:
            for (x0, y0, x1, y1) in EYES:
                X0, Y0 = max(0, x0 - PAD), max(0, y0 - PAD)
                X1, Y1 = min(base.width - 1, x1 + PAD), min(base.height - 1, y1 + PAD)
                patch = base.crop((X0, Y0, X1 + 1, Y1 + 1))
                pmask = alpha_mask.crop((X0, Y0, X1 + 1, Y1 + 1))
                h = max(3, round(patch.height * ratio))
                squashed = patch.resize((patch.width, h), Image.LANCZOS)
                smask = pmask.resize((patch.width, h), Image.LANCZOS)
                frame.paste(squashed, (X0, Y0 + (patch.height - h) // 2), smask)
        frame.save(OUT / f"buddy_{idx}.png")
        print("frame", idx, "ratio", ratio, frame.size)

    (OUT / "frames.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    # 3 倍放大用于人工检查
    contact = Image.new("RGBA", (base.width * len(RATIOS) * 2, base.height * 2), (24, 26, 30, 255))
    for i in range(len(RATIOS)):
        im = Image.open(OUT / f"buddy_{i}.png").resize((base.width * 2, base.height * 2), Image.LANCZOS)
        contact.alpha_composite(im, (i * base.width * 2, 0))
    contact.save(ROOT / "assets" / "ref" / "blink_contact.png")


if __name__ == "__main__":
    main()
