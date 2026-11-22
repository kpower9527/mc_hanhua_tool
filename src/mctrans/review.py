from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font

from .store import ReviewRow, TranslationUpdate, Workspace
from .translate.protect import protect

HEADERS = ["ID", "类别", "文件", "位置", "原文", "译文", "来源", "状态", "备注", "确认"]
WIDTHS = [8, 12, 40, 30, 60, 60, 10, 10, 24, 8]
ORIGIN_LABELS = {"mod_builtin": "mod 自带", "cfpa": "CFPA", "respack": "汉化补丁", "memory": "记忆库", "llm": "机翻", "human": "人工"}
STATUS_LABELS = {"pending": "待翻译", "done": "已完成", "failed": "失败", "confirmed": "已确认"}
TRUTHY = {"是", "y", "yes", "1", "true", "√", "✓", "x", "确认"}
GUIDE = [
    "使用方法",
    "1. 在「审校」工作表中直接修改「译文」列，空着的是还没有译文的条目，可以补上。",
    "2. 不改译文、只想确认机翻没问题时，在「确认」列填「是」。",
    "3. 保存后运行：mctrans review import <实例目录> <本文件>，再运行 mctrans apply 使修改生效。",
    "4. 导入的译文会被标记为人工确认，以后不会被自动覆盖，并会在其他整合包中复用。",
    "注意：不要修改 ID 和原文列；§a、%s、{0}、$(item)、&6 等格式码需要原样保留，否则该行会被拒绝导入。",
]


def _clean(value: str | None) -> str | None:
    return None if value is None else ILLEGAL_CHARACTERS_RE.sub("", value)


def _set_text(cell, value: str | None) -> None:
    cell.value = _clean(value)
    if value is not None:
        cell.data_type = "s"  # 防止以 = 开头的文本被当成公式


def export_review(ws: Workspace, path: Path, include_all: bool = False,
                  handlers: set[str] | None = None) -> int:
    rows = ws.review_rows(include_all=include_all, handlers=handlers)
    wb = Workbook()
    sheet = wb.active
    sheet.title = "审校"
    sheet.append(HEADERS)
    for cell in sheet[1]:
        cell.font = Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    for row_no, r in enumerate(rows, start=2):
        values = [r.handler, r.source, r.locator, r.original, r.text,
                  ORIGIN_LABELS.get(r.origin or "", r.origin or ""), STATUS_LABELS.get(r.status, r.status), r.note]
        sheet.cell(row=row_no, column=1, value=r.id)
        for col, value in enumerate(values, start=2):
            _set_text(sheet.cell(row=row_no, column=col), value)
        sheet.cell(row=row_no, column=5).alignment = wrap
        sheet.cell(row=row_no, column=6).alignment = wrap
    for i, width in enumerate(WIDTHS):
        sheet.column_dimensions[chr(ord("A") + i)].width = width
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    guide = wb.create_sheet("说明")
    for line in GUIDE:
        guide.append([line])
    guide.column_dimensions["A"].width = 100
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return len(rows)


@dataclass
class ImportReport:
    updated: int = 0
    confirmed: int = 0
    unchanged: int = 0
    unknown: int = 0
    invalid: list[str] = field(default_factory=list)


def _tokens(text: str) -> Counter:
    return Counter(protect(text).tokens)


def import_review(ws: Workspace, path: Path, force: bool = False) -> ImportReport:
    report = ImportReport()
    wb = load_workbook(path, read_only=True)
    sheet = wb["审校"] if "审校" in wb.sheetnames else wb.active
    rows = sheet.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows, [])]
    try:
        col = {name: header.index(name) for name in ("ID", "原文", "译文", "确认")}
    except ValueError:
        wb.close()
        raise ValueError("表格缺少 ID / 原文 / 译文 / 确认 列，请使用 mctrans review export 导出的文件") from None
    updates: list[TranslationUpdate] = []
    for line_no, values in enumerate(rows, start=2):
        values = list(values) + [None] * (len(header) - len(values))
        raw_id = values[col["ID"]]
        if raw_id is None or str(raw_id).strip() == "":
            continue
        try:
            entry_id = int(raw_id)
        except (TypeError, ValueError):
            report.invalid.append(f"第 {line_no} 行：ID 无效")
            continue
        current: ReviewRow | None = ws.get_review_row(entry_id)
        if current is None:
            report.unknown += 1
            continue
        if (values[col["原文"]] or "") != (_clean(current.original) or ""):
            report.invalid.append(f"第 {line_no} 行：原文已变化，请重新导出后再审校")
            continue
        text = values[col["译文"]]
        text = "" if text is None else str(text)
        confirm = str(values[col["确认"]] or "").strip().lower() in TRUTHY
        if not text.strip():
            if confirm and current.text:
                text = current.text
            else:
                report.unchanged += 1
                continue
        changed = text != (_clean(current.text) or "")
        if not changed and not confirm:
            report.unchanged += 1
            continue
        if not changed:
            text = current.text or text
        if not force and _tokens(current.original) != _tokens(text):
            report.invalid.append(f"第 {line_no} 行：格式码与原文不一致（{current.original[:40]}）")
            continue
        updates.append(TranslationUpdate(entry_id, text, "human", "confirmed"))
        if changed:
            report.updated += 1
        else:
            report.confirmed += 1
    wb.close()
    ws.save_translations(updates)
    return report
