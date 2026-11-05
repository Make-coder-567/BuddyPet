# 增量架构设计｜Buddy 图鉴联动与皮肤切换

> 需求基线：`docs/prd_buddy_gallery.md`（v0.1）。本文只描述**增量**，存量能力（待机眨眼 / 视线跟随 / 四边贴边抓握 / 拖拽 / 10 个特效 / 设置面板 / 托盘 / 单实例命令通道）视为不可破坏契约。
> 项目：`C:/Users/mrhua/Desktop/桌面宠物`｜Python 3.13+ / PyQt5 5.15+｜**不新增任何第三方依赖**（现有仅 `PyQt5` + `pillow`，本期连 pillow 都不需要）。

---

## 0. 一页结论（先给裁决，后给论证）

| # | 关键裁决 | 理由（一句话） |
| --- | --- | --- |
| D1 | **换肤 = 新建一个 `BuddySprites` 实例并整体替换**，不做"原地改目录" | 五级缩放缓存天然清零，`FileNotFoundError` 在建实例时抛出，天然具备原子性与可回滚性 |
| D2 | **`assets.py` 的画布尺寸从模块常量改为实例字段**（`canvas_w/h` 由 `buddy_0.png` 实测得出） | 否则跨尺寸皮肤会把 `width_for()` 算错 → 立绘、遮罩、贴边全错位 |
| D3 | **网络用 `QNetworkAccessManager` 异步 + 主线程，不用 QThread / QThreadPool** | `QPixmap` 只能在 GUI 线程构造；异步天然满足"启动不等待"，且无需跨线程信号同步 |
| D4 | **设置面板必须新增 `QScrollArea`**（现有面板是裸 `QVBoxLayout`，无滚动容器） | 加完图鉴分组后卡片高度将超过 1080p 可用高度，不重构则按钮被截断 |
| D5 | **图鉴选择是"立即提交"语义**：点「应用」立刻落盘并换肤，不受面板「保存/取消」管辖 | 否则 `reject()` 会用旧 `selected_*` 覆盖已生效皮肤 → 配置与画面不一致 |
| D6 | **离线优先靠"内置清单 + `assets/skins/*/` 目录约定"**，不依赖任何云端凭据即可完成换肤闭环与验收 | 凭据缺失是既定事实（PRD F5 / Q1~Q3），验证不能等接口 |
| D7 | **本期只落盘元数据缓存（JSON），云端图片一律不落盘** | Q9 版权未定；用 `IMAGE_POLICY = "memory_only"` 常量集中占位，放开时改一处 |

---

## 1. 模块划分

### 1.1 新增文件（12 个 py + 2 个 md）

| 相对路径 | 单一职责 | 关键约束 |
| --- | --- | --- |
| `app/gallery/__init__.py` | 只做导出：`Buddy` `Skin` `GallerySnapshot` `SourceKind` `SkinStatus` `ValidationResult` `GalleryService` `SkinSwitcher` | **不写逻辑**，避免循环 import |
| `app/gallery/models.py` | 全部 dataclass + 枚举 + `REASON_TEXT` 文案表 | 纯数据，**不 import PyQt5**（可被单测直接 import） |
| `app/gallery/builtin.py` | 随包内置 Buddy 清单、官方默认 Buddy 常量、`assets/skins/*/` 扫描 | 唯一知道"默认素材在 `assets/frames`"的地方 |
| `app/gallery/cache.py` | `GalleryCache`：读写 `gallery_cache.json`、TTL 判定、损坏容错、原子写 | 唯一碰缓存文件的模块 |
| `app/gallery/adapter.py` | 云端原始 dict → `Buddy/Skin` 的字段映射（候选键表） | 隔离 Q3 未知字段结构 |
| `app/gallery/cloud.py` | `CloudClient`：QNAM 请求、超时、鉴权头、错误码归一 | 隔离 Q1/Q2 |
| `app/gallery/validator.py` | `validate_skin()`：素材完整性校验，输出 `ValidationResult` | 只用 `QImage`（不用 `QPixmap`），不依赖 GUI |
| `app/gallery/provider.py` | 四层降级链编排 + `_finalize()` 结构统一 | 保证任一层产出同构 |
| `app/gallery/service.py` | `GalleryService(QObject)`：门面，持 NAM、发信号、`snapshot()` 内存快照 | UI 只跟它打交道 |
| `app/gallery/switcher.py` | `SkinSwitcher`：切换事务、回滚、写配置、气泡 | 唯一允许替换 `pet.sprites` 的地方 |
| `app/gallery/ui_widgets.py` | `BuddyTile` / `SkinRow` / `SkinPreview` / `StatusBar` / `Badge` | 只画控件，不含业务 |
| `app/gallery/ui_section.py` | `GallerySection(QWidget)`：控件树装配、状态机、与 service/switcher 连线 | 面板内的一个分组卡片 |
| `docs/skin_manifest.md` | 皮肤包规范（R17） | 与 `validator.py` 规则逐条对应 |
| `docs/arch_buddy_gallery.md` | 本文 | — |

> 包位置说明：现有 `app/` 是扁平结构（12 个模块全在根），因此**只新增 `app/gallery/` 一个业务子包**，不再造 `app/ui/` 空壳包，`ui_*` 两个文件直接放在 `app/gallery/` 内。

### 1.2 改动存量文件清单（精确到函数/类）

#### `app/assets.py`（4 处）
| 位置 | 改什么 |
| --- | --- |
| `BuddySprites.__init__` | 签名改为 `(self, root: Path, frames_dir: Path \| None = None)`；`frame_dir = Path(frames_dir) if frames_dir else Path(root) / "assets" / "frames"`；保存 `self.frames_dir`；读完 `raw` 后新增 `self.canvas_w = self.raw[0].width() or BASE_W`、`self.canvas_h = self.raw[0].height() or BASE_H` |
| `BuddySprites.width_for` | `round(height * self.canvas_w / self.canvas_h)`（原用模块常量 `BASE_W/BASE_H`） |
| `BuddySprites.has_gaze` | 加严：除 `base_raw/eye_raw/eye_meta` 外，还要 `_eye_meta_ok()`（校验 `size` 为正、`eyes.left/right` 的 x/y/w/h 可转 int）。**现在 `eyes.json` 存在但字段残缺会崩在 `eye_rect()`** |
| 新增方法 | `def _eye_meta_ok(self) -> bool`、`def clear_caches(self) -> None`（清空 `_cache/_base_cache/_eye_cache/_grip_cache/_mask_cache` 五级）、`grip_width` 增加 `grip_raw is None or height==0` 兜底 |

> 模块级 `BASE_W/BASE_H/FRAME_COUNT` 保留（`config` 与旧手势不受影响），仅作为兜底默认值。

#### `app/config.py`（2 处）
| 位置 | 改什么 |
| --- | --- |
| `Settings` dataclass | 追加 §10 的 7 个字段（**必须进 dataclass，否则 `load()` 静默丢弃 → K11**） |
| `Settings.clamp` | 追加：`gallery_cache_ttl_h` 夹到 `1..720`；`gallery_base_url` `.strip()` 且非 `http://`/`https://` 开头则置 `""`；`selected_buddy_id/selected_skin_id` 空串兜底 `"default"` |

#### `app/settings_dialog.py`（3 处）
| 位置 | 改什么 |
| --- | --- |
| `SettingsDialog.__init__` | ① 签名追加 `gallery: GalleryService \| None = None, focus_section: str = ""`；② 头部下方插入 `QScrollArea`（§8.2 给出完整代码）；③ 原 `self.body.addWidget/addLayout(...)` 的全部既有 section **改为 `self.col.addWidget/addLayout(...)`**；④ 底部 footer 保留在 `self.body`（不随滚动） |
| `_row` / `_slider_row` | 末行 `self.body.addLayout(row)` → `self.col.addLayout(row)` |
| `_reset()` / `reject()` | 跳过清单从 `("pos","seen_intro")` 扩到 `GALLERY_OWNED_FIELDS`（§10.3），理由见 D5；`showEvent` 中若 `focus_section=="gallery"` 则 `QTimer.singleShot(0, self.gallery_section.scroll_into_view)`（新增 `showEvent`） |

