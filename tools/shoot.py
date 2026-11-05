"""自动化取景工具：通过本地 socket 触发特效，并连续截取宠物区域。

用法：
    python tools/shoot.py effect:box 3.9 [fps]
    python tools/shoot.py menu ---   # 只截一张
"""
from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtCore import QRect
from PyQt5.QtNetwork import QLocalSocket
from PyQt5.QtWidgets import QApplication

SERVER_NAME = "WorkBuddyDesktopPet-1"
OUT = Path(__file__).resolve().parent.parent / "assets" / "shots"


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def find_pet_rect() -> QRect:
    u = ctypes.WinDLL("user32", use_last_error=True)
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    hits: list[QRect] = []

    def cb(hwnd, _l):
        n = u.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        u.GetWindowTextW(hwnd, buf, n + 1)
        if buf.value == "Buddy 桌面宠物" and u.IsWindowVisible(hwnd):
            r = RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            hits.append(QRect(r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    u.EnumWindows(WNDENUMPROC(cb), 0)
    if not hits:
        raise SystemExit("未找到宠物窗口，请先运行 main.py")
    return max(hits, key=lambda r: r.width() * r.height())


def send(cmd: str) -> None:
    s = QLocalSocket()
    s.connectToServer(SERVER_NAME)
    if not s.waitForConnected(500):
        raise SystemExit("无法连接到宠物进程")
    s.write(cmd.encode("utf-8"))
    s.waitForBytesWritten(500)
    s.disconnectFromServer()


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    duration = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
    fps = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    screen = app.primaryScreen()
    if cmd != "none":
        send(cmd)
    rect = find_pet_rect().adjusted(-120, -150, 120, 60)
    rect = rect.intersected(screen.geometry())
    frames = max(1, int(duration * fps))
    stamp = cmd.replace(":", "_")
    for i in range(frames):
        pm = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
        pm.save(str(OUT / f"{stamp}_{i:03d}.png"))
        time.sleep(1.0 / fps)
    print(f"captured {frames} frames -> {OUT}")


if __name__ == "__main__":
    main()
