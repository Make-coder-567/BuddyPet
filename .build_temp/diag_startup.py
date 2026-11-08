"""诊断：启动冻结 exe，抓出 PyInstaller/Qt 错误对话框里的完整文本。"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

u = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E


def text_of(hwnd: int) -> str:
    n = u.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(int(n) + 2)
    u.SendMessageW(hwnd, WM_GETTEXT, len(buf), buf)
    return buf.value


def class_of(hwnd: int) -> str:
    b = ctypes.create_unicode_buffer(256)
    u.GetClassNameW(hwnd, b, 256)
    return b.value


def children(hwnd: int) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []

    def cb(h, _l):
        out.append((hex(int(h)), class_of(h), text_of(h)))
        return True

    u.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return out


def dialogs(pid: int) -> list[int]:
    out: list[int] = []

    def cb(hwnd, _l):
        owner = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and u.IsWindowVisible(hwnd):
            t = text_of(hwnd)
            if class_of(hwnd) == "#32770" or "error" in t.lower() or "错误" in t:
                out.append(int(hwnd))
        return True

    u.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def main() -> int:
    exe = Path(sys.argv[1]).resolve()
    tmp = Path(tempfile.gettempdir()) / f"buddy_diag_{os.getpid()}"
    appdata = tmp / "appdata"
    appdata.mkdir(parents=True, exist_ok=True)
    log = tmp / "stdout.log"
    env = os.environ.copy()
    env["APPDATA"] = str(appdata)
    env.pop("QT_QPA_PLATFORM", None)
    f = log.open("w", encoding="utf-8", errors="replace")
    p = subprocess.Popen([str(exe)], cwd=str(exe.parent.parent), env=env, stdout=f, stderr=subprocess.STDOUT)
    print(f"pid={p.pid} appdata={appdata}", flush=True)

    found: list[int] = []
    deadline = time.time() + 25
    while time.time() < deadline and not found:
        if p.poll() is not None:
            print(f"process exited early code={p.returncode}", flush=True)
            break
        found = dialogs(p.pid)
        if not found:
            time.sleep(0.4)
    print(f"dialogs={found}", flush=True)
    for h in found:
        print(f"--- dialog {hex(h)} title={text_of(h)!r} ---", flush=True)
        for item in children(h):
            print("   ", item, flush=True)
    f.flush()
    f.close()
    print("STDOUT_LOG = " + repr(log.read_text(encoding="utf-8", errors="replace")[:6000]), flush=True)
    if p.poll() is None:
        p.kill()
        p.wait(timeout=10)
        print("killed diagnostic instance", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
