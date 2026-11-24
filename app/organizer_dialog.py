"""文件整理卡片：三个按钮选择 → 方案预览 → 用户确认 → 结果摘要 / 撤销。

目录默认是当前用户桌面，也可由右上角「选择路径…」改到其它文件夹。
安全约定（界面层也必须守住）：
- 「扫描文件」和「开始整理」只调用只读函数，不碰文件；
- 「确认移动」是唯一会调用 ``execute_plan`` 的入口，且必须已有展示过的方案；
- 「撤销上次整理」同样先展示还原清单，再让用户确认；
- 执行后立刻禁用确认按钮，避免重复点击造成二次整理。
"""
from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import desktop_organizer as org
from . import icons, theme
from .settings_dialog import CardDialog

_LOCAL_QSS = """
QFrame#TopAccent {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #FF7A3D, stop:0.55 rgba(255,122,61,0.45), stop:1 rgba(255,122,61,0));
    border: none;
}
QLabel#Kicker {
    color: #FFB27A; font-size: 8pt; font-weight: 600; letter-spacing: 2px;
}
QFrame#PathChip {
    background: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,255,255,0.09);
    border-radius: 8px;
}
QFrame#StatusPanel {
    background: rgba(255,122,61,0.07);
    border: 1px solid rgba(255,122,61,0.18);
    border-radius: 9px;
}
QPushButton#PrimaryAction {
    background-color: #FF7A3D; border: none; border-radius: 10px;
    padding: 10px 6px; color: #201006; font-weight: 600; font-size: 9.5pt;
}
QPushButton#PrimaryAction:hover { background-color: #FF8C56; }
QPushButton#PrimaryAction:pressed { background-color: #E85E22; }
QPushButton#Action {
    background-color: rgba(255,255,255,0.05);
    border: 1px solid rgba(255,122,61,0.38);
    border-radius: 10px; padding: 10px 6px; color: #F3F4F6; font-size: 9.5pt;
}
QPushButton#Action:hover { background-color: rgba(255,122,61,0.14); border-color: rgba(255,122,61,0.85); }
QPushButton#Action:pressed { background-color: rgba(255,122,61,0.25); }
QPushButton#GhostAction {
    background-color: transparent;
    border: 1px solid rgba(255,255,255,0.11);
    border-radius: 10px; padding: 10px 6px; color: #C9CDD6; font-size: 9.5pt;
}
QPushButton#GhostAction:hover { background-color: rgba(255,255,255,0.06); color: #F3F4F6; }
QTableWidget {
    background-color: rgba(255,255,255,0.035);
    alternate-background-color: rgba(255,255,255,0.02);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 10px;
    gridline-color: rgba(255,255,255,0.05);
    color: #F3F4F6;
    font-family: "Microsoft YaHei UI";
    font-size: 8.8pt;
}
QHeaderView::section {
    background-color: rgba(255,122,61,0.12);
    color: #FFB27A;
    border: none;
    padding: 7px 8px;
    font-family: "Microsoft YaHei UI";
    font-size: 8.8pt;
    font-weight: 600;
}
QTableWidget::item { padding: 5px 7px; border-bottom: 1px solid rgba(255,255,255,0.04); }
QTableWidget::item:alternate { background-color: rgba(255,255,255,0.02); }
QTableWidget::item:hover { background-color: rgba(255,122,61,0.08); }
QTableWidget::item:selected { background-color: rgba(255,122,61,0.20); }
QLabel#EmptyTitle { color: #C9CDD6; font-size: 10pt; font-weight: 600; }
QLabel#EmptyHint { color: #9CA1AB; font-size: 8.8pt; }
"""

_COLS = ("状态", "文件", "目标位置", "说明 / 原因")


