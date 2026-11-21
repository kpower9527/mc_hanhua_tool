# 计划 5：Excel 审校与补丁导出 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 支持把待审校的条目导出为 Excel，人工修改后导入（标记为人工确认并写入记忆库）；支持把汉化结果导出为独立的补丁目录，便于分发。

**Architecture:** `review.py` 负责 Excel 读写（openpyxl），通过 `Workspace.review_rows/get_review_row` 读取数据、`save_translations` 写回；导入时校验原文是否一致、格式码是否完整。`output/patch.py` 复用 `build_outputs` 生成资源包与实例文件，写到指定目录并附使用说明（含 CFPA 内容时附许可声明）。

**Tech Stack:** 新增依赖 `openpyxl`。

**Spec:** `docs/superpowers/specs/2026-11-21-mc-modpack-translator-design.md`（第 7.4、8 节）

## Global Constraints

- 计划 1–4 的全局约束继续有效
- 导入的修改一律记为 `origin="human"`、`status="confirmed"`，并同步写入全局记忆库（已确认）
- 导入时原文与当前工作区不一致的行、格式码（`protect` 提取的占位符多重集合）与原文不一致的行一律跳过并报告；`--force` 只跳过格式码检查
- 写入 Excel 的文本单元格一律强制为字符串类型（避免以 `=` 开头的文本被当成公式），并去除 Excel 不允许的控制字符
- 补丁目录必须不存在或为空；不修改实例本身

## 文件结构

```
src/mctrans/
├── store.py            # 修改：ReviewRow、review_rows、get_review_row
├── review.py           # 新增：export_review、import_review、ImportReport
├── output/patch.py     # 新增：export_patch、PatchReport
└── cli.py              # 修改：review export / review import 子命令、export-patch 命令
```

---

### Task 1: 审校数据查询

**Files:**
- Modify: `src/mctrans/store.py`
- Test: 追加到 `tests/test_store.py`

**Interfaces:**
- Produces:
  - `store.ReviewRow(id: int, handler: str, source: str, locator: str, original: str, text: str | None, origin: str | None, status: str, note: str | None)`
  - `Workspace.review_rows(include_all: bool = False, handlers: set[str] | None = None) -> list[ReviewRow]`：排除过时和 `skipped`；默认只包含 `pending`/`failed`、来源为 `llm`/`memory` 的 `done`、以及 `note` 非空的条目；按 handler、source、id 排序
  - `Workspace.get_review_row(entry_id: int) -> ReviewRow | None`

- [ ] **Step 1: 在 `tests/test_store.py` 末尾追加失败测试**

```python
def test_review_rows(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear", "齿轮"), e("k2", "Shaft"), e("k3", "Belt"), e("k4", "Cog"), e("k5", "%s")])
    ws.sync_entries("ftbquests", [Entry("ftbquests", "q", "t1", "ftbquests", "Quest")])
    ws.apply_builtin()
    ids = {p.locator: p.id for p in ws.pending_entries()}
    ws.save_translations([
        TranslationUpdate(ids["k2"], "轴", "llm", "done", "m"),
        TranslationUpdate(ids["k3"], "传送带", "human", "confirmed"),
        TranslationUpdate(ids["k5"], None, None, "skipped"),
    ])
    default = [(r.locator, r.status) for r in ws.review_rows()]
    assert default == [("t1", "pending"), ("k2", "done"), ("k4", "pending")]
    assert [r.locator for r in ws.review_rows(include_all=True)] == ["t1", "k1", "k2", "k3", "k4"]
    assert [r.locator for r in ws.review_rows(handlers={"ftbquests"})] == ["t1"]
    row = ws.get_review_row(ids["k2"])
    assert (row.original, row.text, row.origin) == ("Shaft", "轴", "llm")
    assert ws.get_review_row(999999) is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL，`AttributeError: 'Workspace' object has no attribute 'review_rows'`

- [ ] **Step 3: 修改 `src/mctrans/store.py`**

在 `TranslationUpdate` 定义之后加入：

```python
@dataclass
class ReviewRow:
    id: int
    handler: str
    source: str
    locator: str
    original: str
    text: str | None
    origin: str | None
    status: str
    note: str | None
