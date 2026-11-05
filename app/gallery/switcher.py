"""换肤事务：唯一允许替换 ``pet.sprites`` 的地方（架构 §9）。

核心不变量：
    * **D1**：换肤 = 新建一个 :class:`BuddySprites` 实例并整体替换，绝不"原地改目录"。
      五级缩放缓存天然为空，``FileNotFoundError`` 在建实例时抛，天然原子。
    * ``cfg.selected_*`` 只在"新实例构造 + 挂载"全部成功之后才写盘 →
      磁盘配置永远不会指向一个加载不了的皮肤（K4/K5）。
"""
from __future__ import annotations

import logging
from pathlib import Path

from PyQt5.QtCore import QObject, pyqtSignal

from ..assets import BuddySprites
from ..config import Settings, save
from . import builtin as builtin_mod
from .models import Buddy, Skin, SkinStatus
from .validator import validate_skin

__all__ = ["SkinSwitcher", "DEGRADED_TOKENS"]

logger = logging.getLogger(__name__)

#: ``ValidationResult.degraded`` 里的"能力 token"，UI 明细要跳过它们
DEGRADED_TOKENS: tuple[str, ...] = ("视线跟随", "抓边姿态")

MSG_OK = "已切换为「{buddy}」·「{skin}」"
MSG_DEGRADED = "已切换为「{buddy}」·「{skin}」（{detail}）"
MSG_FAILED = "切换失败，已恢复上一形象"


def degraded_detail(degraded: tuple[str, ...]) -> str:
    """从 ``degraded`` 里取第一个"非 token"元素当明细（T02 报告 ④-1 的约定）。"""
    for item in degraded:
        if item not in DEGRADED_TOKENS:
            return item.lstrip("缺 ").strip()
    return "，".join(degraded)