class OrganizerDialog(CardDialog):
    """文件整理卡片。

    Signals:
        organize_finished: 整理完成后的摘要文字（供气泡播报）。
        undo_finished: 撤销完成后的摘要文字。
    """

    organize_finished = pyqtSignal(str)
    undo_finished = pyqtSignal(str)

    def __init__(self, parent=None, initial_text: str = ""):
        super().__init__(parent, width=760)
        self.setStyleSheet(theme.DIALOG_QSS + _LOCAL_QSS)
        self._plan: org.Plan | None = None
        self._spec: org.PlanSpec | None = None
        self._pending_undo = False
        self.root: Path | None = None        # None = 当前用户桌面（默认）

        # ---------------- 头部：图标 + 标题 + 当前目录
        head = QHBoxLayout()
        head.setSpacing(10)
        icon_label = QLabel()
        icon_label.setPixmap(icons.icon_folder("#FF7A3D").pixmap(26, 26))
        head.addWidget(icon_label)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        kicker = QLabel("BUDDY · 文件工具")
        kicker.setObjectName("Kicker")
        title = QLabel("文件整理")
        title.setObjectName("Title")
        titles.addWidget(kicker)
        titles.addWidget(title)
        head.addLayout(titles)
        head.addStretch(1)

        chip = QFrame()
        chip.setObjectName("PathChip")
        chip_layout = QHBoxLayout(chip)
        chip_layout.setContentsMargins(9, 5, 9, 5)
        chip_layout.setSpacing(6)
        cap = QLabel("当前目录")
        cap.setObjectName("Sub")
        self.path_label = QLabel("桌面（默认）")
        self.path_label.setObjectName("Sub")
        chip_layout.addWidget(cap)
        chip_layout.addWidget(self.path_label)
        head.addWidget(chip)

        self.btn_path = QPushButton("选择路径…")
        self.btn_path.clicked.connect(self.choose_path)
        head.addWidget(self.btn_path)
        self.body.addLayout(head)

        accent = QFrame()
        accent.setObjectName("TopAccent")
        accent.setFixedHeight(2)
        self.body.addWidget(accent)

        sub = QLabel("Buddy 先给出方案，你确认后才会移动文件；不会删除，也不会覆盖同名文件。")
        sub.setObjectName("Sub")
        sub.setWordWrap(True)
        self.body.addWidget(sub)

        # ---------------- 操作区
        sec = QLabel("01   选择操作")
        sec.setObjectName("Section")
        self.body.addWidget(sec)

        row = QHBoxLayout()
        row.setSpacing(9)
        for text, name, slot in (
            ("扫描文件", "Action", self.scan_desktop),
            ("开始整理", "PrimaryAction", self.organize_desktop),
            ("撤销上次整理", "GhostAction", self.prepare_undo),
        ):
            btn = QPushButton(text)
            btn.setObjectName(name)
            btn.clicked.connect(slot)
            row.addWidget(btn, 1)
        self.body.addLayout(row)

        # ---------------- 方案区（空状态 + 表格）
        sec_plan = QLabel("02   方案预览（确认前不会移动任何文件）")
        sec_plan.setObjectName("Section")
        self.body.addWidget(sec_plan)

        self.table = QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels(_COLS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)

        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setAlignment(Qt.AlignCenter)
        empty_layout.setSpacing(6)
        empty_title = QLabel("还没有方案")
        empty_title.setObjectName("EmptyTitle")
        empty_title.setAlignment(Qt.AlignCenter)
        empty_hint = QLabel("点「扫描文件」看看目录内容，或点「开始整理」生成可确认的方案")
        empty_hint.setObjectName("EmptyHint")
        empty_hint.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_hint)

        self.pages = QStackedWidget()
        self.pages.setMinimumHeight(210)
        self.pages.addWidget(empty)
        self.pages.addWidget(self.table)
        self.pages.setCurrentIndex(0)
        self.body.addWidget(self.pages)

        # ---------------- 状态条
        status_panel = QFrame()
        status_panel.setObjectName("StatusPanel")
        status_layout = QHBoxLayout(status_panel)
        status_layout.setContentsMargins(10, 7, 10, 7)
        self.status = QLabel("点上面的按钮，先看看 Buddy 打算怎么做。")
        self.status.setObjectName("Sub")
        self.status.setWordWrap(True)
        status_layout.addWidget(self.status)
        self.body.addWidget(status_panel)

        # ---------------- 底部操作
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        self.btn_close = QPushButton("关闭")
        self.btn_close.setObjectName("Ghost")
        self.btn_close.clicked.connect(self.reject)
        actions.addWidget(self.btn_close)
        self.btn_confirm = QPushButton("确认移动")
        self.btn_confirm.setObjectName("Primary")
        self.btn_confirm.setEnabled(False)
        self.btn_confirm.clicked.connect(self.confirm)
        actions.addWidget(self.btn_confirm)
        self.body.addLayout(actions)

        # 兼容外部命令通道：organize:undo / organize:scan / organize:<其它>
        if initial_text:
            if "撤销" in initial_text:
                self.prepare_undo()
            elif "扫描" in initial_text:
                self.scan_desktop()
            else:
                self.organize_desktop()

    # ------------------------------------------------------------ 表格辅助
    def _clear_table(self) -> None:
        self.table.setRowCount(0)
        self.pages.setCurrentIndex(0)

    def _add_row(self, cells: tuple[str, str, str, str], warn: bool = False) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            item.setToolTip(text)
            if col == 0 and warn:
                item.setForeground(theme.Q_ORANGE)
            self.table.setItem(row, col, item)
        self.pages.setCurrentIndex(1)

    def _set_status(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {'#FFB27A' if error else theme.MUTED};")

    def _reset_state(self) -> None:
        self._plan = None
        self._spec = None
        self._pending_undo = False
        self.btn_confirm.setEnabled(False)
        self.btn_confirm.setText("确认移动")

    # ------------------------------------------------------------ 选择路径
    def choose_path(self) -> None:
        """右上角按钮：选一个要整理的文件夹；不选就用当前用户桌面。"""
        start = str(self.root) if self.root is not None else str(org.resolve_desktop())
        folder = QFileDialog.getExistingDirectory(self, "选择要整理的文件夹", start)
        if not folder:
            return
        try:
            root = org.resolve_desktop(Path(folder))   # 含系统目录保护
        except ValueError as exc:
            self._set_status(str(exc), True)
            return
        self.root = root
        self.path_label.setText(root.name or str(root))
        self.path_label.setToolTip(str(root))
        self._reset_state()
        self._clear_table()
        self._set_status(f"当前目录：{root}。点上面的按钮生成方案。")

    @staticmethod
    def _display_path(path: Path, desktop: Path) -> str:
        try:
            return str(path.relative_to(desktop))
        except ValueError:
            return str(path)

    # ------------------------------------------------------------ 扫描（只读）
    def scan_desktop(self) -> None:
        self._reset_state()
        self._clear_table()
        try:
            desktop, entries = org.scan_desktop(self.root)
        except ValueError as exc:
            self._set_status(str(exc), True)
            return
        for entry in entries:
            self._add_row(("已发现", entry.name, entry.folder, "仅扫描，未移动任何文件"))
        self._set_status(f"目录：{desktop}　共扫描到 {len(entries)} 个文件（子文件夹不纳入整理）")

    # ------------------------------------------------------------ 整理方案（只读）
    def organize_desktop(self) -> None:
        self._reset_state()
        self._clear_table()
        spec = org.parse_command("整理文件")
        try:
            plan = org.build_plan(spec, self.root)   # 只读
        except ValueError as exc:
            self._set_status(str(exc), True)
            return
        self._plan = plan
        self._spec = spec
        for move in plan.moves:
            target = self._display_path(move.dst, plan.desktop)
            self._add_row(("将移动", move.src.name, target, move.reason))
        for skip in plan.skipped:
            self._add_row(("保持原位", skip.path.name, "—", skip.reason), warn=True)
        self._set_status(f"{plan.summary}　规则：按文件后缀名分类。确认后才会真正移动。")
        if plan.moves:
            self.btn_confirm.setEnabled(True)
            self.btn_confirm.setText(f"确认移动 {len(plan.moves)} 个文件")
        else:
            self._set_status(plan.summary + "。没有可移动的文件，目录保持原样。")

    # ------------------------------------------------------------ 撤销预览（只读）
    def prepare_undo(self) -> None:
        session = org.last_session()
        if session is None:
            self._set_status("没有可撤销的整理记录。", True)
            return
        if session.get("undone"):
            self._set_status("上次整理已经撤销过了，不能重复撤销。", True)
            return
        self._reset_state()
        self._clear_table()
        self._pending_undo = True
        for move in session.get("moves", []):
            self._add_row(("将还原", Path(move.get("dst", "")).name,
                           move.get("src", ""), "撤销本次整理，移回原位置"))
        self._set_status(
            f"准备撤销 {session.get('id', '')} 的整理：共 {len(session.get('moves', []))} 个文件。确认后才还原。"
        )
        if session.get("moves"):
            self.btn_confirm.setEnabled(True)
            self.btn_confirm.setText("确认撤销")

    # ------------------------------------------------------------ 执行（唯一写入口）
    def confirm(self) -> None:
        if self._pending_undo:
            result = org.undo_last()
            self._clear_table()
            for item in result.restored:
                self._add_row(("已还原", Path(item["src"]).name, item["src"], "已移回原位"))
            for item in result.failed:
                self._add_row(("还原失败", Path(item["path"]).name, "—", item["reason"]), warn=True)
            self._set_status(result.summary)
            self.undo_finished.emit(result.summary)
            self._reset_state()
            return

        if self._plan is None or not self._plan.moves:
            return
        plan = self._plan
        self.btn_confirm.setEnabled(False)  # 先锁住，杜绝双击重复执行
        result = org.execute_plan(plan)
        self._clear_table()
        for move in result.moved:
            target = self._display_path(move.dst, plan.desktop)
            self._add_row(("已移动", move.src.name, target, move.reason))
        for fail in result.failed:
            self._add_row(("移动失败", fail.path.name, "—", fail.reason), warn=True)
        for skip in result.skipped:
            self._add_row(("保持原位", skip.path.name, "—", skip.reason), warn=True)
        self._set_status(result.summary)
        self.organize_finished.emit(result.summary)
        self._plan = None
        self._spec = None
        self.btn_confirm.setText("确认移动")