#### `app/pet_window.py`（4 处）
| 位置 | 改什么 |
| --- | --- |
| 类头 | 新增信号 `sprites_changed = pyqtSignal()`、`gallery_requested = pyqtSignal()` |
| 新增方法 `set_sprites(self, sprites)` | 换肤唯一入口（§9.2 给出完整实现） |
| `_show_menu()` | 在 `act_settings`（"设置…"）**之后**插入 `act_gallery = QAction(icons.icon_star(), "切换 Buddy…", menu)` → `self.gallery_requested.emit()` |
| `paintEvent`（可选 P2） | 乘一个 `self._swap_fade`（0→1，180ms `QPropertyAnimation`）实现淡入（R18） |

#### `main.py`（5 处）
| 位置 | 改什么 |
| --- | --- |
| `PetApp.__init__` | 在 `self.pet` 之后创建 `self.gallery = GalleryService(cfg, ROOT)`、`self.switcher = SkinSwitcher(cfg, ROOT, self.pet)`；连接 `pet.gallery_requested → open_settings(section="gallery")`、`pet.sprites_changed → refresh_icon`；末尾 `QTimer.singleShot(0, self.gallery.startup_sync)`（**不等待结果**） |
| 新增 `refresh_icon()` | `self.icon = icons.app_icon(self.pet.sprites.raw[0]); self.tray.setIcon(self.icon); self.app.setWindowIcon(self.icon)` |
| `open_settings()` | 签名加 `section: str = ""`，透传给 `SettingsDialog(..., gallery=self.gallery, focus_section=section)` |
| `_build_tray()` | 在 `act_settings` 之后加托盘菜单项「切换 Buddy…」 |
| `handle_command()` | 新增命令：`gallery`（打开面板并定位分组）、`skin:<buddy_id>:<skin_id>`（直接应用，供 QA 自动化）、`gallery:refresh`、`gallery:dump`（把当前 `GallerySnapshot` 摘要写 `%APPDATA%/WorkBuddyPet/gallery_dump.json`，供 QA 断言） |

> **`assets.py` 之外的存量路径全部不变**：`main.py` 仍然 `BuddySprites(ROOT)`，默认皮肤 `asset_dir == ROOT/"assets"/"frames"`，行为与现在逐像素一致。

---

## 2. 数据模型（`app/gallery/models.py`）

```python
# ---------- 枚举 ----------
class SourceKind(str, Enum):
    CLOUD   = "cloud"      # L1
    CACHE   = "cache"      # L2
    BUILTIN = "builtin"    # L3
    DEFAULT = "default"    # L4

class SkinStatus(str, Enum):
    READY    = "ready"     # 可用
    DEGRADED = "degraded"  # 可降级（视线或抓边缺失）
    BROKEN   = "broken"    # 不可用（硬依赖缺失 / 尺寸不一致）

# ---------- 数据 ----------
@dataclass(frozen=True)
class Skin:
    id: str
    name: str
    buddy_id: str
    asset_dir: str | None = None      # 本地素材目录绝对路径；None = 云端资源尚未落地
    manifest: dict = field(default_factory=dict)   # 原始 manifest（frames / eyes / canvas）
    remote_ref: str = ""              # 预留：云端 zip / 包名（Q5 未定）
    is_default: bool = False
    packaged: bool = True             # True=随包/本地可用；False=仅云端可见（本期不可应用）

@dataclass(frozen=True)
class Buddy:
    id: str
    name: str
    owned: bool = True
    thumbnail: str = ""               # URL 或本地路径；空则用占位图
    skins: tuple[Skin, ...] = ()
    order: int = 0

@dataclass
class GallerySnapshot:
    source: SourceKind
    reason: str                        # 见 REASON_TEXT 的 key
    fetched_at: float = 0.0            # epoch 秒；0 表示未知
    buddies: list[Buddy] = field(default_factory=list)
    detail: str = ""                   # 追加说明（被丢弃条目数、异常摘要等）

    def buddy(self, buddy_id: str) -> Buddy | None: ...
    def skin(self, buddy_id: str, skin_id: str) -> Skin | None: ...

@dataclass
class ValidationResult:
    status: SkinStatus
    missing: list[str] = field(default_factory=list)   # 硬缺失（人类可读："buddy_3.png"）
    degraded: list[str] = field(default_factory=list)  # 降级项："视线跟随" / "抓边姿态"
    canvas: tuple[int, int] | None = None
    frames: int = 0
    has_gaze: bool = False
    has_grip: bool = False
    message: str = ""                                  # 一句话结论（直接上 UI）
```

### 2.1 与 `assets.py` 的解耦方式

- `Skin.asset_dir` 是**纯字符串路径**，`models.py` 完全不认识 `BuddySprites`。
- 只有 `switcher.py` / `validator.py` 把 `asset_dir` 换成 `Path` 并交给 `BuddySprites(root, frames_dir=...)`。
- `manifest` 只是 dict 透传，规则全部落在 `validator.py`；`assets.py` 依旧**只认目录里的文件名**（6 帧 + base + 眼 + eyes.json + grip），不读 manifest。这样云端将来给什么字段都不影响加载层。
- `packaged=False` 的皮肤（云端只有 URL、磁盘无素材）**不出现在可选列表**，只作「云端可见 / 本期不可应用」的灰色条目（Q5/Q9 落地后再开下载）。

### 2.2 原因码表（`REASON_TEXT`，`models.py` 内）

| reason | UI 文案（状态条 / 气泡） | 出自哪层 |
| --- | --- | --- |
| `ok` | 云端 · 更新于 {t} | L1 |
| `not_configured` | 尚未配置 WorkBuddy 云端连接，当前展示{层名} | 短路 |
| `connect_timeout` | 云端连接超时，正在使用{层名} | L1 失败 |
| `read_timeout` | 云端响应超时，正在使用{层名} | L1 失败 |
| `http_401` / `http_403` | 凭据无效或已过期，正在使用{层名} | L1 失败 |
| `http_5xx` | 云端服务暂不可用，正在使用{层名} | L1 失败 |
| `network_error` | 网络不可用，正在使用{层名} | L1 失败 |
| `parse_error` | 云端返回格式无法识别，正在使用{层名} | L1 失败 |
| `empty_payload` | 云端未返回任何形象，正在使用{层名} | L1 失败 |
| `cache_hit` | 离线缓存 · {t} | L2 |
| `cache_stale` | 离线缓存 · {t}（可能不是最新） | L2 |
| `cache_corrupt` | 缓存文件损坏，已回退内置清单 | L2 失败 |
| `cache_scope_mismatch` | 缓存属于其他账号/地址，已回退内置清单 | L2 失败 |
| `no_cache` | 内置清单 | L2 失败 → L3 |
| `builtin` | 内置清单 | L3 |
| `assets_missing` | 默认 Buddy（素材目录异常） | L4 |

---

## 3. 素材目录可配置化方案

### 3.1 签名与默认值

```python
class BuddySprites:
    def __init__(self, root: Path, frames_dir: Path | None = None) -> None:
        frame_dir = Path(frames_dir) if frames_dir is not None else Path(root) / "assets" / "frames"
        self.frames_dir = frame_dir
        self.raw = []
        for i in range(FRAME_COUNT):
            pm = QPixmap(str(frame_dir / f"buddy_{i}.png"))
            if pm.isNull():
                raise FileNotFoundError(f"缺少素材：{frame_dir / f'buddy_{i}.png'}")
            self.raw.append(pm)
        self.canvas_w = self.raw[0].width() or BASE_W
        self.canvas_h = self.raw[0].height() or BASE_H
        ...
```

- `main.py` 现有调用 `BuddySprites(ROOT)` **无需改动**，落到 `ROOT/assets/frames`，与今日完全一致（向后兼容）。
- 换肤调用 `BuddySprites(root, frames_dir=Path(skin.asset_dir))`。

### 3.2 画布尺寸

`width_for()` 改用实例字段：

```python
def width_for(self, height: int) -> int:
    return round(height * self.canvas_w / self.canvas_h)
```

这一步是 D2：`pet_window._metrics()` 用 `sprites.width_for(h)` 算窗口宽度，`_build_mask()` / `_paint_pet()` / `_snap_edge_position()` 全部依赖它。若不改，任何非 268×392 的皮肤都会出现立绘与遮罩错位。

