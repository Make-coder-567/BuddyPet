"""石头剪刀布小游戏：纯逻辑 + 一张无边框卡片对话框。

- 纯逻辑部分（``CHOICES`` / ``LABELS`` / ``judge``）不依赖 Qt，方便单测；
- ``RPSDialog`` 继承 :class:`~app.settings_dialog.CardDialog`，与设置面板同一套
  视觉语言（暖色深灰卡片 + 橙色强调）；
- 不在这里 ``exec_()``，由调用方（``main.PetApp.open_rps``）决定何时弹出；
- 计数直接写 ``cfg.rps_wins / rps_losses / rps_draws`` 并落盘。
"""
from __future__ import annotations

import ctypes
import random
import sys
from ctypes import wintypes

from PyQt5.QtCore import QAbstractNativeEventFilter, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import lines, theme
from .config import Settings, save
from .settings_dialog import CardDialog

#: 出拳顺序：石头 > 剪刀 > 布 > 石头
CHOICES: tuple[str, ...] = ("rock", "scissors", "paper")

#: 中文名（气泡/按钮都用它，不用 emoji）
LABELS: dict[str, str] = {"rock": "石头", "scissors": "剪刀", "paper": "布"}

#: 谁克谁：key 克 value
BEATS: dict[str, str] = {"rock": "scissors", "scissors": "paper", "paper": "rock"}

#: 结果 -> (状态行文案, 台词池 key)
_RESULT_TEXT: dict[str, tuple[str, str]] = {
    "win": ("你赢了！", "rps_win"),
    "lose": ("我赢了！", "rps_lose"),
    "draw": ("平局", "rps_draw"),
}

#: 倒计时文案（每 220ms 一句）
COUNTDOWN: tuple[str, ...] = ("石头…", "剪刀…", "布！")
COUNTDOWN_MS = 220

_IS_WINDOWS = sys.platform == "win32"
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104

#: 虚拟键码 -> 内部动作名
_VK_ACTIONS: dict[int, str] = {
    0x1B: "escape",      # VK_ESCAPE
    0x0D: "enter",       # VK_RETURN
    0x31: "1",           # 主键盘 1
    0x32: "2",
    0x33: "3",
    0x61: "1",           # 小键盘 1（VK_NUMPAD1）
    0x62: "2",
    0x63: "3",
}

#: 选项按钮的补充样式（橙边、圆角、hover 微亮、disabled 降透明度）
_RPS_QSS = """
QLabel#RPSPanel {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 12px;
    color: #FF7A3D;
    font-size: 15pt;
    font-weight: 600;
}
QPushButton#RPSChoice {
    background-color: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 122, 61, 0.45);
    border-radius: 12px;
    color: #F3F4F6;
    font-size: 11pt;
    font-weight: 600;
    min-height: 76px;
}
QPushButton#RPSChoice:hover {
    background-color: rgba(255, 122, 61, 0.16);
    border-color: rgba(255, 122, 61, 0.85);
}
QPushButton#RPSChoice:pressed {
    background-color: rgba(255, 122, 61, 0.28);
}
QPushButton#RPSChoice:disabled {
    background-color: rgba(255, 255, 255, 0.03);
    border-color: rgba(255, 255, 255, 0.07);
    color: rgba(243, 244, 246, 0.30);
}
"""


class _RpsKeyFilter(QAbstractNativeEventFilter):
    """应用级原生事件过滤器：接管小游戏窗口上的 ``WM_KEYDOWN``。

    为什么不用 ``QWidget.nativeEvent`` / 只靠 ``keyPressEvent``：Windows 下
    ``WM_KEYDOWN`` 会先经过 Qt 的键盘映射器，若该窗口当时不是焦点窗口
    （自动化里直接 ``PostMessageW`` 就是这种情况），Qt 会把键事件丢掉，
    ``keyPressEvent`` 根本收不到。装在 ``QApplication`` 上的原生过滤器能看到
    **每一条**消息、与焦点无关，最稳妥；命中后返回 ``True`` 把消息吃掉，
    Qt 就不会再翻译成 ``QKeyEvent``，因此不会出现「键盘与原生两条路径各触发一次」。
    """

    def __init__(self, owner: "RPSDialog", hwnd: int):
        super().__init__()
        self._owner = owner
        self._hwnd = int(hwnd or 0)

    def nativeEventFilter(self, eventType, message):  # noqa: N802 (Qt 命名)
        owner = self._owner
        try:
            if owner is None or self._hwnd == 0:
                return (False, 0)
            if eventType not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                return (False, 0)
            msg = wintypes.MSG.from_address(int(message))
            if msg.message not in (WM_KEYDOWN, WM_SYSKEYDOWN):
                return (False, 0)
            if int(msg.hwnd) != self._hwnd:
                return (False, 0)
            action = _VK_ACTIONS.get(int(msg.wParam))
            if action is None:
                return (False, 0)
            owner.handle_action(action)
            return (True, 0)
        except Exception:
            # 原生回调里抛异常会打断事件循环，一律吞掉
            return (False, 0)


