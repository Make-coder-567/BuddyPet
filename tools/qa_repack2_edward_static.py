# -*- coding: utf-8 -*-
"""QA 独立验收 (Edward / repack2 / v1.3.1) —— 静态部分：A 产物完整性 / B 冻结 exe 字节码 / D 配置基线 / E 改动范围。

设计原则：
  * 只读：不修改任何源码、产物、用户配置；唯一写入是本报告 tools/qa_repack2_static_report.txt。
  * 独立：CArchive / PYZ 用标准库 struct+zlib+marshal 自行解析，不 import PyInstaller；
          PyInstaller 官方 CLI 仅用于外部交叉印证。
  * 数值全部现场复算，不引用工程师本次报告的结论。

用法（解释器 marshal 版本须与 exe 内嵌 Python 一致，脚本会自动检测并提示）：
    C:/Users/mrhua/AppData/Local/Programs/Python/Python314/python.exe tools/qa_repack2_edward_static.py
    C:/Users/mrhua/python-sdk/python3.13.2/python.exe            tools/qa_repack2_edward_static.py
"""
from __future__ import annotations

import dis
import hashlib
import importlib.util
import itertools
import json
import marshal
import os
import re
import struct
import sys
import types
import zipfile
import zlib
from datetime import datetime
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path("C:/Users/mrhua/Desktop/桌面宠物")
SHARE = ROOT / "BuddyPet_分享包"
EXE_SHARE = SHARE / "BuddyPet.exe"
EXE_DIST = ROOT / "dist" / "BuddyPet.exe"
ZIP_PATH = ROOT / "BuddyPet_分享包.zip"
DOC = SHARE / "使用说明.txt"
SPEC = ROOT / "BuddyPet.spec"
SETTINGS = ROOT / "app" / "settings_dialog.py"
MUSIC = ROOT / "app" / "music.py"
REPORT = ROOT / "tools" / "qa_repack2_static_report.txt"

# 独立第三方基准：上一轮 v1.3.0 的 QA 报告 tools/qa_packaged_report.txt（非工程师本次报告）
BASE_V130_EXE_SHA = "b2fd213f561027f3c45872bd1efa3eec9f33708c3cbe422d0e10feef97dd3d96"
BASE_V130_EXE_SIZE = 63225313
BASE_V130_DOC_SHA = "dec34884f37972a498b91a541a5c0cf19b4077e893667791477c32fa15bd1408"
BASE_V130_DOC_SIZE = 4084
BASE_V130_DOC_LINES = 67
BASE_SPEC_SHA = "3a14bb6867b1b962d45c77e821254f2aa7536db1101aa44d90ee174e861a9d1c"
BASE_MUSIC_SHA_PREFIX = "ba24871c"

_OUT: list[str] = []
_COUNTS = {"PASS": 0, "FAIL": 0, "INFO": 0}
_FAILURES: list[str] = []


def emit(s: str = "") -> None:
    _OUT.append(s)
    try:
        print(s)
    except Exception:
        pass


def check(section: str, name: str, ok: bool, evidence: str = "") -> bool:
    tag = "PASS" if ok else "FAIL"
    _COUNTS[tag] += 1
    if not ok:
        _FAILURES.append(f"[{section}] {name}: {evidence}")
    emit(f"[{tag}] {section}.{name}" + (f": {evidence}" if evidence else ""))
    return ok


def info(section: str, s: str) -> None:
    _COUNTS["INFO"] += 1
    emit(f"[INFO] {section}: {s}")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fp(p: Path) -> dict:
    st = p.stat()
    return {
        "size": st.st_size,
        "mtime_ns": st.st_mtime_ns,
        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "sha256": sha256_file(p),
    }


def hdr(title: str) -> None:
    emit("")
    emit("=" * 78)
    emit(title)
    emit("=" * 78)