### 3.3 五级缩放缓存清空的准确接口

```python
def clear_caches(self) -> None:
    """清空五级缩放缓存：帧 / 底板 / 眼睛 / 抓边 / 轮廓。"""
    self._cache.clear()          # pixmap(frame, h)
    self._base_cache.clear()     # base(h)
    self._eye_cache.clear()      # eye(i, h)
    self._grip_cache.clear()     # grip(h, mirrored)
    self._mask_cache.clear()     # mask(h)
```

**清空策略（两层，缺一不可）**：

1. **主策略**：`SkinSwitcher` 新建 `BuddySprites` 实例 → 五个 dict 天然为空 → 不存在"旧图残留"（K5）。旧实例随后 `old.clear_caches()` 释放显存。
2. **辅策略**：`PetWindow.set_sprites()` 必须同时清 `PetWindow` 自己的 `self._mask_cache`（`_edge_mask` 按 `(h, side)` 缓存，**这是第六处缓存，极易漏**）并把 `_mask_idle/_mask_air` 置 `None` 后由 `_apply_geometry()` 重建。

> 注意：`_edge_mask` 缓存是 `pet_window` 内部懒创建的（`getattr(self, "_mask_cache", None)`），不是 `assets.py` 的五级缓存，PRD R08 的"清空五级"必须**外加**这一处，否则贴边状态下换肤会保留旧轮廓遮罩。

---

## 4. Provider 降级链

### 4.1 接口（`app/gallery/provider.py`）

```python
class GalleryProvider:
    def __init__(self, cfg: Settings, root: Path) -> None: ...

    # L2 → L3 → L4，纯本地、同步、必然返回、永不抛
    def offline_snapshot(self, reason: str = "", force_refresh: bool = False) -> GallerySnapshot: ...

    # L1：把已拿到的原始响应归一；成功则写缓存；失败抛 CloudPayloadError
    def from_cloud(self, payload: bytes | dict) -> GallerySnapshot: ...

    # 结构统一闸门：所有层都必须过这里
    def _finalize(self, buddies: list[Buddy], source: SourceKind, reason: str,
                  fetched_at: float, detail: str = "") -> GallerySnapshot: ...
```

### 4.2 每层判定条件 / 返回 / source / reason

| 层 | 判定进入条件 | 返回 | source | reason |
| --- | --- | --- | --- | --- |
| **L1 云端** | `cfg.gallery_base_url` 非空 **且** 能解析到 token（`cloud.resolve_token()`） | `from_cloud()` 成功 → `_finalize(..., CLOUD, "ok", now)`，并 `cache.write(snapshot)`；然后 `cfg.gallery_last_sync = now_iso` | `cloud` | `ok` |
| | 未配置 base_url 或 token | 不进网络，直接 L2（`prefix="not_configured"`） | — | `not_configured` |
| | 连接 3s 超时 / 读取 5s 超时 | 进 L2 | — | `connect_timeout` / `read_timeout` |
| | HTTP 401/403 / 4xx / 5xx | 进 L2 | — | `http_<code>` |
| | 网络层 error（`reply.error()`） | 进 L2 | — | `network_error` |
| | 能解析但归一后 0 个 buddy | 进 L2 | — | `parse_error`（部分丢弃时 `detail` 记条数）/ `empty_payload`（原始就空） |
| **L2 缓存** | `cache.read()` 返回非 None **且** scope 匹配 | `_finalize(cache.buddies, CACHE, "cache_hit"` 或 `"cache_stale", cache.fetched_at)` | `cache` | `cache_hit`（TTL 内）/ `cache_stale`（已过期但仍是有效快照；`force_refresh` 失败时也允许） |
| | 文件不存在 | 进 L3 | — | `no_cache` |
| | JSON 解析失败 / 顶层结构非法 | 进 L3（可选把坏文件改名 `.corrupt`） | — | `cache_corrupt` |
| | scope 不匹配（base_url 或凭据变了） | 进 L3 | — | `cache_scope_mismatch` |
| **L3 内置** | 前两层均失败 | `builtin.snapshot(root)`：≥1 只官方 Buddy + `assets/frames` 默认皮肤 + `assets/skins/*/` 扫描出的随包皮肤 | `builtin` | `builtin` |
| **L4 默认** | L3 抛异常（`assets/frames` 的 6 帧缺失等） | `_finalize([builtin.DEFAULT_BUDDY], DEFAULT, "assets_missing", 0)` —— **仍返回同一结构**，由 UI 标注"默认 Buddy（素材目录异常）"，宠物本体走启动时的旧 `sprites`（启动成功说明素材本就齐备，此分支仅防御） | `default` | `assets_missing` |

### 4.3 "任一层产出同一结构"的保证

`_finalize()` 是唯一出口，做 5 件强制归一化：

1. `buddies` 按 `order, name` 稳定排序；
2. `id` 强转 `str` 并 `strip()`，重复 id 去重（保留首个）；
3. 每个 buddy **必须有且仅有 1 个 `is_default=True` 的 skin**：没有就取 `skins[0]` 标记为 default；`skins` 为空则合成一个 `Skin(id="default", name="默认", packaged=False)`；
4. `thumbnail` 保证是 str（空串允许）；
5. 至少 1 个 buddy（空则回填 `builtin.DEFAULT_BUDDY`），`source` 保持不变 —— 这样 UI 永远不会面"空图鉴"。

UI 只读 `snapshot.source`（决定状态条）与 `snapshot.buddies`，**从不关心 reason 之外的差异**。

---

## 5. 网络层与线程模型

### 5.1 选型：`QNetworkAccessManager`（主线程异步），**不用** QThread / QRunnable

| 方案 | 裁决 | 理由 |
| --- | --- | --- |
| `QThread` 子类 | ❌ | 需自己管 start/quit/wait 生命周期；结果必须 `QueuedConnection` 回主线程；子线程里**不能建 QPixmap**（缩略图必须在主线程），等于还要再跳一次线程 |
| `QRunnable` + `QThreadPool` | ❌ | 同上；且单次 JSON GET 的并发价值为零，纯增复杂度 |
| `QNetworkAccessManager` | ✅ | 事件驱动、天然非阻塞；`reply.finished` 在主线程事件循环里触发，`QPixmap` 可直接构造；"启动不等待结果"零成本达成；与 `QLocalServer` 命令通道共用同一事件循环，无竞争 |

**唯一适合后台化的部分**（`validator` 的文件扫描）实测为 7 个 `QImage` 头解析 + 1 个 JSON 读，< 10ms，**明确不线程化**（避免过度设计）。

### 5.2 超时（PRD 5.1 建议值落地）

不用 `QNetworkRequest.setTransferTimeout`（5.15 才引入，低版本会 AttributeError），改用 `QTimer` + `abort()`，兼容 5.9+：

```python
CONNECT_TIMEOUT_MS = 3000
READ_TIMEOUT_MS = 5000

self._reply = self._nam.get(req)
self._got_bytes = False
self._connect_timer = QTimer(self); self._connect_timer.setSingleShot(True)
self._connect_timer.timeout.connect(self._on_connect_timeout); self._connect_timer.start(CONNECT_TIMEOUT_MS)
self._reply.downloadProgress.connect(self._on_progress)   # 首次 >0 → self._got_bytes = True
self._reply.finished.connect(self._on_finished)

def _on_connect_timeout(self):
    if self._got_bytes or self._reply is None:
        return
    self._timed_out = "connect_timeout"
    self._reply.abort()            # abort 也会发 finished，统一在那里收口

def _on_progress(self, recv, total):
    if recv > 0 and not self._got_bytes:
        self._got_bytes = True
        self._connect_timer.stop()
        self._read_timer.start(READ_TIMEOUT_MS)   # 3s 已过、开始读 → 换 5s 读超时

def _on_finished(self):
    self._connect_timer.stop(); self._read_timer.stop()
    if self._timed_out:  reason = self._timed_out
    elif self._reply.error() != QNetworkReply.NoError: reason = "network_error"（或从 HTTP status 取 http_<code>）
    else: 交给 provider.from_cloud(bytes(self._reply.readAll()))
    self._reply.deleteLater(); self._reply = None
```