```

在 `status_counts` 方法之前加入：

```python
    _REVIEW_SQL = (
        "SELECT e.id, e.handler, e.source, e.locator, e.original, t.text, t.origin, t.status, t.note "
        "FROM entries e JOIN translations t ON t.entry_id=e.id WHERE e.stale=0 AND t.status != 'skipped'"
    )

    @staticmethod
    def _review_row(r: sqlite3.Row) -> ReviewRow:
        return ReviewRow(r["id"], r["handler"], r["source"], r["locator"], r["original"],
                         r["text"], r["origin"], r["status"], r["note"])

    def review_rows(self, include_all: bool = False, handlers: set[str] | None = None) -> list[ReviewRow]:
        sql = self._REVIEW_SQL
        params: list = []
        if not include_all:
            sql += (" AND (t.status IN ('pending', 'failed') OR (t.status='done' AND t.origin IN ('llm', 'memory'))"
                    " OR t.note IS NOT NULL)")
        if handlers:
            sql += f" AND e.handler IN ({','.join('?' * len(handlers))})"
            params.extend(sorted(handlers))
        sql += " ORDER BY e.handler, e.source, e.id"
        return [self._review_row(r) for r in self.conn.execute(sql, params)]

    def get_review_row(self, entry_id: int) -> ReviewRow | None:
        row = self.conn.execute(self._REVIEW_SQL + " AND e.id=?", (entry_id,)).fetchone()
        return self._review_row(row) if row else None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_store.py -v`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/store.py tests/test_store.py
git commit -m "feat: 审校数据查询"
```

---

### Task 2: Excel 导出与导入

**Files:**
- Create: `src/mctrans/review.py`
- Modify: `pyproject.toml`（依赖加 `openpyxl>=3.1`）
- Test: `tests/test_review.py`

**Interfaces:**
- Consumes: `Workspace.review_rows/get_review_row/save_translations`, `protect.protect`
- Produces:
  - `review.HEADERS = ["ID", "类别", "文件", "位置", "原文", "译文", "来源", "状态", "备注", "确认"]`
  - `review.export_review(ws, path: Path, include_all: bool = False, handlers: set[str] | None = None) -> int`（返回导出行数；工作表名"审校"，另有"说明"工作表）
  - `review.ImportReport(updated: int = 0, confirmed: int = 0, unchanged: int = 0, unknown: int = 0, invalid: list[str])`
  - `review.import_review(ws, path: Path, force: bool = False) -> ImportReport`：按表头名称定位列；"确认"列填 `是/y/yes/1/true/√/✓/x/确认`（不区分大小写）视为确认

- [ ] **Step 1: 写失败测试 `tests/test_review.py`**

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv sync` 之前先在 `pyproject.toml` 的 dependencies 中加入 `"openpyxl>=3.1",`，然后运行 `uv sync` 与 `uv run pytest tests/test_review.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'mctrans.review'`

- [ ] **Step 3: 实现 `src/mctrans/review.py`**

```python
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
ORIGIN_LABELS = {"mod_builtin": "mod 自带", "cfpa": "CFPA", "memory": "记忆库", "llm": "机翻", "human": "人工"}
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
    for r in rows:
        values = [r.id, r.handler, r.source, r.locator, r.original, r.text,
                  ORIGIN_LABELS.get(r.origin or "", r.origin or ""), STATUS_LABELS.get(r.status, r.status), r.note, None]
        sheet.append([None] * len(HEADERS))
        cells = sheet[sheet.max_row]
        cells[0].value = r.id
        for i, value in enumerate(values[1:], start=1):
            _set_text(cells[i], value)
        cells[4].alignment = wrap
        cells[5].alignment = wrap
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_review.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml uv.lock src/mctrans/review.py tests/test_review.py
git commit -m "feat: Excel 审校导出与导入"
```

---

### Task 3: 补丁导出

**Files:**
- Create: `src/mctrans/output/patch.py`
- Test: `tests/test_patch.py`

**Interfaces:**
- Consumes: `apply.build_outputs`、`apply.ApplyError`、`apply.MODE_STANDALONE/MODE_I18N/PACK_DESCRIPTION`、`resourcepack.build_pack_zip/pack_mcmeta`
- Produces:
  - `patch.README_NAME = "汉化补丁说明.txt"`
  - `patch.PatchReport(entries: int, files: list[str], includes_cfpa: bool)`
  - `patch.export_patch(root: Path, ws, mc_version: str, out_dir: Path, mode: str) -> PatchReport`：`out_dir` 存在且非空时抛 `ApplyError`；资源包写到 `<out>/resourcepacks/`，实例文件写到 `<out>/<相对路径>`，并写说明文件。`includes_cfpa` 为独立模式且工作区有 `cfpa` 来源译文时为 True，此时说明文件包含 CC BY-NC-SA 4.0 声明

- [ ] **Step 1: 写失败测试 `tests/test_patch.py`**

```python
import io
import json
import zipfile

import pytest