# ---------------------------------------------------------------- A
def section_a() -> None:
    hdr("A. 产物完整性（全部现场复算）")

    a1 = fp(EXE_SHARE)
    a2 = fp(EXE_DIST)
    emit(f"A.1 分享包 exe : {a1}")
    emit(f"A.1 dist   exe : {a2}")
    same = (a1["size"] == a2["size"] and a1["sha256"] == a2["sha256"]
            and a1["mtime_ns"] == a2["mtime_ns"])
    check("A", "1_exe_同构", same,
          f"share size={a1['size']} sha256={a1['sha256']} mtime={a1['mtime']} mtime_ns={a1['mtime_ns']} | "
          f"dist size={a2['size']} sha256={a2['sha256']} mtime={a2['mtime']} mtime_ns={a2['mtime_ns']}")

    diff_old = (a1["size"] != BASE_V130_EXE_SIZE and a1["sha256"] != BASE_V130_EXE_SHA)
    check("A", "1_确实不是旧v1.3.0构建", diff_old,
          f"本次 size={a1['size']} sha256={a1['sha256']} | 上一轮QA报告 v1.3.0 "
          f"size={BASE_V130_EXE_SIZE} sha256={BASE_V130_EXE_SHA}")
    prev = ROOT / "BuddyPet_v1.1_prev.exe"
    if prev.exists():
        check("A", "1_与v1.1_prev也不同", sha256_file(prev) != a1["sha256"],
              f"prev size={prev.stat().st_size} sha256={sha256_file(prev)}")

    # A2 zip
    zsize = ZIP_PATH.stat().st_size
    with zipfile.ZipFile(ZIP_PATH) as z:
        bad = z.testzip()
        names = z.namelist()
        infos = z.infolist()
        emit(f"A.2 zip size={zsize} mtime={datetime.fromtimestamp(ZIP_PATH.stat().st_mtime):%Y-%m-%d %H:%M:%S} "
             f"testzip()={bad} namelist={names}")
        check("A", "2_testzip", bad is None, f"testzip()={bad!r}")
        expected = {"BuddyPet_分享包/BuddyPet.exe", "BuddyPet_分享包/使用说明.txt"}
        check("A", "2_恰好两文件且带前缀", len(names) == 2 and set(names) == expected
              and all(n.startswith("BuddyPet_分享包/") for n in names), f"namelist={names}")
        deflate_ok = all(i.compress_type == zipfile.ZIP_DEFLATED for i in infos)
        for i in infos:
            emit(f"      {i.filename}: file_size={i.file_size} compress_size={i.compress_size} "
                 f"compress_type={i.compress_type} (ZIP_DEFLATED=8)")
        check("A", "2_均为deflate", deflate_ok,
              f"compress_types={[i.compress_type for i in infos]}")
        inner = {}
        for i in infos:
            data = z.read(i.filename)
            inner[i.filename] = (len(data), sha256_bytes(data))
        pairs = [
            ("BuddyPet_分享包/BuddyPet.exe", EXE_SHARE),
            ("BuddyPet_分享包/使用说明.txt", DOC),
        ]
        all_same = True
        for n, p in pairs:
            d = fp(p)
            ok = inner[n][0] == d["size"] and inner[n][1] == d["sha256"]
            all_same &= ok
            emit(f"      内容一致[{n}]: zip len={inner[n][0]} sha256={inner[n][1]} | "
                 f"目录 len={d['size']} sha256={d['sha256']} -> {ok}")
        check("A", "2_zip内两文件与目录内一致", all_same, f"zip总大小={zsize} B")
    info("A", f"zip 总大小 = {zsize} B；内部两文件压缩后合计 "
              f"{sum(i.compress_size for i in infos)} B")

    # A3 settings_dialog.py
    sbytes = SETTINGS.read_bytes()
    stxt = sbytes.decode("utf-8")
    slines = stxt.splitlines()
    line37 = slines[36] if len(slines) >= 37 else ""
    line640 = slines[639] if len(slines) >= 640 else ""
    emit(f"A.3 line37={line37!r}")
    emit(f"A.3 line640={line640!r}")
    check("A", "3_APP_VERSION_1.3.1", line37.strip() == 'APP_VERSION = "1.3.1"', f"line37={line37!r}")
    check("A", "3_第640行消费APP_VERSION", "APP_VERSION" in line640 and "f\"v{" in line640,
          f"line640={line640!r}")
    n131 = stxt.count("1.3.1")
    n130 = stxt.count("1.3.0")
    check("A", "3_全文仅1处1.3.1且0处1.3.0", n131 == 1 and n130 == 0,
          f"count('1.3.1')={n131} count('1.3.0')={n130}")

    # A4 说明
    raw = DOC.read_bytes()
    try:
        doc = raw.decode("utf-8")
        utf8_ok = True
    except Exception as e:
        doc, utf8_ok = "", False
        info("A", f"UTF-8 decode error: {e}")
    dl = doc.splitlines()
    bom = raw[:3] == b"\xef\xbb\xbf"
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n")
    emit(f"A.4 size={len(raw)}B sha256={sha256_bytes(raw)} BOM={bom} CRLF={crlf} LF={lf} "
         f"splitlines={len(dl)} ends_with_0A={raw.endswith(bytes([10]))}")
    check("A", "4_UTF8可读_70行_无BOM", utf8_ok and len(dl) == 70 and not bom,
          f"size={len(raw)}B sha256={sha256_bytes(raw)} splitlines={len(dl)}")
    check("A", "4_顶部版本v1.3.1", len(dl) >= 3 and "版本 v1.3.1" in dl[2],
          f"line3={dl[2]!r}" if len(dl) >= 3 else "no line3")
    check("A", "4_含v1.3.1优化说明", any("v1.3.1" in l and ("音乐感知" in l or "修复" in l) for l in dl),
          f"命中行号={[i+1 for i,l in enumerate(dl) if 'v1.3.1' in l]}")
    check("A", "4_含新增音乐感知FAQ", any("音乐感知没反应" in l for l in dl),
          f"命中行号={[i+1 for i,l in enumerate(dl) if '音乐感知没反应' in l]}")

    headers = ["【这是什么】", "【对方需要做什么】", "【首次启动】", "【基本操作】",
               "【设置】", "【配置保存在哪】", "【常见问题】", "【文件清单】"]
    positions = [next((i + 1 for i, l in enumerate(dl) if h in l), 0) for h in headers]
    check("A", "4_旧8章节齐全且顺序保留",
          all(p > 0 for p in positions) and positions == sorted(positions),
          f"章节行号 {dict(zip(headers, positions))}")
    tokens = {
        "10个特效": "共 10 个",
        "4套皮肤": "默认 + 樱粉 / 霜蓝 / 紫罗兰",
        "Ctrl+Alt+B": "Ctrl+Alt+B",
        "拖放文件": "把文件拖到宠物身上",
        "绝不复制移动打开": "绝不复制、移动或打开",
        "双击石头剪刀布": "双击宠物",
        "音乐感知": "音乐感知",
        "透明区不接收拖放": "透明区域不接收拖放",
        "两个设置开关": "新增两个开关",
        "文件清单": "BuddyPet.exe    —— 程序本体",
        "FAQ_杀软": "被杀毒软件拦截",
        "FAQ_置顶": "宠物被别的窗口挡住",
        "FAQ_托盘": "托盘图标不见了",
        "FAQ_自启": "想开机自动启动",
        "FAQ_快捷键冲突": "Ctrl+Alt+B 没反应",
        "FAQ_音乐感知": "音乐感知没反应",
    }
    missing = [k for k, v in tokens.items() if v not in doc]
    check("A", "4_旧功能条目全部仍在且未写错", not missing,
          f"缺失={missing}；命中 {len(tokens)-len(missing)}/{len(tokens)} 项")

    # A4 决定性证据：从 70 行中精确删除 3 行 + 版本行回退，暴力还原 v1.3.0 的 67 行，
    # 若重建字节的 sha256/size 与上一轮 QA 报告记录完全一致 => 旧 67 行按原顺序一字未改。
    trailer = b"\n" if raw.endswith(b"\n") else b""
    found: list[tuple[tuple[int, ...], str]] = []
    seen: set[str] = set()
    for combo in itertools.combinations(range(len(dl)), 3):
        recon = [l for i, l in enumerate(dl) if i not in combo]
        recon = [l.replace("版本 v1.3.1", "版本 v1.3.0") for l in recon]
        b = ("\n".join(recon) + ("\n" if trailer else "")).encode("utf-8")
        h = hashlib.sha256(b).hexdigest()
        if h == BASE_V130_DOC_SHA:
            key = b.decode("utf-8", "replace")
            if key not in seen:
                seen.add(key)
                found.append((tuple(i + 1 for i in combo), h))
    ok_recon = bool(found) and len("\n".join(dl).splitlines()) == 70
    combos_str = "; ".join(f"删除行号={c}" for c, _ in found[:4])
    check("A", "4_旧v1.3.0的67行按原顺序完整保留[sha256重建]", ok_recon,
          f"重建命中数={len(found)}；{combos_str}；重建 sha256={BASE_V130_DOC_SHA} "
          f"size={BASE_V130_DOC_SIZE}（上一轮QA报告基准）")
    if found:
        removed = sorted(found[0][0])
        emit(f"      本版相对 v1.3.0 的精确差异：新增 {len(removed)} 行 = {removed}")
        for n in removed:
            emit(f"        +{n}| {dl[n-1]!r}")
        emit(f"        改写第 3 行: {dl[2]!r}  ->  '版本 v1.3.0'")
        rebuilt = [l for i, l in enumerate(dl, 1) if i not in removed]
        rebuilt = [l.replace("版本 v1.3.1", "版本 v1.3.0") for l in rebuilt]
        emit(f"      还原后行数={len(rebuilt)}（期望 {BASE_V130_DOC_LINES}）"
             f" 首行={rebuilt[0]!r} 末行={rebuilt[-1]!r}")
    # 与上一轮 QA 报告 dump 的「更早 49 行原文」再做子序列比对
    old49 = [
        "Buddy 桌面宠物 · 单文件版使用说明", "================================", "",
        "【这是什么】", "WorkBuddy 的桌面常驻小助手（猫耳机器人 + 橙色工装）。常驻桌面、透明置顶、",
        "鼠标可穿透。本包是「单文件 exe」，已把所有依赖（Python、PyQt5、素材、皮肤、", "音效）全部打包进去。", "",
        "【对方需要做什么】", "  什么都不用装。直接双击 BuddyPet.exe 即可运行。",
        "  ⚠️ 仅支持 Windows（Win10 / Win11）。macOS / Linux 无法运行。", "",
        "【首次启动】", "  第一次双击会在后台解压到临时目录（约 1~2 秒），之后宠物出现在桌面右下角",
        "  并常驻系统托盘，可关闭这个说明。", "", "【基本操作】",
        "  · 左键单击：打开 WorkBuddy（需本机已安装 WorkBuddy，否则该按钮无反应，",
        "             不影响其它功能）",
        "  · 右键单击：上下文菜单（互动效果 / 设置 / 切换 Buddy / 隐藏 / 退出）",
        "  · 按住左键拖动：移动位置，松手自动记住",
        "  · 拖到屏幕左/右/上/下边缘：自动吸附「抓住」该边",
        "  · 鼠标靠近（约 460px 内）：眼睛会看向光标",
        "  · 双击托盘图标：从隐藏状态叫回宠物", "",
        "  右键菜单 → 互动效果：盲盒开箱 / 爱心暴击 / 元气弹跳 / 星光闪烁 / 彩带礼花 /",
        "  摇摆舞步 / 眨眼卖萌 / 咖啡时间 / 摸鱼打盹 / 旋转登场（共 10 个）。",
        "  右键菜单 → 切换 Buddy…：浏览并更换内置皮肤（默认 + 樱粉 / 霜蓝 / 紫罗兰）。", "",
        "【设置】", "  右键 → 设置…：可调大小、不透明度、活泼程度、左键行为、定时随机互动、",
        "  悬停提示、贴边依附、视线跟随、开机自启、WorkBuddy 路径等。",
        "  改动即时预览，保存生效。", "", "【配置保存在哪】",
        "  个人配置写在：C:\\Users\\你的用户名\\AppData\\Roaming\\WorkBuddyPet\\config.json",
        "  （换电脑或重装系统不会带走，属正常）。", "", "【常见问题】",
        "  · 被杀毒软件拦截 / 误报？",
        "    单文件 exe 首次运行会解压到临时目录，部分杀软可能误报，请选择「允许 /",
        "    信任 / 加入白名单」即可。",
        "  · 宠物被别的窗口挡住？右键 → 始终置顶。",
        "  · 托盘图标不见了？Windows 把它折叠进「^」里，拖出来固定即可。",
        "  · 想开机自动启动？右键 → 设置… → 打开「开机自动启动」。", "",
        "【文件清单】", "  BuddyPet.exe    —— 程序本体（单文件，删掉它就等于卸载，不残留）",
        "  （配置与缓存仅存在于你自己的 AppData，本包不含任何个人信息）",
    ]
    it = iter(dl)
    subseq = all(any(x == y for x in it) for y in old49)
    check("A", "4_上一轮QA报告49行原文仍为子序列", subseq,
          f"旧 49 行逐行按序命中当前 70 行 = {subseq}（旧功能未写错/未删）")

    # A5
    spec_sha = sha256_file(SPEC)
    check("A", "5_spec未被改动", spec_sha == BASE_SPEC_SHA,
          f"spec sha256={spec_sha} | 上一轮QA报告基准 {BASE_SPEC_SHA}")
    exists = {
        "BuddyPet_v1.1_prev.exe": (ROOT / "BuddyPet_v1.1_prev.exe").exists(),
        "BuddyPet_dbg.spec": (ROOT / "BuddyPet_dbg.spec").exists(),
        "requirements.txt": (ROOT / "requirements.txt").exists(),
        "assets/": (ROOT / "assets").is_dir(),
    }
    check("A", "5_旧文件与assets仍在", all(exists.values()), f"{exists}")
    emit(f"      requirements.txt 内容 = {ROOT.joinpath('requirements.txt').read_text(encoding='utf-8')!r}")