超时值集中为 `cloud.py` 顶部常量，便于 QA 用 `gallery_cache_ttl_h` 之外的开关不复现网络问题时也能调。

### 5.3 信号回主线程

`GalleryService(QObject)` 定义 3 个信号，全部由主线程发出（因为 NAM 在主线程）：

```python
snapshot_ready = pyqtSignal(object)   # GallerySnapshot
sync_state = pyqtSignal(str)          # "idle" | "loading" | "failed"
```

`GallerySection` 在 `__init__` 里 `service.snapshot_ready.connect(self._on_snapshot)`。用 `object` 而非自定义类型注册，避免 `pyqtSignal` 类型注册顺序问题。

### 5.4 启动"不等待结果"的准确时序

```
PetApp.__init__
  ├── config.load()
  ├── BuddySprites(ROOT)                 ← 本地，~30ms
  ├── PetWindow(...).show()              ← 宠物立即可见
  ├── GalleryService(cfg, ROOT)          ← 不联网、不读盘（懒）
  └── QTimer.singleShot(0, gallery.startup_sync)   ← 让出一次事件循环，先完成首帧绘制

GalleryService.startup_sync()  [0ms，同步完成，绝不阻塞]
  ├── if not cfg.gallery_auto_sync: emit(offline_snapshot()); return
  ├── snap = provider.offline_snapshot()        # 缓存命中 → CACHE；否则 BUILTIN
  ├── emit(snapshot_ready, snap)                # UI 立刻有货
  └── if snap.source is not CACHE or cache 已过期:
          self.refresh(force=False)             # 异步，结果再 emit 一次
```

结论：宠物显示与图鉴可用性**均与网络无关**；网络最坏情况 8s 后才补一次 `snapshot_ready`，UI 早已渲染完成（满足 R03 "3s 内产出可用清单"、"不阻塞界面"）。

---

## 6. 缓存设计

### 6.1 路径

```python
# app/gallery/cache.py
from ..config import config_dir
def cache_path() -> Path:      return config_dir() / "gallery_cache.json"   # %APPDATA%/WorkBuddyPet/
def credential_path() -> Path: return config_dir() / "credentials.json"     # 仅鉴权引用，见 §12-Q2
```
与 `config.py::config_dir()` 复用同一目录，保证"缓存写入 `%APPDATA%/WorkBuddyPet/`"（R04）。

### 6.2 结构

```json
{
  "version": 1,
  "fetched_at": 1762260000.0,
  "source": "cloud",
  "scope": "9f2c1ab34d5e",
  "buddies": [
    {
      "id": "buddy_a", "name": "小橙", "owned": true, "order": 0,
      "thumbnail": "https://.../a.png",
      "skins": [
        {"id": "default", "name": "默认", "is_default": true,
         "asset_dir": null, "manifest": {}, "remote_ref": "buddy_a/default.zip"}
      ]
    }
  ]
}
```

- **只存元数据与 URL，不存任何二进制**（Q9 未定）。估算 50 只 Buddy × 3 皮肤 ≈ 60KB。
- `scope = sha1(f"{base_url}|{auth_ref}")[:12]`：解决 Q6（换账号/换地址后不误用旧缓存）。

### 6.3 TTL 判定

```python
def is_fresh(self, ttl_h: int) -> bool:
    return (time.time() - self.fetched_at) <= max(1, int(ttl_h)) * 3600
```
- `startup_sync` / 打开面板：TTL 内命中 → 直接 `CACHE`，**不联网**（R04）。
- `refresh(force=True)`：忽略 TTL，强制走云端；云端失败仍回退读缓存，但 reason 记 `cache_stale`。
- TTL 来源 `cfg.gallery_cache_ttl_h`（默认 24h）。

### 6.4 损坏容错

```python
def read(self) -> GalleryCache | None:
    try:
        raw = json.loads(cache_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None                                  # → no_cache
    except Exception:
        self._quarantine()                           # 改名 gallery_cache.json.corrupt（忽略失败）
        return None                                  # → cache_corrupt
    if raw.get("version") != 1 or not isinstance(raw.get("buddies"), list):
        return None
    if raw.get("scope") != self.scope():
        return None                                  # → cache_scope_mismatch
    buddies = [b for b in (self._buddy(b) for b in raw["buddies"]) if b]   # 逐条容错，坏的丢弃
    if not buddies:
        return None
    return GalleryCache(...)
```
写盘用 `tmp + os.replace` 原子替换；`write()` 全部包在 `try/except: pass`，缓存失败永不影响主流程。

---

## 7. 校验器（`app/gallery/validator.py`）

### 7.1 输入 / 输出

```python
def validate_skin(skin: Skin, use_cache: bool = True) -> ValidationResult
```
- 输入 `skin.asset_dir`（`None` → 直接 `BROKEN`，`missing=["云端皮肤尚未下载"]`）。
- 只用 `QImage`（非 `QPixmap`）读尺寸，**可在任意线程调用**；结果按 `(asset_dir, dir_mtime)` 做进程内 LRU（上限 32），避免网格每次重绘都扫盘。

### 7.2 规则（逐条对齐 PRD 7.2）

| # | 检查 | 结果 |
| --- | --- | --- |
| V1 | 目录存在且是目录 | 否则 `BROKEN`，`missing=["素材目录缺失"]` |
| V2 | `buddy_0..5.png` 存在 **且** `QImage` 可加载 **且** 尺寸 > 0 | 否则 `BROKEN`，`missing += [缺失/损坏的文件名...]`（逐个列出，R15） |
| V3 | **尺寸一致性**：`base`、`grip`、6 帧四者宽高全部相等 | 否则 `BROKEN`，`missing += ["尺寸不一致 268×392 ≠ 300×400"]`，`canvas=(268,392)` |
| V4 | `buddy_base.png` + `eye_l.png` + `eye_r.png` + `eyes.json` 齐备；`eyes.json` 的 `size == canvas`；`eyes.left/right` 的 `x/y/w/h` 均可转 int 且 `w,h > 0` | 缺失/非法 → 累加降级项 `"视线跟随"`（不改 status 到 BROKEN） |
| V5 | `buddy_grip.png` 存在 | 缺失 → 累加降级项 `"抓边姿态"` |
| V6 | `frames.json` / `manifest.json` 存在 | 缺失不降级，`message` 追加「元数据缺失，按 6 帧/首帧尺寸推断」（R17 建议级） |
| — | 定级 | 有 `missing` → `BROKEN`；有 `degraded` → `DEGRADED`；否则 `READY` |

输出字段：

| 字段 | 用法 |
| --- | --- |
| `status` | 决定「应用」按钮可用性、皮肤行置灰 |
| `missing` | 皮肤行右侧红色文案「缺 buddy_3.png」；BROKEN 时 tooltip 全量 |
| `degraded` | 皮肤行橙色 badge「可降级：无抓边姿态」；应用后气泡追加（PRD 8.4） |
| `canvas` / `frames` | 预览区显示「268×392 · 6 帧」 |
| `has_gaze` / `has_grip` | 预览区「视线跟随 ✓/✕ · 抓边姿态 ✓/✕」 |
| `message` | 一句话结论 |

### 7.3 与 `assets.py` 的一致性
- V2 与 `BuddySprites.__init__` 的 `FileNotFoundError` 判据**完全同源**（同 6 个文件名）→ 校验通过即意味着构造不会抛。
- V4/V5 的判据与 `has_gaze` / `has_grip` 同源（含 §1.2 加严后的 `_eye_meta_ok`）→ 校验结论与运行时能力一致，不会"说可用但视线失效"。

---

## 8. UI 集成

### 8.1 分组控件树（`app/gallery/ui_section.py`）