from mctrans.handlers.base import Handler
from mctrans.models import Entry
from mctrans.output import apply as apply_mod
from mctrans.output.apply import ApplyError, MODE_I18N, MODE_STANDALONE
from mctrans.output.patch import README_NAME, export_patch
from mctrans.store import TranslationUpdate, Workspace


def e(key, text):
    return Entry("lang_json", "jar:assets/a/lang/en_us.json", key, "a", text, context=key)


@pytest.fixture
def ws(instance_dir):
    w = Workspace.for_instance(instance_dir)
    w.sync_entries("lang_json", [e("k1", "Gear"), e("k2", "Shaft")])
    ids = {p.locator: p.id for p in w.pending_entries()}
    w.save_translations([
        TranslationUpdate(ids["k1"], "齿轮", "cfpa", "done"),
        TranslationUpdate(ids["k2"], "轴", "llm", "done", "m"),
    ])
    yield w
    w.close()


def test_export_standalone_with_cfpa_notice(instance_dir, ws, tmp_path):
    out = tmp_path / "patch"
    report = export_patch(instance_dir, ws, "1.20.1", out, MODE_STANDALONE)
    assert report.includes_cfpa and report.entries == 2
    assert report.files == ["resourcepacks/mctrans-zh_cn.zip", README_NAME]
    with zipfile.ZipFile(io.BytesIO((out / "resourcepacks" / "mctrans-zh_cn.zip").read_bytes())) as z:
        assert json.loads(z.read("assets/a/lang/zh_cn.json")) == {"k1": "齿轮", "k2": "轴"}
    readme = (out / README_NAME).read_text(encoding="utf-8")
    assert "CC BY-NC-SA 4.0" in readme and "1.20.1" in readme
    assert not (instance_dir / "resourcepacks").exists()  # 不修改实例


def test_export_i18n_mode_has_no_cfpa(instance_dir, ws, tmp_path):
    report = export_patch(instance_dir, ws, "1.20.1", tmp_path / "p", MODE_I18N)
    assert not report.includes_cfpa
    assert report.files == ["resourcepacks/mctrans-zh_cn-fill.zip", README_NAME]
    assert "CC BY-NC-SA" not in (tmp_path / "p" / README_NAME).read_text(encoding="utf-8")


def test_refuses_non_empty_dir(instance_dir, ws, tmp_path):
    out = tmp_path / "p"
    out.mkdir()
    (out / "x.txt").write_text("x")
    with pytest.raises(ApplyError):
        export_patch(instance_dir, ws, "1.20.1", out, MODE_STANDALONE)


class FileHandler(Handler):
    name = "lang_json"
    kind_label = "x"
    output = "instance"

    def detect(self, instance):
        return True

    def extract(self, instance):
        return iter(())

    def write(self, items, sink):
        sink.add_instance_file("config/quests.txt", "\n".join(i.text for i in items).encode("utf-8"))


