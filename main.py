"""WorkBuddy 桌面宠物 · 程序入口。"""
from __future__ import annotations

import ctypes
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import Qt, QTimer  # noqa: E402
from PyQt5.QtNetwork import QLocalServer, QLocalSocket  # noqa: E402
from PyQt5.QtWidgets import QAction, QApplication, QMenu, QSystemTrayIcon  # noqa: E402

from app import config, icons, lines, theme  # noqa: E402
from app.assets import BuddySprites  # noqa: E402
from app.effects import EFFECT_ORDER, EFFECTS, EffectEngine  # noqa: E402
from app.gallery.service import GalleryService  # noqa: E402
from app.gallery.switcher import SkinSwitcher  # noqa: E402
from app.hotkeys import GlobalHotkeys  # noqa: E402
from app.idle_roam import FORCE_HOLD_S, IdleWatcher, RoamController  # noqa: E402
from app.music import MusicDetector  # noqa: E402
from app.overlay import EffectOverlay  # noqa: E402
from app.pet_window import PetWindow  # noqa: E402
from app.rps import RPSDialog  # noqa: E402
from app.settings_dialog import AboutDialog, SettingsDialog  # noqa: E402

SERVER_NAME = "WorkBuddyDesktopPet-1"
APP_ID = "WorkBuddy.DesktopPet.1"
DUMP_NAME = "gallery_dump.json"
MUSIC_STATUS_NAME = "music_status.json"
IDLE_STATUS_NAME = "idle_status.json"


def _dialog_kwargs(section: str, gallery=None, switcher=None) -> dict:
    """构造 ``SettingsDialog`` 的可选关键字参数。

    T04 给面板加了 ``focus_section`` / ``gallery`` / ``switcher`` 三个新形参。
    这里用 ``inspect.signature`` 探测真实形参名，只传面板真正声明了的那几个 ——
    这样即使面板被回滚到旧版（没有图鉴分组），``main`` 也不会抛 ``TypeError``。
    """
    try:
        params = inspect.signature(SettingsDialog.__init__).parameters
    except (TypeError, ValueError):
        return {}
    kwargs: dict = {}
    if "focus_section" in params:
        kwargs["focus_section"] = section
    if "gallery" in params:
        kwargs["gallery"] = gallery
    if "switcher" in params:
        kwargs["switcher"] = switcher
    return kwargs


