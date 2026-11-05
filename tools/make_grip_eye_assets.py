"""给「抓边立绘」拆出可动眼睛，让左右贴边时也能视线跟随。

背景
----
``buddy_grip.png`` 是 AI 生成的专用抓边立绘（925×1524），只有一整张图，
没有「无眼底板 + 左右眼」的分层，所以 ``_paint_pet`` 走 grip 分支时无法移动眼睛。
本工具补齐这三件素材，四套配色皮肤**共用同一组几何**（变体只改色、不改形）。

产出（写进每个皮肤目录）
------------------------
* ``buddy_grip_base.png``  去掉眼睛的面罩底板（水平渐变修补）
* ``grip_eye_l.png`` / ``grip_eye_r.png``  带软边 alpha 的眼睛
* ``eyes_grip.json``       眼睛在 grip 立绘坐标系里的位置与尺寸

软遮罩为什么不沿用青色检测
--------------------------
原 ``make_eye_assets.py`` 用 ``g > r + 12`` 找青色眼睛，但霜蓝皮肤的眼睛是橙色、
紫罗兰是黄色，固定色相判据会直接失效。这里改用**相对面罩底色的亮度抬升**：
面罩近黑、眼睛发亮，与具体色相无关，四套皮肤通用。

用法：``python tools/make_grip_eye_assets.py``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
PAD = 10                      # 眼睛裁剪外扩，给软边留余量
ROI_X = (0.15, 0.85)          # 面罩横向搜索范围（占图宽比例）
ROI_Y = (0.24, 0.44)          # 眼睛纵向搜索范围（占图高比例）
MIN_AREA_RATIO = 0.0006       # 连通块最小面积（占全图比例），滤掉高光噪点


def _luminance(arr: np.ndarray) -> np.ndarray:
    return arr[..., 0] * 0.299 + arr[..., 1] * 0.587 + arr[..., 2] * 0.114


def detect_eye_boxes(path: Path) -> dict[str, tuple[int, int, int, int]]:
    """在抓边立绘的面罩区域里自动定位两只发光眼睛。

    Returns:
        ``{"left": (x0,y0,x1,y1), "right": (...)}``，坐标为原图像素、含端点。
    """
    with Image.open(path) as raw:
        im = raw.convert("RGBA")
    arr = np.asarray(im, dtype=np.float32)
    height, width = arr.shape[:2]
    lum = _luminance(arr)
    alpha = arr[..., 3]

    x0, x1 = int(width * ROI_X[0]), int(width * ROI_X[1])
    y0, y1 = int(height * ROI_Y[0]), int(height * ROI_Y[1])
    roi_lum = lum[y0:y1, x0:x1]
    roi_alpha = alpha[y0:y1, x0:x1]

    # 面罩近黑、眼睛发亮：以 ROI 亮度中位数为底，向上抬 45 作为发光阈值。
    # 阈值另设 110 下限：面罩两侧有白色高光描边，亮度可达 120，阈值太低会把描边吃进来。
    floor = float(np.median(roi_lum[roi_alpha > 200]))
    threshold = max(floor + 45.0, 110.0)
    mask = (roi_lum > threshold) & (roi_alpha > 200)
    labels, count = ndimage.label(mask)
    if count == 0:
        raise RuntimeError(f"{path.name} 的面罩区域里没找到发光眼睛（阈值 {threshold:.1f}）")

    min_area = width * height * MIN_AREA_RATIO
    blobs: list[tuple[int, int, int, int, int]] = []
    for index in range(1, count + 1):
        ys, xs = np.nonzero(labels == index)
        area = int(ys.size)
        if area < min_area:
            continue
        bx0, by0 = int(xs.min()) + x0, int(ys.min()) + y0
        bx1, by1 = int(xs.max()) + x0, int(ys.max()) + y0
        # 关键筛选：眼睛是面罩**内部**的独立胶囊，不接触搜索区边界。
        # 面罩的白色描边与顶部高光带必然贴到 ROI 边上，靠这一条就能整批排除。
        if bx0 <= x0 or by0 <= y0 or bx1 >= x1 - 1 or by1 >= y1 - 1:
            continue
        blobs.append((bx0, by0, bx1, by1, area))
    if len(blobs) < 2:
        raise RuntimeError(f"{path.name} 只找到 {len(blobs)} 个发光块，无法确定左右眼")
    blobs.sort(key=lambda b: -b[4])
    left, right = sorted(blobs[:2], key=lambda b: b[0])
    print(f"  面罩亮度底 {floor:.1f}，阈值 {threshold:.1f}，候选块 {len(blobs)} 个")
    print(f"  左眼 box={left[:4]} 面积={left[4]}  右眼 box={right[:4]} 面积={right[4]}")
    return {"left": left[:4], "right": right[:4]}


def inpaint(arr: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    """把眼睛区域用左右邻域的面罩色做水平渐变填补（沿用原拆眼工具的补法）。"""
    x0, y0, x1, y1 = box
    X0, Y0 = max(0, x0 - PAD), max(0, y0 - PAD)
    X1 = min(arr.shape[1] - 1, x1 + PAD)
    Y1 = min(arr.shape[0] - 1, y1 + PAD)
    for y in range(Y0, Y1 + 1):
        left = arr[y, max(0, X0 - 6):X0, :3].mean(axis=0)
        right = arr[y, X1 + 1:min(arr.shape[1], X1 + 7), :3].mean(axis=0)
        span = X1 - X0 + 1
        t = (np.arange(span) + 0.5) / span
        arr[y, X0:X1 + 1, :3] = left[None, :] * (1 - t)[:, None] + right[None, :] * t[:, None]
    return arr


def build_skin(directory: Path, boxes: dict[str, tuple[int, int, int, int]]) -> bool:
    source = directory / "buddy_grip.png"
    if not source.is_file():
        return False
    with Image.open(source) as raw:
        im = raw.convert("RGBA")
    arr = np.asarray(im, dtype=np.float32)
    clean = arr.copy()
    for box in boxes.values():
        clean = inpaint(clean, box)
    Image.fromarray(np.clip(clean, 0, 255).astype(np.uint8), "RGBA").save(
        directory / "buddy_grip_base.png", optimize=True
    )

    lum = _luminance(arr)
    meta = {"size": [im.width, im.height], "eyes": {}}
    for name, box in boxes.items():
        x0, y0, x1, y1 = box
        X0, Y0 = max(0, x0 - PAD), max(0, y0 - PAD)
        X1, Y1 = min(im.width - 1, x1 + PAD), min(im.height - 1, y1 + PAD)
        crop_box = (X0, Y0, X1 + 1, Y1 + 1)
        region = lum[Y0:Y1 + 1, X0:X1 + 1]
        # 用该区域自身的亮度分布定软边：10 分位当面罩底，92 分位当眼睛亮度
        lo = float(np.percentile(region, 10))
        hi = float(np.percentile(region, 92))
        soft = np.clip((region - lo) / max(1.0, hi - lo), 0.0, 1.0)
        mask = Image.fromarray((soft * 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(0.8))
        eye = im.crop(crop_box)
        eye.putalpha(mask)
        eye.save(directory / f"grip_eye_{'l' if name == 'left' else 'r'}.png", optimize=True)
        meta["eyes"][name] = {"x": X0, "y": Y0, "w": eye.width, "h": eye.height}
    (directory / "eyes_grip.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"  ✓ {directory.relative_to(ROOT)}  base + 2 眼 + eyes_grip.json")
    return True


def main() -> int:
    reference = ROOT / "assets" / "frames" / "buddy_grip.png"
    if not reference.is_file():
        print(f"缺少参考抓边立绘 {reference}", file=sys.stderr)
        return 2
    print("① 在默认皮肤上检测眼位（四套皮肤共用这组几何）")
    boxes = detect_eye_boxes(reference)

    targets = [ROOT / "assets" / "frames"]
    skins = ROOT / "assets" / "skins"
    if skins.is_dir():
        targets.extend(sorted(p for p in skins.rglob("*") if p.is_dir() and (p / "buddy_grip.png").is_file()))
    print(f"\n② 为 {len(targets)} 个皮肤目录生成抓边眼睛素材")
    done = sum(1 for d in targets if build_skin(d, boxes))
    print(f"\n完成 {done}/{len(targets)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