def test_instance_files_are_copied(instance_dir, ws, tmp_path, monkeypatch):
    monkeypatch.setattr(apply_mod, "all_handlers", lambda: [FileHandler()])
    report = export_patch(instance_dir, ws, "1.20.1", tmp_path / "p", MODE_STANDALONE)
    assert report.files == ["config/quests.txt", README_NAME]
    assert (tmp_path / "p" / "config" / "quests.txt").read_text(encoding="utf-8") == "齿轮\n轴"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_patch.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 `src/mctrans/output/patch.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..store import Workspace
from . import apply as apply_mod
from .resourcepack import build_pack_zip, pack_mcmeta

README_NAME = "汉化补丁说明.txt"
CFPA_NOTICE = """
本补丁包含来自 CFPA（Minecraft 模组简体中文翻译项目，https://github.com/CFPAOrg/Minecraft-Mod-Language-Package）
的译文，这部分内容采用 知识共享 署名-非商业性使用-相同方式共享 4.0 国际许可协议（CC BY-NC-SA 4.0）授权：
https://creativecommons.org/licenses/by-nc-sa/4.0/deed.zh-hans
分发本补丁时请保留本说明，且不得用于商业用途。
"""


@dataclass
class PatchReport:
    entries: int
    files: list[str]
    includes_cfpa: bool


def _readme(mc_version: str, mode: str, packs: list[str], instance_files: list[str], includes_cfpa: bool) -> str:
    lines = [
        "Minecraft 整合包汉化补丁",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}    Minecraft 版本：{mc_version}",
        "生成工具：mctrans（大模型机翻 + 社区译文，可能存在错译，欢迎反馈修正）",
        "",
        "使用方法：",
        "1. 把本目录中除本说明外的所有文件，按相同的目录结构复制到整合包的实例目录（包含 mods 文件夹的那一层），覆盖同名文件。",
    ]
    if packs:
        if mode == apply_mod.MODE_I18N:
            lines.append("2. 进入游戏，在「选项 → 资源包」中启用以下资源包："
                         "补全包（mctrans-zh_cn-fill.zip）放在 I18nUpdateMod 的汉化资源包下方，"
                         "修正包（mctrans-zh_cn-fix.zip，如有）放在其上方。")
        else:
            lines.append("2. 进入游戏，在「选项 → 资源包」中启用 mctrans-zh_cn.zip，并放在最上方。")
        lines.append("3. 在「选项 → 语言」中选择简体中文。")
    lines += ["", "包含的文件："]
    lines += [f"  resourcepacks/{name}" for name in packs]
    lines += [f"  {rel}（覆盖整合包中的同名文件，建议先备份）" for rel in instance_files]
    if includes_cfpa:
        lines.append(CFPA_NOTICE)
    return "\n".join(lines) + "\n"


def export_patch(root: Path, ws: Workspace, mc_version: str, out_dir: Path, mode: str) -> PatchReport:
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()):
        raise apply_mod.ApplyError(f"输出目录不为空：{out_dir}")
    outputs = apply_mod.build_outputs(ws, mode, Path(root))
    files: list[str] = []
    for rel, data in sorted(outputs.instance.instance_files.items()):
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append(rel)
    pack_names = sorted(outputs.packs)
    for name in pack_names:
        target = out_dir / "resourcepacks" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(build_pack_zip(outputs.packs[name].pack_files,
                                          pack_mcmeta(mc_version, apply_mod.PACK_DESCRIPTION)))
        files.append(f"resourcepacks/{name}")
    includes_cfpa = mode == apply_mod.MODE_STANDALONE and ws.origin_counts().get("cfpa", 0) > 0
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / README_NAME).write_text(
        _readme(mc_version, mode, pack_names, sorted(outputs.instance.instance_files), includes_cfpa),
        encoding="utf-8")
    files.append(README_NAME)
    return PatchReport(outputs.entries, sorted(files[:-1]) + [README_NAME], includes_cfpa)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_patch.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/output/patch.py tests/test_patch.py
git commit -m "feat: 导出汉化补丁目录"
```

---

### Task 4: CLI 命令与文档

**Files:**
- Modify: `src/mctrans/cli.py`, `tests/test_cli.py`, `README.md`

**Interfaces:**
- Produces:
  - `mctrans review export <实例> <xlsx> [--all] [--only <类别>]...`
  - `mctrans review import <实例> <xlsx> [--force]`（导入后把人工译文写入记忆库，并提示运行 apply）
  - `mctrans export-patch <实例> <目录> [--mode auto|standalone|i18nupdate]`

- [ ] **Step 1: 在 `tests/test_cli.py` 末尾追加失败测试**

```python
def test_review_round_trip_and_patch(pack, tmp_path):
    from openpyxl import load_workbook
    root, _ = pack
    invoke("run", root, "--yes")
    xlsx = tmp_path / "review.xlsx"
    out = invoke("review", "export", root, xlsx).output
    assert "导出 1 条" in out
    wb = load_workbook(xlsx)
    sheet = wb["审校"]
    sheet["F2"].value = "使用齿轮 %s"
    wb.save(xlsx)
    out = invoke("review", "import", root, xlsx).output
    assert "修改 1 条" in out
    with zipfile.ZipFile(io.BytesIO((root / "resourcepacks" / "mctrans-zh_cn.zip").read_bytes())) as z:
        assert json.loads(z.read("assets/create/lang/zh_cn.json"))["tip.create.x"] == "译Use the Gear %s"
    invoke("apply", root)
    with zipfile.ZipFile(io.BytesIO((root / "resourcepacks" / "mctrans-zh_cn.zip").read_bytes())) as z:
        assert json.loads(z.read("assets/create/lang/zh_cn.json"))["tip.create.x"] == "使用齿轮 %s"
    from mctrans.memory import Memory
    with Memory.default() as memory:
        assert memory.lookup("create", "Use the Gear %s").confirmed is True
    patch_dir = tmp_path / "patch"
    out = invoke("export-patch", root, patch_dir).output
    assert "CC BY-NC-SA" in out
    assert (patch_dir / "resourcepacks" / "mctrans-zh_cn.zip").exists()
    result = runner.invoke(cli.app, ["export-patch", str(root), str(patch_dir)])
    assert result.exit_code == 1 and "不为空" in result.output
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL（`No such command 'review'`）

- [ ] **Step 3: 修改 `src/mctrans/cli.py`**

导入区加入：

```python
from .output.patch import export_patch
from .review import export_review, import_review
```

在 `app = typer.Typer(...)` 之后加入：

```python
review_app = typer.Typer(help="导出 / 导入 Excel 审校表格", no_args_is_help=True)
app.add_typer(review_app, name="review")
```

在文件末尾加入：

```python
@review_app.command("export")
def review_export(instance: InstanceArg,
                  file: Annotated[Path, typer.Argument(help="导出的 Excel 文件路径（.xlsx）")],
                  all_rows: Annotated[bool, typer.Option("--all", help="导出全部条目（默认只导出机翻、待翻译和需复核的）")] = False,
                  only: OnlyOpt = None):
    """导出待审校的条目到 Excel。"""
    ws = _open_scanned(instance)
    with ws:
        n = export_review(ws, file, include_all=all_rows, handlers=set(only) if only else None)
    console.print(f"已导出 {n} 条到 {file}")
    console.print("修改「译文」列或在「确认」列填「是」，保存后运行 mctrans review import 导入")


