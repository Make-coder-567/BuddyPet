"""文件整理 QA：验证「用户代码分类逻辑 + 安全外壳」，全部在临时沙箱进行。

运行： python tools/qa_organizer.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config, desktop_organizer as org  # noqa: E402

PASS = 0
FAIL = 0


def check(cond: bool, name: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name}")


def snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text(encoding="utf-8", errors="ignore")
            for p in root.rglob("*") if p.is_file()}


def main() -> int:
    global PASS, FAIL
    tmp = Path(tempfile.mkdtemp(prefix="buddy_organizer_qa_"))
    desktop = tmp / "Desktop"
    desktop.mkdir()
    (tmp / "appdata").mkdir()
    old_dir = config.config_dir
    config.config_dir = lambda: tmp / "appdata"
    old_resolve = org.resolve_desktop
    org.resolve_desktop = lambda root=None: desktop

    try:
        print("\n--- 命令识别 ---")
        for text, action in (("扫描文件", "scan"), ("整理文件", "organize"),
                             ("撤销上次整理", "undo"), ("帮助", "help")):
            check(org.parse_command(text).action == action, f"识别「{text}」")
        for bad in ("", "按修改日期整理桌面", "把 PDF 放到 文档", "今天天气不错"):
            try:
                org.parse_command(bad)
                check(False, f"旧规则命令应被拒绝：{bad!r}")
            except ValueError:
                check(True, f"已移除旧规则解析：{bad!r}")

        if sys.platform == "win32":
            windir = os.environ.get("WINDIR", "C:/Windows")
            check(org._is_system_dir(Path(windir)), "系统目录被拒绝整理")

        print("\n--- 用户脚本分类表 ---")
        expect = {
            "a.jpg": "图片文件", "v.mp4": "视频文件", "s.mp3": "音频文件",
            "r.pdf": "PDF文件", "w.docx": "Word文件", "x.xlsx": "Excel文件",
            "p.pptx": "PPT文件", "h.hex": "Hex数据文件", "z.zip": "zip压缩文件",
            "c.py": "python程序文件", "q.xyz": "其他类型文件",
        }
        for name, folder in expect.items():
            check(org.target_folder(name) == folder, f"{name} → {folder}")

        print("\n--- 沙箱与只读方案 ---")
        for name in expect:
            (desktop / name).write_text(name, encoding="utf-8")
        (desktop / "shortcut.lnk").write_text("lnk", encoding="utf-8")
        (desktop / "desktop.ini").write_text("ini", encoding="utf-8")
        (desktop / "子文件夹").mkdir()
        (desktop / "子文件夹" / "keep.txt").write_text("keep", encoding="utf-8")
        (desktop / "图片文件").mkdir()
        (desktop / "图片文件" / "a.jpg").write_text("原有同名文件", encoding="utf-8")
        before = snapshot(desktop)

        plan = org.build_plan(org.parse_command("整理文件"))
        moves = {m.src.name: m.reason for m in plan.moves}
        skips = {s.path.name: s.reason for s in plan.skipped}
        for name in expect:
            if name == "a.jpg":
                continue
            check(moves.get(name) == expect[name], f"方案中 {name} → {moves.get(name)}")
        check("a.jpg" in skips and "同名" in skips["a.jpg"], "同名冲突不覆盖")
        check("shortcut.lnk" in skips and "desktop.ini" in skips, "快捷方式/系统文件保持原位")
        check("子文件夹" not in snapshot(desktop), "子文件夹未被读取为文件")
        check(snapshot(desktop) == before, "生成方案完全只读")

        print("\n--- 执行与摘要 ---")
        result = org.execute_plan(plan)
        check(len(result.moved) == 10, f"移动 {len(result.moved)} 个文件")
        for name, folder in expect.items():
            if name == "a.jpg":
                check((desktop / folder / name).read_text(encoding="utf-8") == "原有同名文件",
                      "原有同名文件内容未被覆盖")
            else:
                check((desktop / folder / name).is_file(), f"{name} 已移入 {folder}")
        check((desktop / "shortcut.lnk").is_file() and (desktop / "desktop.ini").is_file(),
              "被保护文件仍在原位")
        check(sorted(before.values()) == sorted(snapshot(desktop).values()), "没有任何文件被删除")
        check("整理完成" in result.summary and "保持原位" in result.summary, "返回摘要")

        print("\n--- 撤销 ---")
        undo = org.undo_last()
        check(len(undo.restored) == len(result.moved), f"还原 {len(undo.restored)} 个文件")
        check(snapshot(desktop) == before, "撤销后与整理前逐字节一致")
        check("已撤销过" in org.undo_last().summary, "不能重复撤销")

        print("\n--- 撤销时不覆盖 ---")
        spec = org.PlanSpec(action="organize", raw="QA")
        plan2 = org.build_plan(spec)
        plan2.moves = [m for m in plan2.moves if m.src.name == "c.py"]
        res2 = org.execute_plan(plan2)
        check(len(res2.moved) == 1, "第二批整理成功")
        (desktop / "c.py").write_text("用户新文件", encoding="utf-8")
        undo2 = org.undo_last()
        check(len(undo2.failed) == 1 and "不覆盖" in undo2.failed[0]["reason"], "撤销拒绝覆盖")
        check((desktop / "c.py").read_text(encoding="utf-8") == "用户新文件", "新文件未被破坏")

        print(f"\n结果：PASS {PASS} / FAIL {FAIL}")
        return 1 if FAIL else 0
    finally:
        config.config_dir = old_dir
        org.resolve_desktop = old_resolve
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
