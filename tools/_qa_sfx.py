"""QA 验证脚本：互动音效功能的独立、批判性验证（offscreen）。

覆盖：
  2) 音效 WAV 文件格式校验
  3) SoundManager 单元行为（短路由 / 节流 / 缺失文件）
  4) PetWindow 接线与集成（点击/悬停/完成/apply_settings/右键菜单/_toggle_sound）
  5) SettingsDialog 设置项（sound_switch / volume_slider / _on_sound / _sync_widgets）

用法：``QT_QPA_PLATFORM=offscreen py -3 tools/_qa_sfx.py``
"""
from __future__ import annotations

import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt5.QtWidgets import QApplication, QMenu, QAction  # noqa: E402
from PyQt5.QtCore import QEvent, QPoint, Qt  # noqa: E402
from PyQt5.QtGui import QGuiApplication  # noqa: E402
from PyQt5.QtMultimedia import QSoundEffect  # noqa: E402

import app.config as config_mod  # noqa: E402
import app.sound as sound_mod  # noqa: E402

# ---- 关键 patch：用 SpyQSoundEffect 替换 app.sound 里的 QSoundEffect ----
# 这样所有 SoundManager 实例（包括 PetWindow 的 sfx）创建的是 spy 子类，
# 可以精确统计底层 play() 是否被调用，从而区分：
#   * enabled=False 短路（根本不调 play）
#   * offscreen 静默（调了 play 但不出声 / 这里我们跳过 super 以免触碰音频设备）
qsound_calls: list[int] = []


class SpyQSoundEffect(QSoundEffect):
    def play(self) -> None:
        qsound_calls.append(1)


sound_mod.QSoundEffect = SpyQSoundEffect

# ---- 关键 patch：拦截 config.save，避免写盘真实配置，并记录调用 ----
save_calls: list[int] = []


def recording_save(cfg) -> None:
    save_calls.append(1)


config_mod.save = recording_save
import app.pet_window as pw_mod  # noqa: E402
import app.settings_dialog as sd_mod  # noqa: E402

pw_mod.save = recording_save
sd_mod.save = recording_save

from app.config import Settings  # noqa: E402
from app.sound import SoundManager  # noqa: E402
from app.assets import BuddySprites  # noqa: E402
from app.effects import EffectEngine  # noqa: E402
from app.pet_window import PetWindow  # noqa: E402
from app.settings_dialog import SettingsDialog  # noqa: E402

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), label + (f"  {detail}" if detail else "")))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  {detail}" if detail else ""))


# ----------------------------------------------------------- Item 2
def validate_wavs() -> None:
    print("\n=== Item 2: 音效 WAV 文件校验 ===")
    names = ["click", "hover", "complete", "release", "grab"]
    all_ok = True
    for n in names:
        p = ROOT / "assets" / "sfx" / f"{n}.wav"
        if not p.exists():
            check(False, f"{n}.wav 存在"); all_ok = False; continue
        size = p.stat().st_size
        try:
            with wave.open(str(p), "r") as w:
                ch = w.getnchannels()
                sw = w.getsampwidth()
                fr = w.getframerate()
                nf = w.getnframes()
            ok = (size > 0 and ch == 1 and sw == 2 and fr == 44100 and nf > 0)
            dur = nf / fr if fr else 0.0
            check(ok, f"{n}.wav 格式正确",
                  f"size={size}B ch={ch} samp={sw}B sr={fr} frames={nf} dur={dur:.3f}s")
            if not ok:
                all_ok = False
        except Exception as e:  # noqa: BLE001
            check(False, f"{n}.wav 可读", str(e)); all_ok = False
    if all_ok:
        print("  -> 全部 5 个音效文件存在且为 16-bit 单声道 44100Hz")


