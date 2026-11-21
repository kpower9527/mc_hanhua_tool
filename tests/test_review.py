import pytest
from openpyxl import load_workbook

from mctrans.models import Entry
from mctrans.review import HEADERS, export_review, import_review
from mctrans.store import TranslationUpdate, Workspace


def e(key, text, zh=None):
    return Entry("lang_json", "jar:assets/a/lang/en_us.json", key, "a", text, context=key, existing_zh=zh)


@pytest.fixture
def ws(tmp_path):
    w = Workspace.for_instance(tmp_path / "inst")
    w.sync_entries("lang_json", [
        e("k1", "Gear", "齿轮"), e("k2", "Shaft"), e("k3", "Use %s here"), e("k4", "=== Title ==="), e("k5", "Belt"),
    ])
    w.apply_builtin()
    ids = {p.locator: p.id for p in w.pending_entries()}
    w.save_translations([
        TranslationUpdate(ids["k2"], "轴", "llm", "done", "m"),
        TranslationUpdate(ids["k3"], "在此使用 %s", "llm", "done", "m"),
        TranslationUpdate(ids["k4"], "=== 标题 ===", "llm", "done", "m"),
    ])
    w.ids = ids
    yield w
    w.close()


def sheet_rows(path):
    sheet = load_workbook(path)["审校"]
    return [list(r) for r in sheet.iter_rows(values_only=True)]


def test_export(ws, tmp_path):
    path = tmp_path / "review.xlsx"
    assert export_review(ws, path) == 4
    rows = sheet_rows(path)
    assert rows[0] == HEADERS
    by_locator = {r[3]: r for r in rows[1:]}
    assert by_locator["k2"][4:8] == ["Shaft", "轴", "机翻", "已完成"]
    assert by_locator["k5"][5] is None and by_locator["k5"][7] == "待翻译"
    assert by_locator["k4"][5] == "=== 标题 ==="  # 以 = 开头的文本不会变成公式
    assert "说明" in load_workbook(path).sheetnames
    assert export_review(ws, tmp_path / "all.xlsx", include_all=True) == 5


def edit(path, changes):
    """changes: {locator: (译文, 确认)}"""
    wb = load_workbook(path)
    sheet = wb["审校"]
    for row in sheet.iter_rows(min_row=2):
        loc = row[3].value
        if loc in changes:
            text, confirm = changes[loc]
            if text is not None:
                row[5].value = text
            row[9].value = confirm
    wb.save(path)


def test_import(ws, tmp_path):
    path = tmp_path / "review.xlsx"
    export_review(ws, path)
    edit(path, {
        "k2": ("传动杆", None),          # 修改
        "k5": ("皮带", None),            # 补译
        "k4": (None, "是"),              # 仅确认
        "k3": ("在此使用", None),        # 丢了 %s：拒绝
    })
    report = import_review(ws, path)
    assert (report.updated, report.confirmed, report.unknown) == (2, 1, 0)
    assert len(report.invalid) == 1 and "格式码" in report.invalid[0]
    texts = {i.entry.locator: (i.text, i.origin, i.status) for i in ws.translated_items()}
    assert texts["k2"] == ("传动杆", "human", "confirmed")
    assert texts["k5"] == ("皮带", "human", "confirmed")
    assert texts["k4"] == ("=== 标题 ===", "human", "confirmed")
    assert texts["k3"] == ("在此使用 %s", "llm", "done")


def test_import_force_skips_token_check(ws, tmp_path):
    path = tmp_path / "review.xlsx"
    export_review(ws, path)
    edit(path, {"k3": ("在此使用", None)})
    assert import_review(ws, path, force=True).updated == 1


def test_import_rejects_changed_original_and_unknown_ids(ws, tmp_path):
    path = tmp_path / "review.xlsx"
    export_review(ws, path)
    wb = load_workbook(path)
    sheet = wb["审校"]
    for row in sheet.iter_rows(min_row=2):
        if row[3].value == "k2":
            row[4].value = "Shaft v2"
            row[5].value = "新轴"
        if row[3].value == "k5":
            row[0].value = 999999
            row[5].value = "皮带"
    wb.save(path)
    report = import_review(ws, path)
    assert report.updated == 0 and report.unknown == 1
    assert len(report.invalid) == 1 and "原文" in report.invalid[0]
