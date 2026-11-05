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

from app import config, icons, theme  # noqa: E402
from app.assets import BuddySprites  # noqa: E402
from app.effects import EFFECT_ORDER, EFFECTS, EffectEngine  # noqa: E402
from app.gallery.service import GalleryService  # noqa: E402
from app.gallery.switcher import SkinSwitcher  # noqa: E402
from app.overlay import EffectOverlay  # noqa: E402
from app.pet_window import PetWindow  # noqa: E402
from app.settings_dialog import AboutDialog, SettingsDialog  # noqa: E402

SERVER_NAME = "WorkBuddyDesktopPet-1"
APP_ID = "WorkBuddy.DesktopPet.1"
DUMP_NAME = "gallery_dump.json"


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

        self.icon = icons.app_icon(self.sprites.raw[0])
        self.app.setWindowIcon(self.icon)
        self._build_tray()
        self.pet.show()
        self.pet.show_intro()
        # 让出一次事件循环，先完成首帧绘制再补图鉴（不等待网络结果）
        QTimer.singleShot(0, self.gallery.startup_sync)

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
        menu.addMenu(fx_menu).setIcon(icons.icon_sparkle())

        act_settings = QAction(icons.icon_gear(), "设置…", menu)
        act_settings.triggered.connect(self.open_settings)
        menu.addAction(act_settings)

        act_gallery = QAction(icons.icon_star(), "切换 Buddy…", menu)
        act_gallery.triggered.connect(lambda: self.open_settings(section="gallery"))
        menu.addAction(act_gallery)
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
        self.tray.showMessage("Buddy 已隐藏", "点托盘图标可以叫我回来", self.icon, 2500)

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
        self.pet.set_overlay_suppressed(True)
        try:
            accepted = bool(dlg.exec_())
        finally:
            self.pet.set_overlay_suppressed(False)
        self.pet.apply_settings()
        if accepted:
            self.engine.say("设置已保存", 1.5)

    def open_about(self) -> None:
        dlg = AboutDialog(self.sprites.raw[0], self.pet)
        self.pet.set_overlay_suppressed(True)
        try:
            dlg.exec_()
        finally:
            self.pet.set_overlay_suppressed(False)

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
