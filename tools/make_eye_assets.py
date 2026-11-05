"""把立绘拆成「无眼底板 + 左右眼」两套素材，供视线跟随使用。

产出：
    assets/frames/buddy_base.png   去掉眼睛的面罩底板
    assets/frames/eye_l.png        左眼（带软边 alpha）
    assets/frames/eye_r.png        右眼
    assets/frames/eyes.json        眼睛在立绘坐标系里的位置
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "ref" / "silhouette.png"
OUT = ROOT / "assets" / "frames"

EYES = {"left": (81, 110, 94, 148), "right": (163, 110, 177, 148)}
PAD = 8


def inpaint_eye(arr: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x0, y0, x1, y1 = box
    X0, Y0 = max(0, x0 - PAD), max(0, y0 - PAD)
    X1, Y1 = min(arr.shape[1] - 1, x1 + PAD), min(arr.shape[0] - 1, y1 + PAD)
    for y in range(Y0, Y1 + 1):
        left = arr[y, max(0, X0 - 5):X0].mean(axis=0)
        right = arr[y, X1 + 1:min(arr.shape[1], X1 + 6)].mean(axis=0)
        span = X1 - X0 + 1
        t = (np.arange(span) + 0.5) / span
        arr[y, X0:X1 + 1] = left[None, :] * (1 - t)[:, None] + right[None, :] * t[:, None]
    return arr


def main() -> None:
    base = Image.open(SRC).convert("RGBA")
    arr = np.array(base).astype(np.float32)

    clean = arr.copy()
    for box in EYES.values():
        clean = inpaint_eye(clean, box)
    Image.fromarray(np.clip(clean, 0, 255).astype(np.uint8), "RGBA").save(OUT / "buddy_base.png")

    # 眼睛软遮罩：青色像素 + 抗锯齿边缘
    rgb = np.array(base).astype(np.int16)
    g, r = rgb[:, :, 1], rgb[:, :, 0]
    soft = np.clip((g - 45) / 60.0, 0, 1) * (g > r + 12)
    mask = Image.fromarray((soft * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(1.0))

    meta = {"size": [base.width, base.height], "eyes": {}}
    for name, (x0, y0, x1, y1) in EYES.items():
        X0, Y0 = max(0, x0 - PAD), max(0, y0 - PAD)
        X1, Y1 = min(base.width - 1, x1 + PAD), min(base.height - 1, y1 + PAD)
        eye = base.crop((X0, Y0, X1 + 1, Y1 + 1))
        eye.putalpha(mask.crop((X0, Y0, X1 + 1, Y1 + 1)))
        eye.save(OUT / f"eye_{'l' if name == 'left' else 'r'}.png")
        meta["eyes"][name] = {"x": X0, "y": Y0, "w": eye.width, "h": eye.height}
        print(name, meta["eyes"][name])

    (OUT / "eyes.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("saved base + eyes")


if __name__ == "__main__":
    main()
