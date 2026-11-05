"""检查桌面宠物是否在运行，并输出窗口位置。"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

u = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def main() -> None:
    hits: list[tuple] = []

    def cb(hwnd, _lparam):
        n = u.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            if "Buddy 桌面宠物" in buf.value and u.IsWindowVisible(hwnd):
                r = RECT()
                u.GetWindowRect(hwnd, ctypes.byref(r))
                pid = wintypes.DWORD()
                u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                hits.append((pid.value, r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    u.EnumWindows(WNDENUMPROC(cb), 0)
    if hits:
        for pid, x, y, w, h in hits:
            print(f"RUNNING pid={pid} pos=({x},{y}) size={w}x{h}")
    else:
        print("NOT RUNNING")


if __name__ == "__main__":
    main()
