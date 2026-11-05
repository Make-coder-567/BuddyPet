"""素材完整性校验：一个皮肤目录能否被 ``BuddySprites`` 安全加载。

设计约束（架构文档 §7）：
    * 只用 ``QImage``，**不用** ``QPixmap`` —— 不依赖 GUI 线程，可在任意上下文调用。
    * 判据与 ``app/assets.py`` 的 ``FileNotFoundError`` / ``has_gaze`` / ``has_grip``
      **完全同源**：校验通过即意味着构造不会抛，校验结论与运行时能力一致。
    * 与文档 §7.2 的唯一差异（已与架构确认）：**V3 尺寸一致性检查排除
      ``buddy_grip.png``**。实测默认素材 ``buddy_grip.png`` 为 925×1524，
      而 6 帧 + base 为 268×392；若按文档字面要求四者相等，默认皮肤会被
      误判 BROKEN。grip 只校验"可加载、宽高 > 0"，尺寸任意（运行时靠
      ``BuddySprites.grip_width()`` 按比例缩放）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PyQt5.QtGui import QImage

from .models import SkinStatus, ValidationResult

__all__ = ["validate_skin", "FRAME_NAMES", "BASE_NAME", "EYE_NAMES", "META_NAME", "GRIP_NAME"]

FRAME_COUNT = 6
FRAME_NAMES: tuple[str, ...] = tuple(f"buddy_{i}.png" for i in range(FRAME_COUNT))
BASE_NAME = "buddy_base.png"
EYE_NAMES: tuple[str, ...] = ("eye_l.png", "eye_r.png")
META_NAME = "eyes.json"
GRIP_NAME = "buddy_grip.png"
EXTRA_META_NAMES: tuple[str, ...] = ("frames.json", "manifest.json")

#: 降级项的标准 token（必须与架构/UI 口径逐字一致，不要改）
DEGRADED_GAZE = "视线跟随"
DEGRADED_GRIP = "抓边姿态"

#: 参与缓存签名的文件（全部 stat 一遍，< 1ms）
_SIG_FILES: tuple[str, ...] = (
    FRAME_NAMES + (BASE_NAME, GRIP_NAME, META_NAME) + EYE_NAMES + EXTRA_META_NAMES
)

#: 进程内 LRU：{(asset_dir, 文件签名): ValidationResult}，上限 32 条
_CACHE: dict[tuple[str, tuple], ValidationResult] = {}
_CACHE_LIMIT = 32


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _image_size(path: Path) -> tuple[int, int] | None:
    """读取图片宽高；文件缺失 / 非图片 / 尺寸为 0 一律返回 ``None``。"""
    try:
        if not path.is_file():
            return None
        img = QImage(str(path))
        if img.isNull():
            return None
        w, h = img.width(), img.height()
        if w <= 0 or h <= 0:
            return None
        return (w, h)
    except Exception:  # noqa: BLE001 —— 校验器绝不允许把异常抛给调用方
        return None


def _dir_signature(path: Path) -> tuple:
    """素材目录的轻量签名：关键文件的 (大小, mtime_ns)。

    只 stat 目录 mtime 是不够的 —— **改文件内容不会更新目录 mtime**，
    会导致 LRU 返回旧结论（"改完 eyes.json 还是 READY"这类假通过）。
    """
    parts: list[tuple[str, int, int]] = []
    for name in _SIG_FILES:
        try:
            st = (path / name).stat()
            parts.append((name, int(st.st_size), int(st.st_mtime_ns)))
        except OSError:
            parts.append((name, -1, -1))
    return tuple(parts)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _eye_meta_issue(meta: Any) -> str:
    """校验 ``eyes.json`` 结构，返回问题描述；结构完整时返回空串。

    判据与 ``BuddySprites._eye_meta_ok`` 完全同源（架构 §1.2 加严版）。
    """
    if not isinstance(meta, dict):
        return "eyes.json 顶层不是对象"
    size = meta.get("size")
    if not isinstance(size, (list, tuple)) or len(size) != 2:
        return "eyes.json 缺合法 size"
    try:
        sw, sh = int(size[0]), int(size[1])
    except (TypeError, ValueError):
        return "eyes.json 的 size 不是整数"
    if sw <= 0 or sh <= 0:
        return "eyes.json 的 size 非正"
    eyes = meta.get("eyes")
    if not isinstance(eyes, dict):
        return "eyes.json 缺 eyes"
    for name in ("left", "right"):
        item = eyes.get(name)
        if not isinstance(item, dict):
            return f"eyes.json 缺 eyes.{name}"
        try:
            w = int(item["w"])
            h = int(item["h"])
            int(item["x"])
            int(item["y"])
        except (KeyError, TypeError, ValueError):
            return f"eyes.json 的 eyes.{name} 坐标非法"
        if w <= 0 or h <= 0:
            return f"eyes.json 的 eyes.{name} 宽高非正"
    return ""


def _resolve_dir(asset_dir: Any) -> Path | None:
    """容忍 ``Skin`` 实例 / 字符串 / ``Path`` / ``None`` 四种入参。"""
    if asset_dir is None:
        return None
    candidate = getattr(asset_dir, "asset_dir", asset_dir)
    if candidate is None:
        return None
    try:
        return Path(str(candidate))
    except (TypeError, ValueError, OSError):
        return None


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #
def validate_skin(
    asset_dir: Any,
    manifest: dict | None = None,
    use_cache: bool = True,
) -> ValidationResult:
    """校验一个皮肤素材目录。

    Args:
        asset_dir: 素材目录（``str`` / ``Path``），或直接传一个 ``Skin`` 实例
            （此时取其 ``asset_dir``）；``None`` 视为"云端皮肤尚未下载"。
        manifest: 可选的清单 dict，仅用于补充 ``message`` 提示，不参与定级。
        use_cache: 是否使用进程内 LRU（键为 (目录, 目录 mtime)）。

    Returns:
        :class:`~app.gallery.models.ValidationResult`；**永不抛异常**。
    """
    raw_dir = getattr(asset_dir, "asset_dir", asset_dir)
    if raw_dir is None:
        return ValidationResult(
            status=SkinStatus.BROKEN,
            missing=("云端皮肤尚未下载",),
            message="不可用：素材不在本地",
        )

    path = _resolve_dir(raw_dir)
    if path is None:
        return ValidationResult(
            status=SkinStatus.BROKEN,
            missing=("素材目录非法",),
            message="不可用：素材目录非法",
        )

    key = (str(path), _dir_signature(path))
    if use_cache:
        hit = _CACHE.get(key)
        if hit is not None:
            return hit

    result = _validate(path, manifest)
    if use_cache:
        if len(_CACHE) >= _CACHE_LIMIT:
            _CACHE.pop(next(iter(_CACHE)), None)
        _CACHE[key] = result
    return result


def clear_validation_cache() -> None:
    """清空校验结果 LRU（测试与换肤后强制重扫用）。"""
    _CACHE.clear()


def _validate(path: Path, manifest: dict | None) -> ValidationResult:
    missing: list[str] = []
    degraded: list[str] = []
    notes: list[str] = []

    # ---- V1 目录 ----
    if not path.exists() or not path.is_dir():
        return ValidationResult(
            status=SkinStatus.BROKEN,
            missing=(f"素材目录缺失：{path.name}",),
            message=f"不可用：目录不存在（{path}）",
        )

    # ---- V2 六帧 ----
    frame_sizes: dict[str, tuple[int, int]] = {}
    for name in FRAME_NAMES:  # 顺序固定 = 确定性
        size = _image_size(path / name)
        if size is None:
            missing.append(name)
        else:
            frame_sizes[name] = size

    # ---- V3 尺寸一致性（仅 6 帧 + base；grip 豁免） ----
    base_size = _image_size(path / BASE_NAME)
    if base_size is not None:
        sizes = list(frame_sizes.values()) + [base_size]
        first = sizes[0]
        if any(s != first for s in sizes[1:]):
            canvas_txt = f"{first[0]}×{first[1]}"
            others = sorted(
                {f"{n}={s[0]}×{s[1]}" for n, s in frame_sizes.items() if s != first}
            )
            if base_size != first:
                others.append(f"{BASE_NAME}={base_size[0]}×{base_size[1]}")
            missing.append(f"尺寸不一致 {canvas_txt} ≠ {', '.join(others)}")
            degraded.append(f"尺寸明细：基准 {canvas_txt}，异常项 {', '.join(others)}")

    canvas = frame_sizes.get(FRAME_NAMES[0]) or base_size
    frames = len(frame_sizes)

    # ---- V4 视线三件套（缺失/非法 → 降级，不判 BROKEN） ----
    gaze_issues: list[str] = []
    if base_size is None:
        gaze_issues.append(f"缺 {BASE_NAME}")
    for name in EYE_NAMES:
        if _image_size(path / name) is None:
            gaze_issues.append(f"缺 {name}")
    meta = _read_json(path / META_NAME) if (path / META_NAME).is_file() else None
    if meta is None:
        gaze_issues.append(f"缺 {META_NAME}")
    else:
        issue = _eye_meta_issue(meta)
        if issue:
            gaze_issues.append(issue)
        elif canvas is not None:
            try:
                msize = (int(meta["size"][0]), int(meta["size"][1]))
            except (KeyError, TypeError, ValueError, IndexError):
                msize = None
            if msize is not None and msize != canvas:
                gaze_issues.append(
                    f"eyes.json 的 size={msize[0]}×{msize[1]} 与画布 {canvas[0]}×{canvas[1]} 不符"
                )
    has_gaze = not gaze_issues
    if not has_gaze:
        degraded.append(DEGRADED_GAZE)
        degraded.extend(gaze_issues)

    # ---- V5 抓边（只校验可加载 + 宽高 > 0；尺寸任意，见模块 docstring） ----
    grip_size = _image_size(path / GRIP_NAME)
    has_grip = grip_size is not None
    if not has_grip:
        degraded.append(DEGRADED_GRIP)
        degraded.append(f"缺 {GRIP_NAME}")

    # ---- V6 元数据（仅提示，不降级） ----
    if not any((path / name).is_file() for name in EXTRA_META_NAMES):
        notes.append("元数据缺失，按 6 帧/首帧尺寸推断")
    else:
        frames_json = _read_json(path / "frames.json")
        if isinstance(frames_json, dict):
            declared = frames_json.get("frames")
            if isinstance(declared, int) and declared != frames:
                notes.append(f"frames.json 声明 {declared} 帧，实际 {frames} 帧")
    if isinstance(manifest, dict):
        hint = manifest.get("canvas") or manifest.get("size")
        if isinstance(hint, (list, tuple)) and len(hint) == 2 and canvas is not None:
            try:
                if (int(hint[0]), int(hint[1])) != canvas:
                    notes.append(
                        f"manifest 声明画布 {int(hint[0])}×{int(hint[1])}，实测 {canvas[0]}×{canvas[1]}"
                    )
            except (TypeError, ValueError):
                pass

    # ---- V6 定级 ----
    if missing:
        status = SkinStatus.BROKEN
        message = "不可用：缺少 " + "、".join(missing[:3])
        if len(missing) > 3:
            message += f" 等 {len(missing)} 项"
    elif degraded:
        status = SkinStatus.DEGRADED
        flags = []
        if not has_gaze:
            flags.append("视线跟随")
        if not has_grip:
            flags.append("抓边姿态")
        message = "可降级：无" + "、无".join(flags) if flags else "可降级"
    else:
        status = SkinStatus.READY
        message = "可用"
    if notes:
        message += "（" + "；".join(notes) + "）"

    return ValidationResult(
        status=status,
        missing=tuple(missing),
        degraded=tuple(degraded),
        canvas=canvas,
        frames=frames,
        has_gaze=has_gaze,
        has_grip=has_grip,
        message=message,
    )
