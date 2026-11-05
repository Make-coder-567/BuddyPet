"""校验配色变体皮肤包相对源素材的完整性。

断言清单
--------
1. 10 个贴图 + eyes.json + frames.json + manifest.json 齐备
2. 6 帧与 buddy_base.png 画布尺寸一致（buddy_grip 允许不同尺寸）
3. alpha 通道与源图**逐位相同**（只许改色，不许改轮廓）
4. 源图中性像素（饱和度 < 0.06）在变体里 RGB 三通道**逐位相同**（描线/高光/白装不许染色）
5. 高饱和像素的色相确实发生了位移（证明染色生效，不是空跑）
6. eyes.json 与源文件 json 语义相同（几何未变）

用法：``python tools/verify_variant_skins.py <变体根目录>``
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "frames"

TINT_FILES = (
    "buddy_0.png",
    "buddy_1.png",
    "buddy_2.png",
    "buddy_3.png",
    "buddy_4.png",
    "buddy_5.png",
    "buddy_base.png",
    "eye_l.png",
    "eye_r.png",
    "buddy_grip.png",
)
REQUIRED = TINT_FILES + ("eyes.json", "frames.json", "manifest.json")

#: 参与「画布尺寸必须一致」校验的文件（眼睛小图与 grip 高清图天然尺寸不同）
CANVAS_FILES = (
    "buddy_0.png",
    "buddy_1.png",
    "buddy_2.png",
    "buddy_3.png",
    "buddy_4.png",
    "buddy_5.png",
    "buddy_base.png",
)

NEUTRAL_SAT_MAX = 0.06
SATURATED_SAT_MIN = 0.20


def _load(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    with Image.open(path) as raw:
        im = raw.convert("RGBA")
    rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)
    alpha = np.asarray(im.getchannel("A"), dtype=np.uint8)
    hsv = np.asarray(im.convert("HSV"), dtype=np.uint8)
    return rgb, alpha, hsv


def _median_hue(hsv: np.ndarray, alpha: np.ndarray, lo: float, hi: float) -> float | None:
    hue = hsv[..., 0].astype(np.float32) * (360.0 / 255.0)
    sat = hsv[..., 1].astype(np.float32) / 255.0
    mask = (alpha > 200) & (sat > SATURATED_SAT_MIN) & (hue >= lo) & (hue <= hi)
    if not mask.any():
        return None
    return float(np.median(hue[mask]))


def verify_variant(dest: Path) -> list[str]:
    problems: list[str] = []
    for name in REQUIRED:
        if not (dest / name).is_file():
            problems.append(f"缺文件 {name}")
    if problems:
        return problems

    sizes: dict[str, tuple[int, int]] = {}
    hue_probe: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
    hue_probe_out: tuple[np.ndarray, np.ndarray, np.ndarray] | None = None
    for name in TINT_FILES:
        source_rgb, source_alpha, source_hsv = _load(SRC / name)
        out_rgb, out_alpha, out_hsv = _load(dest / name)
        sizes[name] = (out_rgb.shape[1], out_rgb.shape[0])
        if name == "buddy_0.png":
            hue_probe = (source_rgb, source_alpha, source_hsv)
            hue_probe_out = (out_rgb, out_alpha, out_hsv)

        if out_rgb.shape != source_rgb.shape:
            problems.append(f"{name} 尺寸与源图不同 {out_rgb.shape} != {source_rgb.shape}")
            continue
        if not np.array_equal(source_alpha, out_alpha):
            diff = int(np.count_nonzero(source_alpha != out_alpha))
            problems.append(f"{name} alpha 被改动，{diff} 个像素不一致")
        sat = source_hsv[..., 1].astype(np.float32) / 255.0
        neutral = (sat < NEUTRAL_SAT_MAX) & (source_alpha > 0)
        changed = np.any(source_rgb[neutral] != out_rgb[neutral], axis=-1)
        if changed.any():
            problems.append(f"{name} 中性像素被染色，{int(changed.sum())} 个像素漂移")
        # 逐像素改动幅度：不允许出现「整体过曝/过暗」这类破坏明暗的变换
        moved = np.abs(out_rgb.astype(np.int16) - source_rgb.astype(np.int16)).max(axis=-1)
        if float(moved.mean()) > 90.0:
            problems.append(f"{name} 改动幅度异常，均值 {moved.mean():.1f}")

    # 画布一致性只针对「6 帧 + buddy_base」；眼睛小图与 grip 高清图天然不同尺寸
    canvas = {name: sizes[name] for name in CANVAS_FILES}
    if len(set(canvas.values())) != 1:
        problems.append(f"6 帧 + base 画布尺寸不一致：{canvas}")

    with (SRC / "eyes.json").open(encoding="utf-8") as fp:
        src_eyes = json.load(fp)
    with (dest / "eyes.json").open(encoding="utf-8") as fp:
        out_eyes = json.load(fp)
    if src_eyes != out_eyes:
        problems.append("eyes.json 几何信息被改动")

    if hue_probe is None or hue_probe_out is None:
        problems.append("缺少 buddy_0.png，无法验证染色")
    else:
        _, src_alpha0, src_hsv0 = hue_probe
        _, out_alpha0, out_hsv0 = hue_probe_out
        before = _median_hue(src_hsv0, src_alpha0, 0.0, 360.0)
        after = _median_hue(out_hsv0, out_alpha0, 0.0, 360.0)
        if before is None or after is None:
            problems.append("取不到高饱和像素，无法验证染色")
        elif abs((after - before + 180.0) % 360.0 - 180.0) < 8.0:
            problems.append(f"色相几乎没动（{before:.0f}° -> {after:.0f}°），染色未生效")
        else:
            problems.append(
                f"__INFO__ 主色相 {before:.0f}° -> {after:.0f}°，画布 {canvas['buddy_0.png']}"
            )
    return problems


def main(argv: list[str]) -> int:
    target = Path(argv[1]).expanduser() if len(argv) > 1 else Path("")
    if not target.is_dir():
        print(f"用法：python {Path(__file__).name} <变体根目录>", file=sys.stderr)
        return 2
    dirs = sorted(p for p in target.iterdir() if p.is_dir() and (p / "buddy_0.png").is_file())
    if not dirs:
        print(f"{target} 下没有找到变体目录", file=sys.stderr)
        return 2
    failed = 0
    for dest in dirs:
        issues = verify_variant(dest)
        infos = [i.replace("__INFO__ ", "") for i in issues if i.startswith("__INFO__")]
        errors = [i for i in issues if not i.startswith("__INFO__")]
        name = dest.name
        if errors:
            failed += 1
            print(f"✗ {name}")
            for item in errors:
                print(f"    - {item}")
        else:
            print(f"✓ {name}  {' | '.join(infos)}")
    print(f"\n{len(dirs) - failed}/{len(dirs)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