@review_app.command("import")
def review_import(instance: InstanceArg,
                  file: Annotated[Path, typer.Argument(help="审校后的 Excel 文件", exists=True, dir_okay=False)],
                  force: Annotated[bool, typer.Option("--force", help="不检查格式码是否与原文一致")] = False):
    """导入审校结果（标记为人工确认，并写入记忆库）。"""
    ws = _open_scanned(instance)
    with ws:
        try:
            report = import_review(ws, file, force=force)
        except ValueError as exc:
            _fail(str(exc))
        with Memory.default() as memory:
            remember_translations(ws, memory)
    console.print(
        f"导入完成：修改 {report.updated} 条，确认 {report.confirmed} 条，未改动 {report.unchanged} 条，"
        f"找不到的 ID {report.unknown} 条，拒绝 {len(report.invalid)} 条"
    )
    for line in report.invalid[:MAX_WARNINGS_SHOWN]:
        console.print(f"[yellow]拒绝：[/yellow]{line}")
    if report.updated or report.confirmed:
        console.print("运行 mctrans apply 使修改生效")


@app.command("export-patch")
def export_patch_cmd(instance: InstanceArg,
                     out_dir: Annotated[Path, typer.Argument(help="补丁输出目录（必须不存在或为空）")],
                     mode: ModeOpt = "auto"):
    """把汉化结果导出为独立的补丁目录，便于分发（不修改实例）。"""
    ws = _open_scanned(instance)
    with ws:
        try:
            chosen = resolve_mode(mode, ws.get_meta("has_i18nupdatemod") == "1")
            report = export_patch(instance, ws, ws.get_meta("mc_version"), out_dir, chosen)
        except ApplyError as exc:
            _fail(str(exc))
    console.print(f"已导出补丁到 {out_dir}（译文 {report.entries} 条，文件 {len(report.files)} 个）")
    if report.includes_cfpa:
        console.print("[yellow]补丁包含 CFPA 社区译文（CC BY-NC-SA 4.0），分发时请保留说明文件，且不得商用[/yellow]")
```

- [ ] **Step 4: 更新 `README.md`**

在"### 实例目录"之前加入：

````markdown
### 人工审校

```
uv run mctrans review export <实例目录> review.xlsx     # 导出机翻、待翻译和需复核的条目（--all 导出全部，--only 按类别）
# 用 Excel / WPS 修改「译文」列，或在「确认」列填「是」
uv run mctrans review import <实例目录> review.xlsx     # 导入，标记为人工确认
uv run mctrans apply <实例目录>                          # 重新应用使修改生效
```

人工确认的译文不会被自动覆盖，也会写入记忆库，在其他整合包中直接复用。格式码（`§a`、`%s`、`{0}`、`$(item)`、`&6` 等）与原文不一致的行会被拒绝导入。

### 导出补丁分发

```
uv run mctrans export-patch <实例目录> <输出目录>
```

生成与实例目录结构相同的补丁（资源包、修改过的任务书和脚本等）以及使用说明，不修改实例本身。包含 CFPA 社区译文时请遵守 CC BY-NC-SA 4.0（署名、非商业、相同方式共享）。
````

- [ ] **Step 5: 运行全部测试**

Run: `uv run pytest -q`
Expected: 全部通过

- [ ] **Step 6: 提交**

```bash
git add src/mctrans/cli.py tests/test_cli.py README.md
git commit -m "feat: review export/import 与 export-patch 命令"
```
