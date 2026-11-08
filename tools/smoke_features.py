"""离屏冒烟测试：不启动可见 GUI，只验证新增能力的对象契约。

运行： QT_QPA_PLATFORM=offscreen python tools/smoke_features.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# 沙箱：把 APPDATA 指到临时目录，冒烟测试绝不动用户真实配置
_SANDBOX = tempfile.mkdtemp(prefix="buddy_smoke_")
os.environ["APPDATA"] = _SANDBOX

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import Qt, QTimer  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

from app import config, lines  # noqa: E402
from app.assets import BuddySprites  # noqa: E402
from app.audio_probe import detect_music_app, probe_peak  # noqa: E402
from app.effects import EffectEngine  # noqa: E402
from app.hotkeys import parse_spec  # noqa: E402
from app.music import MusicDetector  # noqa: E402
from app.overlay import EffectOverlay  # noqa: E402
from app.pet_window import PetWindow  # noqa: E402
from app.rps import CHOICES, RPSDialog, judge  # noqa: E402


def main() -> int:
    fail: list[str] = []
    app = QApplication(sys.argv[:1])

    cfg = config.Settings().clamp()
    print(f"PASS config: hotkey_toggle={cfg.hotkey_toggle} music_aware={cfg.music_aware}")

    sprites = BuddySprites(ROOT)
    engine = EffectEngine()
    overlay = EffectOverlay(engine)
    pet = PetWindow(cfg, sprites, engine, overlay, ROOT)
    print("PASS pet_window 构造")

    # ---- 台词池 ----
    need = [
        "hotkey_show", "hotkey_hide", "hotkey_fail", "drag_enter", "drag_leave",
        "drop_image", "drop_audio", "drop_code", "drop_doc", "drop_archive",
        "drop_other", "drop_folder", "drop_url", "drop_multi",
        "music_start", "music_start_track", "music_stop",
        "rps_open", "rps_win", "rps_lose", "rps_draw",
    ]
    missing = [k for k in need if k not in lines.POOLS or len(lines.POOLS[k]) < 3]
    if missing:
        fail.append(f"lines 缺池/不够 3 句: {missing}")
    for k in need:
        text = lines.pick(k, title="测试曲", n=3)
        if not text or "{" in text:
            fail.append(f"lines.pick({k}) 异常: {text!r}")
    print(f"PASS lines: {len(lines.POOLS)} 个池，最长 {max(len(s) for p in lines.POOLS.values() for s in p)} 字")

    # ---- 热键解析 ----
    if parse_spec("Ctrl+Alt+B") != (0x0002 | 0x0001, ord("B")):
        fail.append("parse_spec('Ctrl+Alt+B') 解析错误")
    if parse_spec("Shift+F12") is None:
        fail.append("parse_spec 应支持 F12")
    print("PASS hotkeys.parse_spec")

    # ---- 音频探测 ----
    peak = probe_peak()
    if peak is not None and not isinstance(peak, float):
        fail.append(f"probe_peak 返回类型异常: {type(peak)}")
    hit, title = detect_music_app()
    if not isinstance(hit, bool) or not isinstance(title, str):
        fail.append("detect_music_app 返回类型异常")
    print(f"PASS audio_probe: peak={peak} music_app={(hit, title)}")

    # ---- 拖放 ----
    tmp = Path(tempfile.mkdtemp()) / "冒烟测试图.png"
    tmp.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    before = int(cfg.drop_count)
    pet.simulate_drop([str(tmp)])
    if int(cfg.drop_count) != before + 1:
        fail.append(f"drop_count 未递增: {cfg.drop_count}")
    if not engine.bubble:
        fail.append("拖放后没有气泡")
    else:
        print(f"PASS drop: bubble={engine.bubble['text']!r} count={cfg.drop_count}")
    # 多文件 + 文件夹 + 链接
    pet.simulate_drop([str(tmp), str(tmp)])
    pet.simulate_drop([str(tmp.parent)])
    pet.simulate_drop(["https://example.com/a.png"])
    print(f"PASS drop 多形态: count={cfg.drop_count} last={cfg.last_drop_path!r}")

    # ---- 音乐律动 ----
    pet.on_music_state(True, "music", "测试曲")
    for _ in range(30):
        pet._tick()
    if pet._music_groove <= 0.0:
        fail.append("music_groove 未上升")
    pet.on_music_state(False)
    for _ in range(30):
        pet._tick()
    if pet._music_groove >= 0.5:
        fail.append("music_groove 未回落")
    print(f"PASS music groove: {pet._music_groove:.3f} on={pet._music_on}")

    # ---- MusicDetector.force ----
    det = MusicDetector(cfg)
    seen: list[tuple] = []
    det.started.connect(lambda k, t: seen.append(("started", k, t)))
    det.stopped.connect(lambda: seen.append(("stopped",)))
    det.force(True, "music", "测试曲")
    det.force(False)
    if len(seen) != 2:
        fail.append(f"MusicDetector 信号异常: {seen}")
    print(f"PASS MusicDetector.force: {seen}")

    # ---- judge 9 组合 ----
    expected = {
        ("rock", "rock"): "draw", ("rock", "scissors"): "win", ("rock", "paper"): "lose",
        ("scissors", "rock"): "lose", ("scissors", "scissors"): "draw", ("scissors", "paper"): "win",
        ("paper", "rock"): "win", ("paper", "scissors"): "lose", ("paper", "paper"): "draw",
    }
    for (p, q), want in expected.items():
        got = judge(p, q)
        if got != want:
            fail.append(f"judge({p},{q})={got} 期望 {want}")
    print("PASS judge 9/9")

    # ---- RPSDialog 构造（不 exec_） ----
    dlg = RPSDialog(cfg, sprites.raw[0], None)
    before_rounds = int(cfg.rps_wins) + int(cfg.rps_losses) + int(cfg.rps_draws)
    dlg.choose("rock")
    for _ in range(6):          # 多打几次，验证不会被重复结算
        dlg._step()
    after_rounds = int(cfg.rps_wins) + int(cfg.rps_losses) + int(cfg.rps_draws)
    if after_rounds != before_rounds + 1:
        fail.append(f"RPSDialog 记分次数异常: {before_rounds} -> {after_rounds}")
    print(f"PASS RPSDialog: {dlg.score.text()} / 你={dlg.you_panel.text()} 我={dlg.me_panel.text()}")
    dlg.reject()

    # ---- P0 回归：键盘 1/2/3 / Esc / Enter 不得抛 AttributeError ----
    # （PyQt5 没有 Qt.Key_NumPad1/2/3；一旦写错会在 Qt 虚函数里 qFatal 让进程 abort）
    from PyQt5.QtTest import QTest

    if hasattr(Qt, "Key_NumPad1"):
        print("NOTE: 本机 Qt 存在 Key_NumPad1（新版本才有），仍只判 Key_1")
    kbd = RPSDialog(cfg, sprites.raw[0], None)
    kbd.show()
    QTest.qWait(30)
    QTest.keyClick(kbd, Qt.Key_1)
    for _ in range(6):
        kbd._step()
    QTest.keyClick(kbd, Qt.Key_Return)     # 再来一局
    QTest.keyClick(kbd, Qt.Key_2)
    for _ in range(6):
        kbd._step()
    QTest.keyClick(kbd, Qt.Key_3)
    QTest.keyClick(kbd, Qt.Key_Escape)     # 退出，且要停掉倒计时
    if kbd.isVisible():
        fail.append("Esc 未关闭小游戏")
    if kbd._timer.isActive():
        fail.append("Esc 后倒计时定时器仍在跑")
    print("PASS 键盘 1/2/3/Esc/Enter（无 AttributeError、无残留定时器）")

    # ---- P2-1 回归：压缩包（走 box 特效）的气泡不能被「开个盲盒…」覆盖 ----
    arc = tmp.parent / "qa_arc.zip"
    arc.write_bytes(b"PK\x03\x04" + b"0" * 200)
    pet.simulate_drop([str(arc)])
    bubble = (engine.bubble or {}).get("text", "")
    if "qa_arc.zip" not in bubble:
        fail.append(f"压缩包气泡被特效台词覆盖: {bubble!r}")
    else:
        print(f"PASS 压缩包气泡: {bubble!r}")

    # ---- P2-2 回归：stopped / force 不重复广播 ----
    det2 = MusicDetector(cfg)
    evts: list[str] = []
    det2.stopped.connect(lambda: evts.append("stopped"))
    det2._playing = True
    det2.set_enabled(False)
    det2.set_enabled(False)
    if evts != ["stopped"]:
        fail.append(f"set_enabled(False) 重复发 stopped: {evts}")
    evts.clear()
    det2.force(False)      # 首次 force：无论当前状态都发一次（外部可能还没轮询过）
    det2.force(False)      # 连续同向：不重复发
    if evts != ["stopped"]:
        fail.append(f"force(False) 应只发一次 stopped: {evts}")
    print("PASS 音乐 stopped 去重")

    # ---- 单击延迟 / 双击不误触发 ----
    if not hasattr(pet, "_click_timer") or not pet._click_timer.isSingleShot():
        fail.append("_click_timer 未配置成单次定时器")
    print("PASS 单击/双击定时器")

    # ---- 真实拖放事件：进入 → 脉冲 → 绘制 → 落地 → 离开 ----
    from PyQt5.QtCore import QMimeData, QPoint, QUrl
    from PyQt5.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent, QPixmap

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(tmp))])
    enter = QDragEnterEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    pet.dragEnterEvent(enter)
    if not pet._drag_over:
        fail.append("dragEnterEvent 未进入拖入态")
    if pet.mask().boundingRect() != pet.rect():
        fail.append(f"拖入遮罩未铺满: {pet.mask().boundingRect()} vs {pet.rect()}")
    for _ in range(10):
        pet._tick()
    shot = QPixmap(pet.size())
    shot.fill(Qt.transparent)
    pet.render(shot)          # 拖入态下 paintEvent（虚线框）不能崩
    before_event = int(cfg.drop_count)
    drop = QDropEvent(QPoint(10, 10), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    pet.dropEvent(drop)
    if pet._drag_over:
        fail.append("dropEvent 后仍停留在拖入态")
    if int(cfg.drop_count) != before_event + 1:
        fail.append("dropEvent 未计数")
    pet.dragLeaveEvent(QDragLeaveEvent())     # 幂等：不在拖入态也不应出错
    if pet.mask().isEmpty():
        fail.append("退出拖入态后遮罩为空")
    print(f"PASS 拖放事件链: drop_count={cfg.drop_count}")

    # ---- 右键菜单：不弹窗也要能装配（验证图标/动作接线） ----
    from PyQt5.QtWidgets import QMenu

    collected: list[str] = []
    original_exec = QMenu.exec_

    def _fake_exec(self, *args, **kwargs):
        collected.extend(a.text() for a in self.actions())
        for act in self.actions():
            if act.menu() is not None:
                collected.extend("fx:" + a.text() for a in act.menu().actions())
        return None

    QMenu.exec_ = _fake_exec
    try:
        pet._show_menu(pet.rect().center())
    finally:
        QMenu.exec_ = original_exec
    for want in ("石头剪刀布…", "音乐感知"):
        if not any(want in text for text in collected):
            fail.append(f"右键菜单缺少「{want}」: {collected}")

    # ---- 全局热键：注册 / 注销不抛异常 ----
    from app.hotkeys import GlobalHotkeys

    hk = GlobalHotkeys(None)
    ok = hk.register("Ctrl+Alt+B")
    registered = hk.is_registered()
    hk.stop()
    hk.stop()               # 幂等
    if hk.is_registered():
        fail.append("hotkeys.stop() 后仍处于已注册状态")
    print(f"PASS hotkeys 注册: ok={ok} registered={registered}（非 Windows 下为 False 属正常）")
    if ok and not registered:
        fail.append("hotkeys.register 返回 True 但未标记已注册")

    pet.close()
    overlay.close()

    # ---- 整机装配：PetApp 全量接线（离屏，跑 ~1.2s 事件循环）----
    import main as main_mod

    pet_app = main_mod.PetApp(app)
    pet_app.toggle_pet()          # 藏
    pet_app.toggle_pet()          # 叫回来
    pet_app.set_music_aware(False)
    pet_app.set_music_aware(True)
    pet_app.dump_music_status()
    status_path = config.config_dir() / main_mod.MUSIC_STATUS_NAME
    if not status_path.exists():
        fail.append("music_status.json 未写出")
    QTimer.singleShot(2400, app.quit)     # 跑满 400ms 延迟注册 + 1500ms 首次音乐采样
    app.exec_()
    if not pet_app.hotkeys.is_enabled():
        fail.append("热键未按配置启用（400ms 延迟注册）")
    pet_app.music.stop()
    pet_app.hotkeys.stop()
    if pet_app.hotkeys.is_registered():
        fail.append("PetApp 退出后热键仍在注册状态")
    print("PASS PetApp 装配（延迟注册 / 音乐轮询 / 状态落盘 / 注销）")

    if fail:
        for item in fail:
            print("FAIL", item)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