```
GallerySection(QWidget, objectName="GallerySection")
├─ QLabel#Section "Buddy 图鉴"
├─ StatusBar(QWidget#GalleryStatus)                       ← 状态条
│   ├─ QLabel#GallerySource   "内置清单 · 更新于 --"
│   ├─ QLabel#Sub             "尚未配置 WorkBuddy 云端连接，当前展示内置清单"（可隐藏）
│   └─ QPushButton#Ghost      "刷新图鉴"  ← 右对齐
├─ QHBoxLayout  body
│   ├─ QVBoxLayout left (固定宽 226)
│   │   ├─ QGridLayout buddyGrid  3 列：BuddyTile × N       ← 形象区
│   │   └─ QVBoxLayout skinList   SkinRow × N              ← 皮肤区（随形象联动）
│   └─ QVBoxLayout right (固定宽 168)                      ← 预览区
│       ├─ SkinPreview(132×192, objectName="PreviewBox")
│       ├─ QLabel#Sub  "小橙 · 默认"
│       ├─ QLabel#Sub  "视线跟随 ✓ · 抓边姿态 ✓"
│       └─ QLabel#Sub  "268×392 · 6 帧" / 缺失项红色文案
└─ QHBoxLayout actionRow                                   ← 操作区
    ├─ QPushButton#Primary "应用"
    ├─ QPushButton#Ghost   "取消"
    └─ QPushButton#Ghost   "恢复默认"
```

控件由 `ui_widgets.py` 提供：
- `BuddyTile(QWidget)`：`set_buddy(buddy, selected)`，`clicked` 信号；64×64 缩略图 + 8.5pt 名字 + 「已获得」角标；不可用时 `setEnabled(False)` 且 `paintEvent` 用 `opacity 0.42` 画。
- `SkinRow(QWidget)`：`set_skin(skin, vr, selected)`，`clicked` 信号；左缩略图 28×40，右侧名称 + `Badge`（默认/可用/可降级/不可用）+ 缺失项小字。
- `SkinPreview(QWidget)`：持一个 `BuddySprites`（预览专用，height=128），`set_source(sprites|None)`；`blink()` 播一次眨眼；无素材时画占位灰块 + 「素材不可用」。
- `StatusBar(QWidget)`：`set_status(text, sub, can_refresh)`。

### 8.2 设置面板滚动容器（必须做，D4）

`settings_dialog.py::SettingsDialog.__init__` 中，头部与 divider 之后插入：

```python
from PyQt5.QtWidgets import QScrollArea

self.scroll = QScrollArea(self.card)
self.scroll.setWidgetResizable(True)
self.scroll.setFrameShape(QFrame.NoFrame)
self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
self.scroll.viewport().setAutoFillBackground(False)
self.scroll.viewport().setStyleSheet("background: transparent;")
self.scroll.setStyleSheet(
    "QScrollArea{background:transparent;border:none;}"
    "QScrollBar:vertical{width:6px;background:transparent;margin:0px;}"
    "QScrollBar::handle:vertical{background:rgba(255,255,255,0.16);border-radius:3px;min-height:28px;}"
    "QScrollBar::handle:vertical:hover{background:rgba(255,255,255,0.28);}"
    "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0px;}"
    "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical{background:transparent;}"
)
_avail = QApplication.primaryScreen().availableGeometry().height()
self.scroll.setFixedHeight(max(320, min(600, _avail - 300)))
self.content = QWidget()
self.content.setStyleSheet("background: transparent;")
self.col = QVBoxLayout(self.content)
self.col.setContentsMargins(0, 0, 8, 0)
self.col.setSpacing(9)
self.scroll.setWidget(self.content)
self.body.addWidget(self.scroll)
```

随后：
- 所有既有 section（外观 / 交互 / 系统）的 `self.body.addWidget(...)`、`self.body.addLayout(...)` → `self.col.*`；
- `_row()` / `_slider_row()` 内部 `self.body.addLayout(row)` → `self.col.addLayout(row)`；
- `self.gallery_section = GallerySection(...)` → `self.col.addWidget(self.gallery_section)`，**位置：外观之前**（图鉴是本次主角，优先级最高，避免用户找）；
- footer（恢复默认/取消/保存）**留在 `self.body`**，恒可见。

新增 `showEvent`（注意：`settings_dialog.py` 顶部 `from PyQt5.QtCore import ...` 需追加 `QTimer`，`from PyQt5.QtWidgets import ...` 需追加 `QScrollArea`）：
```python
def showEvent(self, event):
    super().showEvent(event)
    if self._focus_section == "gallery":
        self._focus_section = ""
        QTimer.singleShot(0, self.gallery_section.scroll_into_view)
```
`GallerySection.scroll_into_view()` → `self.parent_scroll.ensureWidgetVisible(self, 0, 12)`（由 `SettingsDialog` 注入 `self.gallery_section.set_scroll_parent(self.scroll)`）。

### 8.3 懒加载 vs 打开即加载

**打开即渲染内存快照，绝不触发网络**：

```python
# GallerySection.__init__ 末尾
self._render(self.service.snapshot())      # snapshot() 首次调用 → provider.offline_snapshot()，纯本地 <10ms
self.service.snapshot_ready.connect(self._on_snapshot)   # 之后云端结果到达则热更新
```
- 打开面板 = 读一次内存/缓存，0 网络等待（R03 / US3）。
- 网络只在这三种情形发生：① 启动 `startup_sync`；② 点「刷新图鉴」；③ `startup_sync` 发现缓存过期。
- 缩略图（R16）**按 tick 懒加载**：`BuddyTile` 首帧先画占位，`QTimer.singleShot(0)` 之后异步 `QNAM` 拉取，失败保持占位。缩略图只进内存 dict（`{url: QPixmap}`，上限 60 条），**不落盘**（Q9）。

### 8.4 预览眨眼（`QTimer` 驱动）

```python
class SkinPreview(QWidget):
    BLINK_SEQ = (1, 2, 3, 4, 5, 4, 3, 2, 1)
    def _schedule(self):
        self._delay = QTimer(self); self._delay.setSingleShot(True)
        self._delay.timeout.connect(self.blink)
        self._delay.start(random.randint(2400, 5200))
    def blink(self):
        self._step = 0
        self._tick = QTimer(self); self._tick.setInterval(45)
        self._tick.timeout.connect(self._next_frame); self._tick.start()
    def _next_frame(self):
        self._step += 1
        if self._step >= len(self.BLINK_SEQ):
            self._tick.stop(); self._tick.deleteLater(); self._tick = None
            self._frame = 0; self.update(); self._schedule(); return
        self._frame = self.BLINK_SEQ[self._step]; self.update()
    def set_source(self, sprites):      # 切皮肤时
        self._stop_all(); self._sprites = sprites; self._frame = 0; self.update()
        if sprites is not None: self._schedule()
    def hideEvent(self, e):  self._stop_all(); super().hideEvent(e)   # 面板关闭不再空转
```
`paintEvent` 里 `pm = self._sprites.pixmap(self._frame, 128)`；`sprites is None` → 占位。
预览用 `BuddySprites` 复用现有缩放逻辑，**不重复实现**；按 `asset_dir` 做 4 条 LRU，避免反复构造。

### 8.5 禁用态与置灰规则

| 控件 | 规则 |
| --- | --- |
| `BuddyTile` | `owned=False` 或 `skins` 空 → `setEnabled(False)`、0.42 透明、tooltip「未获得」 |
| `SkinRow` | `vr.status == BROKEN` → 置灰不可选，右侧红字 `缺失：buddy_3.png`；`DEGRADED` → 可选，橙色 badge「可降级」+ tooltip 列出 `degraded`；`READY` → 可选 |
| `applyBtn` | 仅当 `selection != current` **且** 选中皮肤 `status != BROKEN` **且** `not switcher.is_busy()` |
| `cancelBtn` | 仅当 `selection != current`（dirty） |
| `resetBtn` | 仅当 `current != ("default","default")` |
| `refreshBtn` | 请求中 `setEnabled(False)` + 文案「刷新中…」，`sync_state` 回 `idle` 复原 |
| 整个 section | `switcher.is_busy()` 时 `setEnabled(False)`（防重复点击，R07 并发约束） |

### 8.6 右键菜单入口位置

`app/pet_window.py::PetWindow._show_menu()`，插在现有：

