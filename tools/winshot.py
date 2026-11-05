"""按窗口标题截取某个窗口区域，便于验收弹窗。"""
from __future__ import annotations

import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtCore import QRect
from PyQt5.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def find(title: str) -> QRect | None:
    u = ctypes.WinDLL("user32", use_last_error=True)
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    hits: list[QRect] = []

    def cb(hwnd, _l):
        n = u.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value == title and u.IsWindowVisible(hwnd):
            r = RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            hits.append(QRect(r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    u.EnumWindows(WNDENUMPROC(cb), 0)
    return hits[0] if hits else None


def main() -> None:
    title = sys.argv[1]
    out = sys.argv[2]
    app = QApplication([])
    rect = find(title)
    if rect is None:
        raise SystemExit(f"未找到窗口：{title}")
    pm = app.primaryScreen().grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
    pm.save(str(ROOT / out))
    print(f"{title} {rect} -> {out}")


if __name__ == "__main__":
    main()
