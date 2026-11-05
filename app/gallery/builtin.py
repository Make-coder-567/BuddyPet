"""内置 Buddy 清单：官方默认形象 + ``assets/skins/<buddy_id>/<skin_id>/`` 扫描。

本模块是**唯一**知道"默认素材在 ``assets/frames``"的地方（架构 §1.1 / D6）。
纯标准库依赖，不 import PyQt5，可被单测直接 import。

目录约定（离线闭环的唯一凭据，见 ``docs/skin_manifest.md``）::

    <ROOT>/assets/frames/                  官方默认皮肤（buddy=default, skin=default）
    <ROOT>/assets/skins/<buddy_id>/<skin_id>/   随包附加皮肤，每个二级目录 = 一套皮肤

``assets/skins/`` 不存在或为空时**静默返回空列表**，绝不抛异常。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import Buddy, GallerySnapshot, Skin, SourceKind

__all__ = [
    "ROOT",
    "DEFAULT_BUDDY_ID",
    "DEFAULT_SKIN_ID",
    "DEFAULT_BUDDY_NAME",
    "DEFAULT_BUDDY",
    "DEFAULT_SKIN",
    "default_skin",
    "default_buddy",
    "skins_root",
    "scan_skins",
    "builtin_buddies",
    "builtin_snapshot",
]

#: 项目根目录（本文件位于 ``<ROOT>/app/gallery/builtin.py``）
ROOT: Path = Path(__file__).resolve().parent.parent.parent

DEFAULT_BUDDY_ID = "default"
DEFAULT_SKIN_ID = "default"
DEFAULT_BUDDY_NAME = "官方 Buddy"

#: 一个目录至少要有其中任意一个文件，才被认为是"皮肤目录"（避免误收垃圾目录）
_SKIN_MARKERS: tuple[str, ...] = (
    "buddy_0.png",
    "buddy_1.png",
    "buddy_2.png",
    "buddy_3.png",
    "buddy_4.png",
    "buddy_5.png",
    "buddy_base.png",
    "eyes.json",
    "manifest.json",
    "frames.json",
)

_MANIFEST_NAMES: tuple[str, ...] = ("manifest.json", "frames.json")


# --------------------------------------------------------------------------- #
# 官方默认形象
# --------------------------------------------------------------------------- #
def default_skin(root: Path | str | None = None) -> Skin:
    """官方默认皮肤（``asset_dir`` 指向 ``<root>/assets/frames`` 的绝对路径）。"""
    base = Path(root) if root is not None else ROOT
    return Skin(
        id=DEFAULT_SKIN_ID,
        name="默认",
        buddy_id=DEFAULT_BUDDY_ID,
        asset_dir=str((base / "assets" / "frames").resolve()),
        manifest={},
        remote_ref="",
        is_default=True,
        packaged=True,
    )


def default_buddy(root: Path | str | None = None) -> Buddy:
    """官方默认形象（单皮肤、已获得、order=0）。"""
    base = Path(root) if root is not None else ROOT
    return Buddy(
        id=DEFAULT_BUDDY_ID,
        name=DEFAULT_BUDDY_NAME,
        owned=True,
        thumbnail="",
        skins=(default_skin(base),),
        order=0,
    )


#: 以模块根目录为基准的默认常量（provider 的 L4 兜底直接用）
DEFAULT_SKIN: Skin = default_skin(ROOT)
DEFAULT_BUDDY: Buddy = default_buddy(ROOT)


# --------------------------------------------------------------------------- #
# 目录扫描
# --------------------------------------------------------------------------- #
def skins_root(root: Path | str | None = None) -> Path:
    """``<root>/assets/skins`` 路径（不保证存在）。"""
    base = Path(root) if root is not None else ROOT
    return base / "assets" / "skins"


def _read_manifest(directory: Path) -> dict:
    """读目录下的 manifest.json / frames.json；读不到就返回空 dict。"""
    for name in _MANIFEST_NAMES:
        path = directory / name
        if not path.is_file():
            continue
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 —— 清单坏了不影响列皮肤
            continue
        if isinstance(data, dict):
            return dict(data)
    return {}


def _display_name(manifest: dict, fallback: str) -> str:
    for key in ("name", "title", "display_name", "label"):
        value = manifest.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _iter_skin_dirs(buddy_dir: Path) -> list[Path]:
    """列出某个形象目录下所有"看起来像皮肤"的子目录（按名排序，确定性）。"""
    try:
        children = sorted(p for p in buddy_dir.iterdir() if p.is_dir())
    except OSError:
        return []
    out: list[Path] = []
    for child in children:
        try:
            names = {p.name for p in child.iterdir()}
        except OSError:
            continue
        if names & set(_SKIN_MARKERS):
            out.append(child)
    return out


def scan_skins(root: Path | str | None = None) -> tuple[Buddy, ...]:
    """扫描 ``assets/skins/<buddy_id>/<skin_id>/``，按形象聚合成 ``Buddy``。

    Args:
        root: 项目根目录，缺省用模块 ``ROOT``。

    Returns:
        按 ``order`` 稳定排序的 ``tuple[Buddy, ...]``；目录不存在或为空时返回 ``()``。
        **无论磁盘状态如何都不会抛异常。**
    """
    top = skins_root(root)
    try:
        if not top.is_dir():
            return ()
        buddy_dirs = sorted(p for p in top.iterdir() if p.is_dir())
    except OSError:
        return ()

    buddies: list[Buddy] = []
    for order, buddy_dir in enumerate(buddy_dirs):
        buddy_id = buddy_dir.name
        if not buddy_id or buddy_id.startswith("."):
            continue
        skins: list[Skin] = []
        for skin_dir in _iter_skin_dirs(buddy_dir):
            skin_id = skin_dir.name
            if not skin_id or skin_id.startswith("."):
                continue
            manifest = _read_manifest(skin_dir)
            skins.append(
                Skin(
                    id=skin_id,
                    name=_display_name(manifest, skin_id),
                    buddy_id=buddy_id,
                    asset_dir=str(skin_dir.resolve()),
                    manifest=manifest,
                    remote_ref="",
                    is_default=(skin_id == DEFAULT_SKIN_ID),
                    packaged=True,
                )
            )
        if not skins:
            continue
        # 没有任何 skin_id == "default" 时，把第一个标为默认（结构统一性）
        if not any(s.is_default for s in skins):
            first = skins[0]
            skins[0] = Skin(
                id=first.id,
                name=first.name,
                buddy_id=first.buddy_id,
                asset_dir=first.asset_dir,
                manifest=first.manifest,
                remote_ref=first.remote_ref,
                is_default=True,
                packaged=first.packaged,
            )
        buddy_manifest = _read_manifest(buddy_dir)
        buddies.append(
            Buddy(
                id=buddy_id,
                name=_display_name(buddy_manifest, buddy_id),
                owned=True,
                thumbnail="",
                skins=tuple(skins),
                order=order,
            )
        )
    return tuple(buddies)


def builtin_buddies(root: Path | str | None = None) -> tuple[Buddy, ...]:
    """内置清单：官方默认形象 + 扫描出的随包形象。

    ``assets/skins/default/...`` 下的皮肤会**并入官方默认形象**，
    这样 ``skin:default:test`` 这类"默认形象换皮肤"的用法才成立。
    """
    base = Path(root) if root is not None else ROOT
    official = default_buddy(base)
    extra = scan_skins(base)
    if not extra:
        return (official,)

    merged_skins = list(official.skins)
    rest: list[Buddy] = []
    for buddy in extra:
        if buddy.id == DEFAULT_BUDDY_ID:
            # 并入官方形象时一律降为非默认，官方自己的 default 皮肤才是唯一默认
            for skin in buddy.skins:
                merged_skins.append(
                    Skin(
                        id=skin.id,
                        name=skin.name,
                        buddy_id=skin.buddy_id,
                        asset_dir=skin.asset_dir,
                        manifest=skin.manifest,
                        remote_ref=skin.remote_ref,
                        is_default=False,
                        packaged=skin.packaged,
                    )
                )
            continue
        rest.append(buddy)

    # 去重（同 id 的皮肤只保留首个），保持插入顺序
    seen: set[str] = set()
    unique: list[Skin] = []
    for skin in merged_skins:
        if skin.id in seen:
            continue
        seen.add(skin.id)
        unique.append(skin)

    merged = Buddy(
        id=official.id,
        name=official.name,
        owned=official.owned,
        thumbnail=official.thumbnail,
        skins=tuple(unique),
        order=official.order,
    )
    return (merged, *rest)


def builtin_snapshot(root: Path | str | None = None) -> GallerySnapshot:
    """L3 内置层快照（``source=BUILTIN``，``reason="builtin"``）。"""
    buddies = builtin_buddies(root)
    return GallerySnapshot(
        source=SourceKind.BUILTIN,
        reason="builtin",
        buddies=buddies,
        fetched_at=0.0,
        cache_stale=False,
        detail="" if buddies else "内置清单为空",
    )