def judge(player: str, pet: str) -> str:
    """判定胜负。

    Args:
        player: 玩家出拳，取值见 :data:`CHOICES`。
        pet: 宠物出拳，取值见 :data:`CHOICES`。

    Returns:
        str: ``"win"``（玩家赢）/ ``"lose"``（宠物赢）/ ``"draw"``（平局）。
        非法入参一律按平局处理，保证不会抛异常。
    """
    if player not in BEATS or pet not in BEATS:
        return "draw"
    if player == pet:
        return "draw"
    return "win" if BEATS[player] == pet else "lose"


class RPSDialog(CardDialog):
    """石头剪刀布卡片。

    Signals:
        round_finished: 一局结束，参数为 ``"win"`` / ``"lose"`` / ``"draw"``，
            供主窗口播放对应特效。
    """

    round_finished = pyqtSignal(str)

    def __init__(self, cfg: Settings, pet_pixmap: QPixmap, parent: QWidget | None = None):
        super().__init__(parent, width=380)
        self.cfg = cfg
        self._busy = False          # 倒计时期间锁住输入
        self._seq = 0               # 倒计时进度
        self._player = ""
        self._pet = ""
        self._rounds = 0

        self._timer = QTimer(self)
        self._timer.setInterval(COUNTDOWN_MS)
        self._timer.timeout.connect(self._step)

        # 键盘：键盘事件（keyPressEvent）与原生过滤器双保险，见 _RpsKeyFilter 注释
        self._hwnd = 0
        self._key_filter: _RpsKeyFilter | None = None

        self.setWindowTitle("石头 · 剪刀 · 布")
        self.setFocusPolicy(Qt.StrongFocus)
        self._build(pet_pixmap)
        self.setStyleSheet(theme.DIALOG_QSS + _RPS_QSS)
        self._update_score()

    # ------------------------------------------------------------ 原生按键
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._install_key_filter()

    def _install_key_filter(self) -> None:
        """把应用级原生过滤器挂上（仅 Windows；其它平台返回无损）。"""
        if not _IS_WINDOWS or self._key_filter is not None:
            return
        try:
            hwnd = int(self.winId())
        except Exception:
            return
        if hwnd == 0:
            return
        self._hwnd = hwnd
        try:
            app = QApplication.instance()
            if app is None:
                return
            self._key_filter = _RpsKeyFilter(self, hwnd)
            app.installNativeEventFilter(self._key_filter)
        except Exception:
            self._key_filter = None

    def _remove_key_filter(self) -> None:
        flt = self._key_filter
        if flt is None:
            return
        self._key_filter = None
        try:
            app = QApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(flt)
        except Exception:
            pass

    def handle_action(self, action: str) -> None:
        """把「按键动作名」翻译成具体行为（键盘 / 原生过滤器共用）。"""
        if not self.isVisible():
            return
        if action == "escape":
            self.reject()
        elif action == "enter":
            if self.again_btn.isVisible():
                self.again()
        elif action in ("1", "2", "3"):
            self.choose({"1": "rock", "2": "scissors", "3": "paper"}[action])

    # ------------------------------------------------------------ 构建
    def _build(self, pet_pixmap: QPixmap) -> None:
        self.body.setContentsMargins(22, 18, 22, 18)
        self.body.setSpacing(10)

        # ---- 标题行 ----
        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("石头 · 剪刀 · 布")
        title.setObjectName("Title")
        header.addWidget(title)
        header.addStretch(1)
        close = QPushButton("×")
        close.setObjectName("Ghost")
        close.setFixedSize(26, 26)
        close.setFocusPolicy(Qt.NoFocus)
        close.setStyleSheet("font-size: 15pt; padding: 0px; margin-bottom: 4px;")
        close.clicked.connect(self.reject)
        header.addWidget(close, 0, Qt.AlignTop)
        self.body.addLayout(header)

        # ---- 宠物头像 + 台词 ----
        pet_row = QHBoxLayout()
        pet_row.setSpacing(10)
        avatar = QLabel()
        avatar.setFixedSize(72, 72)
        avatar.setAlignment(Qt.AlignCenter)
        if pet_pixmap is not None and not pet_pixmap.isNull():
            try:
                avatar.setPixmap(pet_pixmap.scaledToHeight(72, Qt.SmoothTransformation))
            except Exception:
                pass
        pet_row.addWidget(avatar, 0, Qt.AlignTop)
        self.speech = QLabel(lines.pick("rps_open"))
        self.speech.setObjectName("Sub")
        self.speech.setWordWrap(True)
        self.speech.setMinimumHeight(48)
        pet_row.addWidget(self.speech, 1)
        self.body.addLayout(pet_row)

        # ---- 双方结果面板 ----
        panels = QHBoxLayout()
        panels.setSpacing(12)
        you_holder, self.you_panel = self._make_panel("你")
        me_holder, self.me_panel = self._make_panel("我")
        panels.addWidget(you_holder, 1)
        panels.addWidget(me_holder, 1)
        self.body.addLayout(panels)

        # ---- 状态行 / 战绩行 ----
        self.status = QLabel("选一个出吧～")
        self.status.setObjectName("Sub")
        self.status.setAlignment(Qt.AlignCenter)
        self.body.addWidget(self.status)

        self.score = QLabel("")
        self.score.setObjectName("Sub")
        self.score.setAlignment(Qt.AlignCenter)
        self.body.addWidget(self.score)

        # ---- 三个大按钮 ----
        choices = QHBoxLayout()
        choices.setSpacing(10)
        self._buttons: dict[str, QPushButton] = {}
        for choice in CHOICES:
            btn = QPushButton(LABELS[choice])
            btn.setObjectName("RPSChoice")
            btn.setMinimumHeight(76)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)      # 键盘统一由对话框处理，避免被按钮抢走
            btn.clicked.connect(lambda _checked=False, c=choice: self.choose(c))
            choices.addWidget(btn, 1)
            self._buttons[choice] = btn
        self.body.addLayout(choices)

        tip = QLabel("按 1 / 2 / 3 也能出拳 · Esc 退出")
        tip.setObjectName("Sub")
        tip.setAlignment(Qt.AlignCenter)
        self.body.addWidget(tip)

        # ---- 底部：再来一局 / 结束 ----
        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.again_btn = QPushButton("再来一局")
        self.again_btn.setObjectName("Primary")
        self.again_btn.setFocusPolicy(Qt.NoFocus)
        self.again_btn.clicked.connect(self.again)
        self.again_btn.hide()
        footer.addWidget(self.again_btn)
        footer.addStretch(1)
        self.quit_btn = QPushButton("结束")
        self.quit_btn.setFocusPolicy(Qt.NoFocus)
        self.quit_btn.clicked.connect(self.reject)
        footer.addWidget(self.quit_btn)
        self.body.addLayout(footer)

    def _make_panel(self, caption: str) -> tuple[QWidget, QLabel]:
        """造一列「标题 + 大写字面板」，返回 (外壳, 值标签)。"""
        col = QVBoxLayout()
        col.setSpacing(5)
        col.setContentsMargins(0, 0, 0, 0)
        cap = QLabel(caption)
        cap.setObjectName("Sub")
        cap.setAlignment(Qt.AlignCenter)
        value = QLabel("?")
        value.setObjectName("RPSPanel")
        value.setFixedHeight(72)
        value.setAlignment(Qt.AlignCenter)
        col.addWidget(cap)
        col.addWidget(value)
        holder = QWidget()
        holder.setLayout(col)
        return holder, value

    # ------------------------------------------------------------ 交互
    def choose(self, choice: str) -> None:
        """玩家出拳：进入倒计时 → 亮拳 → 判定 → 记分。"""
        if self._busy or choice not in CHOICES:
            return
        self._busy = True
        self._player = choice
        self._pet = random.choice(CHOICES)
        self._seq = 0
        for btn in self._buttons.values():
            btn.setEnabled(False)
        self.again_btn.hide()
        self.you_panel.setText("?")
        self.me_panel.setText("?")
        self.speech.setText(f"你出了{LABELS[choice]}，我看招～")
        self.status.setText("")
        self._step()                 # 立刻显示第一句，别先干等 220ms
        if self._busy:
            self._timer.start()

    def _step(self) -> None:
        """倒计时推进一格；走完就揭晓结果（重复调用不会重复记分）。"""
        if not self._busy:
            return
        if self._seq < len(COUNTDOWN):
            self.status.setText(COUNTDOWN[self._seq])
            self._seq += 1
            return
        self._timer.stop()
        self._busy = False          # 先解锁，避免重复进入同一局的结算
        self._reveal()

    def _reveal(self) -> None:
        result = judge(self._player, self._pet)
        self.you_panel.setText(LABELS.get(self._player, "?"))
        self.me_panel.setText(LABELS.get(self._pet, "?"))
        status_text, line_key = _RESULT_TEXT.get(result, ("平局", "rps_draw"))
        self.status.setText(status_text)
        self.speech.setText(lines.pick(line_key))

        if result == "win":
            self.cfg.rps_wins = int(self.cfg.rps_wins) + 1
        elif result == "lose":
            self.cfg.rps_losses = int(self.cfg.rps_losses) + 1
        else:
            self.cfg.rps_draws = int(self.cfg.rps_draws) + 1
        self.cfg.clamp()             # 计数兜底（负数/非整数一律归一）
        save(self.cfg)

        self._rounds += 1
        self._update_score()
        for btn in self._buttons.values():
            btn.setEnabled(True)
        self.again_btn.show()
        self._busy = False
        self.round_finished.emit(result)

    def again(self) -> None:
        """清盘，准备下一局。"""
        if self._busy:
            return
        self._timer.stop()
        self.you_panel.setText("?")
        self.me_panel.setText("?")
        self.status.setText("选一个出吧～")
        self.speech.setText(lines.pick("rps_open"))
        self.again_btn.hide()

    def _update_score(self) -> None:
        self.score.setText(
            f"战绩 {int(self.cfg.rps_wins)} 胜 · "
            f"{int(self.cfg.rps_draws)} 平 · "
            f"{int(self.cfg.rps_losses)} 负"
        )

    # ------------------------------------------------------------ 键盘
    def keyPressEvent(self, event) -> None:
        """键盘操作：1/2/3 出拳、Esc 退出、Enter 再来一局。

        注意：Qt5 里**没有** ``Qt.Key_NumPad1/2/3`` 这几个枚举（PyQt5 5.15 只有
        ``Key_NumLock``），而且 Qt5 把小键盘数字与主键盘数字映射到同一个键码
        （``Key_1 == 0x31``），所以这里只判 ``Key_1/2/3`` 就同时覆盖了两种情况。
        写 ``Qt.Key_NumPad1`` 会抛 AttributeError，进而在 Qt 虚函数里触发
        ``qFatal`` 让整个进程 abort —— 千万不要加回来。
        """
        key = event.key()
        action = {
            Qt.Key_1: "1",
            Qt.Key_2: "2",
            Qt.Key_3: "3",
            Qt.Key_Escape: "escape",
            Qt.Key_Return: "enter",
            Qt.Key_Enter: "enter",
        }.get(key)
        if action is not None:
            self.handle_action(action)
            event.accept()
            return
        super().keyPressEvent(event)

    def done(self, result: int) -> None:
        """关闭（Esc / 结束 / 右上 ×）时先停掉倒计时并摘掉原生过滤器。

        ``reject()`` 走的是 ``done()`` 而不是 ``closeEvent()``，若只在
        ``closeEvent`` 里收尾，按 Esc 后定时器还会在隐藏的对话框上继续
        「亮拳 + 记分」并补发 ``round_finished``。
        """
        self._timer.stop()
        self._busy = False
        self._remove_key_filter()
        super().done(result)

    def closeEvent(self, event) -> None:
        """关掉窗口前先停掉倒计时，避免定时器在已销毁的控件上跑。"""
        self._timer.stop()
        self._remove_key_filter()
        super().closeEvent(event)
