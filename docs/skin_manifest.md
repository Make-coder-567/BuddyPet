# 皮肤包规范（Skin Manifest）

本文档定义桌面宠物「一套皮肤」在磁盘上的目录结构与素材约定，是
`app/gallery/validator.py` 校验逻辑的可读版，二者必须保持一致。新增或改造
皮肤素材时，以本规范为准。

## 1. 目录约定

皮肤素材统一放在：

```
assets/skins/<buddy_id>/<skin_id>/
```

- `<buddy_id>` 是形象标识（如 `default`），与 `app/gallery/models.py` 的 `Buddy.id` 对应。
- `<skin_id>` 是皮肤标识（如 `sakura`）。
- 官方默认形象的特殊目录：`assets/skins/default/` 下的子目录会**并入**官方默认形象
  的皮肤列表（见 `app/gallery/builtin.py` 的 `scan_skins` / `builtin_buddies`）。
- 仅含 `buddy_N.png` / `eyes.json` / `manifest.json` / `frames.json` 中任一文件的子目录才被识别为
  皮肤目录；空目录或垃圾目录会被忽略，不会污染图鉴。

## 2. 素材清单与分级

| 文件 | 级别 | 缺失后果 | 说明 |
|---|---|---|---|
| `buddy_0.png` … `buddy_5.png` | **硬依赖** | `FileNotFoundError`，皮肤不可用 | 6 帧眨眼序列，尺寸必须**彼此相等** |
| `buddy_base.png` | 硬依赖（与帧同尺寸） | 同上 | 去眼底板；尺寸必须与 6 帧**完全一致**，否则判 BROKEN |
| `eye_l.png` / `eye_r.png` | 视线依赖 | DEGRADED（无视线跟随） | 左右眼分层；尺寸任意 |
| `eyes.json` | 视线依赖 | 同上 | 眼位元数据，字段见 §3 |
| `buddy_grip.png` | 可选 | DEGRADED（无抓边姿态） | 专用抓边立绘；**尺寸任意**，不参与尺寸一致性检查 |
| `buddy_grip_base.png` | 抓边视线依赖 | 抓边姿态退化为静态整图 | 抓边立绘的去眼底板；尺寸须与 `buddy_grip.png` 一致 |
| `grip_eye_l.png` / `grip_eye_r.png` | 抓边视线依赖 | 同上 | 抓边立绘的左右眼分层 |
| `eyes_grip.json` | 抓边视线依赖 | 同上 | 抓边立绘眼位元数据，格式同 `eyes.json` |
| `frames.json` | 元数据（可选） | 回退到默认 6 帧 / 首帧尺寸推断 | 声明帧数与画布尺寸 |
| `manifest.json` | 元数据（可选） | 回退到目录名作为显示名 | 声明 `name` 等展示信息 |

> **尺寸一致性规则（V3）**：只对「6 帧 + `buddy_base.png`」四者做尺寸相等检查。
> `buddy_grip.png` 及其衍生素材**豁免**（它是高清素材，运行时按高度等比缩放）。
> 若 6 帧与底板尺寸不一致 → 判 `BROKEN`，`missing` 记录形如
> `"尺寸不一致 200×300 ≠ buddy_base.png=210×300"`。

## 3. 元数据格式

`eyes.json` 与 `eyes_grip.json` 结构相同：

```json
{
  "size": [268, 392],
  "eyes": {
    "left":  { "x": 92, "y": 122, "w": 30, "h": 55 },
    "right": { "x": 146, "y": 122, "w": 31, "h": 55 }
  }
}
```

- `size`：素材画布宽高（两正整数）。
- `eyes.left` / `eyes.right`：眼睛相对**素材左上角**的位置与尺寸，四个字段均为正整数的像素值。
- 字段残缺（缺 `size`、缺某只眼、`w/h ≤ 0`、`x/y/w/h` 无法转 `int`）→ 该套眼睛判定为不可用，
  **不会抛异常**（`has_gaze` / `has_grip_gaze` 返回 `False`，而不是崩在取字段时）。

`frames.json`（可选）：

```json
{ "frames": 6, "size": [268, 392] }
```

`manifest.json`（可选）：

```json
{ "name": "樱粉" }
```

## 4. 校验结果三档

`validate_skin()` 返回 `ValidationResult`，`status` 三档：

- `READY`（可用）：硬依赖齐备，可正常应用。
- `DEGRADED`（可降级）：硬依赖齐备，但缺视线或抓边素材——仍可应用，应用时会弹降级提示。
- `BROKEN`（不可用）：硬依赖缺失（缺帧 / 尺寸不一致）——按钮置灰，不可应用。

`can_apply = (status in {READY, DEGRADED})`。

## 5. 换肤运行期行为

- 换肤 = **新建一个 `BuddySprites` 实例整体替换**（见 `app/pet_window.py::set_sprites`），不做原地改目录。
  这天然清空五级缩放缓存与窗口自身的贴边轮廓缓存，且构建失败可原子回滚。
- 画布尺寸按 `BuddySprites.canvas_w / canvas_h`（由 `buddy_0.png` 实测）计算宽高比，
  跨尺寸皮肤窗口宽度会随之变化（`width_for()`）。
- 换肤后触发 180ms 淡入（`PetWindow._swap_fade`）。

## 6. 生成工具

- `tools/make_variant_skins.py`：基于官方立绘做**色相分带重映射**生成配色变体（白/黑/灰中性像素
  保持逐位不变），输出到指定目录。
- `tools/make_grip_eye_assets.py`：从 `buddy_grip.png` 自动检测眼位、生成 `buddy_grip_base.png`
  + `grip_eye_l.png` + `grip_eye_r.png` + `eyes_grip.json`（基于面罩亮度对比 + 连通块筛选，
  不依赖固定青色检测，适配各配色变体）。
- `tools/verify_variant_skins.py`：校验变体皮肤的 alpha 逐位保留、中性像素零污染、画布一致、
  眼位几何未变、主色相确实位移。
