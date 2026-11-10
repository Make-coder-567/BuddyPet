"""「空闲自动漫游」离屏冒烟测试。

运行： QT_QPA_PLATFORM=offscreen python tools/smoke_idle_roam.py

离屏平台拿不到可信的系统空闲信息（``platform_available()`` 返回 False），
所以这里统一用 ``IdleWatcher.force()`` 显式驱动状态机 —— 既不会误判，
也不会真的等 3 分钟。APPDATA 指向临时目录，绝不碰用户真实配置。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["APPDATA"] = tempfile.mkdtemp(prefix="buddy_roam_smoke_")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import QPoint  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMenu  # noqa: E402

from app import config, lines  # noqa: E402
from app.assets import BuddySprites  # noqa: E402
from app.effects import EffectEngine  # noqa: E402
from app.idle_roam import (  # noqa: E402
    IdleWatcher,
    RoamController,
    _idle_seconds_win32,
    platform_available,
)
from app.overlay import EffectOverlay  # noqa: E402
from app.pet_window import PetWindow  # noqa: E402

DT = 1.0 / 60.0


def check(fail: list[str], ok: bool, text: str) -> None:
    if not ok:
        fail.append(text)


def inside_bounds(ctrl, pet) -> bool:
    x0, x1, y0, y1 = ctrl._bounds()
    return x0 - 1 <= pet.x() <= x1 + 1 and y0 - 1 <= pet.y() <= y1 + 1


def main() -> int:
    fail: list[str] = []
    app = QApplication(sys.argv[:1])

    cfg = config.Settings().clamp()
    check(fail, cfg.roam_enabled is True, "roam_enabled 默认应为 True")
    check(fail, cfg.roam_idle_min == 3, "roam_idle_min 默认应为 3")
    check(fail, 30 <= cfg.roam_speed <= 180, "roam_speed 默认值越界")
    cfg.roam_idle_min, cfg.roam_speed = 999, -5
    cfg.clamp()
    check(fail, cfg.roam_idle_min == 30 and cfg.roam_speed == 30, "clamp 未夹住漫游参数")
    cfg.roam_idle_min, cfg.roam_speed = 3, 78
    print("PASS config 字段与 clamp")

    for key in ("roam_start", "roam_wander", "roam_back"):
        check(fail, len(lines.POOLS.get(key, ())) >= 3, f"台词池 {key} 不足 3 句")
        check(fail, lines.pick(key).strip() != "", f"lines.pick({key}) 为空")
    print("PASS lines 新增 3 个台词池")

    sprites = BuddySprites(ROOT)
    engine = EffectEngine()
    overlay = EffectOverlay(engine)
    pet = PetWindow(cfg, sprites, engine, overlay, ROOT)
    pet.show()
    check(fail, pet.isVisible(), "离屏下 PetWindow.show() 后应可见")

    watcher = IdleWatcher(cfg)
    roam = RoamController(cfg, pet)
    pet.set_roam_controller(roam)
    watcher.idle_started.connect(roam.start)
    watcher.idle_ended.connect(roam.finish)

    # ---- 平台降级 / Win32 探测 ----
    check(fail, not platform_available(), "离屏平台应标记为不可用")
    info = watcher.poll_now()
    check(fail, info["source"] == "unavailable" and info["idle_sec"] is None,
          f"离屏探测应返回 unavailable: {info}")
    sec = _idle_seconds_win32()
    check(fail, sec is None or isinstance(sec, float), f"win32 空闲探测类型异常: {sec!r}")
    print(f"PASS 平台降级: source={info['source']} win32_sec={sec}")

    # ---- 信号：force 幂等 + 关闭补发 ----
    seen: list[str] = []
    watcher.idle_started.connect(lambda: seen.append("started"))
    watcher.idle_ended.connect(lambda: seen.append("ended"))
    watcher.force(True, hold_s=2.0)
    watcher.force(True, hold_s=2.0)
    check(fail, seen == ["started"], f"force(True) 重复广播: {seen}")
    check(fail, roam.active, "idle_started 后漫游未启动")
    watcher.force(False)
    check(fail, seen == ["started", "ended"], f"force(False) 信号异常: {seen}")
    watcher.force(True)
    watcher.set_enabled(False)
    check(fail, seen[-1] == "ended", "set_enabled(False) 未补发 idle_ended")
    print(f"PASS watcher 信号: {seen}")

    # ---- 主流程：启动 → 位移 → 边界 → 停顿 → 回来归位 ----
    home = QPoint(pet.x(), pet.y())
    cfg.pos = [home.x(), home.y()]
    cfg.roam_return_home = True
    cfg.roam_effects = False          # 测试里关掉随机节目，保证状态路径确定
    watcher.force(False)              # 先回到确定状态
    roam.finish(immediate=True)
    watcher.force(True)
    check(fail, roam.active and roam.state == "walk", f"启动状态异常: {roam.state}")
    start_pos = QPoint(pet.x(), pet.y())
    states = set()
    moved = False
    for _ in range(900):              # 15 秒虚拟时间，足够走到目标并停顿几轮
        rp = roam.step(DT)
        if rp is not None:
            check(fail, -2.5 <= rp.bob <= 0.5, f"步态 bob 越界: {rp.bob}")
            check(fail, 0.85 <= rp.sx <= 1.02, f"步态 sx 越界: {rp.sx}")
        states.add(roam.state)
        if QPoint(pet.x(), pet.y()) != start_pos:
            moved = True
        check(fail, inside_bounds(roam, pet), f"漫游越界: {(pet.x(), pet.y())}")
    check(fail, moved, "漫游期间窗口没有移动")
    check(fail, "pause" in states, f"未进入停顿状态: {states}")
    check(fail, cfg.pos == [home.x(), home.y()], "漫游期间不应改写 cfg.pos")
    print(f"PASS 漫游位移: states={sorted(states)} pos={(pet.x(), pet.y())}")

    watcher.force(False)
    check(fail, roam.state == "return", f"回来后应进入归位: {roam.state}")
    for _ in range(1200):
        roam.step(DT)
        if not roam.active:
            break
    check(fail, not roam.active, "归位未完成")
    check(fail, QPoint(pet.x(), pet.y()) == home, f"未回到原位: {pet.pos()} vs {home}")
    print("PASS 回来归位（state=off, pos=home）")

    # ---- 打断：点宠物就地停住，窗口绝不瞬移（否则这次拖拽会错位）----
    watcher.force(True)
    check(fail, roam.active, "二次启动失败")
    for _ in range(180):
        roam.step(DT)
    before = QPoint(pet.x(), pet.y())
    roam.interrupt()
    check(fail, not roam.active, "interrupt 未结束漫游")
    check(fail, QPoint(pet.x(), pet.y()) == before, "interrupt 不应瞬移窗口")
    check(fail, cfg.pos == [before.x(), before.y()], f"打断落点未记为新家: {cfg.pos}")
    watcher.force(False)                 # 补发 idle_ended：off 状态下必须幂等
    check(fail, not roam.active, "idle_ended 在 off 状态下不应重启漫游")
    pet.move(home)
    cfg.pos = [home.x(), home.y()]
    print("PASS interrupt 就地停住 / 后续 idle_ended 幂等")

    # ---- 锁定位置：不应启动 ----
    cfg.lock_pos = True
    watcher.force(False)
    watcher.force(True)
    check(fail, not roam.active, "lock_pos=True 时不应开始漫游")
    cfg.lock_pos = False
    watcher.force(False)
    print("PASS 锁定位置优先")

    # ---- 不归位模式：结束时把落点记成新家 ----
    cfg.roam_return_home = False
    cfg.pos = [home.x(), home.y()]
    watcher.force(True)
    for _ in range(240):
        roam.step(DT)
    landed = QPoint(pet.x(), pet.y())
    roam.finish(immediate=True)
    check(fail, not roam.active, "不归位模式未结束")
    check(fail, QPoint(pet.x(), pet.y()) == landed, "不归位模式不应移动窗口")
    check(fail, cfg.pos == [landed.x(), landed.y()], f"落点未记为新位置: {cfg.pos}")
    cfg.roam_return_home = True
    pet.move(home)
    cfg.pos = [home.x(), home.y()]
    print(f"PASS 不归位模式: new_pos={cfg.pos}")

    # ---- 贴边状态：漫游前松开、回来后恢复 ----
    watcher.force(False)
    pet._edge = "bottom"
    watcher.force(True)
    check(fail, pet._edge is None, "漫游前应松开边缘")
    roam.finish(immediate=True)
    check(fail, pet._edge == "bottom", f"回来后未恢复贴边: {pet._edge}")
    pet._edge = None
    pet.move(home)                 # 吸附会把窗口挪到下边缘，测试里复位
    pet.refresh_mask()
    print("PASS 贴边状态恢复")

    # ---- 状态快照 ----
    status = roam.status()
    for key in ("enabled", "active", "state", "pos", "home", "target", "return_home", "speed"):
        check(fail, key in status, f"roam.status 缺字段 {key}")
    print(f"PASS roam.status: {status}")

    # ---- 设置面板：控件装配 / 同步 / 总开关置灰 ----
    from app.settings_dialog import SettingsDialog

    dlg = SettingsDialog(cfg, ROOT, sprites.raw[0], pet)
    check(fail, dlg.roam_switch.isChecked() == cfg.roam_enabled, "设置面板漫游开关未同步")
    check(fail, dlg.roam_idle_slider.value() == cfg.roam_idle_min, "空闲时长滑块未同步")
    check(fail, dlg.roam_speed_slider.value() == cfg.roam_speed, "速度滑块未同步")
    # Switch.setChecked() 是「程序化同步」，按项目约定不发 toggled；
    # 这里用真实鼠标点击走 mousePressEvent → toggled 的完整用户路径。
    from PyQt5.QtCore import Qt as _Qt
    from PyQt5.QtTest import QTest

    QTest.mouseClick(dlg.roam_switch, _Qt.LeftButton)
    check(fail, not dlg.cfg.roam_enabled, "点击总开关后 cfg 未关闭")
    check(fail, not dlg.roam_speed_slider.isEnabled(), "总开关关闭后子项未置灰")
    QTest.mouseClick(dlg.roam_switch, _Qt.LeftButton)
    check(fail, dlg.cfg.roam_enabled, "点击总开关后 cfg 未开启")
    check(fail, dlg.roam_speed_slider.isEnabled(), "总开关打开后子项未启用")
    dlg._sync_widgets()
    dlg.reject()
    print("PASS SettingsDialog 漫游分组")

    # ---- 菜单项 ----
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
    check(fail, any("空闲自动漫游" in t for t in collected), "右键菜单缺空闲自动漫游")
    print("PASS 右键菜单勾选项")

    pet.close()
    overlay.close()

    # ---- 整机装配：PetApp 接线 + 命令通道 + 状态落盘 ----
    import main as main_mod

    pet_app = main_mod.PetApp(app)
    check(fail, pet_app.roam is pet_app.pet._roam, "PetApp 未注入漫游控制器")
    pet_app.handle_command  # 存在性检查
    pet_app.dump_idle_status()
    status_path = config.config_dir() / main_mod.IDLE_STATUS_NAME
    check(fail, status_path.exists(), "idle_status.json 未写出")
    if status_path.exists():
        import json

        payload = json.loads(status_path.read_text(encoding="utf-8"))
        check(fail, "roam" in payload and "idle_sec" in payload, f"状态文件字段不全: {payload}")
    pet_app.set_roam_enabled(False)
    check(fail, not pet_app.cfg.roam_enabled, "set_roam_enabled(False) 未落盘")
    pet_app.set_roam_enabled(True)
    check(fail, pet_app.cfg.roam_enabled, "set_roam_enabled(True) 未落盘")
    pet_app.idle.force(True, hold_s=1.0)
    check(fail, pet_app.roam.active, "整机 force 未启动漫游")
    pet_app.roam.finish(immediate=True)
    print("PASS PetApp 接线 / 命令状态落盘 / 开关")

    pet_app.music.stop()
    pet_app.hotkeys.stop()
    pet_app.idle.stop()
    pet_app.roam.finish(immediate=True)
    pet_app.tray.hide()

    if fail:
        for item in fail:
            print("FAIL", item)
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
