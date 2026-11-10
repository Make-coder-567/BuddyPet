"""桌面宠物配置：JSON 持久化到 %APPDATA%\\WorkBuddyPet\\config.json。"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

APP_DIR_NAME = "WorkBuddyPet"
CONFIG_NAME = "config.json"

LEFT_CLICK_CHOICES = {
    "open": "打开 WorkBuddy",
    "effect": "播放互动效果",
    "none": "无操作",
}

#: 图鉴"立即提交"语义下，设置面板的 `_reset()` / `reject()` **必须跳过**的字段（D5）。
#: 这些字段由 ``SkinSwitcher`` 独占写入，面板无权回滚，否则会出现
#: "配置说 default、画面是樱粉"的长期不一致。
GALLERY_OWNED_FIELDS: tuple[str, ...] = (
    "selected_buddy_id",
    "selected_skin_id",
    "gallery_base_url",
    "gallery_auth_ref",
    "gallery_cache_ttl_h",
    "gallery_last_sync",
    "gallery_auto_sync",
    "pos",
    "seen_intro",
)

#: 新增「统计类」字段：只增不减的计数与最后一次拖放路径。
#: 设置面板的 `_reset()` / `reject()` 同样必须跳过 —— 这些不是「设置项」，
#: 而是使用数据，回滚设置不应该把战绩清掉。
STATS_OWNED_FIELDS: tuple[str, ...] = (
    "drop_count",
    "last_drop_path",
    "rps_wins",
    "rps_losses",
    "rps_draws",
)

#: 全局快捷键默认组合键
DEFAULT_HOTKEY = "Ctrl+Alt+B"


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home())
    path = Path(base) / APP_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return config_dir() / CONFIG_NAME


@dataclass
class Settings:
    size: int = 212                 # 宠物显示高度（px）
    opacity: float = 1.0            # 整体不透明度
    always_on_top: bool = True
    idle_anim: bool = True          # 呼吸 / 漂浮等待机动画
    liveliness: float = 1.0         # 活泼程度，影响待机与互动幅度
    lock_pos: bool = False
    edge_cling: bool = True         # 拖到屏幕左右边缘时贴边抓住
    gaze_follow: bool = True        # 视线跟随鼠标
    sound_enabled: bool = True      # 互动音效总开关（关闭后完全静音、零播放开销）
    sound_volume: float = 0.7       # 音效音量 0.0~1.0
    left_click: str = "open"
    auto_effect: bool = True        # 定时随机互动
    auto_effect_min: int = 8
    pos: list[int] | None = field(default=None)
    workbuddy_path: str = ""
    seen_intro: bool = False
    last_effect: str = "box"
    bubble_hint: bool = True

    # ---------------- Buddy 图鉴（增量，架构文档 §10.1） ----------------
    selected_buddy_id: str = "default"    # 当前形象
    selected_skin_id: str = "default"     # 当前皮肤
    gallery_base_url: str = ""            # 云端地址（含路径）；非 http(s) 一律视为未配置
    gallery_auth_ref: str = ""            # 凭据引用名（环境变量名 / credentials.json 的 key）
    gallery_cache_ttl_h: int = 24         # 缓存 TTL（小时），1~720
    gallery_last_sync: str = ""           # 上次成功同步时间（ISO8601，仅展示冗余）
    gallery_auto_sync: bool = True        # 启动时是否异步拉取一次

    # ---------------- 新增能力（全局热键 / 音乐感知 / 拖放 / 小游戏） ----------------
    hotkey_enabled: bool = True           # 是否启用全局快捷键
    hotkey_toggle: str = DEFAULT_HOTKEY   # 唤出/隐藏的组合键（形如 "Ctrl+Alt+B"）
    music_aware: bool = True              # 是否感知系统音频/音乐并做出反应
    last_drop_path: str = ""              # 最后一次拖入的文件（仅记录，不复制）
    drop_count: int = 0                   # 累计拖入文件数
    rps_wins: int = 0                     # 石头剪刀布：胜
    rps_losses: int = 0                   # 石头剪刀布：负
    rps_draws: int = 0                    # 石头剪刀布：平

    # ---------------- 空闲自动漫游 ----------------
    roam_enabled: bool = True             # 无操作一段时间后，Buddy 自己满屏溜达
    roam_idle_min: int = 3                # 判定「人不在」的空闲时长（分钟，1~30）
    roam_speed: int = 78                  # 漫游速度（px/s，30~180）
    roam_return_home: bool = True         # 人回来后小跑回原位；否则就地停住
    roam_effects: bool = True             # 漫游途中限频的台词 / 小特效

    def clamp(self) -> "Settings":
        self.size = int(max(120, min(360, self.size)))
        self.opacity = float(max(0.35, min(1.0, self.opacity)))
        self.liveliness = float(max(0.2, min(1.8, self.liveliness)))
        self.auto_effect_min = int(max(1, min(180, self.auto_effect_min)))
        if self.left_click not in LEFT_CLICK_CHOICES:
            self.left_click = "open"
        if self.pos and (not isinstance(self.pos, list) or len(self.pos) != 2):
            self.pos = None
        # ---- 图鉴字段（架构文档 §10.2） ----
        self.gallery_cache_ttl_h = int(max(1, min(720, self.gallery_cache_ttl_h)))
        url = (self.gallery_base_url or "").strip()
        self.gallery_base_url = url if url.startswith(("http://", "https://")) else ""
        self.gallery_auth_ref = (self.gallery_auth_ref or "").strip()
        self.selected_buddy_id = (self.selected_buddy_id or "default").strip() or "default"
        self.selected_skin_id = (self.selected_skin_id or "default").strip() or "default"
        self.gallery_last_sync = str(self.gallery_last_sync or "").strip()
        self.gallery_auto_sync = bool(self.gallery_auto_sync)
        # ---- 音效字段 ----
        self.sound_volume = float(max(0.0, min(1.0, self.sound_volume)))
        # ---- 新增能力字段 ----
        self.hotkey_enabled = bool(self.hotkey_enabled)
        self.music_aware = bool(self.music_aware)
        self.hotkey_toggle = str(self.hotkey_toggle or "").strip() or DEFAULT_HOTKEY
        self.last_drop_path = str(self.last_drop_path or "")
        self.drop_count = max(0, int(self.drop_count))
        self.rps_wins = max(0, int(self.rps_wins))
        self.rps_losses = max(0, int(self.rps_losses))
        self.rps_draws = max(0, int(self.rps_draws))
        # ---- 空闲自动漫游 ----
        self.roam_enabled = bool(self.roam_enabled)
        self.roam_idle_min = int(max(1, min(30, self.roam_idle_min)))
        self.roam_speed = int(max(30, min(180, self.roam_speed)))
        self.roam_return_home = bool(self.roam_return_home)
        self.roam_effects = bool(self.roam_effects)
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load() -> Settings:
    path = config_path()
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return Settings()
    known = {f for f in Settings().__dataclass_fields__}
    data = {k: v for k, v in raw.items() if k in known}
    try:
        return Settings(**data).clamp()
    except Exception:
        return Settings()


def save(cfg: Settings) -> None:
    try:
        config_path().write_text(
            json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