# ---------------------------------------------------------------- B
class CArchive:
    """独立实现的 PyInstaller CArchive 解析（格式核对自 PyInstaller 6.22.3 readers.py）。"""
    MAGIC = b"MEI\014\013\012\013\016"
    COOKIE_FMT = "!8sIIII64s"
    TOC_FMT = "!IIIIBc"

    def __init__(self, path: Path):
        self.path = path
        self.data = path.read_bytes()
        idx = self.data.rfind(self.MAGIC)
        if idx < 0:
            raise RuntimeError("CArchive cookie MAGIC not found")
        (magic, archive_length, toc_offset, toc_length, pyvers, pylib) = struct.unpack(
            self.COOKIE_FMT, self.data[idx:idx + struct.calcsize(self.COOKIE_FMT)])
        self.cookie_offset = idx
        self.end_offset = idx + struct.calcsize(self.COOKIE_FMT)
        self.start_offset = self.end_offset - archive_length
        self.pyvers = pyvers
        self.pylib = pylib.rstrip(b"\0").decode("utf-8", "replace")
        self.toc: dict[str, tuple] = {}
        self.options: list[str] = []
        p = self.start_offset + toc_offset
        tend = p + toc_length
        eh = struct.calcsize(self.TOC_FMT)
        while p < tend:
            (elen, eoff, dlen, ulen, cflag, tcode) = struct.unpack(self.TOC_FMT, self.data[p:p + eh])
            p += eh
            name = self.data[p:p + elen - eh].rstrip(b"\0").decode("utf-8", "replace")
            p += elen - eh
            tcode = tcode.decode("ascii")
            if tcode == "o":
                self.options.append(name)
            else:
                self.toc[name] = (eoff, dlen, ulen, cflag, tcode)

    def extract(self, name: str) -> bytes:
        eoff, dlen, ulen, cflag, tcode = self.toc[name]
        b = self.data[self.start_offset + eoff:self.start_offset + eoff + dlen]
        if cflag:
            b = zlib.decompress(b)
        return b


