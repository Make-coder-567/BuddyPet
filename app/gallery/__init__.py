"""Buddy 图鉴子包。

导出策略（两条相互冲突的约束的折中）：
    1. 架构 §2 要求 ``models.py`` 必须能被**没有 PyQt5** 的单测直接 import；
    2. 架构 §1.1 要求本文件导出 ``GalleryService`` / ``SkinSwitcher``，
       而它们依赖 PyQt5。

因为 ``import app.gallery.models`` 会先执行本文件，若在这里顶层 import
service/switcher，约束 1 就被破坏了。所以这两个名字改用 **PEP 562 模块级
``__getattr__`` 延迟导入**：平时 ``app.gallery`` 是纯数据包，
``from app.gallery import GalleryService`` 时才会真正拉起 Qt。
"""
from __future__ import annotations

from .models import (
    REASON_TEXT,
    Buddy,
    GallerySnapshot,
    Skin,
    SkinStatus,
    SourceKind,
    ValidationResult,
    format_reason,
)

__all__ = [
    "Buddy",
    "Skin",
    "GallerySnapshot",
    "SourceKind",
    "SkinStatus",
    "ValidationResult",
    "GalleryService",
    "SkinSwitcher",
    "REASON_TEXT",
    "format_reason",
]

#: 延迟导入表：属性名 -> (子模块, 模块内名字)
_LAZY: dict[str, tuple[str, str]] = {
    "GalleryService": (".service", "GalleryService"),
    "SkinSwitcher": (".switcher", "SkinSwitcher"),
}


def __getattr__(name: str):
    """按需加载依赖 PyQt5 的门面/事务类（PEP 562）。"""
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value          # 缓存，后续访问不再走 __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__) | set(globals()))
