"""定位并唤起 WorkBuddy 主程序。"""
from __future__ import annotations

import ctypes
import os
import subprocess
import winreg
from ctypes import wintypes
from pathlib import Path

EXE_NAME = "WorkBuddy.exe"
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_u32 = ctypes.WinDLL("user32", use_last_error=True)

TH32CS_SNAPPROCESS = 0x00000002
SW_RESTORE = 9


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _iter_process_pids(name: str) -> list[int]:
    pids: list[int] = []
    snapshot = _k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (0, -1):
        return pids
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    try:
        ok = _k32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            if entry.szExeFile.lower() == name.lower():
                pids.append(int(entry.th32ProcessID))
            ok = _k32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        _k32.CloseHandle(snapshot)
    return pids


def _windows_of_pids(pids: set[int]) -> list[int]:
    found: list[int] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if not _u32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and _u32.GetWindowTextLengthW(hwnd) > 0:
            found.append(int(hwnd))
        return True

    _u32.EnumWindows(WNDENUMPROC(_cb), 0)
    return found


def activate_running() -> bool:
    pids = set(_iter_process_pids(EXE_NAME))
    if not pids:
        return False
    windows = _windows_of_pids(pids)
    if not windows:
        return True  # 进程在，但还没有可见窗口（启动中）
    hwnd = windows[0]
    _u32.ShowWindow(hwnd, SW_RESTORE)
    _u32.BringWindowToTop(hwnd)
    try:
        _u32.SetForegroundWindow(hwnd)
    except Exception:
        pass
    try:
        # 前台权限不足时的兜底：SwitchToThisWindow 由系统代为激活
        _u32.SwitchToThisWindow(hwnd, True)
    except Exception:
        pass
    return True


def _registry_candidates() -> list[Path]:
    out: list[Path] = []
    keys = [
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + EXE_NAME),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\\" + EXE_NAME),
    ]
    for root, sub in keys:
        try:
            with winreg.OpenKey(root, sub) as key:
                value, _ = winreg.QueryValueEx(key, "")
                if value:
                    out.append(Path(value.strip('"')))
        except OSError:
            continue

    try:
        base = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
        )
    except OSError:
        return out
    with base:
        try:
            count = winreg.QueryInfoKey(base)[0]
        except OSError:
            return out
        for i in range(count):
            try:
                name = winreg.EnumKey(base, i)
                if EXE_NAME[:-4].lower() not in name.lower():
                    continue
                with winreg.OpenKey(base, name) as sub:
                    for value_name in ("DisplayIcon", "InstallLocation"):
                        try:
                            value, _ = winreg.QueryValueEx(sub, value_name)
                        except OSError:
                            continue
                        if not value:
                            continue
                        path = Path(str(value).split(",")[0].strip('"'))
                        out.append(path if path.suffix.lower() == ".exe" else path / EXE_NAME)
            except OSError:
                continue
    return out


def _common_candidates() -> list[Path]:
    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    program_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    return [
        Path(r"D:\WorkBuddy") / EXE_NAME,
        Path(program_files) / "WorkBuddy" / EXE_NAME,
        Path(program_files_x86) / "WorkBuddy" / EXE_NAME,
        Path(local) / "Programs" / "WorkBuddy" / EXE_NAME,
        Path(local) / "WorkBuddy" / EXE_NAME,
        Path(r"C:\WorkBuddy") / EXE_NAME,
    ]


def find_exe(preferred: str = "") -> Path | None:
    if preferred:
        p = Path(preferred)
        if p.is_file():
            return p
        if p.is_dir() and (p / EXE_NAME).is_file():
            return p / EXE_NAME
    for candidate in _registry_candidates() + _common_candidates():
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def open_workbuddy(preferred: str = "") -> tuple[bool, str]:
    """返回 (是否成功, 提示文案)。"""
    if activate_running():
        return True, "WorkBuddy 已在运行，已切到前台"
    exe = find_exe(preferred)
    if exe is None:
        return False, "没找到 WorkBuddy.exe"
    try:
        subprocess.Popen(
            [str(exe)],
            cwd=str(exe.parent),
            close_fds=True,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        )
        return True, "正在打开 WorkBuddy…"
    except Exception as exc:  # pragma: no cover - 仅异常兜底
        return False, f"启动失败：{exc}"


def is_running() -> bool:
    return bool(_iter_process_pids(EXE_NAME))