class PYZArchive:
    """独立实现的 PyInstaller PYZ 解析（格式核对自 pyimod01_archive.py）。"""
    MAGIC = b"PYZ\0"

    def __init__(self, blob: bytes):
        self.blob = blob
        if blob[:4] != self.MAGIC:
            raise RuntimeError(f"PYZ magic mismatch: {blob[:4]!r}")
        self.pymagic = blob[4:8]
        self.toc_offset = struct.unpack("!i", blob[8:12])[0]
        self.toc = dict(marshal.loads(blob[self.toc_offset:]))

    def code(self, name: str):
        tcode, off, length = self.toc[name]
        raw = zlib.decompress(self.blob[off:off + length])
        return marshal.loads(raw)


def all_codes(co):
    yield co
    for c in co.co_consts:
        if isinstance(c, types.CodeType):
            yield from all_codes(c)


def section_b() -> None:
    hdr("B. 冻结 exe 存档内容 —— 独立解析 CArchive / PYZ / marshal / dis")
    emit(f"B.0 运行解释器: {sys.version}")
    emit(f"B.0 解释器 MAGIC_NUMBER = {importlib.util.MAGIC_NUMBER.hex()}")
    ca = CArchive(EXE_SHARE)
    emit(f"B.0 cookie: exe_size={len(ca.data)} cookie_offset={ca.cookie_offset} "
         f"archive_start={ca.start_offset} python_version_field={ca.pyvers} "
         f"python_libname={ca.pylib!r}")
    emit(f"B.0 CArchive 顶层条目数={len(ca.toc)} options={ca.options}")
    pyz_names = [n for n, e in ca.toc.items() if e[4] == "z"]
    emit(f"B.0 PYZ 条目 = {pyz_names}")
    check("B", "0_CArchive可解析且有唯一PYZ", len(pyz_names) == 1,
          f"PYZ条目={pyz_names}, 顶层条目={len(ca.toc)}, pylib={ca.pylib!r}, pyvers={ca.pyvers}")

    pyz_blob = ca.extract(pyz_names[0])
    emit(f"B.0 PYZ 原始字节 = {len(pyz_blob)} B; header={pyz_blob[:12]!r}")
    pyz = PYZArchive(pyz_blob)
    emit(f"B.0 PYZ pymagic = {pyz.pymagic.hex()} toc_offset={pyz.toc_offset} PYZ模块数={len(pyz.toc)}")
    magic_match = pyz.pymagic == importlib.util.MAGIC_NUMBER
    check("B", "0_PYZ字节码版本与运行解释器匹配", magic_match,
          f"PYZ pymagic={pyz.pymagic.hex()} | 运行解释器 MAGIC={importlib.util.MAGIC_NUMBER.hex()} "
          f"({'3.14.6' if importlib.util.MAGIC_NUMBER.hex()=='2b0e0d0a' else '3.13.x' if importlib.util.MAGIC_NUMBER.hex()=='f30d0d0a' else '?'})")
    if not magic_match:
        emit("[WARN] marshal 版本不匹配，后续 code object 解析不可靠 —— 请换对应解释器重跑本脚本。")

    # 顶层 / PYZ 名称集合
    top_names = list(ca.toc.keys())
    pyz_names_all = list(pyz.toc.keys())
    emit(f"B.0 PYZ 顶层模块示例 = {sorted({n.split('.')[0] for n in pyz_names_all})[:40]}")
    info("B", f"CArchive 顶层条目={len(top_names)}, PYZ 模块数={len(pyz_names_all)} "
              f"(上一轮 v1.3.0 QA 报告: 顶层=291, PYZ=152)")

    # 五个模块
    for m in ("app.lines", "app.hotkeys", "app.audio_probe", "app.music", "app.rps"):
        check("B", f"模块在PYZ[{m}]", m in pyz.toc and m not in ca.toc,
              f"PYZ命中={m in pyz.toc}; 顶层命中={m in ca.toc}")
    emit(f"      PYZ 中所有 app 模块 = {sorted(n for n in pyz_names_all if n == 'app' or n.startswith('app.'))}")

    # OCR 回滚
    for lib in ("onnxruntime", "rapidocr", "paddle", "torch"):
        def match(n: str) -> bool:
            p = n.replace("\\", "/").lower()
            return (p == lib or p.startswith(lib + "/") or p.startswith(lib + ".")
                    or p.startswith(lib + "-") or ("/" + lib + "/") in ("/" + p + "/"))
        th = [n for n in top_names if match(n)]
        ph = [n for n in pyz_names_all if match(n)]
        loose = [n for n in top_names + pyz_names_all if lib in n.lower()]
        check("B", f"已回滚OCR不在存档[{lib}]", not th and not ph,
              f"顶层命中={th} PYZ命中={ph}（宽松子串命中={loose[:6]}）")

    # ---- app.music 字节码 ----
    check("B", "music_取到code_object", "app.music" in pyz.toc, "")
    mc = pyz.code("app.music")
    emit(f"B.music code: name={mc.co_name!r} filename={mc.co_filename!r} "
         f"co_names(前30)={list(mc.co_names)[:30]}")
    emit(f"B.music 模块级常量 = {[c for c in mc.co_consts if isinstance(c,(int,float,str,type(None))) and not isinstance(c,bool)][:24]}")
    emit(f"B.music 模块级 STORE_NAME = {[i.argval for i in dis.get_instructions(mc) if i.opname=='STORE_NAME']}")

    expected = {
        "POLL_MS": 500,
        "START_CONFIRM_MS": 600,
        "STOP_CONFIRM_MS": 1200,
        "APP_SCAN_EVERY": 4,
        "DEBOUNCE": 2,
        "START_PEAK": 0.004,
        "STOP_PEAK": 0.0015,
    }
    for name, val in expected.items():
        ok = (name in mc.co_names) and (val in mc.co_consts)
        check("B", f"music_模块级常量[{name}={val}]", ok,
              f"co_names命中={name in mc.co_names}, co_consts命中={val in mc.co_consts}; "
              f"实际常量={[c for c in mc.co_consts if isinstance(c,(int,float))][:12]}")

    # _manual_override 全面排除
    hits: list[str] = []
    for co in all_codes(mc):
        for attr in ("co_names", "co_varnames", "co_freevars", "co_cellvars"):
            for n in getattr(co, attr):
                if "_manual_override" in str(n):
                    hits.append(f"{co.co_name}.{attr}:{n}")
        for c in co.co_consts:
            if isinstance(c, str) and "_manual_override" in c:
                hits.append(f"{co.co_name}.co_consts:{c[:60]!r}")
    check("B", "music_P1修复_manual_override彻底不存在", not hits,
          f"命中={hits[:10]}；递归扫描 code object 数={len(list(all_codes(mc)))}")

    # time.monotonic 真实引用
    mono: list[str] = []
    for co in all_codes(mc):
        for ins in dis.get_instructions(co):
            if ins.argval == "monotonic" or "monotonic" in str(ins.argrepr):
                mono.append(f"{co.co_name}:{ins.opname}({ins.argval})")
    has_time = "time" in mc.co_names
    check("B", "music_time.monotonic被真实引用", bool(mono) and has_time,
          f"import time={has_time}; 指令命中={mono[:8]}")

    # 旧 POLL_MS=8000 不得作为模块级 int 常量
    int_consts = [c for c in mc.co_consts if isinstance(c, int) and not isinstance(c, bool)]
    has_8000_int = 8000 in int_consts
    doc_hits = [c for c in mc.co_consts if isinstance(c, str) and "8000" in c]
    check("B", "music_旧POLL_MS=8000未作为模块级int常量", not has_8000_int,
          f"模块级 int 常量={int_consts}; 含'8000'的字符串常量(docstring)={len(doc_hits)} 处（允许）")
    if doc_hits:
        emit(f"      docstring 提及: {doc_hits[0][:90]!r} ...")

    # 与当前源码编译结果对照：证明编入的是最新 app/music.py
    try:
        src_code = compile(MUSIC.read_text(encoding="utf-8"), "app/music.py", "exec", dont_inherit=True)
        src_ints = [c for c in src_code.co_consts if isinstance(c, (int, float)) and not isinstance(c, bool)]
        exe_ints = [c for c in mc.co_consts if isinstance(c, (int, float)) and not isinstance(c, bool)]
        same = src_ints == exe_ints
        doc_same = (src_code.co_consts[0] == mc.co_consts[0])
        check("B", "music_冻结字节码常量与最新源码编译一致", bool(same and doc_same),
              f"源码数值常量={src_ints} | 冻结数值常量={exe_ints} | docstring一致={doc_same}")
    except Exception as e:
        info("B", f"源码编译对照跳过/异常: {e}")

    # settings_dialog 版本
    sdc = pyz.code("app.settings_dialog")
    sd_strs = [c for co in all_codes(sdc) for c in co.co_consts if isinstance(c, str)]
    has131 = [s for s in sd_strs if "1.3.1" in s]
    has130 = [s for s in sd_strs if "1.3.0" in s]
    emit(f"B.settings_dialog: 含'1.3.1'的字符串常量={has131[:5]}")
    check("B", "settings_dialog_内嵌1.3.1且无1.3.0", bool(has131) and not has130,
          f"1.3.1命中={len(has131)} 1.3.0命中={len(has130)} {has130[:3]}")

    # dis 片段作为证据
    emit("---- app.music 模块级 STORE_NAME 指令片段 ----")
    for ins in dis.get_instructions(mc):
        if ins.opname in ("LOAD_CONST", "STORE_NAME") and ins.argval in (
            list(expected.keys()) + list(expected.values())):
            emit(f"      {ins.offset:5d} {ins.opname:12s} {ins.argrepr}")
    emit("---- (end) ----")


