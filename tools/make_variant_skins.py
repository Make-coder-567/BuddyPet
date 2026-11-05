"""生成内置「配色变体」皮肤包（离线兜底用的图鉴内容源）。

设计取舍
--------
官方立绘只有一只 Buddy，无法凭空造出新角色立绘而不破坏画风一致性。
因此本工具做**色相分带重映射**：只改动高饱和像素，白/黑/灰中性像素逐像素保持原值，
于是描线、高光、明暗结构完全不变，观感是「同一只 Buddy 的另一套配色」，
在数据模型上正确表达为「同一个形象的多套皮肤」（1 Buddy : N Skins）。

分带（基于实测色相分布）
------------------------
* outfit：背包/上衣/鞋 的橙色带，中心 22°，覆盖 350°~56°
* accent：护目镜发光眼与耳机细节的青色带，中心 205°，覆盖 132°~278°

用法
----
    python tools/make_variant_skins.py --out <目标目录>

不传 ``--out`` 时输出到 ``%TEMP%/buddy_variants``，便于先审图再落库。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "frames"

#: 需要参与变色的文件（相对 SRC）
TINT_FILES: tuple[str, ...] = (
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

#: 原样复制的元数据文件
COPY_FILES: tuple[str, ...] = ("eyes.json", "frames.json")

#: 变体定义：(皮肤 id, 显示名, 上衣目标色相, 眼睛目标色相)
#: 眼睛刻意取与上衣接近互补的色相，保住原画「暖衣 + 冷光眼」的对比张力。
VARIANTS: tuple[tuple[str, str, float, float], ...] = (
    ("sakura", "樱粉", 340.0, 190.0),
    ("frost", "霜蓝", 205.0, 45.0),
    ("violet", "紫罗兰", 275.0, 55.0),
)

OUTFIT_CENTER, OUTFIT_HALF, OUTFIT_FEATHER = 22.0, 34.0, 14.0
ACCENT_CENTER, ACCENT_HALF, ACCENT_FEATHER = 205.0, 58.0, 16.0

SAT_LO, SAT_HI = 0.06, 0.16


def _wrap(deg: np.ndarray) -> np.ndarray:
    """把角度差折到 (-180, 180]。"""
    return (deg + 180.0) % 360.0 - 180.0


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _band_weight(hue: np.ndarray, center: float, half: float, feather: float) -> np.ndarray:
    dist = np.abs(_wrap(hue - center))
    weight = 1.0 - _smoothstep(half, half + feather, dist)
    return weight.astype(np.float32)


def tint_image(path: Path, outfit_deg: float, accent_deg: float) -> Image.Image:
    """读入 RGBA 图，按色相分带重映射后返回新图（alpha 原样保留）。"""
    with Image.open(path) as raw:
        im = raw.convert("RGBA")
    alpha = np.asarray(im.getchannel("A"), dtype=np.uint8)

    hsv = np.asarray(im.convert("HSV"), dtype=np.uint8).copy()
    hue = hsv[..., 0].astype(np.float32) * (360.0 / 255.0)
    sat = hsv[..., 1].astype(np.float32) / 255.0

    w_outfit = _band_weight(hue, OUTFIT_CENTER, OUTFIT_HALF, OUTFIT_FEATHER)
    w_accent = _band_weight(hue, ACCENT_CENTER, ACCENT_HALF, ACCENT_FEATHER)
    gate = _smoothstep(SAT_LO, SAT_HI, sat)

    # 带内保留 55% 的局部色相起伏，避免把渐变压平成一块死色。
    # 目标是**绝对**落位（带中心恰好映射到目标色相），因此先算目标位再回推增量；
    # 若写成「目标减中心」的相对偏移，偏离中心的像素会被整体带偏（眼睛就这样跑过色）。
    delta_outfit = _wrap(outfit_deg + (hue - OUTFIT_CENTER) * 0.55 - hue)
    delta_accent = _wrap(accent_deg + (hue - ACCENT_CENTER) * 0.55 - hue)

    weight = np.clip(gate * (w_outfit + w_accent), 0.0, 1.0)
    new_hue = np.mod(hue + w_outfit * delta_outfit + w_accent * delta_accent, 360.0)

    hsv[..., 0] = np.clip(np.rint(new_hue * (255.0 / 360.0)), 0, 255).astype(np.uint8)
    tinted = np.asarray(Image.fromarray(hsv, mode="HSV").convert("RGB"), dtype=np.float32)

    # RGB<->HSV 是 8bit 量化往返，即使色相没动也会漂 ±1~2。
    # 因此只在权重 > 0 的像素上做混合，中性像素（描线 / 高光 / 白装）逐位保留原值。
    base = np.asarray(im.convert("RGB"), dtype=np.float32)
    mixed = base + weight[..., None] * (tinted - base)
    out_rgb = np.where(weight[..., None] > 0.0, np.rint(mixed), base).clip(0, 255).astype(np.uint8)

    out = Image.fromarray(out_rgb, mode="RGB").convert("RGBA")
    out.putalpha(Image.fromarray(alpha, mode="L"))
    return out


def build_variant(skin_id: str, name: str, outfit: float, accent: float, dest: Path) -> int:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    written = 0
    for filename in TINT_FILES:
        src = SRC / filename
        if not src.is_file():
            print(f"  ! 跳过缺失文件 {filename}", file=sys.stderr)
            continue
        tint_image(src, outfit, accent).save(dest / filename, optimize=True)
        written += 1
    for filename in COPY_FILES:
        src = SRC / filename
        if src.is_file():
            shutil.copy2(src, dest / filename)
            written += 1

    with Image.open(dest / "buddy_0.png") as canvas:
        size = list(canvas.size)
    manifest = {
        "name": name,
        "id": skin_id,
        "buddy": "default",
        "kind": "palette-variant",
        "canvas": size,
        "palette": {"outfit_hue": outfit, "accent_hue": accent},
        "generator": "tools/make_variant_skins.py",
        "based_on": "assets/frames",
    }
    with (dest / "manifest.json").open("w", encoding="utf-8") as fp:
        json.dump(manifest, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    written += 1
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成内置配色变体皮肤包")
    parser.add_argument(
        "--out",
        default=str(Path(tempfile.gettempdir()) / "buddy_variants"),
        help="输出根目录（每个变体一个子目录）",
    )
    args = parser.parse_args(argv)

    out_root = Path(args.out).expanduser()
    out_root.mkdir(parents=True, exist_ok=True)
    print(f"源素材 {SRC}")
    print(f"输出   {out_root}")
    for skin_id, name, outfit, accent in VARIANTS:
        dest = out_root / skin_id
        count = build_variant(skin_id, name, outfit, accent, dest)
        with Image.open(dest / "buddy_0.png") as probe:
            print(f"  ✓ {skin_id:8s} {name:6s} 上衣 {outfit:5.1f}° 眼睛 {accent:5.1f}° "
                  f"{probe.size[0]}x{probe.size[1]} {count} 文件 -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
