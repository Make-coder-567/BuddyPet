"""云端原始 dict → ``Buddy`` / ``Skin`` 的字段映射（候选键表）。

隔离 Q3（响应字段结构未知）：不假设任何字段名，每种字段给一张候选键表，
按顺序取第一个"存在且类型正确"的键。

约束：纯函数，**不 import PyQt5**，可被单测直接 import。
"""
from __future__ import annotations

import json
from typing import Any, Iterable

from .models import Buddy, Skin

__all__ = [
    "ID_KEYS",
    "NAME_KEYS",
    "OWNED_KEYS",
    "THUMBNAIL_KEYS",
    "SKINS_KEYS",
    "CONTAINER_KEYS",
    "adapt_buddies",
    "adapt_buddy",
    "adapt_skin",
]

# ---- 形象字段候选表（架构 §12-Q3 定稿） ----
ID_KEYS: tuple[str, ...] = ("id", "buddy_id", "buddyId", "uuid")
NAME_KEYS: tuple[str, ...] = ("name", "title", "display_name", "nickname")
OWNED_KEYS: tuple[str, ...] = ("owned", "is_owned", "obtained", "unlocked")
THUMBNAIL_KEYS: tuple[str, ...] = ("thumbnail", "thumb", "icon", "avatar", "cover")
SKINS_KEYS: tuple[str, ...] = ("skins", "skins_list", "skin_list", "appearances")

# ---- 皮肤字段候选表 ----
SKIN_ID_KEYS: tuple[str, ...] = ("id", "skin_id", "skinId", "uuid")
SKIN_NAME_KEYS: tuple[str, ...] = ("name", "title", "display_name", "label")
SKIN_DEFAULT_KEYS: tuple[str, ...] = ("is_default", "default", "isDefault")
SKIN_ASSET_KEYS: tuple[str, ...] = ("asset_dir", "dir", "path", "local_path")
SKIN_REMOTE_KEYS: tuple[str, ...] = ("remote_ref", "url", "zip", "package")
SKIN_PACKAGED_KEYS: tuple[str, ...] = ("packaged", "local", "downloaded", "available")
SKIN_MANIFEST_KEYS: tuple[str, ...] = ("manifest", "meta", "spec")

#: 顶层不是 list 时，依次在这些键里找第一个 list（架构 §12-Q3）
CONTAINER_KEYS: tuple[str, ...] = ("data", "list", "items", "buddies", "result")

_TRUE_TOKENS = {"true", "1", "yes", "y", "on", "是", "已获得"}
_FALSE_TOKENS = {"false", "0", "no", "n", "off", "否", "未获得"}


# --------------------------------------------------------------------------- #
# 基础取值
# --------------------------------------------------------------------------- #
def _pick_str(raw: dict, keys: Iterable[str], default: str = "") -> str:
    """按候选键取第一个非空字符串。"""
    for key in keys:
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, str) and value.strip():
            return value.strip()
    return default


def _to_bool(value: Any) -> bool | None:
    """宽松布尔解析；无法判定返回 ``None``。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
    return None


def _pick_bool(raw: dict, keys: Iterable[str], default: bool | None = None) -> bool | None:
    """按候选键取第一个**类型正确**的布尔值。"""
    for key in keys:
        if key not in raw:
            continue
        parsed = _to_bool(raw[key])
        if parsed is not None:
            return parsed
    return default


def _pick_dict(raw: dict, keys: Iterable[str]) -> dict:
    for key in keys:
        if key not in raw:
            continue
        value = raw[key]
        if isinstance(value, dict):
            return dict(value)
    return {}


def _find_list(payload: Any, depth: int = 0) -> list | None:
    """从任意 payload 中找出"形象列表"。

    * ``list`` → 直接用；
    * ``dict`` → 先看 :data:`CONTAINER_KEYS`，再看唯一/首个 list 值；
    * 其余类型 → ``None``。

    Args:
        payload: 云端原始结构。
        depth: 递归深度（最多 2 层，避免脏数据打转）。
    """
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return None
    for key in CONTAINER_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    if depth >= 2:
        return None
    for value in payload.values():
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            found = _find_list(value, depth + 1)
            if found is not None:
                return found
    return None


# --------------------------------------------------------------------------- #
# 映射
# --------------------------------------------------------------------------- #
def adapt_skin(raw: Any, buddy_id: str) -> Skin | None:
    """把一条皮肤 dict 映射成 :class:`Skin`；结构不可用返回 ``None``。"""
    if not isinstance(raw, dict):
        return None
    skin_id = _pick_str(raw, SKIN_ID_KEYS)
    if not skin_id:
        return None
    asset_dir = _pick_str(raw, SKIN_ASSET_KEYS) or None
    packaged_flag = _pick_bool(raw, SKIN_PACKAGED_KEYS)
    packaged = bool(asset_dir) if packaged_flag is None else packaged_flag
    return Skin(
        id=skin_id,
        name=_pick_str(raw, SKIN_NAME_KEYS, skin_id),
        buddy_id=buddy_id,
        asset_dir=asset_dir,
        manifest=_pick_dict(raw, SKIN_MANIFEST_KEYS),
        remote_ref=_pick_str(raw, SKIN_REMOTE_KEYS),
        is_default=bool(_pick_bool(raw, SKIN_DEFAULT_KEYS, False)),
        packaged=packaged,
    )


def adapt_buddy(raw: Any) -> Buddy | None:
    """把一条形象 dict 映射成 :class:`Buddy`；缺 ``id`` 返回 ``None``（调用方应累加丢弃数）。"""
    if not isinstance(raw, dict):
        return None
    buddy_id = _pick_str(raw, ID_KEYS)
    if not buddy_id:
        return None
    skins: list[Skin] = []
    skins_raw = None
    for key in SKINS_KEYS:
        if isinstance(raw.get(key), list):
            skins_raw = raw[key]
            break
    if isinstance(skins_raw, list):
        for item in skins_raw:
            skin = adapt_skin(item, buddy_id)
            if skin is not None:
                skins.append(skin)
    return Buddy(
        id=buddy_id,
        name=_pick_str(raw, NAME_KEYS, buddy_id),
        # owned 缺失 → 默认 True（PRD 只展示已获得项，缺失视为已获得）
        owned=bool(_pick_bool(raw, OWNED_KEYS, True)),
        thumbnail=_pick_str(raw, THUMBNAIL_KEYS),
        skins=tuple(skins),
        order=0,
    )


def adapt_buddies(payload: Any, only_owned: bool = True) -> tuple[Buddy, ...]:
    """云端原始响应 → ``tuple[Buddy, ...]``。

    Args:
        payload: 已解析的 JSON（``dict`` / ``list``），或原始 ``bytes``。
        only_owned: 是否过滤掉 ``owned=False`` 的形象（默认 ``True``）。

    Returns:
        同构的 ``Buddy`` 元组；结构无法识别时返回 ``()``，**永不抛异常**。
    """
    if isinstance(payload, (bytes, bytearray)):
        try:
            payload = json.loads(payload.decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            return ()
    items = _find_list(payload)
    if not items:
        return ()
    out: list[Buddy] = []
    for index, item in enumerate(items):
        buddy = adapt_buddy(item)
        if buddy is None:
            continue
        if only_owned and not buddy.owned:
            continue
        # order 用"原始位置"更稳定，便于云端顺序透传
        out.append(
            Buddy(
                id=buddy.id,
                name=buddy.name,
                owned=buddy.owned,
                thumbnail=buddy.thumbnail,
                skins=buddy.skins,
                order=index,
            )
        )
    return tuple(out)
