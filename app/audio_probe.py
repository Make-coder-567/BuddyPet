"""系统音频探测（零依赖，仅 Windows）。

两个能力
-------
- ``probe_peak()``：读默认输出设备的峰值电平
  （Core Audio 的 ``IAudioMeterInformation::GetPeakValue``）；
- ``detect_music_app()``：枚举可见窗口，按进程名 / 标题判断是否有音乐播放器在运行。

设计原则
-------
- 全程 ``try/except``：任何一步失败都只表示「探测不到」，绝不把异常抛给调用方；
- 非 Windows 直接返回 ``None`` / ``(False, "")``，保证跨平台不崩；
- 只用标准库 ``ctypes``，不引入任何第三方依赖；
- 不阻塞：单次探测是纯函数调用级开销（毫秒级），由调用方控制轮询频率。

COM 调用约定提醒：``IAudioMeterInformation`` 的虚表索引为
``[0]=QueryInterface [1]=AddRef [2]=Release [3]=GetPeakValue``；
``IMMDeviceEnumerator`` 的 ``[4]=GetDefaultAudioEndpoint``；
``IMMDevice`` 的 ``[3]=Activate``。
"""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import POINTER, byref, c_float, c_void_p, wintypes

_IS_WINDOWS = sys.platform == "win32"

_CLSCTX_ALL = 23

#: 音乐播放器进程名（小写）——命中即认为是音乐
MUSIC_EXES: frozenset[str] = frozenset(
    {
        "cloudmusic.exe",
        "qqmusic.exe",
        "spotify.exe",
        "foobar2000.exe",
        "music.ui.exe",
        "aimp.exe",
        "lx-music-desktop.exe",
        "yesplaymusic.exe",
        "kugou.exe",
        "kuwo.exe",
    }
)

#: 窗口标题关键词（进程名识别不到时的回退判据）
MUSIC_TITLE_KEYWORDS: tuple[str, ...] = (
    "网易云音乐",
    "QQ音乐",
    "酷狗音乐",
    "酷我音乐",
    "Spotify",
    "foobar2000",
    "Apple Music",
)

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

#: COM 只需在本进程初始化一次（重复 CoInitialize 会让引用计数涨上去）
_COM_READY = False


class GUID(ctypes.Structure):
    """Windows GUID（16 字节）。"""

    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid(text: str) -> GUID:
    """把 ``"{XXXX-...}"`` 形式的字符串转成 :class:`GUID`。"""
    g = GUID()
    if ctypes.windll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), byref(g)) != 0:
        raise OSError("CLSIDFromString failed")
    return g


def _ensure_com() -> None:
    """初始化本线程的 COM（幂等）。即使失败也只是让后续调用返回 None。"""
    global _COM_READY
    if _COM_READY:
        return
    try:
        ctypes.windll.ole32.CoInitialize(None)
        _COM_READY = True
    except Exception:
        pass


def probe_peak() -> float | None:
    """读默认播放设备的峰值电平（0.0~1.0）。

    Returns:
        float | None: 成功时返回峰值（静音/无输出时约为 0.0）；
        设备不可用、无音频硬件、非 Windows 或任何 COM 失败时返回 ``None``。
    """
    if not _IS_WINDOWS:
        return None
    try:
        ole32 = ctypes.windll.ole32
        CLSID = _guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")          # MMDeviceEnumerator
        IID_EN = _guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")         # IMMDeviceEnumerator
        IID_MT = _guid("{C02216F6-8C67-4B5B-9D00-D008E73E0064}")         # IAudioMeterInformation
        _ensure_com()
        enum = c_void_p()
        if ole32.CoCreateInstance(byref(CLSID), None, _CLSCTX_ALL, byref(IID_EN), byref(enum)) != 0:
            return None
        try:
            vt = ctypes.cast(enum, POINTER(POINTER(c_void_p)))[0]
            GetDefault = ctypes.WINFUNCTYPE(
                ctypes.c_long, c_void_p, ctypes.c_int, ctypes.c_int, POINTER(c_void_p)
            )(vt[4])
            dev = c_void_p()
            if GetDefault(enum, 0, 0, byref(dev)) != 0:
                return None
            try:
                vt2 = ctypes.cast(dev, POINTER(POINTER(c_void_p)))[0]
                Activate = ctypes.WINFUNCTYPE(
                    ctypes.c_long,
                    c_void_p,
                    POINTER(GUID),
                    wintypes.DWORD,
                    c_void_p,
                    POINTER(c_void_p),
                )(vt2[3])
                meter = c_void_p()
                if Activate(dev, byref(IID_MT), _CLSCTX_ALL, None, byref(meter)) != 0:
                    return None
                try:
                    vt3 = ctypes.cast(meter, POINTER(POINTER(c_void_p)))[0]
                    GetPeak = ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, POINTER(c_float))(vt3[3])
                    peak = c_float()
                    if GetPeak(meter, byref(peak)) != 0:
                        return None
                    return float(peak.value)
                finally:
                    ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vt3[2])(meter)
            finally:
                ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vt2[2])(dev)
        finally:
            ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vt[2])(enum)
    except Exception:
        return None


def _process_name(pid: int) -> str:
    """按 PID 取可执行文件名（小写）；取不到返回空串。"""
    if pid <= 0:
        return ""
    handle = None
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return ""
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(int(size.value))
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, byref(size)):
            return ""
        return os.path.basename(buf.value).strip().lower()
    except Exception:
        return ""
    finally:
        if handle:
            try:
                ctypes.windll.kernel32.CloseHandle(handle)
            except Exception:
                pass


def detect_music_app() -> tuple[bool, str]:
    """枚举可见顶层窗口，判断是否有音乐播放器在跑。

    Returns:
        tuple[bool, str]: ``(是否命中, 命中窗口的标题)``；未命中为 ``(False, "")``。
    """
    if not _IS_WINDOWS:
        return (False, "")
    try:
        user32 = ctypes.windll.user32
        found: list[str] = []

        def _visit(hwnd, _lparam) -> bool:
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = int(user32.GetWindowTextLengthW(hwnd))
                if length <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                if not user32.GetWindowTextW(hwnd, buf, length + 1):
                    return True
                title = (buf.value or "").strip()
                if not title:
                    return True
                pid = wintypes.DWORD(0)
                user32.GetWindowThreadProcessId(hwnd, byref(pid))
                exe = _process_name(int(pid.value))
                hit = exe in MUSIC_EXES
                if not hit:
                    hit = any(key.lower() in title.lower() for key in MUSIC_TITLE_KEYWORDS)
                if hit:
                    found.append(title)
                    return False     # 找到一个就停，省时间
                return True
            except Exception:
                return True

        enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(_visit)
        user32.EnumWindows(enum_proc, 0)
        if found:
            return (True, found[0])
        return (False, "")
    except Exception:
        return (False, "")