# ----------------------------------------------------------- Item 3
def test_sound_manager_unit() -> None:
    print("\n=== Item 3: SoundManager 单元行为 (offscreen) ===")
    # a) 构造不抛异常
    try:
        sm = SoundManager(Settings(), ROOT)
        check(True, "SoundManager(Settings(), ROOT) 构造无异常")
    except Exception as e:  # noqa: BLE001
        check(False, "SoundManager 构造", str(e))
        return

    # b) 关闭后 play 是 no-op（不调底层 play）；开启后触发 1 次
    qsound_calls.clear()
    sm.set_enabled(False)
    try:
        sm.play("click")
        check(len(qsound_calls) == 0,
              "set_enabled(False) 后 play('click') 不触发底层 QSoundEffect.play（零开销短路）",
              f"calls={len(qsound_calls)}")
    except Exception as e:  # noqa: BLE001
        check(False, "关闭后 play 不抛异常", str(e))

    qsound_calls.clear()
    sm.set_enabled(True)
    try:
        sm.play("click")
        check(len(qsound_calls) == 1,
              "set_enabled(True) 后 play('click') 触发底层 QSoundEffect.play 1 次",
              f"calls={len(qsound_calls)}")
    except Exception as e:  # noqa: BLE001
        check(False, "开启后 play 触发底层 play", str(e))

    # c) 节流：100 次极快连续 play，仅触发 1 次
    qsound_calls.clear()
    sm.set_enabled(True)
    for _ in range(100):
        sm.play("hover", cooldown_ms=600)
    check(len(qsound_calls) == 1,
          "100 次极快连续 play('hover',600ms) 仅触发 1 次底层 play（节流生效）",
          f"calls={len(qsound_calls)}")

    # d) 缺失文件不抛异常
    try:
        sm.play("nonexistent")
        check(True, "play('nonexistent') 不抛异常")
    except Exception as e:  # noqa: BLE001
        check(False, "play('nonexistent') 不抛异常", str(e))


# ----------------------------------------------------------- Item 4
def test_integration() -> None:
    print("\n=== Item 4: 接线/集成 (offscreen PetWindow) ===")
    cfg = Settings()
    cfg.gaze_follow = True
    cfg.edge_cling = True
    pet = PetWindow(cfg, BuddySprites(ROOT), EffectEngine(), None, ROOT)
    pet.show()
    QApplication.instance().processEvents()

    # spy on pet.sfx.play 以记录被调用的音效名（同时保留真实行为）
    calls: list[str] = []
    orig_play = pet.sfx.play

    def spy(name: str, cooldown_ms: int = 60):
        calls.append(name)
        return orig_play(name, cooldown_ms)

    pet.sfx.play = spy  # type: ignore[assignment]

    # a) 点击
    calls.clear()
    try:
        pet._on_click()
        check("click" in calls, "点击 _on_click() 触发 play('click')", f"calls={calls}")
    except Exception as e:  # noqa: BLE001
        check(False, "_on_click() 触发 click", str(e))

    # b) 悬停：首次触发，立即再次被节流屏蔽，间隔 >0.6s 后再次触发
    calls.clear()
    try:
        pet.enterEvent(QEvent(QEvent.Enter))
        first = list(calls)
        check("hover" in first, "悬停 enterEvent 首次触发 play('hover')", f"calls={first}")
    except Exception as e:  # noqa: BLE001
        check(False, "悬停事件首次触发 hover", str(e))

    calls.clear()
    try:
        pet.enterEvent(QEvent(QEvent.Enter))  # 立即再次进入
        second = list(calls)
        check("hover" not in second,
              "悬停：立即再次 enterEvent 被节流屏蔽（不重复触发）", f"calls={second}")
    except Exception as e:  # noqa: BLE001
        check(False, "悬停立即重复进入被屏蔽", str(e))

    calls.clear()
    try:
        time.sleep(0.7)  # 超过 0.6s 节流窗口
        pet.enterEvent(QEvent(QEvent.Enter))
        check(calls.count("hover") == 1,
              "悬停：间隔 >0.6s 后再次触发（节流基于时间，非永久禁用）",
              f"hover_count={calls.count('hover')}")
    except Exception as e:  # noqa: BLE001
        check(False, "悬停间隔后再次触发", str(e))

    # c) 完成操作：effect_finished -> _on_effect_finished -> play('complete')
    calls.clear()
    pet.engine.stop_all()
    # 确认信号已连接 _on_effect_finished（用 receivers 探测；失败则用下游行为兜底）
    try:
        nrec = pet.engine.receivers(pet.engine.effect_finished)
    except Exception:  # noqa: BLE001
        nrec = -1
    pet.engine.start("hop")
    dt = 1.0 / 60.0
    for _ in range(300):
        pet.engine.tick(dt)
        if not pet.engine.effects:
            break
    check(nrec != 0, "engine.effect_finished 信号已连接槽（receivers>0 或已通过下游行为证明）",
          f"receivers={nrec}")
    check(calls.count("complete") >= 1,
          "完成操作：hop 自然播放完成后触发 play('complete')",
          f"complete_count={calls.count('complete')} calls={calls}")

    # c2) BlinkEffect 完成不应触发 'complete'
    calls.clear()
    pet.engine.stop_all()
    pet.engine.start("blink")
    for _ in range(300):
        pet.engine.tick(dt)
        if not pet.engine.effects:
            break
    check(calls.count("complete") == 0,
          "BlinkEffect 完成不触发 'complete'（effect_finished 对非眨眼特效才发）",
          f"calls={calls}")

    # d) apply_settings 同步音效开关
    cfg.sound_enabled = False
    pet.apply_settings()
    qsound_calls.clear()
    pet.sfx.play("click")
    check(pet.sfx._cfg.sound_enabled is False,
          "apply_settings() 后 sfx 的 enabled 同步为 False",
          f"sfx._cfg.sound_enabled={pet.sfx._cfg.sound_enabled}")
    check(len(qsound_calls) == 0,
          "关闭后 play 短路（底层 QSoundEffect.play 不被调用）",
          f"calls={len(qsound_calls)}")

    # e) 右键菜单含可勾选「音效」QAction（拦截 exec_ 避免阻塞）
    captured: dict = {}
    orig_exec = QMenu.exec_

    def fake_exec(self_menu, *a, **k):  # noqa: ANN001
        captured["menu"] = self_menu
        return 0

    QMenu.exec_ = fake_exec  # type: ignore[assignment]
    try:
        pet._show_menu(QPoint(0, 0))
        menu = captured.get("menu")
        actions = menu.findChildren(QAction) if menu is not None else []
        sound_actions = [a for a in actions if "音效" in a.text() and a.isCheckable()]
        check(len(sound_actions) >= 1,
              "右键菜单含文本含「音效」且 isCheckable() 为 True 的 QAction",
              f"found={len(sound_actions)} total_actions={len(actions)}")
    except Exception as e:  # noqa: BLE001
        check(False, "右键菜单构造并查找音效 action", str(e))
    finally:
        QMenu.exec_ = orig_exec  # type: ignore[assignment]

    # f) _toggle_sound 存在且正确
    check(hasattr(pet, "_toggle_sound"), "_toggle_sound 方法存在")
    save_calls.clear()
    pet._toggle_sound(False)
    check(pet.cfg.sound_enabled is False, "_toggle_sound(False) 后 cfg.sound_enabled == False")
    check(pet.sfx._cfg.sound_enabled is False, "_toggle_sound(False) 后 sfx 关闭")
    check(len(save_calls) >= 1, "_toggle_sound(False) 调用了 save()", f"save_calls={len(save_calls)}")
    save_calls.clear()
    pet._toggle_sound(True)
    check(pet.cfg.sound_enabled is True and pet.sfx._cfg.sound_enabled is True,
          "_toggle_sound(True) 后 cfg / sfx 均开启")