class SkinSwitcher(QObject):
    """换肤事务管理器。

    Args:
        cfg: 配置对象（成功后写 ``selected_buddy_id`` / ``selected_skin_id``）。
        root: 项目根目录（构造 ``BuddySprites`` 用）。
        pet: :class:`~app.pet_window.PetWindow`，需提供 ``sprites`` 属性、
            :meth:`set_sprites`、``engine.say(text, seconds)``。
        parent: Qt 父对象。

    Signals:
        swapped (object): 成功，参数是新的 :class:`Skin`。
        failed (str, str): 失败，参数是 ``"<buddy_id>/<skin_id>"`` 与原因码。
        busy_changed (bool): 事务开始/结束（UI 用来禁用整块）。
    """

    swapped = pyqtSignal(object)
    failed = pyqtSignal(str, str)
    busy_changed = pyqtSignal(bool)

    def __init__(self, cfg: Settings, root: Path | str, pet, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.root = Path(root)
        self.pet = pet
        self._busy = False

    # ---------------- 状态 ----------------
    def is_busy(self) -> bool:
        """是否正在换肤（架构 §8.5 里以方法形式调用）。"""
        return self._busy

    @property
    def busy(self) -> bool:
        return self._busy

    # ---------------- 对外入口 ----------------
    def apply(self, buddy: Buddy, skin: Skin) -> bool:
        """应用一套皮肤（事务语义）。

        Returns:
            ``True`` 表示已生效并写盘；``False`` 表示被拒绝或失败（状态已回滚）。
        """
        name = f"{getattr(buddy, 'id', '?')}/{getattr(skin, 'id', '?')}"
        if self._busy:
            logger.info("gallery switcher: busy, ignore %s", name)
            return False

        # ---- 1) 校验（不写盘、不动任何状态）----
        vr = validate_skin(skin, use_cache=False)
        if not vr.can_apply:
            reason = "invalid_skin"
            logger.warning("gallery switcher: %s invalid (%s)", name, vr.missing)
            self._say(MSG_FAILED)
            self.failed.emit(name, reason)
            return False

        self._busy = True
        self.busy_changed.emit(True)
        old_sprites = getattr(self.pet, "sprites", None)
        old_buddy_id = getattr(self.cfg, "selected_buddy_id", "")
        old_skin_id = getattr(self.cfg, "selected_skin_id", "")
        mounted = False
        try:
            # ---- 2) 构造新实例（D1：建实例即校验，失败天然原子）----
            asset_dir = Path(str(skin.asset_dir)) if skin.asset_dir else None
            if asset_dir is None:
                raise FileNotFoundError("皮肤没有本地素材目录")
            new_sprites = BuddySprites(self.root, frames_dir=asset_dir)

            # ---- 3) 挂载（换肤唯一入口）----
            self.pet.set_sprites(new_sprites)
            mounted = True

            # ---- 4) 落盘（放在最后，保证"写盘即有画面"）----
            self.cfg.selected_buddy_id = buddy.id
            self.cfg.selected_skin_id = skin.id
            save(self.cfg)

            # ---- 5) 气泡 + 信号 ----
            if vr.status is SkinStatus.DEGRADED:
                self._say(MSG_DEGRADED.format(buddy=buddy.name, skin=skin.name, detail=degraded_detail(vr.degraded)))
            else:
                self._say(MSG_OK.format(buddy=buddy.name, skin=skin.name))
            logger.info("gallery switcher: applied %s (%s)", name, vr.status.value)
            self.swapped.emit(skin)
            return True
        except Exception as exc:  # noqa: BLE001 —— 任何异常都必须回滚，不许外泄
            logger.warning("gallery switcher: %s failed: %r", name, exc)
            try:
                if mounted and old_sprites is not None:
                    self.pet.set_sprites(old_sprites)      # 6) 回滚画面
            except Exception as rollback_exc:  # noqa: BLE001
                logger.error("gallery switcher: rollback failed: %r", rollback_exc)
            try:
                self.cfg.selected_buddy_id = old_buddy_id  # 6) 复位配置
                self.cfg.selected_skin_id = old_skin_id
                save(self.cfg)
            except Exception as cfg_exc:  # noqa: BLE001
                logger.error("gallery switcher: config restore failed: %r", cfg_exc)
            self._say(MSG_FAILED)
            self.failed.emit(name, "load_failed")
            return False
        finally:
            self._busy = False
            self.busy_changed.emit(False)

    def restore_default(self) -> bool:
        """恢复官方默认形象（走同一个 :meth:`apply`，配置天然复位成 ``default``）。"""
        buddy = builtin_mod.default_buddy(self.root)
        return self.apply(buddy, buddy.skins[0])

    def apply_by_ids(self, buddy_id: str, skin_id: str) -> bool:
        """按 id 应用（命令通道用）；查不到直接失败，不猜。"""
        pair = self.resolve_ids(buddy_id, skin_id)
        if pair is None:
            logger.warning("gallery switcher: unknown skin %s/%s", buddy_id, skin_id)
            self._say(MSG_FAILED)
            self.failed.emit(f"{buddy_id}/{skin_id}", "not_found")
            return False
        buddy, skin = pair
        return self.apply(buddy, skin)

    # ---------------- 内部 ----------------
    def resolve_ids(self, buddy_id: str, skin_id: str) -> tuple[Buddy, Skin] | None:
        """在磁盘上按目录约定定位 (形象, 皮肤)。

        不依赖内存快照 —— 命令通道/自动化验收更可靠（快照可能是启动时算的）。
        官方形象必须走 ``builtin_buddies()`` 而不是 ``default_buddy()``：
        前者才带上 ``assets/skins/default/<skin>/`` 并入的皮肤。
        """
        buddy = next((b for b in builtin_mod.builtin_buddies(self.root) if b.id == buddy_id), None)
        if buddy is None:
            return None
        skin = buddy.find_skin(skin_id)
        return (buddy, skin) if skin is not None else None

    def _say(self, text: str, seconds: float = 2.4) -> None:
        """气泡（照抄 ``pet_window`` 现有写法：``engine.say(text, seconds)``）。"""
        if not bool(getattr(self.cfg, "bubble_hint", True)):
            return
        engine = getattr(self.pet, "engine", None)
        say = getattr(engine, "say", None)
        if say is None:
            return
        try:
            say(text, seconds)
        except Exception:  # noqa: BLE001 —— 气泡失败不能影响换肤结果
            logger.debug("gallery switcher: bubble failed", exc_info=True)
