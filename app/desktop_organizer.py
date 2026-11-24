"""文件整理：按扩展名分类（逻辑来自用户提供的 文件整理.py）。

分类表、文件夹名称与 ``os.listdir / os.path.splitext / os.mkdir / shutil.move``
的流程完全沿用用户代码；只做了三处工程化适配：
1. 硬编码路径 ``d:/Users/ASUS/Desktop/DesktopFiles`` 改为运行时当前用户的桌面目录；
2. 移动前先生成只读方案供用户确认，并拒绝覆盖同名文件；
3. 记录移动日志，支持撤销最近一次整理（用户原脚本没有撤销）。

系统快捷方式/程序/隐藏文件等仍保持原位，这是上一轮明确要求的安全约束。
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config

JOURNAL_NAME = "organizer_journal.json"
JOURNAL_VERSION = 1
JOURNAL_KEEP = 5

#: 分类表：顺序、后缀与文件夹名与用户提供的脚本保持一致。
CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("图片文件", (".jpg", ".jpeg", ".gif", ".png", ".bmp")),
    ("视频文件", (".avi", ".mp4", ".wmv", ".mov", ".flv")),
    ("音频文件", (".wav", ".mp3", ".mid", ".ape", ".flac", ".MP3")),
    ("PDF文件", (".pdf",)),
    ("Word文件", (".docx", ".doc")),
    ("Excel文件", (".xlsx", ".xls")),
    ("PPT文件", (".pptx", ".ppt")),
    ("Hex数据文件", (".hex",)),
    ("zip压缩文件", (".zip",)),
    ("python程序文件", (".py",)),
)
OTHER_FOLDER = "其他类型文件"

#: 这些文件不参与整理（快捷方式/程序/系统文件/临时文件），保持原位。
PROTECTED_EXTS: frozenset[str] = frozenset({
    ".exe", ".msi", ".msp", ".bat", ".cmd", ".ps1", ".reg", ".sys", ".dll",
    ".scr", ".com", ".hta", ".vbs", ".js", ".lnk", ".url", ".app", ".tmp",
    ".temp", ".part", ".crdownload", ".swp",
})
PROTECTED_NAMES: frozenset[str] = frozenset({
    "desktop.ini", "thumbs.db", ".ds_store", "ehthumbs.db", "iconcache.db",
    "autorun.inf", "system.ini", "boot.ini",
})

HELP_TEXT = (
    "支持三句话：\n"
    "• 扫描文件 —— 只列出文件，不移动\n"
    "• 整理文件 —— 按扩展名分到对应文件夹，先看方案再确认\n"
    "• 撤销上次整理 —— 把最近一次整理的文件移回原位\n"
)


# ---------------------------------------------------------------- 数据结构
@dataclass(frozen=True)
class FileEntry:
    path: Path
    name: str
    ext: str
    folder: str


@dataclass(frozen=True)
class MoveItem:
    src: Path
    dst: Path
    reason: str


@dataclass(frozen=True)
class SkipItem:
    path: Path
    reason: str


@dataclass
class PlanSpec:
    action: str = "organize"          # organize | scan | undo | help
    raw: str = ""


@dataclass
class Plan:
    spec: PlanSpec
    desktop: Path
    entries: list[FileEntry] = field(default_factory=list)
    moves: list[MoveItem] = field(default_factory=list)
    skipped: list[SkipItem] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (f"扫描 {len(self.entries)} 个文件，拟移动 {len(self.moves)} 个，"
                f"保持原位 {len(self.skipped)} 个")


@dataclass
class ExecuteResult:
    session_id: str
    desktop: Path
    moved: list[MoveItem] = field(default_factory=list)
    failed: list[SkipItem] = field(default_factory=list)
    skipped: list[SkipItem] = field(default_factory=list)

    @property
    def summary(self) -> str:
        text = f"整理完成：移动 {len(self.moved)} 个，保持原位 {len(self.skipped)} 个"
        if self.failed:
            text += f"，失败 {len(self.failed)} 个"
        return text


@dataclass
class UndoResult:
    session_id: str = ""
    restored: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    had_session: bool = False

    @property
    def summary(self) -> str:
        if not self.had_session:
            return "没有可撤销的整理记录"
        if not self.restored and not self.failed:
            return "上次整理已撤销过，不能重复撤销"
        text = f"已撤销：还原 {len(self.restored)} 个文件"
        if self.failed:
            text += f"，失败 {len(self.failed)} 个"
        return text


# ---------------------------------------------------------------- 命令解析
def parse_command(text: str) -> PlanSpec:
    """只识别三种动作；不再解析日期、关键词等自定义规则。"""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("先告诉我你想扫描、整理还是撤销吧")
    norm = raw.replace("，", " ").replace("。", " ")
    old_rules = ("修改日期", "日期", "时间", "年份", "按月", "按年", "包含",
                 "放到", "移到", "关键词", "之前", "之后")
    if any(word in norm for word in old_rules):
        raise ValueError("现在只按文件后缀名分类，不支持日期/关键词规则。请说「整理文件」")
    if "撤销" in norm or "撤回" in norm or "还原" in norm:
        return PlanSpec(action="undo", raw=raw)
    if "帮助" in norm or "怎么用" in norm:
        return PlanSpec(action="help", raw=raw)
    if "扫描" in norm or "查看" in norm or "列出" in norm or "看看" in norm:
        return PlanSpec(action="scan", raw=raw)
    if "整理" in norm or "归类" in norm or "分类" in norm:
        return PlanSpec(action="organize", raw=raw)
    raise ValueError("没听懂。我能做的是：扫描文件 / 整理文件 / 撤销上次整理")


# ---------------------------------------------------------------- 桌面定位
def desktop_dirs() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            shell32 = ctypes.windll.shell32
            for csidl in (0x0010, 0x0019):  # 当前用户桌面 / 公共桌面
                buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
                if shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
                    roots.append(Path(buf.value))
        except Exception:
            pass
    roots.append(Path.home() / "Desktop")
    seen: set[str] = set()
    result: list[Path] = []
    for root in roots:
        try:
            key = os.path.normcase(str(root.resolve()))
        except OSError:
            continue
        if key not in seen and root.is_dir() and not root.is_symlink():
            seen.add(key)
            result.append(root)
    return result


def _is_system_dir(path: Path) -> bool:
    """拒绝把 Windows / Program Files / AppData 等系统目录当作整理目标。"""
    if sys.platform != "win32":
        return False
    names = ("WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramData",
             "APPDATA", "LOCALAPPDATA")
    roots = []
    for name in names:
        value = os.environ.get(name)
        if value:
            try:
                roots.append(Path(value).resolve())
            except OSError:
                pass
    for root in roots:
        if path == root or root in path.parents:
            return True
    return False


def resolve_desktop(root: Path | None = None) -> Path:
    if root is not None:
        root = Path(root)
        if not root.is_dir() or root.is_symlink():
            raise ValueError("指定的桌面目录不存在或是链接，已取消整理")
        resolved = root.resolve()
        if _is_system_dir(resolved):
            raise ValueError("系统目录不支持整理，请换一个文件夹")
        return resolved
    dirs = desktop_dirs()
    if not dirs:
        raise ValueError("没有找到桌面目录，已取消整理")
    return dirs[0]


def target_folder(item: str) -> str:
    """用户脚本里的分类逻辑：取后缀名，逐项匹配文件夹，其余进「其他类型文件」。"""
    extension = os.path.splitext(item)[1].lower()
    for folder, extensions in CATEGORY_RULES:
        if extension in extensions:
            return folder
    return OTHER_FOLDER


def _protect_reason(path: Path, name: str) -> str | None:
    """系统关键文件保持原位的理由。"""
    if name.lower() in PROTECTED_NAMES or name.startswith("~$"):
        return "系统/临时文件，保持原位"
    if path.is_symlink():
        return "快捷方式/符号链接，保持原位"
    if os.path.splitext(name)[1].lower() in PROTECTED_EXTS:
        return "程序或快捷方式，保持原位"
    try:
        flags = getattr(path.stat(), "st_file_attributes", 0) or 0
        if flags & (getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)
                    | getattr(stat, "FILE_ATTRIBUTE_SYSTEM", 0x4)):
            return "隐藏/系统属性文件，保持原位"
    except OSError:
        return "无法读取文件属性，保持原位"
    return None


def scan_desktop(root: Path | None = None) -> tuple[Path, list[FileEntry]]:
    """只读扫描目录顶层项目（对应脚本的 os.listdir）。"""
    desktop = resolve_desktop(root)
    entries: list[FileEntry] = []
    for name in sorted(os.listdir(desktop), key=str.lower):
        path = desktop / name
        if path.is_dir():
            continue  # 用户脚本用 os.path.isdir() 跳过文件夹
        entries.append(FileEntry(
            path=path,
            name=name,
            ext=os.path.splitext(name)[1].lower(),
            folder=target_folder(name),
        ))
    return desktop, entries


# ---------------------------------------------------------------- 生成方案（只读）
def build_plan(spec: PlanSpec, root: Path | None = None) -> Plan:
    if spec.action in {"scan", "undo", "help"}:
        raise ValueError(f"动作 {spec.action} 不需要生成整理方案")
    desktop, entries = scan_desktop(root)
    plan = Plan(spec=spec, desktop=desktop, entries=entries)
    desktop_real = desktop.resolve()
    reserved: set[str] = set()

    for entry in entries:
        src = entry.path
        try:
            if src.resolve(strict=True).parent != desktop_real:
                plan.skipped.append(SkipItem(src, "文件位置已变化，保持原位"))
                continue
        except OSError:
            plan.skipped.append(SkipItem(src, "无法读取文件，保持原位"))
            continue

        why = _protect_reason(src, entry.name)
        if why:
            plan.skipped.append(SkipItem(src, why))
            continue

        folder = entry.folder
        dst = desktop / folder / entry.name
        target_dir = desktop / folder
        existing = ({os.path.normcase(p) for p in os.listdir(target_dir)}
                    if target_dir.is_dir() else set())
        key = os.path.normcase(entry.name)
        if key in existing or (folder, key) in reserved:
            plan.skipped.append(SkipItem(src, f"「{folder}」中已有同名文件，不会覆盖，保持原位"))
            continue
        reserved.add((folder, key))
        plan.moves.append(MoveItem(src, dst, folder))
    return plan


# ---------------------------------------------------------------- 执行 / 撤销
def _journal_path() -> Path:
    return config.config_dir() / JOURNAL_NAME


def _load_journal() -> dict[str, Any]:
    try:
        data = json.loads(_journal_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("version") == JOURNAL_VERSION:
            data.setdefault("last_id", "")
            data.setdefault("sessions", [])
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {"version": JOURNAL_VERSION, "last_id": "", "sessions": []}


def _save_journal(data: dict[str, Any]) -> Path:
    path = _journal_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def execute_plan(plan: Plan) -> ExecuteResult:
    """执行已确认的方案，逐项复核后才调用 shutil.move。"""
    if not plan.moves and not plan.skipped:
        raise ValueError("方案为空，无需执行")
    desktop = plan.desktop.resolve()
    sid = uuid.uuid4().hex[:12]
    result = ExecuteResult(session_id=sid, desktop=desktop, skipped=list(plan.skipped))

    for item in plan.moves:
        src, dst = item.src, item.dst
        try:
            if not src.is_file() or src.resolve(strict=True).parent != desktop:
                raise OSError("文件在整理前已变化")
            why = _protect_reason(src, src.name)
            if why:
                raise OSError(why)
            target_dir = desktop / item.reason
            if target_dir.is_symlink() or (target_dir.exists() and not target_dir.is_dir()):
                raise OSError("目标文件夹不合法")
            dst = target_dir / src.name
            if dst.exists() or dst.is_symlink():
                raise OSError("目标位置已有同名文件")
            if not os.path.exists(target_dir):
                os.mkdir(target_dir)          # 与用户脚本一致
            shutil.move(str(src), str(target_dir))
            result.moved.append(MoveItem(src, dst, item.reason))
        except (OSError, shutil.Error) as exc:
            result.failed.append(SkipItem(src, f"移动失败：{exc}"))

    journal = _load_journal()
    journal["last_id"] = sid
    journal["sessions"].append({
        "id": sid,
        "time": datetime.now().isoformat(timespec="seconds"),
        "desktop": str(desktop),
        "strategy": "extension",
        "raw": plan.spec.raw,
        "moves": [{"src": str(i.src), "dst": str(i.dst), "reason": i.reason} for i in result.moved],
        "moved_count": len(result.moved),
        "skipped_count": len(result.skipped),
        "failed_count": len(result.failed),
        "status": "partial" if result.failed else "done",
        "undone": False,
    })
    journal["sessions"] = journal["sessions"][-JOURNAL_KEEP:]
    _save_journal(journal)
    return result


def undo_last(root: Path | None = None) -> UndoResult:
    """撤销最近一次整理；同一批只能撤销一次。"""
    journal = _load_journal()
    sid = journal.get("last_id", "")
    session = next((s for s in journal.get("sessions", []) if s.get("id") == sid), None)
    if session is None or session.get("undone"):
        return UndoResult(session_id=sid, had_session=bool(session))

    desktop = Path(root).resolve() if root is not None else Path(session.get("desktop", ""))
    result = UndoResult(session_id=sid, had_session=True)
    for move in reversed(session.get("moves", [])):
        try:
            src, dst = Path(move["src"]), Path(move["dst"])
            if src.resolve().parent != desktop.resolve() or dst.resolve().parent.parent != desktop.resolve():
                raise OSError("日志中的路径不在本次桌面范围内")
            if not dst.is_file() or dst.is_symlink():
                raise OSError("目标文件已不存在")
            if src.exists() or src.is_symlink():
                raise OSError("原位已有文件，不覆盖")
            shutil.move(str(dst), str(src.parent))
            result.restored.append({"src": str(src), "dst": str(dst)})
        except (OSError, ValueError, KeyError, shutil.Error) as exc:
            result.failed.append({"path": str(move.get("dst", "")), "reason": f"还原失败：{exc}"})

    session["undone"] = True
    journal["sessions"] = [s for s in journal.get("sessions", []) if s.get("id") != sid]
    journal["sessions"].append(session)
    journal["sessions"] = journal["sessions"][-JOURNAL_KEEP:]
    _save_journal(journal)
    return result


def last_session() -> dict[str, Any] | None:
    journal = _load_journal()
    sid = journal.get("last_id", "")
    return next((s for s in journal.get("sessions", []) if s.get("id") == sid), None)