```python
act_settings = QAction(icons.icon_gear(), "设置…", menu)
act_settings.triggered.connect(self.settings_requested.emit)
menu.addAction(act_settings)
# ↓↓↓ 新增（紧跟其后，同一分隔段内）
act_gallery = QAction(icons.icon_star(), "切换 Buddy…", menu)
act_gallery.triggered.connect(self.gallery_requested.emit)
menu.addAction(act_gallery)
menu.addSeparator()
```
`icons.icon_star()` 已存在（`app/icons.py:167`），无需新增图标。托盘菜单同理加在 `main.py::_build_tray()` 的 `act_settings` 之后。

---

## 9. 切换事务与回滚

### 9.1 步骤序列（`SkinSwitcher.apply(buddy, skin)`）

| # | 步骤 | 失败处理 |
| --- | --- | --- |
| 0 | `if self._busy: return BUSY`；`self._busy = True`；`busy_changed.emit(True)`；UI 全部禁用 | — |
| 1 | **校验**：`vr = validator.validate_skin(skin)` | `BROKEN` → `failed.emit(skin, "素材不完整：" + missing)`，**不进入任何变更**，goto 9 |
| 2 | **存档快照**：`prev = (cfg.selected_buddy_id, cfg.selected_skin_id, pet.sprites, pet._edge, pet.cfg.pos[:])` | — |
| 3 | **建资源**：`new_sprites = BuddySprites(self.root, frames_dir=Path(skin.asset_dir))` | 抛 `FileNotFoundError`/任何异常 → rollback(goto 8)，reason=`载荷失败` |
| 4 | **切目录 + 清缓存 + 重建 + 重绘**：`pet.set_sprites(new_sprites)`（详见 9.2） | 异常 → rollback |
| 5 | **降级落地**：若 `not new_sprites.has_gaze` → 该皮肤自动无视线（`_update_gaze` 内已有 `has_gaze` 判断，`cfg.gaze_follow` **不改**，符合 7.4）；若 `not has_grip` 且 `pet._edge in ("left","right")` → `pet._snap_edge_position(silent=True)` 走通用轮廓分支 | — |
| 6 | **写配置**（放最后，保证"写盘即有画面"）：`cfg.selected_buddy_id = buddy.id; cfg.selected_skin_id = skin.id; cfg.gallery_last_sync` 不动；`config.save(cfg)` | 写盘失败被 `config.save` 内部吞掉，不回滚（下次启动回到旧皮肤，可接受） |
| 7 | **气泡**：`READY` → 「已切换为「{buddy}」·「{skin}」」；`DEGRADED` → 「已切换为「{skin}」，该皮肤不支持{缺失能力}」；受 `cfg.bubble_hint` 控制（`engine.say(text, 2.2)`）；`applied.emit(buddy, skin)` | — |
| 8 | **rollback(prev)**：`pet.set_sprites(prev_sprites)` → 恢复 `cfg.selected_*` → `config.save` → `engine.say("切换失败，已恢复上一形象", 2.4)` → `failed.emit(skin, reason)` | 若连回滚都抛异常 → 捕获并 `_finalize_error()`（保持当前内存状态、只打日志、不弹模态框） |
| 9 | `self._busy = False; busy_changed.emit(False)` | — |

**关键不变量**：`cfg.selected_*` 只在第 6 步被改写，且在第 3~5 步全部成功之后 → 磁盘配置永远不会指向一个加载不了的皮肤（K4/K5）。

### 9.2 `PetWindow.set_sprites()` 唯一实现

```python
def set_sprites(self, sprites: BuddySprites) -> None:
    old = self.sprites
    self.sprites = sprites
    self._mask_cache = {}          # 第六处缓存：贴边轮廓按 (h, side) 缓存，必须清
    self._mask_idle = None
    self._mask_air = None
    self.gaze.reset()              # 新皮肤眼位不同，视线归零防跳变
    self._apply_geometry(initial=False)   # 按新 width_for() 重排窗口 → 重建 mask → _apply_mask → _sync_overlay → 贴边重吸附
    self.update()
    if old is not None and old is not sprites:
        old.clear_caches()
    self.sprites_changed.emit()
```

`_apply_geometry(initial=False)` 已经覆盖：`setFixedSize`（新比例）→ `_clamp` → `_snap_edge_position(silent=True)`（`_edge` 存在时）→ `_build_mask(airborne=False/True)` → `_apply_mask` → `_sync_overlay`。因此**不需要**另写重绘逻辑。

### 9.3 恢复默认

`SkinSwitcher.apply_default()` → 从 `builtin.py` 取 `DEFAULT_BUDDY / DEFAULT_SKIN`（`asset_dir = root/"assets"/"frames"`）→ 走同一个 `apply()`。因为 `apply()` 末尾会写 `selected_*="default"`，「恢复默认」天然等于回到原始素材 + 配置复位（US5 / R06）。

### 9.4 取消 / 重复点击

- 「取消」：只在 UI 层把选择重置为 `service.current()`，**不碰 `pet` 与 `cfg`**（PRD 8.3-6「不产生任何变更」）。
- 重复点击：`_busy` 布尔锁 + UI 禁用双保险；`apply()` 入口第一行直接返回 `BUSY`。
- 网络刷新与换肤互斥吗？**不互斥**：`refresh` 只快照/缓存，不动 `pet`；`_busy` 只锁 `switcher`。

---

## 10. 配置字段定稿

### 10.1 `Settings` 新增字段（7 个，全部进 dataclass）

```python
    # ---------------- Buddy 图鉴（增量）
    selected_buddy_id: str = "default"    # 当前形象
    selected_skin_id: str = "default"     # 当前皮肤
    gallery_base_url: str = ""            # 云端地址占位，含路径；非 http(s) 一律视为未配置
    gallery_auth_ref: str = ""            # 凭据引用名（环境变量名 / credentials.json 的 key），不存明文
    gallery_cache_ttl_h: int = 24         # 缓存 TTL（小时），1~720
    gallery_last_sync: str = ""           # 上次成功同步时间（ISO8601 本地时间，仅展示冗余）
    gallery_auto_sync: bool = True        # 启动是否异步拉取一次（补充字段，隐私/流量开关）
```

| 字段 | 依据 | 说明 |
| --- | --- | --- |
| `selected_buddy_id` / `selected_skin_id` | PRD 8.5 R09 | 二元组持久化；形象变更时皮肤自动重置为该形象 default（由 UI 保证） |
| `gallery_base_url` | PRD 8.5 R02 | 空 = 未配置，短路降级，不报错 |
| `gallery_auth_ref` | PRD 8.5 R02 | **只存引用不存明文**（K2）。解析顺序见 §12-Q2 |
| `gallery_cache_ttl_h` | PRD 8.5 R04 | clamp 1..720 |
| `gallery_last_sync` | PRD 8.5 R14 | 状态条"更新于"的首选来源是缓存里的 `fetched_at`，此字段为缺失时兜底 |
| `gallery_auto_sync` | **新增** | 给用户/QA 一个"完全不联网"的硬开关；`False` 时 `startup_sync` 只发 offline 快照，`refresh` 仍可手动触发。零成本消除"启动即请求"的隐私顾虑（Q7） |

### 10.2 `clamp()` 追加

```python
self.gallery_cache_ttl_h = int(max(1, min(720, self.gallery_cache_ttl_h)))
u = (self.gallery_base_url or "").strip()
self.gallery_base_url = u if u.startswith(("http://", "https://")) else ""
self.selected_buddy_id = (self.selected_buddy_id or "default").strip() or "default"
self.selected_skin_id = (self.selected_skin_id or "default").strip() or "default"
self.gallery_auth_ref = (self.gallery_auth_ref or "").strip()
```

### 10.3 `GALLERY_OWNED_FIELDS`（`_reset()` / `reject()` 必须跳过）

```python
GALLERY_OWNED_FIELDS = (
    "selected_buddy_id", "selected_skin_id", "gallery_base_url",
    "gallery_auth_ref", "gallery_cache_ttl_h", "gallery_last_sync",
    "gallery_auto_sync", "pos", "seen_intro",
)
```
- `SettingsDialog._reset()`：现有 `if key in ("pos", "seen_intro"): continue` → 改为 `if key in GALLERY_OWNED_FIELDS: continue`。否则点「恢复默认」会把 `selected_*` 改回 `"default"` 但**画面没换** → 配置与显示不一致（且下次启动才"突然变回默认"）。
- `SettingsDialog.reject()`：`for key, value in self._snapshot.items(): setattr(...)` 同样跳过这批字段。因为「应用」是立即提交（D5），取消不该撤销已生效的换肤。

