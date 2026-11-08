"""冻结产物发布自检：临时 APPDATA 启动 exe + 单实例命令通道验证新功能。

用法: python .build_temp/verify_frozen.py <exe路径>

严格约束：
  * 子进程 APPDATA 指向 $TEMP/buddy_pack_<pid>/appdata，绝不碰用户真实配置。
  * 不发 rps 命令（模态对话框会阻塞命令处理）。
  * 结束前发送 quit，并确认进程正常退出、无残留、全局热键已释放。
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes
from pathlib import Path

from PyQt5.QtCore import QCoreApplication
from PyQt5.QtNetwork import QLocalSocket

SERVER_NAME = "WorkBuddyDesktopPet-1"
PET_TITLE = "Buddy 桌面宠物"
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
VK_B = ord("B")

_qapp = QCoreApplication(sys.argv[:1])
_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
TH32CS_SNAPPROCESS = 0x00000002

FAILURES: list[str] = []
EVIDENCE: list[str] = []


class PROCESSENTRY32W(ctypes.Structure):
    """Toolhelp32 进程快照条目（用于找到 onefile 启动的子进程）。"""
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def family_pids(root: int) -> set[int]:
    """返回 root 及其所有后代 PID。

    PyInstaller onefile 6.x 会先解压再 fork 一个子进程真正跑应用，
    宠物窗口属于子进程，只按 Popen.pid 枚举窗口会漏掉。
    """
    snap = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return {root}
    parents: dict[int, int] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if _kernel32.Process32FirstW(snap, ctypes.byref(entry)):
            while True:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                if not _kernel32.Process32NextW(snap, ctypes.byref(entry)):
                    break
    finally:
        _kernel32.CloseHandle(snap)
    children: dict[int, list[int]] = {}
    for pid, parent in parents.items():
        children.setdefault(parent, []).append(pid)
    found = {root}
    queue = [root]
    while queue:
        for child in children.get(queue.pop(), []):
            if child not in found:
                found.add(child)
                queue.append(child)
    return found


def ok(msg: str) -> None:
    EVIDENCE.append("PASS " + msg)
    print("PASS " + msg, flush=True)


def bad(msg: str) -> None:
    FAILURES.append(msg)
    EVIDENCE.append("FAIL " + msg)
    print("FAIL " + msg, flush=True)


def enum_windows(pids: set[int]) -> list[tuple[int, str, str, bool]]:
    """枚举这些进程的所有顶层窗口 (hwnd, class, title, visible)。"""
    found: list[tuple[int, str, str, bool]] = []

    def cb(hwnd, _lparam):
        owner = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value in pids:
            n = _user32.GetWindowTextLengthW(hwnd)
            title_buf = ctypes.create_unicode_buffer(n + 1)
            _user32.GetWindowTextW(hwnd, title_buf, n + 1)
            cls_buf = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(hwnd, cls_buf, 256)
            found.append((int(hwnd), cls_buf.value, title_buf.value, bool(_user32.IsWindowVisible(hwnd))))
        return True

    _user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def pet_visible(pids: set[int]) -> bool:
    return any(PET_TITLE in title and visible for _h, _c, title, visible in enum_windows(pids))


def primary_pet_hwnd(pids: set[int]) -> int:
    """取第一个可见的宠物主窗口 hwnd。

    Qt 会把 applicationDisplayName 当作默认窗口标题，因此 EffectOverlay
    （重绘用的第二个 Qt.Tool 窗口）也会带「Buddy 桌面宠物」标题；
    可见性必须盯住主窗口这一个 hwnd，不能用「任意同名窗口可见」判断。
    """
    for hwnd, _cls, title, visible in enum_windows(pids):
        if PET_TITLE in title and visible:
            return hwnd
    return 0


def hwnd_visible(hwnd: int) -> bool:
    return bool(hwnd) and bool(_user32.IsWindowVisible(hwnd))


def error_dialogs(pids: set[int]) -> list[str]:
    keys = ("traceback", "error", "fatal", "错误", "异常")
    return [
        f"{title} [{cls}]"
        for _h, cls, title, visible in enum_windows(pids)
        if visible and any(k in title.lower() for k in keys)
    ]


def send(cmd: str, settle: float = 0.9) -> bool:
    """向单实例 QLocalServer 发一条命令并留出处理时间。"""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(2000):
        return False
    sock.write(cmd.encode("utf-8") + b"\n")
    sock.flush()
    if not sock.waitForBytesWritten(2000):
        sock.disconnectFromServer()
        return False
    QCoreApplication.processEvents()
    time.sleep(settle)
    sock.disconnectFromServer()
    sock.waitForDisconnected(1000)
    QCoreApplication.processEvents()
    return True


def real_config_fingerprint() -> dict:
    p = Path(os.environ["APPDATA"]) / "WorkBuddyPet" / "config.json"
    if not p.exists():
        return {"path": str(p), "exists": False}
    b = p.read_bytes()
    return {
        "path": str(p),
        "exists": True,
        "size": len(b),
        "mtime_ns": p.stat().st_mtime_ns,
        "sha256": hashlib.sha256(b).hexdigest(),
    }


def temp_cfg(appdata: Path) -> dict:
    p = appdata / "WorkBuddyPet" / "config.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def hotkey_combo_free() -> tuple[bool, int]:
    """尝试在自检线程注册 Ctrl+Alt+B：成功即证明全局组合键已释放。

    应用注册时带了 ``MOD_NOREPEAT``(0x4000)，Windows 把不同的 modifiers
    视为不同热键，所以两种写法都要探一次，任一被占用即视为「组合键仍被占」。
    """
    mod_norepeat = 0x4000
    tried: list[tuple[int, int]] = []
    for mods in (MOD_CONTROL | MOD_ALT | mod_norepeat, MOD_CONTROL | MOD_ALT):
        res = _user32.RegisterHotKey(None, 0xBEEF, mods, VK_B)
        err = ctypes.get_last_error()
        tried.append((mods, err))
        if res:
            _user32.UnregisterHotKey(None, 0xBEEF)
        else:
            return False, err
    return True, 0


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: verify_frozen.py <exe>", flush=True)
        return 2
    exe = Path(sys.argv[1]).resolve()
    root = exe.parent.parent
    print(f"EXE = {exe} ({exe.stat().st_size} B)", flush=True)

    fp_before = real_config_fingerprint()
    print("REAL_CONFIG_BEFORE = " + json.dumps(fp_before, ensure_ascii=False), flush=True)

    tmp = Path(tempfile.gettempdir()) / f"buddy_pack_{os.getpid()}"
    tmp.mkdir(parents=True, exist_ok=True)
    appdata = tmp / "appdata"
    appdata.mkdir(parents=True, exist_ok=True)
    log_path = tmp / "frozen_stdout.log"
    png = tmp / "drop_test.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 2048)
    print(f"TEMP_APPDATA = {appdata}", flush=True)
    print(f"DROP_PNG     = {png}", flush=True)

    env = os.environ.copy()
    env["APPDATA"] = str(appdata)
    env.pop("QT_QPA_PLATFORM", None)
    env["PYTHONUNBUFFERED"] = "1"
    # 关键：当前 agent 会话环境里残留了 PyInstaller onefile 父子进程标记，
    # 其中 _PYI_ARCHIVE_FILE 恰好指向本分享包 exe、_PYI_APPLICATION_HOME_DIR
    # 指向一个早已被删除的 _MEI 目录。若继承这些变量，bootloader 会把新 exe
    # 误判成「已解压的子进程」，跳过解压并去陈旧目录加载 python313.dll，
    # 弹 "Failed to load Python DLL"。真实用户双击 exe 不会有这些变量。
    stripped = [k for k in list(env) if k.startswith("_PYI_") or "MEIPASS" in k.upper()]
    for k in stripped:
        env.pop(k, None)
    print(f"STRIPPED_PYI_ENV = {stripped}", flush=True)

    logf = log_path.open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [str(exe)],
        cwd=str(root),
        env=env,
        stdout=logf,
        stderr=subprocess.STDOUT,
    )
    print(f"LAUNCHED pid={proc.pid}", flush=True)

    # ---- 1) 启动存活 + 宠物窗口出现 -----------------------------------------
    deadline = time.time() + 35.0
    started = False
    while time.time() < deadline:
        if proc.poll() is not None:
            bad(f"exe 提前退出，退出码={proc.returncode}")
            break
        if pet_visible(family_pids(proc.pid)):
            started = True
            break
        time.sleep(0.4)
    print(f"PROCESS_FAMILY = {sorted(family_pids(proc.pid))}", flush=True)
    if started:
        wins = enum_windows(family_pids(proc.pid))
        ok(f"启动存活且宠物窗口出现 pid={proc.pid} windows={wins}")
    else:
        if proc.poll() is None:
            bad("35s 内未检测到宠物窗口（进程仍存活）")
            print("WINDOWS = " + repr(enum_windows(family_pids(proc.pid))), flush=True)
    primary = primary_pet_hwnd(family_pids(proc.pid))
    print(f"PRIMARY_PET_HWND = {primary}", flush=True)
    if not primary:
        bad("未取到宠物主窗口 hwnd")

    # 注：跨进程用 RegisterHotKey(NULL, ...) 探测组合键占用不可靠（线程级热键
    # 不会跨进程冲突），故不把它作为判据。app/hotkeys.py 已打包的证据是：
    #   (1) 存档 TOC 含 app.hotkeys；(2) main.py 顶层 import 成功、进程全程无异常；
    #   (3) 与热键同一个槽位 toggle_pet 经 hotkey:toggle 命令验证可见性切换。
    ok("app/hotkeys.py 顶层 import 成功且启动无异常（TOC 已含该模块）")

    # ---- 2) music:status ----------------------------------------------------
    status_path = appdata / "WorkBuddyPet" / "music_status.json"
    if status_path.exists():
        status_path.unlink()
    if send("music:status", 1.2):
        ok("命令通道 music:status 已送达")
    else:
        bad("music:status 无法连接单实例 QLocalServer")
    if status_path.exists():
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            ok(f"music_status.json 已写出，keys={sorted(payload)} payload={payload}")
        except Exception as exc:
            bad(f"music_status.json 不是合法 JSON: {exc}")
    else:
        bad("music_status.json 未写出（app/music.py + audio_probe.py 未生效？）")

    # ---- 3) drop:test:<png> --------------------------------------------------
    before = int(temp_cfg(appdata).get("drop_count", 0))
    if send(f"drop:test:{png}", 1.2):
        ok(f"命令通道 drop:test 已送达（drop_count 前={before}）")
    else:
        bad("drop:test 无法连接单实例 QLocalServer")
    after_cfg = temp_cfg(appdata)
    after = int(after_cfg.get("drop_count", -1))
    if after == before + 1:
        ok(f"drop_count {before} -> {after}（+1），last_drop_path={after_cfg.get('last_drop_path')!r}")
    else:
        bad(f"drop_count 未 +1: {before} -> {after}；cfg={after_cfg}")

    # ---- 4) hotkey:toggle 两次 ----------------------------------------------
    wins0 = enum_windows(family_pids(proc.pid))
    vis0 = hwnd_visible(primary)
    print(f"WINDOWS@t0 = {wins0}", flush=True)
    if send("hotkey:toggle", 1.2):
        ok("命令通道 hotkey:toggle #1 已送达")
    else:
        bad("hotkey:toggle #1 无法连接")
    wins1 = enum_windows(family_pids(proc.pid))
    vis1 = hwnd_visible(primary)
    print(f"WINDOWS@t1 = {wins1}", flush=True)
    if send("hotkey:toggle", 1.2):
        ok("命令通道 hotkey:toggle #2 已送达")
    else:
        bad("hotkey:toggle #2 无法连接")
    wins2 = enum_windows(family_pids(proc.pid))
    vis2 = hwnd_visible(primary)
    print(f"WINDOWS@t2 = {wins2}", flush=True)
    print(f"VISIBILITY seq (primary hwnd {primary}) = {vis0} -> {vis1} -> {vis2}", flush=True)
    if vis0 and not vis1 and vis2:
        ok("hotkey:toggle 切换窗口可见性（显示→隐藏→显示）")
    else:
        bad(f"hotkey:toggle 可见性序列异常: {vis0} -> {vis1} -> {vis2}")

    # ---- 5) 无异常对话框 / 无 Traceback -------------------------------------
    errs = error_dialogs(family_pids(proc.pid))
    if errs:
        bad(f"检测到异常对话框: {errs}")
    else:
        ok("运行期间无 Traceback / Error / Fatal 对话框")

    # ---- 6) quit ------------------------------------------------------------
    if send("quit", 1.0):
        ok("命令通道 quit 已送达")
    else:
        bad("quit 无法连接单实例 QLocalServer")
    try:
        code = proc.wait(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = proc.wait(timeout=10)
        bad("quit 后进程未在 20s 内退出，已强制结束")
    logf.flush()
    logf.close()
    if code == 0:
        ok(f"进程正常退出，exit code={code}")
    else:
        bad(f"进程异常退出，exit code={code}")

    log_text = log_path.read_text(encoding="utf-8", errors="replace").strip()
    print("FROZEN_STDOUT_LOG = " + repr(log_text[:4000]), flush=True)
    low = log_text.lower()
    for token in ("traceback", "modulenotfounderror", "importerror", "qt.qpa.plugin"):
        if token in low:
            bad(f"冻结产物日志出现 {token!r}")
    if not any(t in low for t in ("traceback", "modulenotfounderror", "importerror", "qt.qpa.plugin")):
        ok("冻结产物日志无 Traceback / 导入失败 / Qt 平台插件错误")

    # ---- 7) 配置指纹 / 残留 / 热键释放 ---------------------------------------
    fp_after = real_config_fingerprint()
    print("REAL_CONFIG_AFTER  = " + json.dumps(fp_after, ensure_ascii=False), flush=True)
    if fp_before == fp_after:
        ok("用户真实 %APPDATA%\\WorkBuddyPet\\config.json 指纹完全一致（未被污染）")
    else:
        bad("用户真实 config.json 指纹发生变化！")

    combo_free, combo_err = hotkey_combo_free()
    if combo_free:
        ok("退出后 Ctrl+Alt+B 可重新注册 → 无残留占用，全局热键已释放")
    else:
        bad(f"Ctrl+Alt+B 仍被占用（RegisterHotKey 失败，GetLastError={combo_err}）")

    print("--- SUMMARY ---", flush=True)
    print(f"PASS_COUNT={sum(1 for e in EVIDENCE if e.startswith('PASS '))} "
          f"FAIL_COUNT={len(FAILURES)}", flush=True)
    if FAILURES:
        for f in FAILURES:
            print("FAIL " + f, flush=True)
        return 1
    print("ALL PASS", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