# ---------------------------------------------------------------- D / E
def section_de() -> None:
    hdr("D. 用户真实配置 BEFORE 基线（只读记录，禁止修改）")
    user_cfg = Path(os.path.expanduser("~")) / "AppData" / "Roaming" / "WorkBuddyPet" / "config.json"
    if user_cfg.exists():
        d = fp(user_cfg)
        emit(f"D.BEFORE path={user_cfg}")
        emit(f"D.BEFORE {json.dumps(d, ensure_ascii=False)}")
        try:
            cfg = json.loads(user_cfg.read_text(encoding="utf-8"))
            keys = {k: cfg.get(k) for k in ("size", "liveliness", "pos", "rps") if k in cfg}
            emit(f"D.BEFORE 关键用户设置 = {json.dumps(keys, ensure_ascii=False)[:300]}")
        except Exception as e:
            emit(f"D.BEFORE config.json parse 异常: {e}")
        check("D", "0_基线已记录", True, f"size={d['size']} sha256={d['sha256']} mtime_ns={d['mtime_ns']}")
    else:
        check("D", "0_基线已记录", False, f"MISSING {user_cfg}")

    hdr("E. 改动范围 / 无新增依赖（mtime + sha256 现场清单）")
    watch = [
        "app/music.py", "app/audio_probe.py", "app/pet_window.py", "app/config.py",
        "app/hotkeys.py", "app/lines.py", "app/rps.py", "app/settings_dialog.py",
        "main.py", "BuddyPet.spec", "requirements.txt",
        ".build_temp/pet_icon.ico",
        "BuddyPet_分享包/使用说明.txt", "BuddyPet_分享包/BuddyPet.exe",
        "dist/BuddyPet.exe", "BuddyPet_分享包.zip",
    ]
    window_start = datetime(2026, 11, 8, 9, 0, 0).timestamp()
    window_end = datetime(2026, 11, 8, 9, 30, 0).timestamp()
    in_window = []
    for rel in watch:
        p = ROOT / rel
        if not p.exists():
            emit(f"      {rel:38s} MISSING")
            continue
        d = fp(p)
        flag = "  <== 本次打包窗口(11-08 09:00~09:30)" if window_start <= p.stat().st_mtime <= window_end else ""
        emit(f"      {rel:38s} size={d['size']:>10d} mtime={d['mtime']} sha256={d['sha256'][:16]}…{flag}")
        if flag:
            in_window.append(rel)
    emit(f"E. 本次打包窗口内时间戳更新的文件 = {in_window}")
    expect_window = {
        "app/settings_dialog.py", "BuddyPet_分享包/使用说明.txt",
        "dist/BuddyPet.exe", "BuddyPet_分享包/BuddyPet.exe", "BuddyPet_分享包.zip",
    }
    check("E", "1_本次改动范围恰为5文件", set(in_window) == expect_window,
          f"实际={sorted(in_window)} | 期望={sorted(expect_window)}")

    music_sha = sha256_file(MUSIC)
    check("E", "2_music.py仍为18:49优化版未被本次改动", music_sha.startswith(BASE_MUSIC_SHA_PREFIX),
          f"app/music.py sha256={music_sha} (基准前缀 {BASE_MUSIC_SHA_PREFIX}…); "
          f"mtime={fp(MUSIC)['mtime']}")
    spec_sha = sha256_file(SPEC)
    check("E", "3_spec与上一轮QA报告一致", spec_sha == BASE_SPEC_SHA, f"spec sha256={spec_sha}")
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    check("E", "4_requirements仅PyQt5+pillow_无新增第三方依赖",
          "PyQt5" in req and "pillow" in req and len([l for l in req.splitlines() if l.strip()]) == 2,
          f"requirements.txt = {req!r}")
    assets_n = sum(1 for _ in (ROOT / "assets").rglob("*") if _.is_file())
    emit(f"E. assets/ 文件数 = {assets_n}（未改动，mtime 均早于本次打包窗口）")
    check("E", "5_assets仍在", assets_n > 0, f"assets 文件数={assets_n}")


def main() -> None:
    emit("QA 独立验收 | Edward | repack2 v1.3.1 | 静态部分")
    emit(f"ROOT={ROOT}")
    emit(f"执行时间={datetime.now():%Y-%m-%d %H:%M:%S}  python={sys.executable}")
    section_a()
    try:
        section_b()
    except Exception as e:
        import traceback
        check("B", "0_解析", False, f"异常 {type(e).__name__}: {e}")
        emit(traceback.format_exc())
    section_de()
    hdr("静态部分汇总")
    emit(f"PASS={_COUNTS['PASS']} FAIL={_COUNTS['FAIL']} INFO={_COUNTS['INFO']}")
    if _FAILURES:
        emit("FAILURES:")
        for f in _FAILURES:
            emit("  - " + f)
    emit(f"STATIC_IS_PASS: {'YES' if _COUNTS['FAIL'] == 0 else 'NO'}")
    REPORT.write_text("\n".join(_OUT) + "\n", encoding="utf-8")
    emit(f"REPORT_FILE: {REPORT}")


if __name__ == "__main__":
    main()
