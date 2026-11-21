"""审校页：大表格（模型/视图）、筛选、详情编辑、批量操作。"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
    QSplitter, QTableView, QVBoxLayout, QWidget,
)

from .. import service
from ..review import ORIGIN_LABELS
from ..store import ReviewRow

HANDLER_LABELS = {
    "lang_json": "语言文件", "ftbquests": "FTB 任务书", "patchouli": "Patchouli 手册", "betterquesting": "BQ 任务书",
    "kubejs": "KubeJS 脚本", "crafttweaker": "CraftTweaker", "config_text": "配置文本",
}
STATUS_LABELS = {"pending": "待翻译", "done": "已完成", "failed": "失败", "confirmed": "已确认", "skipped": "无需翻译"}
STATUS_FILTERS = ["需审校", "待翻译", "失败", "已完成", "已确认", "全部"]
COLUMNS = ["类别", "文件", "原文", "译文", "来源", "状态"]
TEXT_COL = 3
EDITED_BRUSH = QBrush(QColor(255, 244, 196))


def short_source(source: str) -> str:
    tail = source.replace("!", "/").rstrip("/")
    parts = [p for p in tail.split("/") if p]
    if source.startswith("jar:assets/") and len(parts) >= 2:
        return parts[1]  # jar:assets/<命名空间>/lang/... → 命名空间
    return parts[-1] if parts else source


def needs_review(row: ReviewRow) -> bool:
    return (row.status in ("pending", "failed") or (row.status == "done" and row.origin in ("llm", "memory"))
            or bool(row.note))


class ReviewTableModel(QAbstractTableModel):
    edited = Signal(int, str)       # 条目 ID、新译文（已通过格式码校验）
    rejected = Signal(int, str)     # 条目 ID、拒绝原因

    def __init__(self, rows: list[ReviewRow] | None = None, parent=None) -> None:
        super().__init__(parent)
        self.rows: list[ReviewRow] = list(rows or [])
        self.edited_ids: set[int] = set()

    def set_rows(self, rows: list[ReviewRow]) -> None:
        self.beginResetModel()
        self.rows = list(rows)
        self.edited_ids.clear()
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def _values(self, row: ReviewRow) -> list[str]:
        return [
            HANDLER_LABELS.get(row.handler, row.handler), short_source(row.source),
            row.original, row.text or "", ORIGIN_LABELS.get(row.origin or "", row.origin or ""),
            STATUS_LABELS.get(row.status, row.status),
        ]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            value = self._values(row)[col]
            return value.replace("\n", " ⏎ ") if col in (2, 3) else value
        if role == Qt.EditRole:
            return self._values(row)[col]
        if role == Qt.ToolTipRole and col in (2, 3):
            return self._values(row)[col] or None
        if role == Qt.BackgroundRole and row.id in self.edited_ids:
            return EDITED_BRUSH
        return None

    def flags(self, index):
        base = super().flags(index)
        return base | Qt.ItemIsEditable if index.column() == TEXT_COL else base

    def setData(self, index, value, role=Qt.EditRole) -> bool:
        if role != Qt.EditRole or index.column() != TEXT_COL:
            return False
        row = self.rows[index.row()]
        text = str(value)
        if text == (row.text or ""):
            return False
        if not text.strip():
            self.rejected.emit(row.id, "译文不能为空")
            return False
        problem = service.token_problem(row.original, text)
        if problem:
            self.rejected.emit(row.id, problem)
            return False
        row.text, row.origin, row.status, row.note = text, "human", "confirmed", None
        self.edited_ids.add(row.id)
        self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), len(COLUMNS) - 1))
        self.edited.emit(row.id, text)
        return True

    def mark_confirmed(self, ids: set[int]) -> None:
        for r, row in enumerate(self.rows):
            if row.id in ids and row.text:
                row.origin, row.status, row.note = "human", "confirmed", None
                self.edited_ids.add(row.id)
                self.dataChanged.emit(self.index(r, 0), self.index(r, len(COLUMNS) - 1))


class ReviewFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.handler: str | None = None
        self.origin: str | None = None
        self.status = "需审校"
        self.text = ""

    def set_filters(self, handler: str | None = None, origin: str | None = None,
                    status: str = "需审校", text: str = "") -> None:
        if hasattr(self, "beginFilterChange"):  # Qt 6.9+
            self.beginFilterChange()
            self.handler, self.origin, self.status, self.text = handler, origin, status, text.casefold().strip()
            self.endFilterChange()
        else:
            self.handler, self.origin, self.status, self.text = handler, origin, status, text.casefold().strip()
            self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent) -> bool:
        row: ReviewRow = self.sourceModel().rows[source_row]
        if self.handler and row.handler != self.handler:
            return False
        if self.origin and (row.origin or "") != self.origin:
            return False
        status = self.status
        if status == "需审校" and not needs_review(row):
            return False
        if status == "待翻译" and row.status != "pending":
            return False
        if status == "失败" and row.status != "failed":
            return False
        if status == "已完成" and row.status != "done":
            return False
        if status == "已确认" and row.status != "confirmed":
            return False
        if self.text and self.text not in row.original.casefold() and self.text not in (row.text or "").casefold():
            return False
        return True


class ReviewPage(QWidget):
    apply_requested = Signal()
    retranslate_requested = Signal(list)   # 条目 ID 列表
    message = Signal(str, str)             # 文本、级别（info / warn / error）

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.root: Path | None = None
        self.unapplied = 0
        self.model = ReviewTableModel()
        self.proxy = ReviewFilterProxy()
        self.proxy.setSourceModel(self.model)
        self.model.edited.connect(self._on_edited)
        self.model.rejected.connect(lambda _id, reason: self._set_check(reason, ok=False))

        self.handler_box = QComboBox()
        self.origin_box = QComboBox()
        self.status_box = QComboBox()
        self.status_box.addItems(STATUS_FILTERS)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索原文或译文")
        self.count_label = QLabel()
        for widget in (self.handler_box, self.origin_box, self.status_box):
            widget.currentIndexChanged.connect(self._apply_filters)
        self._search_timer = QTimer(self)  # 防抖：停止输入 300 毫秒后再筛选
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(300)
        self._search_timer.timeout.connect(self._apply_filters)
        self.search.textChanged.connect(self._search_timer.start)
        bar = QHBoxLayout()
        for label, widget in (("类别", self.handler_box), ("来源", self.origin_box), ("状态", self.status_box)):
            bar.addWidget(QLabel(label))
            bar.addWidget(widget)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.count_label)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        for col, width in enumerate((90, 120, 360, 360, 70, 70)):
            self.table.setColumnWidth(col, width)
        header.setStretchLastSection(False)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.selectionModel().currentRowChanged.connect(self._show_detail)

        self.original_view = QPlainTextEdit()
        self.original_view.setReadOnly(True)
        self.text_edit = QPlainTextEdit()
        self.text_edit.textChanged.connect(self._live_check)
        self.check_label = QLabel()
        self.save_button = QPushButton("保存此条（标记为人工确认）")
        self.save_button.clicked.connect(self.save_current)
        detail_buttons = QHBoxLayout()
        detail_buttons.addWidget(self.check_label, 1)
        detail_buttons.addWidget(self.save_button)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.addWidget(QLabel("原文"))
        detail_layout.addWidget(self.original_view)
        detail_layout.addWidget(QLabel("译文"))
        detail_layout.addWidget(self.text_edit)
        detail_layout.addLayout(detail_buttons)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        self.confirm_button = QPushButton("批量确认选中")
        self.confirm_button.clicked.connect(self.confirm_selected)
        self.retranslate_button = QPushButton("重新机翻选中")
        self.retranslate_button.clicked.connect(self._retranslate_selected)
        self.unapplied_label = QLabel()
        self.apply_button = QPushButton("应用到游戏")
        self.apply_button.clicked.connect(self.apply_requested)
        bottom = QHBoxLayout()
        bottom.addWidget(self.confirm_button)
        bottom.addWidget(self.retranslate_button)
        bottom.addStretch(1)
        bottom.addWidget(self.unapplied_label)
        bottom.addWidget(self.apply_button)

        layout = QVBoxLayout(self)
        layout.addLayout(bar)
        layout.addWidget(splitter, 1)
        layout.addLayout(bottom)
        self._update_unapplied()
        self._show_detail(QModelIndex(), QModelIndex())

    # ---- 数据 ----
    def set_instance(self, root: Path | None) -> None:
        self.root = root
        self.unapplied = 0
        self.reload()

    def reload(self) -> None:
        rows: list[ReviewRow] = []
        if self.root is not None and service.summary(self.root).scanned:
            rows = service.review_rows(self.root)
        self.model.set_rows(rows)
        self._fill_filter_boxes()
        self._apply_filters()
        self._update_unapplied()

    def _fill_filter_boxes(self) -> None:
        present = {r.handler for r in self.model.rows}
        handlers = [h for h in HANDLER_LABELS if h in present] + sorted(present - set(HANDLER_LABELS))
        origins = sorted({r.origin for r in self.model.rows if r.origin})
        for box, values, labels in ((self.handler_box, handlers, HANDLER_LABELS), (self.origin_box, origins, ORIGIN_LABELS)):
            box.blockSignals(True)
            current = box.currentData()
            box.clear()
            box.addItem("全部", None)
            for value in values:
                box.addItem(labels.get(value, value), value)
            index = box.findData(current)
            box.setCurrentIndex(index if index >= 0 else 0)
            box.blockSignals(False)

    def _apply_filters(self) -> None:
        self.proxy.set_filters(self.handler_box.currentData(), self.origin_box.currentData(),
                               self.status_box.currentText(), self.search.text())
        self.count_label.setText(f"{self.proxy.rowCount()} / {self.model.rowCount()} 条")

    # ---- 详情区 ----
    def _current_source_row(self) -> int | None:
        index = self.table.currentIndex()
        if not index.isValid():
            return None
        return self.proxy.mapToSource(index).row()

    def _show_detail(self, current, _previous) -> None:
        source_row = self.proxy.mapToSource(current).row() if current.isValid() else None
        enabled = source_row is not None
        self.text_edit.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        if source_row is None:
            self.original_view.setPlainText("")
            self.text_edit.setPlainText("")
            self.check_label.setText("在表格中选择一条查看详情")
            return
        row = self.model.rows[source_row]
        self.original_view.setPlainText(row.original)
        self.text_edit.blockSignals(True)
        self.text_edit.setPlainText(row.text or "")
        self.text_edit.blockSignals(False)
        self._live_check()

    def _set_check(self, text: str, ok: bool) -> None:
        self.check_label.setText(("✓ " if ok else "✗ ") + text)
        self.check_label.setStyleSheet("color: #2e7d32;" if ok else "color: #c62828;")

    def _live_check(self) -> None:
        source_row = self._current_source_row()
        if source_row is None:
            return
        row = self.model.rows[source_row]
        text = self.text_edit.toPlainText()
        if not text.strip():
            self._set_check("译文为空", ok=False)
            return
        problem = service.token_problem(row.original, text)
        self._set_check(problem or "格式码与原文一致", ok=problem is None)

    def save_current(self) -> None:
        source_row = self._current_source_row()
        if source_row is None:
            return
        self.model.setData(self.model.index(source_row, TEXT_COL), self.text_edit.toPlainText())

    # ---- 保存与批量操作 ----
    def _on_edited(self, entry_id: int, text: str) -> None:
        rejected = service.save_edits(self.root, {entry_id: text})
        if rejected:
            self.message.emit(rejected[entry_id], "error")
            return
        self.unapplied += 1
        self._update_unapplied()
        self._set_check("已保存为人工确认", ok=True)

    def selected_ids(self) -> list[int]:
        rows = {self.proxy.mapToSource(i).row() for i in self.table.selectionModel().selectedRows()}
        return [self.model.rows[r].id for r in sorted(rows)]

    def confirm_selected(self) -> int:
        ids = self.selected_ids()
        if not ids or self.root is None:
            return 0
        n = service.confirm_entries(self.root, ids)
        self.model.mark_confirmed(set(ids))
        self.unapplied += n
        self._update_unapplied()
        self.message.emit(f"已确认 {n} 条", "info")
        return n

    def _retranslate_selected(self) -> None:
        ids = self.selected_ids()
        if ids:
            self.retranslate_requested.emit(ids)

    def _update_unapplied(self) -> None:
        if self.unapplied:
            self.unapplied_label.setText(f"有 {self.unapplied} 处修改尚未应用到游戏")
        else:
            self.unapplied_label.setText("")
        self.apply_button.setEnabled(self.root is not None)

    def mark_applied(self) -> None:
        self.unapplied = 0
        self.model.edited_ids.clear()
        self._update_unapplied()
        self.model.layoutChanged.emit()

    def set_busy(self, busy: bool) -> None:
        for widget in (self.confirm_button, self.retranslate_button, self.apply_button):
            widget.setEnabled(not busy and self.root is not None)
