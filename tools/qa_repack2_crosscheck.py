# -*- coding: utf-8 -*-
"""交叉印证：exe 内嵌 PYZ 是否就是本次(09:19~09:20)构建目录里的 PYZ-00.pyz；
并扫描构建 TOC / warn 里的 OCR 回滚证据。纯只读，不启动任何进程。"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

ROOT = Path("C:/Users/mrhua/Desktop/桌面宠物")
sys.path.insert(0, str(ROOT / "tools"))
from qa_repack2_edward_static import CArchive  # 复用独立解析器

exe = ROOT / "BuddyPet_分享包" / "BuddyPet.exe"
build = ROOT / "build" / "BuddyPet"
out = []


def emit(s=""):
    out.append(s)
    print(s)


def sha(b):
    return hashlib.sha256(b).hexdigest()


ca = CArchive(exe)
pyz = ca.extract("PYZ.pyz")
bp = build / "PYZ-00.pyz"
bpb = bp.read_bytes()
emit(f"exe 内嵌 PYZ.pyz      : len={len(pyz)} sha256={sha(pyz)}")
emit(f"build/BuddyPet/PYZ-00.pyz: len={len(bpb)} sha256={sha(bpb)} "
     f"mtime={bp.stat().st_mtime_ns}")
emit(f"[{'PASS' if pyz == bpb else 'FAIL'}] exe内PYZ与本次构建PYZ-00.pyz字节完全一致: {pyz == bpb}")
emit(f"[INFO] exe cookie: pylib={ca.pylib} pyvers={ca.pyvers} 顶层条目={len(ca.toc)}")

# 构建目录 TOC 扫描 OCR 库
for toc_name in ("Analysis-00.toc", "PKG-00.toc", "PYZ-00.toc", "EXE-00.toc"):
    p = build / toc_name
    if not p.exists():
        emit(f"[INFO] {toc_name}: MISSING")
        continue
    raw = p.read_bytes()
    txt = raw.decode("utf-8", "replace")
    hits = {lib: txt.lower().count(lib) for lib in ("onnxruntime", "rapidocr", "paddle", "torch")}
    emit(f"[{'PASS' if not any(hits.values()) else 'FAIL'}] {toc_name} OCR关键词命中={hits} "
         f"(size={len(raw)})")

warn = build / "warn-BuddyPet.txt"
if warn.exists():
    emit(f"[INFO] warn-BuddyPet.txt 前 600 字符: {warn.read_text(encoding='utf-8', errors='replace')[:600]!r}")

(ROOT / "tools" / "qa_repack2_crosscheck_report.txt").write_text("\n".join(out) + "\n", encoding="utf-8")