---

## 11. 实施任务分解（5 个任务，按依赖排序）

> 硬上限 5；每个任务 ≥3 个文件；T04 只依赖 T01+T02（UI 只需 models + provider 契约），可与 T03 并行。

### T01 基座：素材可配置化 + 配置字段 + 数据模型　P0
- **改** `app/assets.py`：`BuddySprites.__init__(root, frames_dir=None)`、`canvas_w/h`、`width_for()`、`_eye_meta_ok()`、`has_gaze` 加严、`clear_caches()`、`grip_width()` 兜底
- **改** `app/config.py`：7 个字段 + `clamp()`
- **建** `app/gallery/__init__.py`、`app/gallery/models.py`
- **验收口径**
  1. `BuddySprites(Path("."))` 仍加载 `assets/frames`，`width_for(212) == 145`（212*268/392 四舍五入）；
  2. `BuddySprites(Path("."), frames_dir=Path("assets/frames"))` 结果与上一行完全等价；
  3. `python -c "import app.config as c; cfg=c.load(); assert cfg.gallery_cache_ttl_h==24 and cfg.selected_buddy_id=='default'"`；
  4. 手改 `config.json` 加入 `{"selected_skin_id":"x"}` → `load()` 后值保留（证明字段已进 dataclass）；
  5. 造一个 `eyes.json` 只有 `{"size":[268,392]}` 的临时目录 → `has_gaze` 为 `False`（不抛异常）；
  6. `python -m compileall app` 通过。
- **依赖**：无

### T02 数据层：四层降级 + 缓存 + 内置清单 + 校验器　P0
- **建** `app/gallery/builtin.py`、`app/gallery/cache.py`、`app/gallery/adapter.py`、`app/gallery/cloud.py`、`app/gallery/validator.py`、`app/gallery/provider.py`
- **要实现**：`builtin.snapshot(root)`（含 `assets/skins/*/` 扫描）、`GalleryCache.read/write/is_fresh/scope`、`adapt_buddies(payload)`、`CloudClient.fetch(cfg, on_ok, on_err)`（QNAM + 3s/5s 超时 + `resolve_token()`）、`validate_skin()`、`GalleryProvider.offline_snapshot/from_cloud/_finalize`
- **验收口径**
  1. 无凭据（`gallery_base_url=""`）→ `provider.offline_snapshot().source == SourceKind.BUILTIN`，`buddies` ≥1，且首个 buddy 的 default skin `asset_dir` 指向 `assets/frames` 绝对路径；
  2. 手工写入合法 `gallery_cache.json` → `source == CACHE` 且 `reason == "cache_hit"`；把 `fetched_at` 改成 3 天前 → `reason == "cache_stale"`；
  3. 把缓存文件内容改成 `"{broken"` → `source == BUILTIN`、`reason` 含 `cache_corrupt`、**不抛异常**；
  4. `validate_skin(默认皮肤)` → `status=READY, has_gaze=True, has_grip=True, frames=6, canvas=(268,392)`；
  5. 复制 `assets/frames` 到临时目录并删掉 `buddy_3.png` → `status=BROKEN` 且 `"buddy_3.png" in missing`；再删 `buddy_grip.png` → `status=DEGRADED` 且 `"抓边姿态" in degraded`；再改 `eyes.json` 的 `size` 为 `[300,400]` → 出现 `"视线跟随" in degraded`；
  6. `_finalize` 对空列表 → 返回 ≥1 buddy；对无 default 皮肤的形象 → 自动标 1 个 default（结构统一性）。
- **依赖**：T01

### T03 服务与切换：门面 + 事务 + 主窗口/入口接入　P0
- **建** `app/gallery/service.py`、`app/gallery/switcher.py`
- **改** `app/pet_window.py`（`sprites_changed`/`gallery_requested` 信号、`set_sprites()`、`_show_menu` 入口）、`main.py`（service/switcher 构建、`refresh_icon()`、`open_settings(section=)`、托盘入口、新命令）
- **验收口径**
  1. 冷启动计时：从进程启动到宠物可见 < 3s，且 `gallery_base_url` 填一个不可达地址（如 `http://10.255.255.1/x`）时**不卡顿**、无模态错误框；
  2. `python main.py` 后发命令 `skin:default:default` → 无异常、画面正常、`config.json` 中 `selected_*` 为 `default`；
  3. 直接把 `assets/skins/test/`（`assets/frames` 的副本）存在时，`skin:default:test` 生效：窗口宽度按新比例重算、贴边遮罩正确、视线/眨眼正常；
  4. `skin:default:nonexistent` → 气泡「切换失败，已恢复上一形象」，`config.json` **不变**，进程不崩；
  5. 贴边（`edge:left`）状态下执行换肤 → 遮罩与新立绘对齐（无旧轮廓残留、无身体被裁）；
  6. `gallery:dump` 产出 `%APPDATA%/WorkBuddyPet/gallery_dump.json`，含 `source/reason/buddies`。
- **依赖**：T01、T02

### T04 设置面板：滚动容器重构 + 图鉴分组 UI　P0
- **建** `app/gallery/ui_widgets.py`、`app/gallery/ui_section.py`
- **改** `app/settings_dialog.py`（QScrollArea 重构、`gallery`/`focus_section` 参数、`showEvent`、`_reset`/`reject` 跳过 `GALLERY_OWNED_FIELDS`）
- **验收口径**
  1. 1080p 与 768p 下打开设置面板，「保存/取消」按钮**始终完整可见**，中间可滚动；
  2. 存量 13 个控件（大小/不透明度/活泼/左键/待机/定时/间隔/气泡/贴边/视线/置顶/锁定/路径）全部仍可操作并即时生效；
  3. 图鉴分组显示：状态条（含"未配置"引导文案）、形象网格、皮肤列表、预览、三个按钮；
  4. 预览区能自动播放一次眨眼（约 2.4~5.2s 内至少一次），关闭面板后 `QTimer` 停止（无后台空转）；
  5. BROKEN 皮肤行置灰、不可选中、右侧显示缺失项；选中该皮肤时「应用」为禁用；
  6. 从右键「切换 Buddy…」进入 → 面板自动滚动到图鉴分组（顶部可见）；
  7. 点「恢复默认」（面板底部）后 `selected_*` 保持不动（不被 `_reset` 重置）。
- **依赖**：T01、T02（契约：`GalleryService.snapshot()/refresh()/snapshot_ready`、`SkinSwitcher.apply()/apply_default()/is_busy`）
- **可并行**：与 T03 并行开发，接口按 §5.3 / §9 冻结

### T05 收尾：文案 / 降级提示 / 淡入 / 规范文档 / 回归　P1
- **改** `app/gallery/ui_section.py`（忙碌态、刷新态、`REASON_TEXT` 渲染、气泡文案）
- **改** `app/pet_window.py`（`_swap_fade` + 180ms `QPropertyAnimation`，R18；`paintEvent` 乘子）
- **建** `docs/skin_manifest.md`（R17）；**改** `README.md` 增补「图鉴 / 皮肤目录约定 / 命令通道新命令」
- **验收口径**
  1. PRD 8.4 五条气泡文案均可复现（成功 / 降级 / 刷新失败 / 未配置 / 应用失败），且 `bubble_hint=False` 时全部静默；
  2. 换肤过渡 150~250ms 淡入，无闪烁（R18）；
  3. 回归清单全绿：视线跟随（460px 触发 / 40px 死区）、待机眨眼节奏、四边贴边四种姿态、拖拽倾斜、10 个特效、大小/不透明度/活泼程度、单实例唤起、托盘、开机自启；
  4. `docs/skin_manifest.md` 的每条字段都能在 `validator.py` 找到对应校验项。
- **依赖**：T03、T04

---

## 12. 对 PRD 第 9 节 Q1~Q9 的架构裁决

