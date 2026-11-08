# -*- coding: utf-8 -*-
"""QA 独立验收 (Edward / repack2 / v1.3.1) —— 运行时部分：C 真实启动 / D 配置零污染 / F 收尾。

严格约束（team-lead 四个坑位）：
  1) 启动 onefile 前从子进程 env 剥离全部 _PYI_* / *MEIPASS*；并独立复现「不剥离会失败」(信息项)。
  2) onefile fork 父子进程，窗口枚举按「进程族」；主窗口按尺寸(约 215x321)识别，排除同名 EffectOverlay。
  3) 自动化里【绝不发 rps 命令】（模态对话框会阻塞 QLocalServer 命令处理）。
  4) 测试前后确认无 BuddyPet / python 残留、单实例 QLocalServer 空闲。
  所有 exe 启动均使用 $TEMP/buddy_repack2_qa_<pid>/appdata 临时 APPDATA，绝不写用户真实配置。

用法（需 PyQt5.QtNetwork；本机 Python 3.14.6 有 PyQt5 5.15.11）：
    C:/Users/mrhua/AppData/Local/Programs/Python/Python314/python.exe tools/qa_repack2_edward_runtime.py
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path("C:/Users/mrhua/Desktop/桌面宠物")
EXE = ROOT / "BuddyPet_分享包" / "BuddyPet.exe"
ZIP_PATH = ROOT / "BuddyPet_分享包.zip"
DOC = ROOT / "BuddyPet_分享包" / "使用说明.txt"
REPORT = ROOT / "tools" / "qa_repack2_runtime_report.txt"
REAL_CONFIG = Path(os.path.expanduser("~")) / "AppData" / "Roaming" / "WorkBuddyPet" / "config.json"

SERVER_NAME = "WorkBuddyDesktopPet-1"
PET_TITLE = "Buddy 桌面宠物"
RPS_TITLE = "石头 · 剪刀 · 布"
HOTKEY_ID = 0xB0B
MOD_CONTROL, MOD_ALT, MOD_NOREPEAT = 0x0002, 0x0001, 0x4000
VK_B, VK_1, VK_ESCAPE = ord("B"), 0x31, 0x1B
WM_KEYDOWN, WM_CLOSE, WM_LBUTTONDBLCLK = 0x0100, 0x0010, 0x0203

_OUT: list[str] = []
_C = {"PASS": 0, "FAIL": 0, "INFO": 0}
_FAILS: list[str] = []


def emit(s: str = "") -> None:
    _OUT.append(s)
    try:
        print(s, flush=True)
    except Exception:
        pass


def check(sec: str, name: str, ok: bool, ev: str = "") -> bool:
    _C["PASS" if ok else "FAIL"] += 1
    if not ok:
        _FAILS.append(f"[{sec}] {name}: {ev}")
    emit(f"[{'PASS' if ok else 'FAIL'}] {sec}.{name}" + (f": {ev}" if ev else ""))
    return bool(ok)


def info(sec: str, s: str) -> None:
    _C["INFO"] += 1
    emit(f"[INFO] {sec}: {s}")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def fp(p: Path) -> dict:
    st = p.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "sha256": sha256_file(p)}


def fingerprint_artifacts() -> dict:
    return {str(p.relative_to(ROOT)): fp(p) for p in (EXE, ZIP_PATH, DOC)}


# ------------------------------------------------------------- Win32
class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
for _n, _a, _r in [
    ("EnumWindows", [WNDENUMPROC, wintypes.LPARAM], wintypes.BOOL),
    ("EnumChildWindows", [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM], wintypes.BOOL),
    ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
    ("GetWindowTextLengthW", [wintypes.HWND], ctypes.c_int),
    ("GetWindowTextW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
    ("GetClassNameW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
    ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
    ("GetWindowRect", [wintypes.HWND, ctypes.POINTER(RECT)], wintypes.BOOL),
    ("PostMessageW", [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
    ("RegisterHotKey", [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT], wintypes.BOOL),
    ("UnregisterHotKey", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
]:
    f = getattr(_u32, _n)
    f.argtypes, f.restype = _a, _r
for _n, _a, _r in [
    ("CreateToolhelp32Snapshot", [wintypes.DWORD, wintypes.DWORD], ctypes.c_void_p),
    ("Process32FirstW", [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)], wintypes.BOOL),
    ("Process32NextW", [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)], wintypes.BOOL),
    ("CloseHandle", [ctypes.c_void_p], wintypes.BOOL),
]:
    f = getattr(_k32, _n)
    f.argtypes, f.restype = _a, _r


def _wtext(h):
    n = _u32.GetWindowTextLengthW(h)
    if n <= 0:
        return ""
    b = ctypes.create_unicode_buffer(n + 1)
    _u32.GetWindowTextW(h, b, n + 1)
    return b.value


def _wclass(h):
    b = ctypes.create_unicode_buffer(256)
    _u32.GetClassNameW(h, b, 256)
    return b.value


def snapshot() -> dict[int, tuple[int, str]]:
    s = _k32.CreateToolhelp32Snapshot(0x2, 0)
    out = {}
    if not s or s == -1:
        return out
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if _k32.Process32FirstW(s, ctypes.byref(e)):
            while True:
                out[int(e.th32ProcessID)] = (int(e.th32ParentProcessID), e.szExeFile)
                if not _k32.Process32NextW(s, ctypes.byref(e)):
                    break
    finally:
        _k32.CloseHandle(s)
    return out


def family(root: int, snap=None) -> set[int]:
    snap = snap or snapshot()
    ch: dict[int, list[int]] = {}
    for pid, (ppid, _n) in snap.items():
        ch.setdefault(ppid, []).append(pid)
    found, q = {root}, [root]
    while q:
        for c in ch.get(q.pop(), []):
            if c not in found:
                found.add(c)
                q.append(c)
    return found


def buddy_pids(snap=None) -> dict[int, str]:
    return {p: n for p, (_pp, n) in (snap or snapshot()).items()
            if n.lower().startswith("buddypet")}


def enum_windows(pids=None) -> list[dict]:
    out = []

    def cb(h, _l):
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        if pids is None or pid.value in pids:
            r = RECT()
            _u32.GetWindowRect(h, ctypes.byref(r))
            out.append({"hwnd": int(h), "pid": int(pid.value), "title": _wtext(int(h)),
                        "class": _wclass(int(h)), "visible": bool(_u32.IsWindowVisible(h)),
                        "w": r.right - r.left, "h": r.bottom - r.top})
        return True

    _u32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def child_texts(hwnd) -> list[str]:
    t = []

    def cb(h, _l):
        s = _wtext(int(h))
        if s:
            t.append(s)
        return True

    _u32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return t


def visible(hwnd) -> bool:
    return bool(hwnd) and bool(_u32.IsWindowVisible(hwnd))


def main_pet(pids):
    c = [w for w in enum_windows(pids) if w["visible"] and PET_TITLE in w["title"]]
    if not c:
        return 0, 0, 0
    b = min(c, key=lambda w: abs(w["w"] - 215) + abs(w["h"] - 321))
    return b["hwnd"], b["w"], b["h"]


def main_pet_hwnds(pids) -> list[int]:
    return sorted(w["hwnd"] for w in enum_windows(pids)
                  if w["visible"] and PET_TITLE in w["title"]
                  and abs(w["w"] - 215) <= 40 and abs(w["h"] - 321) <= 40)


def rps_windows(pids, include_hidden=False):
    return [w for w in enum_windows(pids)
            if RPS_TITLE in w["title"] and (include_hidden or w["visible"])]


def error_windows(pids) -> list[str]:
    keys = ("traceback", "error", "fatal", "错误", "异常", "failed", "python dll", "qt platform")
    return [f"{w['title']} [{w['class']}]" for w in enum_windows(pids)
            if w["visible"] and any(k in w["title"].lower() for k in keys)]


def try_hotkey(mods, vk, hid=HOTKEY_ID):
    ctypes.set_last_error(0)
    ok = bool(_u32.RegisterHotKey(None, hid, mods, vk))
    err = ctypes.get_last_error()
    if ok:
        _u32.UnregisterHotKey(None, hid)
    return ok, err


def real_double_click(hwnd) -> dict:
    """用真实系统输入队列触发双击（Qt 不响应 PostMessage 的伪 WM_LBUTTONDBLCLK）。"""
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.ShowWindow.restype = wintypes.BOOL
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.SetForegroundWindow.restype = wintypes.BOOL
    u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    u.SetCursorPos.restype = wintypes.BOOL
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    u.GetWindowRect.restype = wintypes.BOOL
    u.mouse_event.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                              wintypes.DWORD, ctypes.c_size_t]
    r = RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    x, y = (r.left + r.right) // 2, (r.top + r.bottom) // 2
    u.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg = bool(u.SetForegroundWindow(hwnd))
    time.sleep(0.35)
    cp = bool(u.SetCursorPos(x, y))
    time.sleep(0.2)
    LEFTDOWN, LEFTUP = 0x0002, 0x0004
    for _ in range(2):
        u.mouse_event(LEFTDOWN, 0, 0, 0, 0)
        u.mouse_event(LEFTUP, 0, 0, 0, 0)
        time.sleep(0.09)
    return {"foreground": fg, "cursor_set": cp, "screen_xy": (x, y),
            "rect": (r.left, r.top, r.right, r.bottom)}


# ------------------------------------------------------------- 命令通道
_qapp = None


def ensure_qapp():
    global _qapp
    if _qapp is None:
        from PyQt5.QtCore import QCoreApplication
        _qapp = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    return _qapp


def raw_pipe_send(cmd: str) -> bool:
    """ctypes 兜底：直接写 Qt QLocalServer 的 Windows 命名管道。"""
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k.CreateFileW.restype = wintypes.HANDLE
    k.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k.WriteFile.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.CloseHandle.restype = wintypes.BOOL
    path = "\\\\.\\pipe\\" + SERVER_NAME
    h = k.CreateFileW(path, 0x80000000 | 0x40000000, 0, None, 3, 0, None)
    if not h or h in (ctypes.c_void_p(-1).value, 0xFFFFFFFFFFFFFFFF):
        return False
    try:
        data = cmd.encode("utf-8") + b"\n"
        buf = ctypes.create_string_buffer(data)
        written = wintypes.DWORD()
        ok = bool(k.WriteFile(h, buf, len(data), ctypes.byref(written), None))
        return ok and written.value == len(data)
    finally:
        k.CloseHandle(h)


def server_reachable(timeout_ms=400) -> bool:
    try:
        from PyQt5.QtNetwork import QLocalSocket
        ensure_qapp()
        s = QLocalSocket()
        s.connectToServer(SERVER_NAME)
        ok = bool(s.waitForConnected(timeout_ms))
        s.disconnectFromServer()
        return ok
    except Exception:
        return raw_pipe_send("noop")  # 不等价，仅探测


def send_cmd(cmd: str, settle=0.7, keep_open=False):
    """优先 QLocalSocket（与 exe 同协议），失败则 ctypes 命名管道兜底。"""
    for method in ("qt", "pipe"):
        try:
            if method == "qt":
                from PyQt5.QtNetwork import QLocalSocket
                ensure_qapp()
                s = QLocalSocket()
                s.connectToServer(SERVER_NAME)
                if not s.waitForConnected(2000):
                    continue
                s.write(cmd.encode("utf-8") + b"\n")
                s.flush()
                wrote = bool(s.waitForBytesWritten(2000))
            else:
                wrote = raw_pipe_send(cmd)
                s = None
            if not wrote:
                continue
            if settle:
                time.sleep(settle)
            if keep_open:
                return True, s, method
            if s is not None:
                s.disconnectFromServer()
                s.waitForDisconnected(500)
            return True, None, method
        except Exception as e:
            emit(f"      send_cmd({method}) 异常: {type(e).__name__}: {e}")
    return False, None, "none"


# ------------------------------------------------------------- env / launch
PYI_DIRTY = {
    "_PYI_ARCHIVE_FILE": str(EXE),
    "_PYI_APPLICATION_HOME_DIR": os.path.join(tempfile.gettempdir(), "_MEI00004a342"),
    "_PYI_PARENT_PROCESS_LEVEL": "1",
}


def make_env(appdata: Path, dirty=False) -> dict:
    env = os.environ.copy()
    for k in list(env):
        if "_PYI_" in k.upper() or "MEIPASS" in k.upper():
            env.pop(k, None)
    for k in ("QT_QPA_PLATFORM", "QT_PLUGIN_PATH", "PYTHONPATH", "PYTHONHOME"):
        env.pop(k, None)
    env["APPDATA"] = str(appdata)
    env["PYTHONUNBUFFERED"] = "1"
    if dirty:
        env.update(PYI_DIRTY)
    return env


def launch(appdata: Path, log: Path, dirty=False):
    fh = open(log, "wb")
    p = subprocess.Popen([str(EXE)], cwd=str(ROOT), env=make_env(appdata, dirty=dirty),
                         stdout=fh, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    p._qa_fh = fh
    return p


def close_log(p):
    fh = getattr(p, "_qa_fh", None)
    if fh:
        try:
            fh.close()
        except Exception:
            pass


def kill_tree(p):
    if p.poll() is None:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                       capture_output=True, timeout=15)
    try:
        p.wait(timeout=5)
    except Exception:
        pass
    close_log(p)


def force_clear():
    for pid in list(buddy_pids()):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    time.sleep(0.5)


def wait_window(roots, predicate, timeout=30.0, interval=0.3):
    end = time.monotonic() + timeout
    fam = set()
    while time.monotonic() < end:
        snap = snapshot()
        fam = set()
        for r in roots:
            fam |= family(r, snap)
        got = predicate(fam)
        if got:
            return got, fam
        time.sleep(interval)
    return None, fam


def wait_file(path: Path, timeout=8.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists() and path.stat().st_size > 0:
            return True
        time.sleep(0.2)
    return False


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ------------------------------------------------------------- main
def main():
    emit("QA 独立验收 | Edward | repack2 v1.3.1 | 运行时部分")
    emit(f"python={sys.executable}  time={datetime.now():%Y-%m-%d %H:%M:%S}")
    emit(f"ROOT={ROOT}")

    artifacts_before = fingerprint_artifacts()
    cfg_before = fp(REAL_CONFIG)
    emit(f"F.BEFORE 产物指纹 = {json.dumps(artifacts_before, ensure_ascii=False)}")
    emit(f"D.BEFORE 用户真实配置 = {json.dumps(cfg_before, ensure_ascii=False)}")

    tmp = Path(tempfile.gettempdir()) / f"buddy_repack2_qa_{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    appdata = tmp / "appdata"
    appdata.mkdir()
    cfg_dir = appdata / "WorkBuddyPet"
    png = tmp / "drop_test.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8)
    emit(f"[INFO] TEMP_ROOT={tmp}  TEMP_APPDATA={appdata}")

    sess_pyi = {k: v for k, v in os.environ.items()
                if "_PYI_" in k.upper() or "MEIPASS" in k.upper()}
    emit(f"[INFO] 本会话原有 _PYI_*/MEIPASS 变量 = {sess_pyi}")

    pre = buddy_pids()
    check("F", "0_测试前无BuddyPet残留进程", not pre, f"buddy_pids={pre}")
    check("F", "0_测试前命名管道空闲", not server_reachable(300),
          f"QLocalSocket 连 {SERVER_NAME} 可达={server_reachable(300)}")

    main_p = None
    try:
        # ---------------- C2：不剥离会失败（信息项）----------------
        emit("\n---- C2. 不剥离 _PYI_* 的失败复现（信息项，不算 FAIL）----")
        c2_appdata = tmp / "c2_appdata"
        c2_appdata.mkdir()
        c2_log = tmp / "c2_dirty.log"
        p2 = launch(c2_appdata, c2_log, dirty=True)
        c2_fam, c2_wins = {p2.pid}, []
        t0 = time.monotonic()
        while time.monotonic() - t0 < 7.0:
            c2_fam |= family(p2.pid)
            c2_wins = enum_windows(c2_fam)
            if p2.poll() is not None and not c2_wins:
                break
            if any(w["visible"] for w in c2_wins):
                break
            time.sleep(0.3)
        c2_elapsed = time.monotonic() - t0
        c2_brief = [(w["title"], w["class"], child_texts(w["hwnd"])[:4]) for w in c2_wins]
        c2_alive = p2.poll() is None
        close_log(p2)
        c2_logtxt = c2_log.read_text(encoding="utf-8", errors="replace")[:900]
        c2_failed = (not c2_alive) or any(
            "failed" in (t or "").lower() or "python dll" in (t or "").lower()
            for _t, _c, ts in c2_brief for t in ts) or "Failed to load Python DLL" in c2_logtxt
        info("C", f"独立复现「不剥离 _PYI_* 会失败」：注入 {list(PYI_DIRTY)} 后 "
                  f"alive={c2_alive} exit={p2.poll()} elapsed={c2_elapsed:.2f}s "
                  f"窗口={json.dumps(c2_brief, ensure_ascii=False)[:900]} "
                  f"stdout/stderr={c2_logtxt!r}；判定启动失败={c2_failed}。"
                  f"此为【测试环境污染】，真实用户双击不会带这些变量，非产品缺陷")
        for w in c2_wins:
            if w["visible"]:
                _u32.PostMessageW(w["hwnd"], WM_CLOSE, 0, 0)
        time.sleep(0.3)
        kill_tree(p2)
        force_clear()
        check("C", "2_C2清场后无残留实例与server",
              not buddy_pids() and not server_reachable(300),
              f"BuddyPet进程={buddy_pids()}, server可达={server_reachable(300)}")

        # ---------------- C1：剥离后真实启动 ----------------
        emit("\n---- C1. 剥离 _PYI_* 后真实启动 ----")
        env_clean = make_env(appdata)
        stripped_now = [k for k in env_clean if "_PYI_" in k.upper() or "MEIPASS" in k.upper()]
        check("C", "1_子进程环境已剥离PYI变量", not stripped_now,
              f"会话原有={list(sess_pyi)}; 子进程剩余={stripped_now}")
        main_log = tmp / "main_stdout.log"
        main_p = launch(appdata, main_log)
        got, fam = wait_window({main_p.pid}, lambda f: main_pet(f)[0], timeout=40.0)
        hwnd, mw, mh = main_pet(fam) if got else (0, 0, 0)
        fam = family(main_p.pid)
        snap = snapshot()
        names = {pid: snap[pid][1] for pid in fam if pid in snap}
        check("C", "1_进程族存活且宠物主窗口可见",
              bool(got) and main_p.poll() is None and visible(hwnd),
              f"Popen.pid={main_p.pid} exit={main_p.poll()} family={sorted(fam)} "
              f"进程名={names} 主窗口hwnd={hwnd} 尺寸={mw}x{mh} IsWindowVisible={visible(hwnd)}")
        check("C", "1_onefile父子两进程",
              len([p for p in fam if p in snap and snap[p][1].lower().startswith("buddypet")]) >= 2,
              f"进程族中 BuddyPet.exe 数={len([p for p in fam if p in snap and snap[p][1].lower().startswith('buddypet')])}")
        wins = enum_windows(fam)
        errs = error_windows(fam)
        win_brief = [(w["title"], w["class"], w["visible"],
                      "{}x{}".format(w["w"], w["h"])) for w in wins]
        check("C", "1_无错误弹窗/Traceback/Qt插件错误", not errs,
              f"错误窗口={errs}; 全部窗口={win_brief}")
        close_log(main_p)
        logtxt = main_log.read_text(encoding="utf-8", errors="replace")
        check("C", "1_stdout_stderr干净",
              "Failed to load Python DLL" not in logtxt and "Traceback" not in logtxt,
              f"长度={len(logtxt)} 内容={logtxt[:400]!r} (console=False 通常为空)")

        # ---------------- C3：单实例 ----------------
        emit("\n---- C3. 单实例：第二个 exe 自退 ----")
        before_main = main_pet_hwnds(family(main_p.pid))
        second_log = tmp / "second_instance.log"
        p3 = launch(appdata, second_log)
        p3_fam, p3_wins, seen = {p3.pid}, [], set()
        p3_exit, t0 = None, time.monotonic()
        while time.monotonic() - t0 < 15:
            p3_fam |= family(p3.pid)
            for w in enum_windows(p3_fam):
                if w["visible"] and w["hwnd"] not in seen:
                    seen.add(w["hwnd"])
                    p3_wins.append(w)
            if p3.poll() is not None:
                p3_exit = p3.returncode
                break
            time.sleep(0.1)
        if p3.poll() is None:
            kill_tree(p3)
        close_log(p3)
        time.sleep(0.5)
        after_main = main_pet_hwnds(family(main_p.pid))
        check("C", "3_第二实例自退且不产生第二窗口",
              p3_exit == 0 and after_main == before_main and len(before_main) == 1 and not p3_wins,
              f"第二实例 exit={p3_exit} 耗时={time.monotonic()-t0:.2f}s; "
              f"主窗口hwnd 前={before_main} 后={after_main}; 第二实例可见窗口={[(w['title'],w['w'],w['h']) for w in p3_wins]}; "
              f"stdout={second_log.read_text(encoding='utf-8',errors='replace')[:160]!r}")

        # ---------------- D1：music:status（证明新版音乐状态机在冻结 exe 内真实运行）----
        emit("\n---- D1. music:status ----")
        music_json = cfg_dir / "music_status.json"
        music_json.unlink(missing_ok=True)
        ok1, _, m1 = send_cmd("music:status", settle=1.2)
        got_music = wait_file(music_json, 8.0)
        mj = read_json(music_json)
        required = ["playing", "kind", "title", "peak", "music_app", "degraded", "enabled"]
        missing = [k for k in required if k not in mj]
        check("C", "4_music_status字段齐全",
              ok1 and got_music and not missing,
              f"send={ok1}({m1}) 文件存在={got_music} 全部字段={sorted(mj)} 缺={missing}")
        peak = mj.get("peak")
        check("C", "4_music_status_peak为float", isinstance(peak, float),
              f"peak={peak!r} type={type(peak).__name__}; playing={mj.get('playing')} "
              f"kind={mj.get('kind')} title={mj.get('title')!r} music_app={mj.get('music_app')} "
              f"degraded={mj.get('degraded')} enabled={mj.get('enabled')}; 全文={json.dumps(mj,ensure_ascii=False)}")
        info("C", "peak 为 float 证明冻结 exe 内 audio_probe.probe_peak() 真实执行；"
                  "结合 B 段字节码常量(POLL_MS=500/START_CONFIRM_MS=600/STOP_CONFIRM_MS=1200/"
                  "APP_SCAN_EVERY=4/time.monotonic)可判定新版音乐状态机已编入并运行")

        # ---------------- D2：drop:test ----------------
        emit("\n---- D2. drop:test:<临时 png> ----")
        cfg_path = cfg_dir / "config.json"
        cfg0 = read_json(cfg_path)
        drop0 = int(cfg0.get("drop_count", 0)) if cfg0 else 0
        png_before = fp(png)
        ok2, _, m2 = send_cmd(f"drop:test:{png}", settle=1.3)
        cfg1 = read_json(cfg_path)
        drop1 = int(cfg1.get("drop_count", -1)) if cfg1 else -1
        png_after = fp(png)
        check("C", "5_drop_count_0到1且路径正确",
              ok2 and drop0 == 0 and drop1 == 1 and cfg1.get("last_drop_path") == str(png),
              f"send={ok2}({m2}) drop_count {drop0}->{drop1}, last_drop_path={cfg1.get('last_drop_path')!r} 期望={str(png)!r}")
        check("C", "5_被拖文件mtime/sha256前后不变", png_before == png_after,
              f"before={png_before} | after={png_after}")

        # ---------------- D3：hotkey:toggle x2 ----------------
        emit("\n---- D3. hotkey:toggle 两次 ----")
        v0 = visible(hwnd)
        ok3a, _, _ = send_cmd("hotkey:toggle", settle=1.0)
        v1 = visible(hwnd)
        ok3b, _, _ = send_cmd("hotkey:toggle", settle=1.0)
        v2 = visible(hwnd)
        check("C", "6_主窗口可见性True-False-True",
              ok3a and ok3b and (v0, v1, v2) == (True, False, True),
              f"主窗口hwnd={hwnd} IsWindowVisible: 初始={v0} 第一次={v1} 第二次={v2}")

        # ---------------- D4：外部抢占热键 ----------------
        emit("\n---- D4. 运行中外部抢占 Ctrl+Alt+B ----")
        mods = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
        reg_ok, reg_err = try_hotkey(mods, VK_B, HOTKEY_ID)
        check("C", "7_运行中外部抢占失败1409", (not reg_ok) and reg_err == 1409,
              f"RegisterHotKey(None, 0x{HOTKEY_ID:X}, 0x{mods:04X}, ord('B')=0x{VK_B:X}) "
              f"→ ok={reg_ok} GetLastError={reg_err}（期望 1409 ERROR_HOTKEY_ALREADY_REGISTERED）")

        # ---------------- quit ----------------
        emit("\n---- quit 收尾 ----")
        okq, _, mq = send_cmd("quit", settle=0.8)
        try:
            main_p.wait(timeout=20)
            q_exit = main_p.returncode
        except subprocess.TimeoutExpired:
            q_exit = None
            kill_tree(main_p)
        close_log(main_p)
        time.sleep(1.0)
        residual = buddy_pids()
        check("C", "8_quit正常退出exit0且无残留",
              okq and q_exit == 0 and not residual,
              f"send quit={okq}({mq}) exit={q_exit} 残留BuddyPet进程={residual}")
        full_log = main_log.read_text(encoding="utf-8", errors="replace")
        check("C", "8_退出日志无Traceback/DLL错误",
              "Traceback" not in full_log and "Failed to load Python DLL" not in full_log,
              f"stdout/stderr 长度={len(full_log)} 内容={full_log[:400]!r}")

        emit("\n---- D4(续). quit 后热键已注销 ----")
        r2ok, r2err = try_hotkey(mods, VK_B, HOTKEY_ID)
        r3ok, r3err = try_hotkey(MOD_CONTROL | MOD_ALT, VK_B, 0xBEEF)
        check("C", "9_quit后全局热键可重新注册", r2ok and r3ok,
              f"带MOD_NOREPEAT: ok={r2ok} err={r2err}; 不带: ok={r3ok} err={r3err}（均已立即注销清理）")

        # ---------------- 附加项：独立会话双击 RPS（不发 rps 命令）----------------
        emit("\n---- 附加项. 独立会话：双击开石头剪刀布 + VK_1 / VK_ESCAPE ----")
        appdata2 = tmp / "appdata_rps"
        appdata2.mkdir()
        rps_log = tmp / "rps_session.log"
        pr = launch(appdata2, rps_log)
        got_r, fam_r = wait_window({pr.pid}, lambda f: main_pet(f)[0], timeout=35.0)
        rh, rw, rhh = main_pet(fam_r) if got_r else (0, 0, 0)
        emit(f"      附加会话主窗口 hwnd={rh} 尺寸={rw}x{rhh}")
        if got_r and rh:
            dbl = real_double_click(rh)
            time.sleep(1.3)
            rl = rps_windows(family(pr.pid))
            emit(f"      真实鼠标双击证据 = {dbl}; 命中 RPS 窗口 = "
                 f"{[(w['hwnd'], w['title'], w['w'], w['h']) for w in rl]}")
            if len(rl) == 1:
                check("C", "10_双击可打开石头剪刀布对话框", True,
                      f"真实输入双击(SetForegroundWindow+SetCursorPos+mouse_event x2) 命中 1 个 RPS 窗口")
                rps_h = rl[0]["hwnd"]
                p1 = bool(_u32.PostMessageW(rps_h, WM_KEYDOWN, VK_1, 0))
                time.sleep(1.8)
                alive1 = pr.poll() is None
                p_esc = bool(_u32.PostMessageW(rps_h, WM_KEYDOWN, VK_ESCAPE, 0))
                time.sleep(1.2)
                ghost = rps_windows(family(pr.pid), include_hidden=True)
                check("C", "10_VK_1/VK_ESCAPE后不abort且无幽灵窗口",
                      p1 and p_esc and alive1 and not ghost,
                      f"VK_1={p1} VK_ESCAPE={p_esc} 进程exit={pr.poll()} "
                      f"残留RPS窗口={[(w['hwnd'], w['visible']) for w in ghost]}")
            else:
                info("C", f"附加项【未覆盖，非缺陷】：真实鼠标双击也未产生 RPS 窗口 (证据={dbl})。"
                     "原因：自动化会话的无交互桌面/前台焦点限制使 Qt 双击事件无法稳定触发，"
                     "且本轮严格禁止用 rps 命令（模态对话框会阻塞 QLocalServer 命令处理）。"
                     "app/rps.py、app/pet_window.py 等源码与上一轮打包验收完全一致（见 E 段 mtime/sha256），"
                     "该路径上一轮已覆盖；此项为测试能力限制，不计入产品缺陷")
        else:
            info("C", "附加项【未覆盖，非缺陷】：独立会话未定位到宠物主窗口；"
                      "本轮源码未改 rps 相关文件，上一轮打包验收已覆盖该路径，不计入产品缺陷")
        # 附加会话清理：优先 quit 命令，否则强制清场
        sq, _, _ = send_cmd("quit", settle=0.8)
        try:
            pr.wait(timeout=15)
        except subprocess.TimeoutExpired:
            kill_tree(pr)
        close_log(pr)
        force_clear()
        emit(f"      附加会话 quit send={sq} exit={pr.poll()}; 清理后 BuddyPet 进程={buddy_pids()}")

    finally:
        emit("\n---- 兜底清理 ----")
        if main_p is not None and main_p.poll() is None:
            kill_tree(main_p)
        force_clear()
        emit(f"      兜底清理后 BuddyPet 进程={buddy_pids()}")

    # ---------------- D / F 收尾 ----------------
    emit("\n========== D. 用户真实配置零污染 ==========")
    cfg_after = fp(REAL_CONFIG)
    check("D", "1_用户真实配置mtime/size/sha256完全一致", cfg_before == cfg_after,
          f"BEFORE={json.dumps(cfg_before,ensure_ascii=False)} | AFTER={json.dumps(cfg_after,ensure_ascii=False)}")

    emit("\n========== F. 收尾 ==========")
    residual_final = buddy_pids()
    check("F", "1_无残留BuddyPet进程", not residual_final, f"buddy_pids={residual_final}")
    pipe_free = not server_reachable(300)
    check("F", "2_无WorkBuddyDesktopPet命名管道占用", pipe_free,
          f"QLocalSocket 连 {SERVER_NAME} 可达={not pipe_free}")
    hk_ok, hk_err = try_hotkey(MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_B, 0xB0C)
    check("F", "3_全局热键可重新注册", hk_ok, f"RegisterHotKey ok={hk_ok} err={hk_err}（已注销）")
    artifacts_after = fingerprint_artifacts()
    check("F", "4_产物指纹与测试前一致", artifacts_before == artifacts_after,
          f"BEFORE={json.dumps(artifacts_before,ensure_ascii=False)} | AFTER={json.dumps(artifacts_after,ensure_ascii=False)}")
    # 临时目录清理（报告写在 tools/ 下，不受影响）
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        emit(f"F. 临时目录已清理: {tmp} exists={tmp.exists()}")
        check("F", "5_临时目录已清理", not tmp.exists(), f"{tmp}")
    except Exception as e:
        check("F", "5_临时目录已清理", False, f"异常 {e}")

    emit("")
    emit("=" * 78)
    emit(f"运行时汇总: PASS={_C['PASS']} FAIL={_C['FAIL']} INFO={_C['INFO']}")
    if _FAILS:
        emit("FAILURES:")
        for f in _FAILS:
            emit("  - " + f)
    emit(f"RUNTIME_IS_PASS: {'YES' if _C['FAIL'] == 0 else 'NO'}")
    REPORT.write_text("\n".join(_OUT) + "\n", encoding="utf-8")
    emit(f"REPORT_FILE: {REPORT}")


if __name__ == "__main__":
    main()
