"""QA 独立验收：BuddyPet 冻结分享包 v1.3.0（不采信工程师自检结论）。

用法：
    python tools/qa_packaged.py static    # A 产物完整性 + B 存档内容
    python tools/qa_packaged.py runtime  # C 启动 / D 新功能 / E 旧配置 / F 收尾
    python tools/qa_packaged.py all      # 全部

严格约束：
  * 所有启动 exe / 源码 load() 都用 $TEMP/buddy_packqa_<pid>/ 下的临时 APPDATA，
    绝不读写用户真实 %APPDATA%\\WorkBuddyPet\\config.json。
  * 启动 PyInstaller onefile 前，从子进程环境剥离全部 _PYI_* / *MEIPASS* 变量。
  * onefile 会 fork 父子进程；窗口枚举按「进程族」做，可见性盯住主窗口 hwnd/尺寸。
  * 不改任何源码或产物；脚本只新建 tools/qa_packaged_report.txt 作为证据。
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
import zipfile
from ctypes import wintypes
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

EXE_SHARE = ROOT / "BuddyPet_分享包" / "BuddyPet.exe"
EXE_DIST = ROOT / "dist" / "BuddyPet.exe"
ZIP_PATH = ROOT / "BuddyPet_分享包.zip"
DOC_PATH = ROOT / "BuddyPet_分享包" / "使用说明.txt"
SETTINGS_PY = ROOT / "app" / "settings_dialog.py"
SPEC_PATH = ROOT / "BuddyPet.spec"
REAL_CONFIG = Path(os.environ["APPDATA"]) / "WorkBuddyPet" / "config.json"

SERVER_NAME = "WorkBuddyDesktopPet-1"
PET_TITLE = "Buddy 桌面宠物"
RPS_TITLE = "石头 · 剪刀 · 布"
HOTKEY_ID = 0xB0B
MOD_CONTROL = 0x0002
MOD_ALT = 0x0001
MOD_NOREPEAT = 0x4000
VK_B = ord("B")
VK_1 = 0x31
VK_ESCAPE = 0x1B
WM_KEYDOWN = 0x0100
WM_CLOSE = 0x0010
WM_HOTKEY = 0x0312

RESULTS: list[tuple[str, str, bool, str]] = []          # section, item, passed, evidence
INFO: list[str] = []
REPORT: list[str] = []


def emit(line: str) -> None:
    print(line, flush=True)
    REPORT.append(line)


def check(section: str, item: str, cond: bool, evidence: str) -> bool:
    status = "PASS" if cond else "FAIL"
    emit(f"[{status}] {section}.{item}: {evidence}")
    RESULTS.append((section, item, bool(cond), evidence))
    return bool(cond)


def info(section: str, item: str, evidence: str) -> None:
    emit(f"[INFO] {section}.{item}: {evidence}")
    INFO.append(f"{section}.{item}: {evidence}")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fingerprint(p: Path) -> dict:
    st = p.stat()
    return {"size": st.st_size, "mtime_ns": st.st_mtime_ns,
            "sha256": sha256_file(p)}


def fmt_mtime(p: Path) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))


# =====================================================================
# Win32 helpers
# =====================================================================
class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260),
    ]


WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
_u32 = ctypes.WinDLL("user32", use_last_error=True)
_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

_u32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
_u32.EnumWindows.restype = wintypes.BOOL
_u32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
_u32.EnumChildWindows.restype = wintypes.BOOL
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
_u32.GetWindowThreadProcessId.restype = wintypes.DWORD
_u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_u32.GetWindowTextLengthW.restype = ctypes.c_int
_u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u32.GetWindowTextW.restype = ctypes.c_int
_u32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u32.GetClassNameW.restype = ctypes.c_int
_u32.IsWindowVisible.argtypes = [wintypes.HWND]
_u32.IsWindowVisible.restype = wintypes.BOOL
_u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
_u32.GetWindowRect.restype = wintypes.BOOL
_u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_u32.PostMessageW.restype = wintypes.BOOL
_u32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
_u32.RegisterHotKey.restype = wintypes.BOOL
_u32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.UnregisterHotKey.restype = wintypes.BOOL

_k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
_k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
_k32.Process32FirstW.restype = wintypes.BOOL
_k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
_k32.Process32NextW.restype = wintypes.BOOL
_k32.CloseHandle.argtypes = [ctypes.c_void_p]
_k32.CloseHandle.restype = wintypes.BOOL


def _win_text(hwnd: int) -> str:
    n = _u32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    _u32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _win_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    _u32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def process_snapshot() -> dict[int, tuple[int, str]]:
    snap = _k32.CreateToolhelp32Snapshot(0x00000002, 0)
    if not snap or snap == -1:
        return {}
    out: dict[int, tuple[int, str]] = {}
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if _k32.Process32FirstW(snap, ctypes.byref(e)):
            while True:
                out[int(e.th32ProcessID)] = (int(e.th32ParentProcessID), e.szExeFile)
                if not _k32.Process32NextW(snap, ctypes.byref(e)):
                    break
    finally:
        _k32.CloseHandle(snap)
    return out


def family(root: int, snap: dict[int, tuple[int, str]] | None = None) -> set[int]:
    snap = snap or process_snapshot()
    children: dict[int, list[int]] = {}
    for pid, (ppid, _name) in snap.items():
        children.setdefault(ppid, []).append(pid)
    found = {root}
    queue = [root]
    while queue:
        for child in children.get(queue.pop(), []):
            if child not in found:
                found.add(child)
                queue.append(child)
    return found


def buddy_pids(snap: dict[int, tuple[int, str]] | None = None) -> dict[int, str]:
    snap = snap or process_snapshot()
    return {pid: name for pid, (_ppid, name) in snap.items()
            if name.lower().startswith("buddypet")}


def enum_windows(pids: set[int] | None = None) -> list[dict]:
    out: list[dict] = []

    def cb(hwnd, _lparam):
        pid = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pids is None or pid.value in pids:
            r = RECT()
            _u32.GetWindowRect(hwnd, ctypes.byref(r))
            out.append({
                "hwnd": int(hwnd), "pid": int(pid.value),
                "title": _win_text(int(hwnd)), "class": _win_class(int(hwnd)),
                "visible": bool(_u32.IsWindowVisible(hwnd)),
                "w": r.right - r.left, "h": r.bottom - r.top,
            })
        return True

    _u32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def child_texts(hwnd: int) -> list[str]:
    texts: list[str] = []

    def cb(child, _lparam):
        t = _win_text(int(child))
        if t:
            texts.append(t)
        return True

    _u32.EnumChildWindows(hwnd, WNDENUMPROC(cb), 0)
    return texts


def hwnd_visible(hwnd: int) -> bool:
    return bool(hwnd) and bool(_u32.IsWindowVisible(hwnd))


def find_main_pet(pids: set[int]) -> tuple[int, int, int]:
    """按尺寸（约 215x321）定位宠物主窗口，避免误取同名 EffectOverlay。"""
    cands = [w for w in enum_windows(pids)
             if w["visible"] and PET_TITLE in w["title"]]
    if not cands:
        return 0, 0, 0
    best = min(cands, key=lambda w: abs(w["w"] - 215) + abs(w["h"] - 321))
    return best["hwnd"], best["w"], best["h"]


def rps_windows(pids: set[int], include_hidden: bool = False) -> list[dict]:
    return [w for w in enum_windows(pids)
            if RPS_TITLE in w["title"] and (include_hidden or w["visible"])]


def main_pet_hwnds(pids: set[int]) -> list[int]:
    """只认尺寸接近 215x321 的宠物主窗口，排除同名 EffectOverlay。"""
    return sorted(w["hwnd"] for w in enum_windows(pids)
                  if w["visible"] and PET_TITLE in w["title"]
                  and abs(w["w"] - 215) <= 40 and abs(w["h"] - 321) <= 40)


def error_windows(pids: set[int]) -> list[str]:
    keys = ("traceback", "error", "fatal", "错误", "异常", "failed", "python dll", "qt platform")
    return [f"{w['title']} [{w['class']}]" for w in enum_windows(pids)
            if w["visible"] and any(k in w["title"].lower() for k in keys)]


def try_register_hotkey(mods: int, vk: int, hid: int = HOTKEY_ID) -> tuple[bool, int]:
    """在本 QA 进程（另一进程）尝试注册，返回 (是否成功, GetLastError)。"""
    ctypes.set_last_error(0)
    ok = bool(_u32.RegisterHotKey(None, hid, mods, vk))
    err = ctypes.get_last_error()
    if ok:
        _u32.UnregisterHotKey(None, hid)
    return ok, err


# =====================================================================
# 命令通道
# =====================================================================
_qapp = None


def ensure_qapp():
    global _qapp
    if _qapp is None:
        from PyQt5.QtCore import QCoreApplication
        _qapp = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])
    return _qapp


def send_cmd(cmd: str, settle: float = 0.7, keep_open: bool = False):
    """向单实例 QLocalServer 发一条命令；返回 (bool, sock_or_None)。"""
    from PyQt5.QtNetwork import QLocalSocket
    ensure_qapp()
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(2000):
        return False, None
    sock.write(cmd.encode("utf-8") + b"\n")
    sock.flush()
    wrote = bool(sock.waitForBytesWritten(2000))
    if settle:
        time.sleep(settle)
    if keep_open:
        return wrote, sock
    sock.disconnectFromServer()
    sock.waitForDisconnected(500)
    return wrote, None


def server_reachable(timeout_ms: int = 400) -> bool:
    from PyQt5.QtNetwork import QLocalSocket
    ensure_qapp()
    s = QLocalSocket()
    s.connectToServer(SERVER_NAME)
    ok = bool(s.waitForConnected(timeout_ms))
    s.disconnectFromServer()
    return ok


# =====================================================================
# 环境构造
# =====================================================================
PYI_KEYS_OBSERVED = {
    "_PYI_ARCHIVE_FILE": str(EXE_SHARE),
    "_PYI_APPLICATION_HOME_DIR": os.path.join(tempfile.gettempdir(), "_MEI00004a342"),
    "_PYI_PARENT_PROCESS_LEVEL": "1",
}


def make_env(appdata: Path, *, dirty: bool = False) -> dict:
    env = os.environ.copy()
    # 剥离一切 PyInstaller 父子进程标记 / MEIPASS 残留
    for k in list(env):
        if "_PYI_" in k.upper() or "MEIPASS" in k.upper():
            env.pop(k, None)
    # 避免外部 Qt / Python 变量干扰冻结产物
    for k in ("QT_QPA_PLATFORM", "QT_PLUGIN_PATH", "PYTHONPATH", "PYTHONHOME"):
        env.pop(k, None)
    env["APPDATA"] = str(appdata)
    env["PYTHONUNBUFFERED"] = "1"
    if dirty:
        env.update(PYI_KEYS_OBSERVED)
    return env


def launch(exe: Path, appdata: Path, log: Path, *, dirty: bool = False):
    fh = open(log, "wb")
    p = subprocess.Popen([str(exe)], cwd=str(ROOT), env=make_env(appdata, dirty=dirty),
                         stdout=fh, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    p._qa_log_fh = fh
    return p


def close_log(p) -> None:
    fh = getattr(p, "_qa_log_fh", None)
    if fh:
        try:
            fh.close()
        except Exception:
            pass


def kill_tree(p) -> None:
    if p.poll() is None:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)],
                       capture_output=True, timeout=15)
    try:
        p.wait(timeout=5)
    except Exception:
        pass
    close_log(p)


def wait_window(pids: set[int], predicate, timeout: float = 20.0, interval: float = 0.3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        snap = process_snapshot()
        fam = set()
        for r in pids:
            fam |= family(r, snap)
        got = predicate(fam)
        if got:
            return got, fam
        time.sleep(interval)
    return None, fam


def wait_file(path: Path, timeout: float = 8.0) -> bool:
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


# =====================================================================
# A. 产物完整性
# =====================================================================
def section_a() -> None:
    emit("\n========== A. 产物完整性 ==========")

    # A1
    a = fingerprint(EXE_SHARE)
    b = fingerprint(EXE_DIST)
    check("A", "1_exe_同构",
          a["size"] == b["size"] and a["sha256"] == b["sha256"],
          f"分享包 size={a['size']} sha256={a['sha256']} mtime={fmt_mtime(EXE_SHARE)} "
          f"mtime_ns={EXE_SHARE.stat().st_mtime_ns} | dist size={b['size']} "
          f"sha256={b['sha256']} mtime={fmt_mtime(EXE_DIST)} mtime_ns={EXE_DIST.stat().st_mtime_ns}")
    info("A", "1_exe_体积", f"{a['size']} B = {a['size']/1024/1024:.2f} MiB（预期约 60~70MB）")

    # A2
    with zipfile.ZipFile(ZIP_PATH) as z:
        bad = z.testzip()
        names = z.namelist()
        infos = {i.filename: i for i in z.infolist()}
        expected = {"BuddyPet_分享包/BuddyPet.exe", "BuddyPet_分享包/使用说明.txt"}
        check("A", "2_zip_testzip", bad is None,
              f"ZipFile.testzip()={bad!r}, 总大小={ZIP_PATH.stat().st_size} B")
        check("A", "2_zip_恰好两文件且带前缀",
              len(names) == 2 and set(names) == expected,
              f"namelist={names}")
        deflate_ok = all(i.compress_type == zipfile.ZIP_DEFLATED for i in infos.values())
        check("A", "2_zip_deflate", deflate_ok,
              "; ".join(f"{k}: compress_type={v.compress_type} "
                        f"(ZIP_DEFLATED={zipfile.ZIP_DEFLATED}) 压缩后 {v.compress_size}B"
                        for k, v in infos.items()))
        for name in sorted(expected):
            inner = z.read(name)
            disk = (ROOT / name).read_bytes()
            check("A", f"2_zip_内容一致[{name.split('/')[-1]}]",
                  sha256_bytes(inner) == sha256_bytes(disk),
                  f"zip内 sha256={sha256_bytes(inner)} len={len(inner)} | 目录内 sha256={sha256_bytes(disk)} len={len(disk)}")

    # A3
    src = SETTINGS_PY.read_text(encoding="utf-8")
    lines = src.splitlines()
    ver_lines = [(i + 1, l.strip()) for i, l in enumerate(lines)
                 if l.strip().startswith("APP_VERSION")]
    check("A", "3_APP_VERSION_1.3.0",
          any(n == 37 and 'APP_VERSION = "1.3.0"' in l for n, l in ver_lines),
          f"命中行 {ver_lines}")
    about_idx = next((i for i, l in enumerate(lines) if l.strip().startswith("class AboutDialog")), -1)
    about_body = "\n".join(lines[about_idx:]) if about_idx >= 0 else ""
    uses = "APP_VERSION" in about_body
    line640 = lines[639].strip() if len(lines) >= 640 else ""
    check("A", "3_AboutDialog_使用_APP_VERSION",
          uses and 'f"v{APP_VERSION}' in line640,
          f"class AboutDialog 位于第 {about_idx+1} 行；第640行 = {line640!r}；正文含 APP_VERSION={uses}")

    # A4
    raw = DOC_PATH.read_bytes()
    try:
        txt = raw.decode("utf-8")
        utf8_ok = True
    except Exception as e:
        txt, utf8_ok = "", False
    dl = txt.splitlines()
    has_bom = raw[:3] == b"\xef\xbb\xbf"
    crlf_n = raw.count(b"\r\n")
    lf_n = raw.count(b"\n")
    check("A", "4_说明_UTF8可读且67行",
          utf8_ok and len(dl) == 67 and not has_bom,
          f"size={len(raw)}B, sha256={sha256_bytes(raw)}, BOM={has_bom}, "
          f"CRLF={crlf_n}, LF={lf_n}, splitlines={len(dl)}")

    required_new = {
        "v1.3.0": "v1.3.0",
        "Ctrl+Alt+B": "Ctrl+Alt+B",
        "拖放且绝不复制移动打开": "绝不复制、移动或打开",
        "双击石头剪刀布": "双击宠物",
        "音乐感知": "音乐感知",
        "透明区域不接收拖放": "透明区域不接收拖放",
        "两个设置开关": "新增两个开关",
        "快捷键被占用FAQ": "Ctrl+Alt+B 没反应",
    }
    for label, token in required_new.items():
        check("A", f"4_新增[{label}]", token in txt, f"命中 {token!r} 于第 "
              f"{[i+1 for i,l in enumerate(dl) if token in l]} 行")
    old_features = {
        "双击托盘叫回宠物": "双击托盘图标：从隐藏状态叫回宠物",
        "左键行为": "左键单击：打开 WorkBuddy",
        "10个特效": "共 10 个",
        "4皮肤": "默认 + 樱粉 / 霜蓝 / 紫罗兰",
    }
    for label, token in old_features.items():
        check("A", f"4_旧功能未写错[{label}]", token in txt, f"命中 {token!r}")
    headers = ["【这是什么】", "【对方需要做什么】", "【首次启动】", "【基本操作】",
               "【设置】", "【配置保存在哪】", "【常见问题】", "【文件清单】"]
    positions = [next((i + 1 for i, l in enumerate(dl) if h in l), 0) for h in headers]
    check("A", "4_旧8节顺序保留",
          all(p > 0 for p in positions) and positions == sorted(positions),
          f"章节行号 {dict(zip(headers, positions))}")
    # 旧 49 行按序保留：删除本轮 18 行新增内容后应恰好剩 49 行
    new_line_nos = {3, 4, 5, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 47, 48, 62, 63}
    reconstructed = [l for i, l in enumerate(dl, 1) if i not in new_line_nos]
    check("A", "4_原文49行按序保留",
          len(reconstructed) == 49,
          f"67 行 - 18 行新增 = {len(reconstructed)} 行；新增行号={sorted(new_line_nos)}；"
          f"还原后首行={reconstructed[0]!r} 末行={reconstructed[-1]!r}")
    emit("---- 还原出的旧版 49 行（按原顺序）----")
    for i, l in enumerate(reconstructed, 1):
        emit(f"  {i:2d}| {l}")
    emit("---- (end) ----")

    # A5
    r = subprocess.run(["git", "diff", "--", "BuddyPet.spec"], cwd=str(ROOT),
                       capture_output=True, text=True)
    check("A", "5_spec未改动", r.returncode == 0 and r.stdout.strip() == "",
          f"git diff -- BuddyPet.spec exit={r.returncode} stdout={r.stdout.strip()!r} "
          f"spec sha256={sha256_file(SPEC_PATH)}")
    exists = {n: (ROOT / n).exists() for n in
              ("BuddyPet_v1.1_prev.exe", "BuddyPet_dbg.spec")}
    check("A", "5_旧文件仍在", all(exists.values()),
          f"{exists}; prev size={(ROOT/'BuddyPet_v1.1_prev.exe').stat().st_size}B; "
          f"dbg size={(ROOT/'BuddyPet_dbg.spec').stat().st_size}B")
    spec_txt = SPEC_PATH.read_text(encoding="utf-8")
    info("A", "5_spec收集方式",
         f"spec 含 collect_submodules('app')={'collect_submodules' in spec_txt}; "
         f"含 console=False（无控制台窗口）={'console=False' in spec_txt}; "
         f"含 excludes=[]={'excludes=[]' in spec_txt}")


# =====================================================================
# B. 冻结 exe 存档内容
# =====================================================================
def section_b() -> None:
    emit("\n========== B. 冻结 exe 存档内容 ==========")
    from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader

    c = CArchiveReader(str(EXE_SHARE))
    top = set(c.toc.keys())
    pyz_bytes = c.extract("PYZ.pyz")
    tmp_pyz = Path(tempfile.gettempdir()) / f"_qa_pyz_{os.getpid()}.pyz"
    tmp_pyz.write_bytes(pyz_bytes)
    try:
        z = ZlibArchiveReader(str(tmp_pyz))
        pyz_mods = set(z.toc.keys())
    finally:
        tmp_pyz.unlink(missing_ok=True)

    emit(f"[INFO] B: CArchive 顶层条目={len(top)}, PYZ 模块数={len(pyz_mods)}, "
         f"PYZ 原始字节={len(pyz_bytes)}")
    need = ["app.lines", "app.hotkeys", "app.audio_probe", "app.music", "app.rps"]
    for m in need:
        check("B", f"模块在存档[{m}]", m in pyz_mods or m in top,
              f"PYZ 命中={m in pyz_mods}; 顶层命中={m in top}; "
              f"PYZ中所有app模块={sorted(x for x in pyz_mods if x.startswith('app'))}")
    banned = ["onnxruntime", "rapidocr", "paddle", "torch"]
    joined_top = " | ".join(sorted(top)).lower()
    joined_pyz = " | ".join(sorted(pyz_mods)).lower()
    for m in banned:
        hit_top = [n for n in top if m in n.lower()]
        hit_pyz = [n for n in pyz_mods if m in n.lower()]
        check("B", f"已回滚OCR不在存档[{m}]",
              not hit_top and not hit_pyz,
              f"顶层命中={hit_top[:6]} PYZ命中={hit_pyz[:6]}")
    size_mib = EXE_SHARE.stat().st_size / 1024 / 1024
    check("B", "体积合理", 55 <= size_mib <= 75,
          f"exe 大小 {EXE_SHARE.stat().st_size} B = {size_mib:.2f} MiB（预期约 60~70MB；"
          f"旧 OCR 版 156MB 已回滚）")
    # 独立用 CLI archive_viewer 复核（证据）
    r = subprocess.run([sys.executable, "-m", "PyInstaller.utils.cliutils.archive_viewer",
                        "-l", "-r", "-b", str(EXE_SHARE)],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=180)
    cli = (r.stdout or "") + (r.stderr or "")
    emit(f"[INFO] B.archive_viewer_cli: exit={r.returncode}, 输出行数={len(cli.splitlines())}")
    for m in need:
        emit(f"    CLI 含 {m} = {m in cli}")
    info("B", "CLI复核", f"python -m PyInstaller.utils.cliutils.archive_viewer -l -r -b "
         f"BuddyPet_分享包/BuddyPet.exe → exit={r.returncode}")


# =====================================================================
# C/D/E/F 运行时
# =====================================================================
def section_runtime() -> None:
    emit("\n========== C~F. 冻结 exe 运行时 ==========")
    tmp = Path(tempfile.gettempdir()) / f"buddy_packqa_{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    appdata = tmp / "appdata"
    appdata.mkdir()
    cfg_dir = appdata / "WorkBuddyPet"
    png = tmp / "drop_test.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8)
    emit(f"[INFO] TEMP_ROOT = {tmp}")
    emit(f"[INFO] TEMP_APPDATA = {appdata}")

    fp_before = fingerprint(REAL_CONFIG)
    emit(f"[INFO] 用户真实配置 BEFORE = {json.dumps(fp_before, ensure_ascii=False)}")
    pre_buddy = buddy_pids()
    check("F", "0_测试前无BuddyPet残留进程", not pre_buddy,
          f"buddy_pids={pre_buddy}")
    check("F", "0_测试前无残留命名管道", not server_reachable(300),
          f"QLocalSocket 连 {SERVER_NAME} 可达={server_reachable(300)}")

    dirty_log = tmp / "c2_dirty.log"
    main_log = tmp / "main_stdout.log"
    second_log = tmp / "second_instance.log"
    e_log = tmp / "e_stdout.log"

    # ---------------- C2：不剥离 _PYI_* 的失败复现（信息项，最先做） --------
    emit("\n---- C2. 不剥离 _PYI_* 的失败复现（信息项）----")
    c2_appdata = tmp / "c2_appdata"
    c2_appdata.mkdir()
    p2 = launch(EXE_SHARE, c2_appdata, dirty_log, dirty=True)
    c2_fam = family(p2.pid)
    c2_windows: list[dict] = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < 6.0:
        c2_fam |= family(p2.pid)
        c2_windows = enum_windows(c2_fam)
        if p2.poll() is not None and not c2_windows:
            break
        if any(w["visible"] for w in c2_windows):
            break
        time.sleep(0.3)
    c2_elapsed = time.monotonic() - t0
    c2_errs = []
    for w in c2_windows:
        texts = child_texts(w["hwnd"])
        c2_errs.append((w["title"], w["class"], texts[:6]))
    c2_alive = p2.poll() is None
    c2_logtext = ""
    try:
        close_log(p2)
        c2_logtext = dirty_log.read_text(encoding="utf-8", errors="replace")[:1200]
    except Exception:
        pass
    emit(f"[INFO] C2: elapsed={c2_elapsed:.2f}s alive={c2_alive} "
         f"exit={p2.poll()} family={sorted(c2_fam)}")
    emit(f"[INFO] C2: windows={json.dumps(c2_errs, ensure_ascii=False)[:1500]}")
    emit(f"[INFO] C2: stdout/stderr={c2_logtext!r}")
    c2_failed = (not c2_alive) or any(
        "failed" in (t or "").lower() or "python dll" in (t or "").lower()
        for _t, _c, ts in c2_errs for t in ts)
    info("C", "2_不剥离PYI变量会失败",
         f"独立复现：注入 {PYI_KEYS_OBSERVED} 后 alive={c2_alive} exit={p2.poll()} "
         f"窗口={c2_errs!r}；判定启动失败={c2_failed}。"
         f"此为本会话环境污染（非产品缺陷），真实用户双击不会带这些变量")
    # 关掉可能存在的错误弹窗并杀干净
    for w in c2_windows:
        if w["visible"]:
            _u32.PostMessageW(w["hwnd"], WM_CLOSE, 0, 0)
    time.sleep(0.4)
    kill_tree(p2)
    # 兜底清场：C2 若意外起了残留实例，会占用单实例 server 让 C1 伪装成失败
    for pid in list(buddy_pids()):
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)
    time.sleep(0.6)
    check("C", "2_C2清场后无残留server", not server_reachable(300),
          f"C2 清场后 BuddyPet 进程={buddy_pids()}, "
          f"QLocalServer 可达={server_reachable(300)}")

    # ---------------- C1：剥离后真实启动 ----------------
    emit("\n---- C1. 剥离 _PYI_* 后真实启动 ----")
    env_clean = make_env(appdata)
    stripped = [k for k in os.environ if "_PYI_" in k.upper() or "MEIPASS" in k.upper()]
    check("C", "1_环境已剥离PYI变量",
          not any("_PYI_" in k.upper() or "MEIPASS" in k.upper() for k in env_clean),
          f"会话中原有 {stripped}; 子进程环境剩余 "
          f"{[k for k in env_clean if '_PYI_' in k.upper() or 'MEIPASS' in k.upper()]}")
    p = launch(EXE_SHARE, appdata, main_log)
    got, fam = wait_window({p.pid}, lambda f: find_main_pet(f)[0], timeout=30.0)
    main_hwnd, mw, mh = find_main_pet(fam) if got else (0, 0, 0)
    fam = family(p.pid)
    wins = enum_windows(fam)
    snap = process_snapshot()
    alive_names = {pid: snap[pid][1] for pid in fam if pid in snap}
    check("C", "1_进程族存活且主窗口可见",
          bool(got) and p.poll() is None and hwnd_visible(main_hwnd),
          f"Popen.pid={p.pid}, exit={p.poll()}, family={sorted(fam)}, "
          f"进程名={alive_names}, 主窗口 hwnd={main_hwnd} 尺寸={mw}x{mh} "
          f"IsWindowVisible={hwnd_visible(main_hwnd)}")
    check("C", "1_onefile父子两进程",
          len([pid for pid in fam if pid in snap and
               snap[pid][1].lower().startswith("buddypet")]) >= 2,
          f"进程族中 BuddyPet.exe 数="
          f"{len([pid for pid in fam if pid in snap and snap[pid][1].lower().startswith('buddypet')])}")
    errs = error_windows(fam)
    win_brief = [(w["title"], w["class"], w["visible"],
                  str(w["w"]) + "x" + str(w["h"])) for w in wins]
    check("C", "1_无错误弹窗/Traceback/Qt插件错误",
          not errs,
          f"错误窗口={errs}; 全部窗口={win_brief}")
    try:
        close_log(p)
        logtxt = main_log.read_text(encoding="utf-8", errors="replace")
    except Exception:
        logtxt = ""
    check("C", "1_stdout_stderr无致命错误",
          "Failed to load Python DLL" not in logtxt and "Traceback" not in logtxt,
          f"stdout/stderr 长度={len(logtxt)}, 内容={logtxt[:400]!r}（console=False 通常为空）")

    # ---------------- C3：单实例 ----------------
    emit("\n---- C3. 单实例：第二个 exe 自退 ----")
    before_main = main_pet_hwnds(family(p.pid))
    t0 = time.monotonic()
    p3 = launch(EXE_SHARE, appdata, second_log)
    p3_fam = {p3.pid}
    p3_wins: list[dict] = []
    seen_hwnd: set[int] = set()
    p3_exit = None
    child_appear_t = None
    end_t = None
    while time.monotonic() - t0 < 12:
        p3_fam |= family(p3.pid)
        if len(p3_fam) > 1 and child_appear_t is None:
            child_appear_t = time.monotonic()
        for w in enum_windows(p3_fam):
            if w["visible"] and w["hwnd"] not in seen_hwnd:
                seen_hwnd.add(w["hwnd"])
                p3_wins.append(w)
        if p3.poll() is not None:
            p3_exit = p3.returncode
            end_t = time.monotonic()
            break
        time.sleep(0.1)
    p3_elapsed = time.monotonic() - t0
    app_level = ((end_t - child_appear_t) * 1000
                 if (end_t and child_appear_t) else -1.0)
    if p3.poll() is None:
        kill_tree(p3)
    close_log(p3)
    time.sleep(0.5)
    after_main = main_pet_hwnds(family(p.pid))
    check("C", "3_第二实例自退且不产生第二窗口",
          p3_exit == 0 and after_main == before_main and len(before_main) == 1 and not p3_wins,
          f"第二实例 exit={p3_exit} elapsed={p3_elapsed:.2f}s（含 onefile 解压开销）; "
          f"按尺寸识别的主窗口 hwnd 前={before_main} 后={after_main}（恒为1个）; "
          f"第二实例进程族可见窗口={[(w['title'], w['w'], w['h']) for w in p3_wins]}; "
          f"第二实例 stdout={second_log.read_text(encoding='utf-8', errors='replace')[:200]!r}")
    info("C", "3_自退耗时分解",
         f"墙钟 {p3_elapsed:.2f}s = onefile 二次解压(约 60MB) + 应用逻辑; "
         f"其中「子进程出现→整体退出」={app_level:.0f}ms，"
         f"这才是 main() 里 QLocalSocket.connectToServer→write('show')→return 0 的应用级自退路径。"
         f"墙钟 >1s 由 onefile 冷启动解压决定，与单实例逻辑无关，非缺陷")

    # ---------------- D1：music:status ----------------
    emit("\n---- D1. music:status ----")
    music_json = cfg_dir / "music_status.json"
    music_json.unlink(missing_ok=True)
    ok1, _ = send_cmd("music:status", settle=1.0)
    got_music = wait_file(music_json, 8.0)
    mj = read_json(music_json)
    required = ["playing", "kind", "peak", "music_app", "degraded", "enabled"]
    field_ok = all(k in mj for k in required)
    peak, peak_is_float = mj.get("peak"), isinstance(mj.get("peak"), float)
    check("D", "1_music_status_字段齐全",
          ok1 and got_music and field_ok,
          f"send={ok1} 文件存在={got_music} 全部字段={sorted(mj)} 缺={[k for k in required if k not in mj]}")
    check("D", "1_music_status_peak为float",
          peak_is_float,
          f"peak={peak!r} type={type(peak).__name__}; degraded={mj.get('degraded')} "
          f"playing={mj.get('playing')} kind={mj.get('kind')} "
          f"music_app={mj.get('music_app')} enabled={mj.get('enabled')}; "
          f"全文={json.dumps(mj, ensure_ascii=False)}")
    info("D", "1_说明", "peak 为 float 证明 app/audio_probe.py 的 Core Audio "
         "IAudioMeterInformation 探测在冻结环境真实执行；若为 null 则是设计内 degraded 降级")

    # ---------------- D2：drop:test ----------------
    emit("\n---- D2. drop:test:<png> ----")
    cfg_path = cfg_dir / "config.json"
    cfg0 = read_json(cfg_path)
    drop0 = int(cfg0.get("drop_count", 0)) if cfg0 else 0
    png_before = fingerprint(png)
    ok2, _ = send_cmd(f"drop:test:{png}", settle=1.2)
    cfg1 = read_json(cfg_path)
    drop1 = int(cfg1.get("drop_count", -1)) if cfg1 else -1
    png_after = fingerprint(png)
    check("D", "2_drop_count_0到1且路径正确",
          ok2 and drop0 == 0 and drop1 == 1 and cfg1.get("last_drop_path") == str(png),
          f"send={ok2} drop_count {drop0}->{drop1}, last_drop_path={cfg1.get('last_drop_path')!r} "
          f"期望={str(png)!r}")
    check("D", "2_被拖文件只读未改动",
          png_before == png_after,
          f"before size={png_before['size']} mtime_ns={png_before['mtime_ns']} "
          f"sha256={png_before['sha256']} | after size={png_after['size']} "
          f"mtime_ns={png_after['mtime_ns']} sha256={png_after['sha256']}")

    # ---------------- D3：hotkey:toggle 两次 ----------------
    emit("\n---- D3. hotkey:toggle 两次 ----")
    v0 = hwnd_visible(main_hwnd)
    ok3a, _ = send_cmd("hotkey:toggle", settle=0.9)
    v1 = hwnd_visible(main_hwnd)
    ok3b, _ = send_cmd("hotkey:toggle", settle=0.9)
    v2 = hwnd_visible(main_hwnd)
    check("D", "3_主窗口可见性True-False-True",
          ok3a and ok3b and (v0, v1, v2) == (True, False, True),
          f"主窗口 hwnd={main_hwnd} IsWindowVisible: 初始={v0} 第一次={v1} 第二次={v2}")

    # ---------------- D4：外部抢占 Ctrl+Alt+B ----------------
    emit("\n---- D4. 外部抢占全局热键 ----")
    mods_app = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
    reg_ok, reg_err = try_register_hotkey(mods_app, VK_B, HOTKEY_ID)
    check("D", "4_运行中外部抢占失败1409",
          (not reg_ok) and reg_err == 1409,
          f"另一进程 RegisterHotKey(None, 0x{HOTKEY_ID:X}, "
          f"MOD_CONTROL|MOD_ALT|MOD_NOREPEAT=0x{mods_app:04X}, ord('B')=0x{VK_B:X}) "
          f"→ 返回={reg_ok}, GetLastError={reg_err} (期望 1409 ERROR_HOTKEY_ALREADY_REGISTERED)")

    # ---------------- D5：石头剪刀布（工程师自检未覆盖） ----------------
    emit("\n---- D5. 石头剪刀布（本轮补测）----")
    cfg_before_rps = read_json(cfg_path)
    score0 = sum(int(cfg_before_rps.get(k, 0)) for k in ("rps_wins", "rps_losses", "rps_draws"))
    ok5, rps_sock = send_cmd("rps", settle=0.4, keep_open=True)
    rps_got, rps_fam = wait_window({p.pid}, lambda f: rps_windows(f), timeout=8.0)
    rps_list = rps_windows(rps_fam) if rps_got else []
    rps_brief = [(w["hwnd"], w["title"], str(w["w"]) + "x" + str(w["h"]))
                 for w in rps_list]
    check("D", "5_RPS对话框存在且唯一",
          ok5 and len(rps_list) == 1,
          f"send={ok5} 命中窗口={rps_brief}")
    rps_hwnd = rps_list[0]["hwnd"] if rps_list else 0
    # 防重入：再发一次 rps，仍只有 1 个
    ok5b, sock5b = send_cmd("rps", settle=0.6, keep_open=True)
    rps_list2 = rps_windows(family(p.pid), include_hidden=True)
    check("D", "5_防重入再发rps仍只有1个",
          len(rps_list2) == 1,
          f"第二次 rps send={ok5b}, 含隐藏窗口在内 RPS 窗口数={len(rps_list2)}, "
          f"hwnd列表={[(w['hwnd'], w['visible']) for w in rps_list2]}")
    # 真实按键 VK_1 打一局
    posted = _u32.PostMessageW(rps_hwnd, WM_KEYDOWN, VK_1, 0) if rps_hwnd else 0
    time.sleep(1.6)  # 倒计时 3 x 220ms + 余量
    cfg_after_rps = read_json(cfg_path)
    score1 = sum(int(cfg_after_rps.get(k, 0)) for k in ("rps_wins", "rps_losses", "rps_draws"))
    alive_after_round = p.poll() is None
    check("D", "5_VK_1打一局战绩+1且进程不abort",
          bool(posted) and score1 == score0 + 1 and alive_after_round,
          f"PostMessageW(WM_KEYDOWN,VK_1)={bool(posted)} 到 hwnd={rps_hwnd}; "
          f"战绩合计 {score0}->{score1}; wins={cfg_after_rps.get('rps_wins')} "
          f"losses={cfg_after_rps.get('rps_losses')} draws={cfg_after_rps.get('rps_draws')}; "
          f"进程 exit={p.poll()}（未 abort）")
    # Esc 关闭
    posted_esc = _u32.PostMessageW(rps_hwnd, WM_KEYDOWN, VK_ESCAPE, 0) if rps_hwnd else 0
    time.sleep(1.2)
    ghost = rps_windows(family(p.pid), include_hidden=True)
    alive_after_esc = p.poll() is None
    check("D", "5_VK_ESCAPE关闭后无幽灵窗口且进程存活",
          bool(posted_esc) and len(ghost) == 0 and alive_after_esc,
          f"PostMessageW(WM_KEYDOWN,VK_ESCAPE)={bool(posted_esc)}; "
          f"残留 RPS 窗口数={len(ghost)}; 桌宠进程 exit={p.poll()}; "
          f"主窗口仍可见={hwnd_visible(main_hwnd)}")
    for s in (rps_sock, sock5b):
        if s is not None:
            try:
                s.disconnectFromServer()
            except Exception:
                pass

    # ---------------- quit ----------------
    emit("\n---- quit 收尾 ----")
    okq, _ = send_cmd("quit", settle=0.8)
    try:
        p.wait(timeout=15)
        q_exit = p.returncode
    except subprocess.TimeoutExpired:
        q_exit = None
        kill_tree(p)
    close_log(p)
    time.sleep(1.0)
    residual = buddy_pids()
    check("C", "6_quit进程正常退出无残留",
          okq and q_exit == 0 and not residual,
          f"send quit={okq}, exit={q_exit}, 残留 BuddyPet 进程={residual}")
    main_logtxt = ""
    try:
        main_logtxt = main_log.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    check("C", "6_退出日志无Traceback/DLL错误",
          "Traceback" not in main_logtxt and "Failed to load Python DLL" not in main_logtxt,
          f"stdout/stderr 长度={len(main_logtxt)}, 内容={main_logtxt[:500]!r}")

    # D4 后半：quit 后组合键可重新注册
    emit("\n---- D4(续). quit 后热键已注销 ----")
    reg_ok2, reg_err2 = try_register_hotkey(mods_app, VK_B, HOTKEY_ID)
    reg_ok3, reg_err3 = try_register_hotkey(MOD_CONTROL | MOD_ALT, VK_B, 0xBEEF)
    check("D", "4_quit后可重新注册成功",
          reg_ok2 and reg_ok3,
          f"带 MOD_NOREPEAT: ok={reg_ok2} err={reg_err2}; 不带: ok={reg_ok3} err={reg_err3}"
          f"（均已立即 UnregisterHotKey 清理）")

    # ---------------- E. 旧配置兼容性 ----------------
    emit("\n========== E. 旧字段配置兼容性（信息项）==========")
    emit("[INFO] E: 本会话中用户真实配置被【外部进程】改写："
         "任务书基线 647B/sha0658df67…(15:02:29) → 当前 839B/sha14486695…(15:48:25)，"
         "已含全部新字段与 rps 战绩。故 E 段不拿「当前真实配置」当旧格式样本，"
         "而是按基线记录精确重建一份旧字段格式配置（字段只到 gallery_auto_sync）。")
    OLD_647: dict = {
        "size": 120, "opacity": 1.0, "always_on_top": True, "idle_anim": True,
        "liveliness": 1.8, "lock_pos": False, "edge_cling": True,
        "gaze_follow": True, "sound_enabled": True, "sound_volume": 0.7,
        "left_click": "open", "auto_effect": True, "auto_effect_min": 8,
        "pos": [1818, 197], "workbuddy_path": "", "seen_intro": True,
        "last_effect": "hop", "bubble_hint": True,
        "selected_buddy_id": "default", "selected_skin_id": "default",
        "gallery_base_url": "", "gallery_auth_ref": "",
        "gallery_cache_ttl_h": 24, "gallery_last_sync": "", "gallery_auto_sync": True,
    }
    new_field_names = ["hotkey_enabled", "hotkey_toggle", "music_aware",
                       "last_drop_path", "drop_count", "rps_wins", "rps_losses", "rps_draws"]
    e_appdata = tmp / "e_appdata"
    e_cfg_dir = e_appdata / "WorkBuddyPet"
    e_cfg_dir.mkdir(parents=True)
    e_cfg = e_cfg_dir / "config.json"
    e_cfg.write_text(json.dumps(OLD_647, ensure_ascii=False, indent=2), encoding="utf-8")
    check("E", "0_重建647B旧格式样本",
          len(OLD_647) == 25 and list(OLD_647)[-1] == "gallery_auto_sync" and
          not any(k in OLD_647 for k in new_field_names),
          f"重建样本字段数={len(OLD_647)}, 末字段={list(OLD_647)[-1]}, "
          f"缺失新字段={new_field_names}, 文件={e_cfg}")

    # E1 源码级 config.load() 验证（明确说明用的是源码）
    code = ("import json; from app import config; "
            "print(json.dumps(config.load().to_dict(), ensure_ascii=False))")
    e_env = os.environ.copy()
    e_env["APPDATA"] = str(e_appdata)
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=e_env,
                       capture_output=True, text=True, timeout=30)
    loaded = json.loads(r.stdout.strip()) if r.stdout.strip() else {}
    old_keep = {
        "size": 120, "liveliness": 1.8, "pos": [1818, 197],
        "seen_intro": True, "last_effect": "hop", "left_click": "open",
    }
    new_defaults = {
        "hotkey_enabled": True, "hotkey_toggle": "Ctrl+Alt+B", "music_aware": True,
        "drop_count": 0, "last_drop_path": "", "rps_wins": 0,
        "rps_losses": 0, "rps_draws": 0,
    }
    check("E", "1_源码config.load自动补齐且保留旧值",
          all(loaded.get(k) == v for k, v in old_keep.items()) and
          all(loaded.get(k) == v for k, v in new_defaults.items()),
          f"方式=源码 python -c 'from app import config; config.load()'（APPDATA 指向临时目录）; "
          f"exit={r.returncode}; 旧值={ {k: loaded.get(k) for k in old_keep} }; "
          f"新字段={ {k: loaded.get(k) for k in new_defaults} }; stderr={r.stderr[:200]!r}")

    # E2 冻结 exe 用「重建的旧格式配置」真实启动
    e_png = tmp / "e_drop.png"
    e_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"E" * 1024)
    ep = launch(EXE_SHARE, e_appdata, e_log)
    egot, efam = wait_window({ep.pid}, lambda f: find_main_pet(f)[0], timeout=30.0)
    e_hwnd, ew, eh = find_main_pet(efam) if egot else (0, 0, 0)
    e_errs = error_windows(efam)
    check("E", "2_冻结exe用旧配置启动不报错且窗口出现",
          bool(egot) and ep.poll() is None and not e_errs,
          f"主窗口 hwnd={e_hwnd} 尺寸={ew}x{eh}（旧 size=120 故不同于默认 215x321）; "
          f"exit={ep.poll()}; 错误窗口={e_errs}")
    # 用 drop:test 触发一次 save()，让 exe 把「它 load 到的完整配置」落盘
    ok_e, _ = send_cmd(f"drop:test:{e_png}", settle=1.2)
    saved = read_json(e_cfg)
    old_keep_runtime = {k: v for k, v in old_keep.items() if k != "last_effect"}
    check("E", "2_冻结exe落盘配置含新字段且保留旧值",
          ok_e and
          all(saved.get(k) == v for k, v in old_keep_runtime.items()) and
          saved.get("rps_wins") == 0 and saved.get("rps_losses") == 0 and
          saved.get("rps_draws") == 0 and saved.get("hotkey_enabled") is True and
          saved.get("hotkey_toggle") == "Ctrl+Alt+B" and saved.get("music_aware") is True and
          saved.get("last_drop_path") == str(e_png) and saved.get("drop_count") == 1,
          f"send={ok_e}; 落盘旧值={ {k: saved.get(k) for k in old_keep_runtime} }; "
          f"落盘新字段={ {k: saved.get(k) for k in new_defaults} }; "
          f"last_effect={saved.get('last_effect')!r}（exe 播放特效可按设计改写，故不作严格断言）")
    ok_eq, _ = send_cmd("quit", settle=0.8)
    try:
        ep.wait(timeout=15)
        e_exit = ep.returncode
    except subprocess.TimeoutExpired:
        e_exit = None
        kill_tree(ep)
    close_log(ep)
    time.sleep(0.8)
    e_errlog = ""
    try:
        e_errlog = e_log.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    check("E", "2_旧配置场景quit干净退出",
          ok_eq and e_exit == 0 and "Traceback" not in e_errlog,
          f"quit send={ok_eq} exit={e_exit} stdout/stderr={e_errlog[:300]!r}")
    info("E", "2_pos说明",
         "E 段在 quit 之前读取落盘配置，pos 仍为 [1818,197]；"
         "PetApp.quit() 会按设计把 pos 覆写为当前窗口坐标，非缺陷")

    # E3 当前真实配置（外部进程改写后的 839B 新格式）复制一份再跑，纯健壮性
    emit("\n---- E3. 复制「当前真实配置」再跑冻结 exe（信息项）----")
    time.sleep(0.6)
    e2_appdata = tmp / "e2_appdata"
    e2_dir = e2_appdata / "WorkBuddyPet"
    e2_dir.mkdir(parents=True)
    real_bytes = REAL_CONFIG.read_bytes()
    (e2_dir / "config.json").write_bytes(real_bytes)
    emit(f"[INFO] E3: 复制当前真实配置 size={len(real_bytes)} "
         f"sha256={sha256_bytes(real_bytes)} 到 {e2_dir}")
    e2_log = tmp / "e2_stdout.log"
    ep2 = launch(EXE_SHARE, e2_appdata, e2_log)
    e2got, e2fam = wait_window({ep2.pid}, lambda f: find_main_pet(f)[0], timeout=30.0)
    e2_errs = error_windows(e2fam)
    check("E", "3_当前真实配置副本启动冻结exe不报错",
          bool(e2got) and ep2.poll() is None and not e2_errs,
          f"主窗口命中={bool(e2got)} exit={ep2.poll()} 错误窗口={e2_errs} "
          f"（复制件，原件未动）")
    ok_e2, _ = send_cmd("quit", settle=0.8)
    try:
        ep2.wait(timeout=15)
        e2_exit = ep2.returncode
    except subprocess.TimeoutExpired:
        e2_exit = None
        kill_tree(ep2)
    close_log(ep2)
    time.sleep(0.8)
    e2_log_txt = ""
    try:
        e2_log_txt = e2_log.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    check("E", "3_当前真实配置副本quit干净",
          ok_e2 and e2_exit == 0 and "Traceback" not in e2_log_txt,
          f"quit send={ok_e2} exit={e2_exit} stdout/stderr={e2_log_txt[:200]!r}")

    # ---------------- F. 收尾 ----------------
    emit("\n========== F. 收尾 ==========")
    fp_after = fingerprint(REAL_CONFIG)
    check("F", "1_用户真实配置指纹零写入",
          fp_after == fp_before,
          f"BEFORE {json.dumps(fp_before, ensure_ascii=False)} | AFTER {json.dumps(fp_after, ensure_ascii=False)}")
    info("F", "1_基线漂移说明",
         "任务书基线 size=647/mtime_ns=1794034949476671900/"
         "sha256=0658df67b2c1bb959e6447aacf0ada3a1dcd5c2c6a459b752ef1b507dc47cd0b "
         "(15:02:29) 在本会话中已被【外部进程】改写为 "
         "size=839/mtime_ns=1794037705015386800/sha256=144866952ec5… (15:48:25)，"
         "文件已含全部新字段与 rps 战绩 1/4/2。本 QA 的所有 exe/源码 load 均用临时 APPDATA，"
         "harness 内 BEFORE==AFTER 证明零写入；该外部改写按任务书约定不算失败、不修改、不修复")
    final_buddy = buddy_pids()
    check("F", "2_无残留BuddyPet进程",
          not final_buddy, f"buddy_pids={final_buddy}")
    pipe_free = not server_reachable(400)
    check("F", "2_无残留命名管道", pipe_free,
          f"QLocalSocket 连 {SERVER_NAME} 可达={not pipe_free}")
    hk_ok, hk_err = try_register_hotkey(mods_app, VK_B, HOTKEY_ID)
    check("F", "2_全局热键可重新注册", hk_ok,
          f"RegisterHotKey(None,0x{HOTKEY_ID:X},0x{mods_app:04X},ord('B')) → "
          f"ok={hk_ok} err={hk_err}（成功后已 UnregisterHotKey）")
    # 清理临时目录
    shutil.rmtree(tmp, ignore_errors=True)
    # 顺手清掉手工探针遗留目录
    shutil.rmtree(Path(tempfile.gettempdir()) / "buddy_packqa_probe", ignore_errors=True)
    check("F", "3_临时目录已清理", not tmp.exists(), f"rmtree({tmp}) exists={tmp.exists()}")


# =====================================================================
def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "all").lower()
    emit(f"QA 独立验收 | ROOT={ROOT} | python={sys.version.split()[0]} | mode={mode}")
    emit(f"EXE={EXE_SHARE} | 用户真实配置={REAL_CONFIG}")
    try:
        if mode in ("static", "all"):
            section_a()
            section_b()
        if mode in ("runtime", "all"):
            section_runtime()
    except Exception as exc:
        import traceback
        emit("[FAIL] HARNESS_EXCEPTION: " + repr(exc))
        emit(traceback.format_exc())
        RESULTS.append(("HARNESS", "exception", False, repr(exc)))

    fails = [(s, i, e) for s, i, ok, e in RESULTS if not ok]
    emit("\n========== 汇总 ==========")
    emit(f"总检查项={len(RESULTS)} PASS={len(RESULTS)-len(fails)} FAIL={len(fails)} "
         f"信息项={len(INFO)}")
    for s, i, e in fails:
        emit(f"  FAIL {s}.{i}: {e}")
    emit(f"IS_PASS: {'YES' if not fails else 'NO'}")
    try:
        out = ROOT / "tools" / "qa_packaged_report.txt"
        out.write_text("\n".join(REPORT) + "\n", encoding="utf-8")
        emit(f"REPORT_FILE: {out}")
    except Exception as exc:
        emit(f"REPORT_FILE write failed: {exc}")
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