# ----------------------------------------------------------- Item 5
def test_settings_dialog() -> None:
    print("\n=== Item 5: 设置面板 (SettingsDialog) ===")
    cfg = Settings()
    cfg.sound_enabled = True
    cfg.sound_volume = 0.7

    cfg2 = Settings()
    cfg2.gaze_follow = True
    cfg2.edge_cling = True
    pet = PetWindow(cfg2, BuddySprites(ROOT), EffectEngine(), None, ROOT)

    try:
        dlg = SettingsDialog(cfg, ROOT, pet.sprites.raw[0], parent=pet)
        check(hasattr(dlg, "sound_switch"), "dialog 有 sound_switch 属性")
        check(hasattr(dlg, "volume_slider"), "dialog 有 volume_slider 属性")
    except Exception as e:  # noqa: BLE001
        check(False, "SettingsDialog 构造", str(e))
        return

    save_calls.clear()
    dlg._on_sound(True)
    check(cfg.sound_enabled is True,
          "dialog._on_sound(True) 后 cfg.sound_enabled == True", f"{cfg.sound_enabled}")
    check(len(save_calls) >= 1, "dialog._on_sound 调用了 save()", f"save_calls={len(save_calls)}")

    cfg.sound_enabled = False
    dlg._sync_widgets()
    check(dlg.sound_switch.isChecked() == cfg.sound_enabled,
          "dialog._sync_widgets() 后 sound_switch.isChecked() == cfg.sound_enabled",
          f"checked={dlg.sound_switch.isChecked()} cfg={cfg.sound_enabled}")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    QGuiApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

    validate_wavs()
    test_sound_manager_unit()
    test_integration()
    test_settings_dialog()

    passed = sum(1 for ok, _ in RESULTS if ok)
    print(f"\n{'=' * 60}\nQA 结果：{passed}/{len(RESULTS)} 通过")
    for ok, label in RESULTS:
        if not ok:
            print(f"  失败：{label}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
