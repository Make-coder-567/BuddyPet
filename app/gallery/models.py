"""Buddy 图鉴数据模型。

设计约束（来自架构文档 §2）：
    * 本模块是**纯数据模块**，只允许标准库依赖，**绝对不 import PyQt5**，
      以便被单元测试、离线脚本、校验器直接 import。
    * 与 ``app.assets.BuddySprites`` 完全解耦：``Skin.asset_dir`` 只是字符串路径，
      只有 ``switcher`` / ``validator`` 才会把它换成 ``Path``。
    * 字段名冻结，后续切片（provider / cache / validator / UI）直接按名引用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable

__all__ = [
    "SourceKind",
    "SkinStatus",
    "Skin",
    "Buddy",
    "GallerySnapshot",
    "ValidationResult",
    "REASON_TEXT",
    "LAYER_TEXT",
    "format_reason",
]


# --------------------------------------------------------------------------- #
# 枚举
# --------------------------------------------------------------------------- #
class SourceKind(str, Enum):
    """快照来源，对应四层降级链 L1~L4。"""

    CLOUD = "cloud"      # L1 云端
    CACHE = "cache"      # L2 离线缓存
    BUILTIN = "builtin"  # L3 内置清单
    DEFAULT = "default"  # L4 默认兜底

    @property
    def layer_name(self) -> str:
        """状态条里用的中文层名。"""
        return LAYER_TEXT.get(self.value, "内置清单")


class SkinStatus(str, Enum):
    """皮肤可用性等级。"""

    READY = "ready"        # 可用
    DEGRADED = "degraded"  # 可降级（视线或抓边缺失）
    BROKEN = "broken"      # 不可用（硬依赖缺失 / 尺寸不一致）


# --------------------------------------------------------------------------- #
# 文案表（架构文档 §2.2）
# --------------------------------------------------------------------------- #
LAYER_TEXT: dict[str, str] = {
    SourceKind.CLOUD.value: "云端",
    SourceKind.CACHE.value: "离线缓存",
    SourceKind.BUILTIN.value: "内置清单",
    SourceKind.DEFAULT.value: "默认 Buddy",
}

#: 原因码 -> UI 文案模板。占位符只用 ``{layer}`` 与 ``{time}`` 两个，
#: 通过 :func:`format_reason` 渲染，避免调用方各自拼 format 造成 KeyError。
REASON_TEXT: dict[str, str] = {
    # ---- L1 云端 ----
    "ok": "云端 · 更新于 {time}",
    "not_configured": "尚未配置 WorkBuddy 云端连接，当前展示{layer}",
    "connect_timeout": "云端连接超时，正在使用{layer}",
    "read_timeout": "云端响应超时，正在使用{layer}",
    "http_401": "凭据无效或已过期，正在使用{layer}",
    "http_403": "凭据无效或已过期，正在使用{layer}",
    "http_404": "云端接口地址无效，正在使用{layer}",
    "http_5xx": "云端服务暂不可用，正在使用{layer}",
    "network_error": "网络不可用，正在使用{layer}",
    "parse_error": "云端返回格式无法识别，正在使用{layer}",
    "empty_payload": "云端未返回任何形象，正在使用{layer}",
    # ---- L2 缓存 ----
    "cache_hit": "离线缓存 · {time}",
    "cache_stale": "离线缓存 · {time}（可能不是最新）",
    "cache_corrupt": "缓存文件损坏，已回退内置清单",
    "cache_scope_mismatch": "缓存属于其他账号/地址，已回退内置清单",
    "no_cache": "内置清单",
    # ---- L3 / L4 ----
    "builtin": "内置清单",
    "assets_missing": "默认 Buddy（素材目录异常）",
    # ---- 兜底 ----
    "unknown": "内置清单",
}

#: 未知原因码的兜底文案。
_FALLBACK_REASON_TEXT = "内置清单"


def format_reason(reason: str, layer: str = "", time_text: str = "--") -> str:
    """把原因码渲染成一句可直接上 UI 的文案。

    Args:
        reason: :data:`REASON_TEXT` 的 key；未知 key 走兜底文案。
        layer: 中文层名，留空则按 ``"内置清单"`` 处理。
        time_text: 时间展示文本（``"--"`` 表示未知）。

    Returns:
        渲染后的文案；模板里不含占位符时原样返回，永不抛异常。
    """
    template = REASON_TEXT.get(str(reason or ""), _FALLBACK_REASON_TEXT)
    try:
        return template.format(layer=layer or "内置清单", time=time_text or "--")
    except (KeyError, IndexError, ValueError):
        return template


def _as_tuple_str(value: Any) -> tuple[str, ...]:
    """把 ``None`` / list / tuple / 单值 归一成 ``tuple[str, ...]``。"""
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    return (str(value),)


def _as_dict(value: Any) -> dict:
    """把 ``manifest`` 之类的容器字段归一成 dict（非 dict 一律丢成空 dict）。"""
    return value if isinstance(value, dict) else {}


# --------------------------------------------------------------------------- #
# 数据模型
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Skin:
    """一套皮肤（一个素材目录）。

    Attributes:
        id: 皮肤标识，同一 Buddy 内唯一。
        name: 展示名。
        buddy_id: 所属形象 id。
        asset_dir: 本地素材目录的绝对路径；``None`` 表示云端资源尚未落地。
        manifest: 原始清单透传（frames / eyes / canvas 等），规则由 validator 解释。
        remote_ref: 预留的云端引用（zip / 包名），本期不下载。
        is_default: 是否为该形象的默认皮肤（同一 Buddy 内有且仅有一个）。
        packaged: ``True`` = 随包/本地可用；``False`` = 仅云端可见，本期不可应用。
    """

    id: str
    name: str
    buddy_id: str
    asset_dir: str | None = None
    manifest: dict = field(default_factory=dict)
    remote_ref: str = ""
    is_default: bool = False
    packaged: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "name", str(self.name))
        object.__setattr__(self, "buddy_id", str(self.buddy_id))
        object.__setattr__(self, "manifest", _as_dict(self.manifest))
        object.__setattr__(self, "remote_ref", str(self.remote_ref or ""))
        object.__setattr__(self, "is_default", bool(self.is_default))
        object.__setattr__(self, "packaged", bool(self.packaged))

    @property
    def is_applicable(self) -> bool:
        """本期是否可被应用：必须有本地素材目录。"""
        return self.packaged and bool(self.asset_dir)


@dataclass(frozen=True)
class Buddy:
    """一个形象（可含多套皮肤）。"""

    id: str
    name: str
    owned: bool = True
    thumbnail: str = ""
    skins: tuple[Skin, ...] = ()
    order: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", str(self.id))
        object.__setattr__(self, "name", str(self.name))
        object.__setattr__(self, "owned", bool(self.owned))
        object.__setattr__(self, "thumbnail", str(self.thumbnail or ""))
        # 允许外部传 list，内部统一成不可变 tuple
        skins = self.skins if isinstance(self.skins, tuple) else tuple(self.skins or ())
        object.__setattr__(self, "skins", skins)
        try:
            object.__setattr__(self, "order", int(self.order))
        except (TypeError, ValueError):
            object.__setattr__(self, "order", 0)

    @property
    def default_skin(self) -> Skin | None:
        """默认皮肤；无皮肤时返回 ``None``（不抛异常）。"""
        for skin in self.skins:
            if skin.is_default:
                return skin
        return None

    def find_skin(self, skin_id: str) -> Skin | None:
        """按 id 找皮肤，找不到返回 ``None``。"""
        target = str(skin_id or "")
        for skin in self.skins:
            if skin.id == target:
                return skin
        return None


@dataclass
class GallerySnapshot:
    """一次图鉴快照。四层降级链的每一层都必须产出本结构（§4.3）。

    Attributes:
        source: 数据来源层。
        reason: 原因码，见 :data:`REASON_TEXT`。
        buddies: 形象列表（构造时统一转成 tuple）。
        fetched_at: 抓取时间。缓存/云端写入 epoch 秒（float），也容忍 ISO 字符串。
        cache_stale: 是否来自已过期缓存（UI 用来加"可能不是最新"角标）。
        detail: 追加说明（被丢弃条目数、异常摘要等）。
    """

    source: SourceKind
    reason: str
    buddies: tuple[Buddy, ...] = ()
    fetched_at: float | str = 0.0
    cache_stale: bool = False
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceKind):
            object.__setattr__(self, "source", SourceKind(str(self.source)))
        object.__setattr__(self, "reason", str(self.reason or ""))
        buddies = self.buddies if isinstance(self.buddies, tuple) else tuple(self.buddies or ())
        object.__setattr__(self, "buddies", buddies)
        object.__setattr__(self, "cache_stale", bool(self.cache_stale))
        object.__setattr__(self, "detail", str(self.detail or ""))

    # ---------------- 查询 ----------------
    def buddy(self, buddy_id: str) -> Buddy | None:
        """按 id 找形象，找不到返回 ``None``。"""
        target = str(buddy_id or "")
        for buddy in self.buddies:
            if buddy.id == target:
                return buddy
        return None

    def skin(self, buddy_id: str, skin_id: str) -> Skin | None:
        """按 (形象 id, 皮肤 id) 找皮肤，任一层找不到返回 ``None``。"""
        buddy = self.buddy(buddy_id)
        return buddy.find_skin(skin_id) if buddy is not None else None

    def find_skin(self, buddy_id: str, skin_id: str) -> Skin | None:
        """:meth:`skin` 的同义方法，供 UI 层按语义调用。"""
        return self.skin(buddy_id, skin_id)

    # ---------------- 展示辅助 ----------------
    @property
    def layer_name(self) -> str:
        """:attr:`source` 对应的中文层名。"""
        return self.source.layer_name

    def reason_text(self, time_text: str = "--") -> str:
        """把 :attr:`reason` 渲染成 UI 文案。"""
        return format_reason(self.reason, self.layer_name, time_text)


@dataclass
class ValidationResult:
    """素材完整性校验结论（§7.1）。

    Attributes:
        status: 可用性等级。
        missing: 硬缺失项（人类可读，如 ``"buddy_3.png"``）；非空即 ``BROKEN``。
        degraded: 降级项（``"视线跟随"`` / ``"抓边姿态"``）。
        canvas: 画布尺寸 ``(w, h)``；未知为 ``None``。
        frames: 有效帧数。
        has_gaze: 是否支持视线跟随。
        has_grip: 是否支持抓边姿态。
        message: 一句话结论，可直接上 UI。
    """

    status: SkinStatus
    missing: tuple[str, ...] = ()
    degraded: tuple[str, ...] = ()
    canvas: tuple[int, int] | None = None
    frames: int = 0
    has_gaze: bool = False
    has_grip: bool = False
    message: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, SkinStatus):
            object.__setattr__(self, "status", SkinStatus(str(self.status)))
        # 允许外部传 list，内部统一成不可变 tuple
        object.__setattr__(self, "missing", _as_tuple_str(self.missing))
        object.__setattr__(self, "degraded", _as_tuple_str(self.degraded))
        if self.canvas is not None:
            try:
                object.__setattr__(self, "canvas", (int(self.canvas[0]), int(self.canvas[1])))
            except (TypeError, ValueError, IndexError):
                object.__setattr__(self, "canvas", None)
        try:
            object.__setattr__(self, "frames", int(self.frames))
        except (TypeError, ValueError):
            object.__setattr__(self, "frames", 0)
        object.__setattr__(self, "has_gaze", bool(self.has_gaze))
        object.__setattr__(self, "has_grip", bool(self.has_grip))
        object.__setattr__(self, "message", str(self.message or ""))

    @property
    def can_apply(self) -> bool:
        """是否允许「应用」：``READY`` / ``DEGRADED`` 可用，``BROKEN`` 不可用。"""
        return self.status is not SkinStatus.BROKEN

    @property
    def notes(self) -> tuple[str, ...]:
        """:attr:`degraded` 的别名（UI 里统称"备注项"）。"""
        return self.degraded