class PetApp:
    def __init__(self, app: QApplication):
        self.app = app
        self.cfg = config.load()
        self.sprites = BuddySprites(ROOT)
        self.engine = EffectEngine()
        self.overlay = EffectOverlay(self.engine)
        self.pet = PetWindow(self.cfg, self.sprites, self.engine, self.overlay, ROOT)
        self.pet.settings_requested.connect(self.open_settings)
        self.pet.about_requested.connect(self.open_about)
        self.pet.quit_requested.connect(self.quit)
        self.pet.hidden_requested.connect(self.hide_pet)

        # 图鉴：门面（不联网、不读盘）+ 换肤事务
        self.gallery = GalleryService(self.cfg, ROOT)
        self.switcher = SkinSwitcher(self.cfg, ROOT, self.pet)
        self.pet.gallery_requested.connect(lambda: self.open_settings(section="gallery"))
        self.pet.sprites_changed.connect(self.refresh_icon)

        # ---------------- 新增能力：全局快捷键 / 音乐感知 / 小游戏 ----------------
        # 1) 全局快捷键：非 Windows 或注册失败都只是「没有这个功能」，不影响其它能力
        #    注意：PetApp 不是 QObject，不能当 Qt 父对象；生命周期由本对象持有引用即可
        self.hotkeys = GlobalHotkeys(None)
        self.hotkeys.triggered.connect(lambda _spec: self.toggle_pet())
        self.hotkeys.failed.connect(self._on_hotkey_failed)

        # 2) 音乐感知：轮询式，全部探测函数自带 try/except，不会崩
        self.music = MusicDetector(self.cfg, None)
        self.music.started.connect(
            lambda kind, title: self.pet.on_music_state(True, kind, title)
        )
        self.music.stopped.connect(lambda: self.pet.on_music_state(False))

        # 3) 小游戏：双击宠物 / 菜单 / 托盘都能进来，靠 _rps_open 防重入
        self._rps_open = False
        self._rps_result = ""
        self.pet.rps_requested.connect(self.open_rps)
        self.pet.music_toggle_requested.connect(self.set_music_aware)

        # 4) 空闲自动漫游：检测器只看「人有没有在操作」，控制器只管「怎么逛」
        self.idle = IdleWatcher(self.cfg, None)
        self.roam = RoamController(self.cfg, self.pet, None)
        self.pet.set_roam_controller(self.roam)
        self.idle.idle_started.connect(self.roam.start)
        self.idle.idle_ended.connect(self.roam.finish)
        self.pet.roam_toggle_requested.connect(self.set_roam_enabled)

        self.icon = icons.app_icon(self.sprites.raw[0])
        self.app.setWindowIcon(self.icon)
        self._build_tray()
        self.pet.show()
        self.pet.show_intro()
        # 让出一次事件循环，先完成首帧绘制再补图鉴（不等待网络结果）
        QTimer.singleShot(0, self.gallery.startup_sync)
        # 首帧之后再注册热键/开轮询：注册失败只弹一次托盘提示，不阻塞启动
        QTimer.singleShot(400, lambda: self.hotkeys.set_enabled(self.cfg.hotkey_enabled))
        QTimer.singleShot(600, lambda: self.idle.set_enabled(self.cfg.roam_enabled))
        self.music.set_enabled(self.cfg.music_aware)

    # ---------------------------------------------------------- 图标
    def refresh_icon(self) -> None:
        """换肤后刷新托盘/窗口图标，并让对话框取到新素材（坑位 P9）。"""
        self.sprites = self.pet.sprites
        try:
            pixmap = self.sprites.raw[0]
        except (AttributeError, IndexError):
            return
        self.icon = icons.app_icon(pixmap)
        self.app.setWindowIcon(self.icon)
        tray = getattr(self, "tray", None)
        if tray is not None:
            tray.setIcon(self.icon)

    # ---------------------------------------------------------- 托盘
    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self.icon)
        self.tray.setToolTip("Buddy 桌面宠物")

        menu = QMenu()
        menu.setStyleSheet(theme.MENU_QSS)
        menu.setAttribute(Qt.WA_TranslucentBackground, True)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)

        act_show = QAction(icons.icon_eye(), "显示宠物", menu)
        act_show.triggered.connect(self.show_pet)
        menu.addAction(act_show)

        act_open = QAction(icons.icon_open(theme.ORANGE), "打开 WorkBuddy", menu)
        act_open.triggered.connect(self.pet.open_workbuddy)
        menu.addAction(act_open)
        menu.addSeparator()

        fx_menu = QMenu("互动效果", menu)
        fx_menu.setStyleSheet(theme.MENU_QSS)
        fx_menu.setAttribute(Qt.WA_TranslucentBackground, True)
        for eid in EFFECT_ORDER:
            act = QAction(icons.effect_icon(eid), EFFECTS[eid].name, fx_menu)
            act.triggered.connect(lambda _=False, e=eid: self.pet.play(e))
            fx_menu.addAction(act)
        fx_menu.addSeparator()
        act_sound = QAction("音效", fx_menu)
        act_sound.setCheckable(True)
        act_sound.setChecked(self.pet.cfg.sound_enabled)
        act_sound.toggled.connect(self.pet._toggle_sound)
        fx_menu.addAction(act_sound)

        act_music = QAction(icons.icon_music(), "音乐感知", fx_menu)
        act_music.setCheckable(True)
        act_music.setChecked(self.cfg.music_aware)
        act_music.toggled.connect(self.set_music_aware)
        fx_menu.addAction(act_music)

        act_roam = QAction(icons.icon_moon(), "空闲自动漫游", fx_menu)
        act_roam.setCheckable(True)
        act_roam.setChecked(self.cfg.roam_enabled)
        act_roam.toggled.connect(self.set_roam_enabled)
        fx_menu.addAction(act_roam)
        menu.addMenu(fx_menu).setIcon(icons.icon_sparkle())

        act_settings = QAction(icons.icon_gear(), "设置…", menu)
        act_settings.triggered.connect(self.open_settings)
        menu.addAction(act_settings)

        act_hotkey = QAction(icons.icon_check() if self.cfg.hotkey_enabled else icons.icon_gear(),
                             "全局快捷键（Ctrl+Alt+B）", menu)
        act_hotkey.setCheckable(True)
        act_hotkey.setChecked(self.cfg.hotkey_enabled)
        act_hotkey.toggled.connect(self.set_hotkey_enabled)
        menu.addAction(act_hotkey)

        act_gallery = QAction(icons.icon_star(), "切换 Buddy…", menu)
        act_gallery.triggered.connect(lambda: self.open_settings(section="gallery"))
        menu.addAction(act_gallery)

        act_rps = QAction(icons.icon_gift(), "石头剪刀布…", menu)
        act_rps.triggered.connect(self.open_rps)
        menu.addAction(act_rps)
        menu.addSeparator()

        act_quit = QAction(icons.icon_power(), "退出", menu)
        act_quit.triggered.connect(self.quit)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_pet()

    # ---------------------------------------------------------- 宠物
    def show_pet(self) -> None:
        if not self.pet.isVisible():
            self.pet.show()
            self.pet._apply_mask(False)
        self.pet.raise_()
        self.engine.say("我在这儿～", 1.6)

    def hide_pet(self) -> None:
        self.pet.hide()
        self.overlay.hide()
        self.tray.showMessage(
            "Buddy 已隐藏", "点托盘图标或按 Ctrl+Alt+B 可以叫我回来", self.icon, 2500
        )

    def toggle_pet(self) -> None:
        """全局快捷键 / 命令通道：可见就藏，藏着就叫出来。

        隐藏时窗口已经不可见，气泡没地方画 —— 所以隐藏只走托盘消息提示，
        显示时才用气泡说一句。
        """
        if self.pet.isVisible():
            self.hide_pet()
            return
        self.show_pet()
        self.engine.say(lines.pick("hotkey_show"), 2.0)

    # ---------------------------------------------------------- 对话框
    def open_settings(self, section: str = "") -> None:
        """打开设置面板；``section="gallery"`` 时定位到图鉴分组（T04 消费）。"""
        dlg = SettingsDialog(
            self.cfg,
            ROOT,
            self.sprites.raw[0],
            self.pet,
            **_dialog_kwargs(section, self.gallery, self.switcher),
        )
        dlg.preview.connect(self.pet.apply_settings)
        self.roam.interrupt()       # 打开面板=人在操作，立刻收场再弹窗
        self.pet.set_overlay_suppressed(True)
        try:
            accepted = bool(dlg.exec_())
        finally:
            self.pet.set_overlay_suppressed(False)
        self.pet.apply_settings()
        if accepted:
            self.engine.say("设置已保存", 1.5)
        # 面板里可能改了热键/音乐感知/漫游开关：取消与保存都要同步到真实状态
        self.hotkeys.set_enabled(self.cfg.hotkey_enabled)
        self.music.set_enabled(self.cfg.music_aware)
        if not self.cfg.music_aware:
            self.pet.on_music_state(False)
        self.roam.set_enabled(self.cfg.roam_enabled)
        self.idle.set_enabled(self.cfg.roam_enabled)

    def open_about(self) -> None:
        dlg = AboutDialog(self.sprites.raw[0], self.pet)
        self.pet.set_overlay_suppressed(True)
        try:
            dlg.exec_()
        finally:
            self.pet.set_overlay_suppressed(False)

    def open_rps(self) -> None:
        """开一局石头剪刀布（模态）；防重入，且不阻塞其它功能。"""
        if self._rps_open:
            return
        self._rps_open = True
        self.pet.set_overlay_suppressed(True)
        try:
            try:
                pixmap = self.pet.sprites.raw[0]
            except (AttributeError, IndexError):
                pixmap = None
            dlg = RPSDialog(self.cfg, pixmap, self.pet)
            dlg.round_finished.connect(self._on_rps_round)
            dlg.exec_()
            # 卡片是一次性的：主动销毁，别留一个隐藏窗口常驻进程
            # （否则按 Esc 之后用 FindWindow 仍能找到一个「幽灵」窗口）
            dlg.deleteLater()
        except Exception:
            # 小游戏打不开不能连累桌宠本身：说一句就继续过日子
            self.engine.say("小游戏没打开，抱歉", 2.0)
        finally:
            self._rps_open = False
            self.pet.set_overlay_suppressed(False)
            result = self._rps_result
            self._rps_result = ""
            if result:
                # 弹窗期间粒子层被压住，看不清；关掉后再放一次，让反馈真正可见
                self._on_rps_round(result)
                self._rps_result = ""
            self.pet.apply_settings()

    def _on_rps_round(self, result: str) -> None:
        """一局结束：赢撒花、输起跳、平眨眼。"""
        self._rps_result = result
        if result == "win":
            self.pet.play("confetti", quiet=True)
        elif result == "lose":
            self.pet.play("hop", quiet=True)
        else:
            self.pet.play("blink", quiet=True)

    # ---------------------------------------------------------- 音乐感知 / 热键
    def set_music_aware(self, checked: bool) -> None:
        """开关音乐感知：写配置、开/关轮询、给一句气泡提示。"""
        checked = bool(checked)
        self.cfg.music_aware = checked
        config.save(self.cfg)
        self.music.set_enabled(checked)
        if not checked:
            # 关掉后立刻收掉律动，不然宠物会一直摇
            self.pet.on_music_state(False)
            self.engine.say("音乐感知已关闭", 1.8)
        else:
            self.engine.say("音乐感知已开启", 1.8)

    def set_roam_enabled(self, checked: bool) -> None:
        """开关空闲自动漫游：写配置、同步检测器与控制器，并给一句气泡。"""
        checked = bool(checked)
        self.cfg.roam_enabled = checked
        config.save(self.cfg)
        self.roam.set_enabled(checked)     # 关闭时立刻归位
        self.idle.set_enabled(checked)     # 关闭时若正空闲会补发 idle_ended（此时已是 off，幂等）
        if checked:
            self.engine.say(f"离开 {self.cfg.roam_idle_min} 分钟我就去溜达", 2.2)
        else:
            self.engine.say("空闲漫游已关闭", 1.8)

    def set_hotkey_enabled(self, checked: bool) -> None:
        """开关全局快捷键：写配置并立即注册/注销。"""
        checked = bool(checked)
        self.cfg.hotkey_enabled = checked
        config.save(self.cfg)
        if not checked:
            self.hotkeys.stop()
            self.engine.say("全局快捷键已关闭", 1.8)
            return
        if self.hotkeys.set_enabled(True):
            self.engine.say(f"按 {self.cfg.hotkey_toggle} 叫我", 2.0)

    def _on_hotkey_failed(self, spec: str) -> None:
        """注册失败：托盘提示 + 气泡，但不影响其它任何功能。"""
        try:
            self.tray.showMessage(
                "全局快捷键没抢到",
                f"{spec} 可能被别的软件占用了，可在设置里关掉",
                self.icon,
                3000,
            )
        except Exception:
            pass
        self.engine.say(lines.pick("hotkey_fail"), 3.0)

    # ---------------------------------------------------------- 外部命令（单实例唤起 / 自动化测试）
    def handle_command(self, conn) -> None:
        raw = bytes(conn.readAll()).decode("utf-8", "ignore").strip()
        for cmd in raw.splitlines():
            cmd = cmd.strip()
            if not cmd or cmd == "show":
                self.show_pet()
            elif cmd == "hide":
                self.hide_pet()
            elif cmd == "settings":
                self.open_settings()
            elif cmd == "menu":
                from PyQt5.QtCore import QTimer
                from PyQt5.QtGui import QCursor

                QTimer.singleShot(0, lambda: self.pet._show_menu(QCursor.pos()))
            elif cmd == "quit":
                self.quit()
            elif cmd.startswith("edge:"):
                self._test_edge(cmd.split(":", 1)[1])
            elif cmd.startswith("effect:"):
                self.show_pet()
                self.pet.play(cmd.split(":", 1)[1])
            elif cmd == "gallery":
                self.open_settings(section="gallery")
            elif cmd == "gallery:refresh":
                self.gallery.refresh()
            elif cmd == "gallery:dump":
                self.dump_gallery()
            elif cmd.startswith("skin:"):
                self._cmd_skin(cmd.split(":", 1)[1])
            elif cmd == "rps":
                self.open_rps()
            elif cmd == "music:on":
                self.music.force(True)
            elif cmd == "music:off":
                self.music.force(False)
            elif cmd == "music:status":
                self.dump_music_status()
            elif cmd == "idle:on":
                self.show_pet()
                self.idle.force(True, hold_s=FORCE_HOLD_S)
            elif cmd == "idle:off":
                self.idle.force(False)
            elif cmd == "idle:status":
                self.dump_idle_status()
            elif cmd == "roam:toggle":
                self.set_roam_enabled(not self.cfg.roam_enabled)
            elif cmd == "hotkey:toggle":
                self.toggle_pet()
            elif cmd.startswith("drop:test:"):
                # 直接喂一条路径给拖放处理，不依赖真实鼠标拖拽
                self.pet.simulate_drop([cmd.split(":", 2)[2]])

    def _cmd_skin(self, rest: str) -> None:
        """``skin:<buddy_id>:<skin_id>``：直接应用（供 QA 自动化）。"""
        parts = rest.split(":")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            return
        self.switcher.apply_by_ids(parts[0], parts[1])

    def dump_gallery(self) -> None:
        """把当前快照摘要写到 ``%APPDATA%/WorkBuddyPet/gallery_dump.json``（供 QA 断言）。"""
        try:
            payload = self.gallery.dump_summary()
            path = config.config_dir() / DUMP_NAME
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            pass

    def dump_music_status(self) -> None:
        """把一次音乐探测的结果写到 ``%APPDATA%/WorkBuddyPet/music_status.json``（供 QA 断言）。"""
        try:
            payload = self.music.poll_now()
            path = config.config_dir() / MUSIC_STATUS_NAME
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            pass

    def dump_idle_status(self) -> None:
        """把空闲检测 + 漫游状态写到 ``%APPDATA%/WorkBuddyPet/idle_status.json``。"""
        try:
            payload = self.idle.poll_now()
            payload["roam"] = self.roam.status()
            path = config.config_dir() / IDLE_STATUS_NAME
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            pass

    def _test_edge(self, side: str) -> None:
        """调试用：把宠物放到指定侧边并触发抓握。"""
        from PyQt5.QtWidgets import QApplication as _QApp

        area = _QApp.primaryScreen().availableGeometry()
        pet = self.pet
        x = max(area.left() + 60, min(pet.x(), area.right() - pet.width() - 60))
        y = max(area.top() + 60, min(pet.y(), area.bottom() - pet.height() - 60))
        if side == "left":
            pet.move(area.left() + 6, y)
        elif side == "right":
            pet.move(area.right() - pet.width() - 5, y)
        elif side == "top":
            pet.move(x, area.top() + 6)
        else:
            pet.move(x, area.bottom() - pet.height() - 5)
        pet._try_grab_edge()

    # ---------------------------------------------------------- 退出
    def quit(self) -> None:
        # 先停掉系统级资源：热键不注销会一直占着这个组合键，直到进程退出
        try:
            self.hotkeys.stop()
        except Exception:
            pass
        try:
            self.music.stop()
        except Exception:
            pass
        # 漫游中退出：先归位（或把落点记成新家），再取位置落盘，
        # 否则会把半路坐标存成下次启动的位置。
        try:
            self.roam.finish(immediate=True)
        except Exception:
            pass
        try:
            self.idle.stop()
        except Exception:
            pass
        self.cfg.pos = [self.pet.x(), self.pet.y()]
        config.save(self.cfg)
        self.overlay.hide()
        self.tray.hide()
        self.app.quit()


def main() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass

    app = QApplication(sys.argv)
    app.setApplicationName("Buddy 桌面宠物")
    app.setQuitOnLastWindowClosed(False)

    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if socket.waitForConnected(200):
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(200)
        return 0

    QLocalServer.removeServer(SERVER_NAME)
    server = QLocalServer()
    server.listen(SERVER_NAME)

    pet_app = PetApp(app)

    def _on_connection() -> None:
        conn = server.nextPendingConnection()
        if conn is None:
            return
        conn.readyRead.connect(lambda: pet_app.handle_command(conn))
        conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(_on_connection)
    app._pet_app = pet_app  # 防止被回收
    app._server = server

    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())
