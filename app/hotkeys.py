"""全局快捷键（仅 Windows）：一个组合键唤出/隐藏宠物。

实现要点
-------
- 用 Win32 ``RegisterHotKey`` 注册系统级热键，再用
  ``QAbstractNativeEventFilter`` 在 Qt 事件循环里捕获 ``WM_HOTKEY``；
- 只注册一个热键，id 固定；``MOD_NOREPEAT`` 关掉长按连发，避免宠物闪烁；
- 非 Windows / 注册失败（典型 1409 = 已被别的软件占用）**不抛异常**，
  只发 ``failed(spec)``，由上层决定如何提示；
- ``stop()`` 幂等：重复调用不会出问题。

注意：``RegisterHotKey`` 只能在**创建窗口的同一个线程**（GUI 线程）里注册，
本类所有方法都只在 GUI 线程调用。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Optional

from PyQt5.QtCore import QAbstractNativeEventFilter, QObject, pyqtSignal

# ---- Win32 常量 ----
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312

DEFAULT_SPEC = "Ctrl+Alt+B"

#: 本进程内唯一的热键 id（0x0000-0xBFFF 为应用私有区间，任取一个不易冲突的值）
HOTKEY_ID = 0xB0B

_IS_WINDOWS = sys.platform == "win32"

#: 修饰键别名 -> Win32 标志位
_MOD_ALIASES: dict[str, int] = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "meta": MOD_WIN,
    "cmd": MOD_WIN,
}

#: 具名按键 -> 虚拟键码（字母 / 数字走 ord，F1-F24 走 0x70 偏移）
_KEY_ALIASES: dict[str, int] = {
    "SPACE": 0x20,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "TAB": 0x09,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "BACKSPACE": 0x08,
    "DELETE": 0x2E,
    "INSERT": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "LEFT": 0x25,
    "UP": 0x26,
    "RIGHT": 0x27,
    "DOWN": 0x28,
    "PLAY": 0xB3,
    "PAUSE": 0xB3,
    "NEXT": 0xB0,
    "PREV": 0xB1,
}


def _parse_key(token: str) -> Optional[int]:
    """把 ``"B"`` / ``"F1"`` / ``"Space"`` 这类按键名解析成虚拟键码。"""
    name = (token or "").strip().upper()
    if not name:
        return None
    if len(name) == 1:
        ch = name[0]
        if ("A" <= ch <= "Z") or ("0" <= ch <= "9"):
            return ord(ch)
        return None
    if name in _KEY_ALIASES:
        return _KEY_ALIASES[name]
    if name.startswith("F") and name[1:].isdigit():
        num = int(name[1:])
        if 1 <= num <= 24:
            return 0x70 + num - 1
    return None


def parse_spec(spec: str) -> Optional[tuple[int, int]]:
    """把 ``"Ctrl+Alt+B"`` 解析成 ``(mods, vk)``；非法字符串返回 ``None``。

    规则：最后一个 ``+`` 之后是主键，其余各项都是修饰键；大小写不敏感。
    """
    raw = (spec or "").strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.split("+") if p.strip()]
    if len(parts) < 2:
        return None
    mods = 0
    for part in parts[:-1]:
        flag = _MOD_ALIASES.get(part.lower())
        if flag is None:
            return None
        mods |= flag
    vk = _parse_key(parts[-1])
    if vk is None:
        return None
    return mods, vk


class _HotkeyFilter(QAbstractNativeEventFilter):
    """原生事件过滤器：只认 ``WM_HOTKEY``，其余一律放行。"""

    def __init__(self, owner: "GlobalHotkeys"):
        super().__init__()
        self._owner = owner

    def nativeEventFilter(self, eventType, message):  # noqa: N802 (Qt 命名)
        owner = self._owner
        try:
            if owner is None or not owner.is_registered():
                return (False, 0)
            # Windows 平台插件有两种事件类型名，都要接
            if eventType not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
                return (False, 0)
            msg = wintypes.MSG.from_address(int(message))
            if msg.message != WM_HOTKEY:
                return (False, 0)
            if int(msg.wParam) != owner.hotkey_id():
                return (False, 0)
            owner.emit_triggered()
        except Exception:
            # 原生事件回调里抛异常会打断 Qt 事件循环，一律吞掉
            return (False, 0)
        return (False, 0)


class GlobalHotkeys(QObject):
    """注册/注销一个全局热键，命中时发 ``triggered(spec)``。

    Signals:
        triggered: 热键被按下，参数为当前注册的组合键字符串。
        failed: 注册失败（含非 Windows），参数为尝试注册的组合键字符串。
    """

    triggered = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._spec: str = DEFAULT_SPEC
        self._mods: int = 0
        self._vk: int = 0
        self._registered: bool = False
        self._enabled: bool = False
        self._filter: _HotkeyFilter | None = None

    # ------------------------------------------------------------ 查询
    def hotkey_id(self) -> int:
        """当前热键 id（事件过滤器用它比对 ``wParam``）。"""
        return HOTKEY_ID

    def is_registered(self) -> bool:
        """热键当前是否真的注册在系统里。"""
        return bool(self._registered)

    def spec(self) -> str:
        """当前注册（或上次尝试注册）的组合键字符串。"""
        return self._spec

    def is_enabled(self) -> bool:
        """设置层面的开关状态（``set_enabled`` 的入参）。"""
        return bool(self._enabled)

    # ------------------------------------------------------------ 注册
    def register(self, spec: str = DEFAULT_SPEC) -> bool:
        """注册 ``spec``；成功返回 ``True``，失败返回 ``False`` 并发 ``failed``。"""
        self._spec = (spec or "").strip() or DEFAULT_SPEC
        if not _IS_WINDOWS:
            # 非 Windows：完整降级，不注册、不抛异常、也不打扰用户
            return False
        parsed = parse_spec(self._spec)
        if parsed is None:
            self.failed.emit(self._spec)
            return False
        mods, vk = parsed

        # 先清掉旧注册：同一进程重复注册同一个键也会失败，必须先放掉
        self._unregister()

        try:
            user32 = ctypes.windll.user32
            ok = bool(
                user32.RegisterHotKey(
                    None, HOTKEY_ID, int(mods | MOD_NOREPEAT), int(vk)
                )
            )
        except Exception:
            ok = False
        if not ok:
            # 典型原因：1409 = 该组合键已被其它程序占用
            self.failed.emit(self._spec)
            return False

        self._mods, self._vk = mods, vk
        self._registered = True
        self._install_filter()
        return True

    def set_enabled(self, value: bool) -> bool:
        """开关热键；返回「当前是否处于已注册状态」。"""
        value = bool(value)
        self._enabled = value
        if not value:
            self._unregister()
            return False
        if self._registered:
            return True
        return self.register(self._spec)

    def emit_triggered(self) -> None:
        """由事件过滤器调用：把「热键被按下」翻译成 Qt 信号。"""
        self.triggered.emit(self._spec)

    # ------------------------------------------------------------ 清理
    def stop(self) -> None:
        """注销热键并移除事件过滤器；幂等，可重复调用。"""
        self._enabled = False
        self._unregister()

    def _unregister(self) -> None:
        if self._registered:
            try:
                ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
            except Exception:
                pass
        self._registered = False
        self._remove_filter()

    def _install_filter(self) -> None:
        if self._filter is not None:
            return
        try:
            from PyQt5.QtWidgets import QApplication

            app = QApplication.instance()
            if app is None:
                return
            self._filter = _HotkeyFilter(self)
            app.installNativeEventFilter(self._filter)
        except Exception:
            self._filter = None

    def _remove_filter(self) -> None:
        flt = self._filter
        if flt is None:
            return
        self._filter = None
        try:
            from PyQt5.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                app.removeNativeEventFilter(flt)
        except Exception:
            pass
