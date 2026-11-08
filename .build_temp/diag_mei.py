"""诊断：把 onefile 运行时解压出的 _MEI 目录内容抓下来，比较 python313.dll。"""
from __future__ import annotations

import ctypes
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

u = ctypes.WinDLL("user32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)


def has_dialog(pid: int) -> bool:
    found = [False]

    def cb(hwnd, _l):
        owner = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and u.IsWindowVisible(hwnd):
            b = ctypes.create_unicode_buffer(256)
            u.GetClassNameW(hwnd, b, 256)
            if b.value == "#32770":
                found[0] = True
        return True

    u.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0]


def main() -> int:
    exe = Path(sys.argv[1]).resolve()
    work = Path(sys.argv[2]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    for child in work.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    appdata = work / "appdata"
    appdata.mkdir(exist_ok=True)
    out = work / "snapshot"
    out.mkdir(exist_ok=True)

    env = os.environ.copy()
    env["APPDATA"] = str(appdata)
    env["TMP"] = str(work)
    env["TEMP"] = str(work)
    env.pop("QT_QPA_PLATFORM", None)
    logf = (work / "stdout.log").open("w", encoding="utf-8", errors="replace")
    p = subprocess.Popen([str(exe)], cwd=str(exe.parent), env=env, stdout=logf, stderr=subprocess.STDOUT)
    print(f"pid={p.pid} TMP={work}", flush=True)

    dialog = False
    deadline = time.time() + 12
    while time.time() < deadline:
        if has_dialog(p.pid):
            dialog = True
            break
        if p.poll() is not None:
            break
        time.sleep(0.3)
    print(f"dialog={dialog} alive={p.poll() is None}", flush=True)

    meis = sorted(work.glob("_MEI*"))
    print(f"_MEI dirs = {[str(m) for m in meis]}", flush=True)
    if meis:
        mei = meis[0]
        names = sorted(x.name for x in mei.iterdir())
        print(f"extracted entries ({len(names)}): {names}", flush=True)
        for name in ("python313.dll", "python3.dll", "VCRUNTIME140.dll", "vcruntime140_1.dll"):
            src = mei / name
            if src.exists():
                data = src.read_bytes()
                (out / name).write_bytes(data)
                print(f"  {name}: size={len(data)} sha256={hashlib.sha256(data).hexdigest()[:24]} "
                      f"head={data[:16].hex()}", flush=True)
            else:
                print(f"  {name}: NOT PRESENT", flush=True)
    logf.flush()
    logf.close()
    print("STDOUT = " + repr((work / "stdout.log").read_text(encoding="utf-8", errors="replace")[:2000]), flush=True)
    if p.poll() is None:
        p.kill()
        p.wait(timeout=10)
    print("killed", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