| # | 问题 | 架构裁决（凭据缺失前提下也不受影响） | 落地位置 | 状态 |
| --- | --- | --- | --- | --- |
| **Q1** | base URL 与路径 | **现在定死**：`gallery_base_url` 直接存**完整 URL（含路径）**，`cloud.py` 不再拼接任何 path。默认约定 `{base}/buddy/gallery` 写进 `docs/skin_manifest.md` 作为示例。空/非 http(s) → `not_configured` 短路，**不进网络**。将来接口拆分（列表/详情两个接口）只需在 `cloud.py` 加第二个请求，`provider/adapter/UI` 全不改 | `cloud.py` | ✅ 已定死 |
| **Q2** | 鉴权方式与凭据获取 | **现在定死**：`gallery_auth_ref` 只存**引用名**。`resolve_token()` 顺序：① 环境变量（`gallery_auth_ref` 值本身作为环境变量名）；② `%APPDATA%/WorkBuddyPet/credentials.json` 的 `{auth_ref: token}`。请求头集中一行常量 `AUTH_HEADER = ("Authorization", "Bearer ")`，换 Cookie / 签名只改 `cloud.py` 一处。**不实现登录流程**；界面回显 `token[:3]+"***"`；日志不打印 token（K2）。**不读 WorkBuddy 本地登录态**（属"范围外"且不可靠） | `cloud.py::resolve_token` | ✅ 已定死（凭据补齐后零 UI 改动） |
| **Q3** | 响应字段结构 | **现在定死**：`adapter.py` 用**候选键表**归一，不假设字段名：<br>`id: ["id","buddy_id","buddyId"]`、`name: ["name","title","displayName"]`、`owned: ["owned","is_owned","obtained","unlocked"]`、`thumbnail: ["thumbnail","thumb","avatar","icon","image"]`、`skins: ["skins","appearances","skin_list"]`、skin 的 `asset: ["asset_dir","dir","path","local_path"]` + `remote: ["url","zip","package","remote_ref"]`。必填缺失 → 丢弃该条并累加到 `detail`；全丢 → `parse_error` 降级 | `adapter.py` | ✅ 已定死（键名可后配） |
| **Q4** | 图片 CDN | **现在定死**：缩略图走独立 `QNetworkAccessManager`（与 JSON 请求分离，避免 abort 互相影响）；失败/超时 3s 显示内置占位（灰底 + 首字）；防盗链导致的 403 同样只影响缩略图，**不影响列表与应用**。缩略图**只进内存 LRU（60 条）**，不落盘（等 Q9） | `ui_widgets.py::BuddyTile` | ✅ 可运行（R16 的"本地缩略图缓存"降级为内存缓存，标注依赖 Q9） |
| **Q5** | 皮肤资源格式 | ✅ **现在就能定死**（PRD 也给了建议）：一个皮肤 = 一个目录，含 `buddy_0..5.png`（6 帧，顺序即眨眼序）、可选 `buddy_base.png`/`eye_l.png`/`eye_r.png`/`eyes.json`/`buddy_grip.png`/`frames.json`/`manifest.json`。**画布尺寸由帧 0 决定，组内必须一致**（不强制 268×392，但默认资产仍是）；`eyes.json.size` 必须等于画布尺寸，`eyes.left/right.{x,y,w,h}` 相对画布左上角。zip/校验和留 `Skin.remote_ref` 占位 | `validator.py` + `docs/skin_manifest.md` | ✅ 已定死 |
| **Q6** | 已获得判定 / 多账号 | **现在定死**：`owned` 字段透传，UI 只允许 `owned=True`；单账号假设，**缓存 scope = sha1(base_url + auth_ref)**，凭据/地址一变旧缓存自动 `cache_scope_mismatch` → 回退内置，不会展示他人形象 | `cache.py::scope` | ✅ 已定死 |
| **Q7** | TTL / 体积 / 离线展示 | **现在定死**：TTL 24h（`gallery_cache_ttl_h`，1~720 可配）；快照体积仅元数据（典型 <100KB，写入上限 512KB，超出不写只警告）；**离线允许展示已获得清单**（来自 cache），状态条常驻标注来源 + 时间 + "可能不是最新"（K8）；`gallery_auto_sync=False` 可彻底关闭启动请求 | `cache.py` + `config.py` | ✅ 已定死 |
| **Q8** | 图鉴皮肤 vs 应用主题皮肤 | **现在定死**：完全不读 `~/.workbuddy/appearance-resources/`；图鉴只处理"立绘贴图包"；所有文案统一「形象 / 皮肤」，`docs/skin_manifest.md` 首段明示"这不是 CSS 主题皮肤"（K10） | 全链路 | ✅ 已定死 |
| **Q9** | 版权 / 落盘合规 | ⚠️ **必须留占位**：本期**只落盘元数据 JSON，云端图片一律不落盘**（缩略图内存、皮肤包不下载）。`cache.py` 顶部单点常量 `IMAGE_POLICY = "memory_only"`；`Skin.remote_ref` + `GalleryCache` 的 `version` 字段预留升级位。将来放开：改 `IMAGE_POLICY` + 在 `cache.py` 打开 `images/` 子目录分支 + `validator` 支持"先下载再校验"，UI 零改动 | `cache.py` | 🟡 占位（不阻塞离线闭环验收） |

**结论**：Q5/Q8 完全定死；Q1/Q2/Q3/Q4/Q6/Q7 用"适配层 + 常量集中 + 未配置短路"三种手段做到**凭据补齐后零 UI 改动**；只有 Q9 是真正的占位项，且已把影响限制在 `cache.py` 一个常量 + 一个分支里。

---

## 13. 风险与既有坑位（给工程师的"别踩"清单）

| # | 坑 | 触发条件 | 对策 |
| --- | --- | --- | --- |
| P1 | **第六处缓存**：`PetWindow._mask_cache`（贴边轮廓）不会被 `assets.py` 的五级缓存清空 | 在贴边状态下换肤 | `set_sprites()` 必须 `self._mask_cache = {}` + `_mask_idle/_mask_air = None` |
| P2 | `_reset()`/`reject()` 覆盖 `selected_*` 但没换肤 | 面板点「恢复默认」或「取消」 | 跳过 `GALLERY_OWNED_FIELDS`（§10.3） |
| P3 | `load()` 丢弃未知字段 | 忘记把字段加进 `Settings` | T01 验收第 4 条专门测这个 |
| P4 | 设置面板高度溢出 | 加完分组 | 必须做 §8.2 的 `QScrollArea` |
| P5 | `eye_rect()` 在 `eyes.json` 缺 `size`/`eyes` 时 KeyError 崩溃 | 残缺皮肤包 | `_eye_meta_ok()` 加严 + validator V4 |
| P6 | 缩略图 `QPixmap` 在子线程构造 → 崩溃 | 若将来引入线程 | 明令：`QPixmap` 只在主线程；后台只允许 `QImage` |
| P7 | `QNetworkReply` 被 GC 导致 `finished` 不触发 | 局部变量持有 | `self._reply` 必须存成实例属性 |
| P8 | 预览 `QTimer` 在面板关闭后继续跑 | `singleShot` 链未停 | `hideEvent` 里 `_stop_all()` |
| P9 | 换肤后托盘图标/关于窗口头像仍是旧图 | 图标在启动时算好 | `sprites_changed` → `PetApp.refresh_icon()`；关于窗口每次打开时重取 `raw[0]` |
| P10 | 尺寸不一致皮肤导致贴边穿帮 | 非 268×392 皮肤 | validator V3 直接判 BROKEN，不允许应用 |
| P11 | **存量接口事实核对（按代码，不按口头描述）**：`has_gaze` / `has_grip` 是 `@property`（**不是方法，调用时不能加括号**）；`assets.py` 里**没有 `frames()`**，6 帧列表是 `self.raw`，取某帧用 `pixmap(frame, height)`。写代码前务必以 `app/assets.py` 实际签名为准 | 照文档/口头描述写 `sprites.has_gaze()` | 统一用属性访问 `if self.sprites.has_gaze:`（`pet_window.py` 现有写法即如此，沿用不要改） |

---

## 14. 附：类图与调用流

- 类图：`docs/class-diagram.mermaid`
- 调用流：`docs/sequence-diagram.mermaid`

（两份文件与本节同批产出；正文 §1~§13 已包含其全部信息，mermaid 供 IDE 预览与后续维护使用。）
