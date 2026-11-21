# 计划 4：KubeJS / CraftTweaker 脚本与配置文本 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 汉化 KubeJS 脚本与 CraftTweaker 脚本中写死的展示文本（物品提示、显示名称等），并支持用户用规则文件指定需要翻译的配置文本。

**Architecture:** 新增 JS/ZS 通用的轻量词法扫描器，产出每个字符串字面量的位置、引号与"所在调用"（如 `Text.of`、`.displayName`、`format.green`、`=displayName`）。脚本处理器按白名单挑选字符串，写回时只替换字符串区间。配置文本处理器读取用户规则（文件 glob + 带 `text` 命名组的正则），按匹配区间提取与替换。三个处理器都直接修改实例内文件（自动备份、可还原）。

**Tech Stack:** 同前，无新增依赖。

**Spec:** `docs/superpowers/specs/2026-11-21-mc-modpack-translator-design.md`（第 6.4、6.5、6.6 节）

## 实现前调研结论（2026-11-21，用户整合包只读探查）

- **ATM10 8.6**：`kubejs/` 下 222 个脚本。展示文本集中在 `Text.of(...)`（约 90 处）、`Text.green/red/blue/gold/yellow/white(...)`、`.displayName(...)`（35 处）；另有 `Text.translatable(...)`（参数是翻译键，不能翻译）
- **ATM10SKY 2.4.0**：99 个脚本，模式相同
- **RLCraft 2.9.3**（1.12.2）：`scripts/` 下 7 个 `.zs`，展示文本在 `format.green/gold/darkRed/...("...")`、`.addTooltip("...")`
- **配置文本**：三个整合包中主菜单类配置（CustomMainMenu、FancyMenu、SimpleMenu）的文字要么是翻译键、要么为空。没有值得内置的通用规则，因此配置文本只支持用户自定义规则

## Global Constraints

- 计划 1–3 的全局约束继续有效
- KubeJS 条目：`handler="kubejs"`、`namespace="kubejs"`；CraftTweaker：`handler="crafttweaker"`、`namespace="crafttweaker"`；配置文本：`handler="config_text"`、`namespace="config"`；三者 `output="instance"`
- 脚本只翻译白名单调用中的字符串；形如 `mod:item` 的资源 ID、空白字符串一律跳过
- 写回时只替换被翻译的字符串区间，引号种类保持不变，文件其余字节不变；原文件有 UTF-8 BOM 时保留 BOM
- 实例内文件一律经 `read_original` 读取

## 文件结构

```
src/mctrans/
├── scriptlex.py            # 新增：JS/ZS 字符串扫描（位置、引号、所在调用）与字符串编码
├── handlers/
│   ├── scripts.py          # 新增：ScriptHandler 基类、KubeJsHandler、CraftTweakerHandler
│   ├── config_text.py      # 新增：ConfigTextHandler、load_rules
│   └── __init__.py         # 修改：注册三个处理器
└── cli.py                  # 修改：--only 帮助文本
```

---

### Task 1: 脚本字符串扫描器

**Files:**
- Create: `src/mctrans/scriptlex.py`
- Test: `tests/test_scriptlex.py`

**Interfaces:**
- Produces:
  - `scriptlex.StrLit(value: str, start: int, end: int, quote: str, callee: str | None)`：`start`/`end` 含引号；`callee` 为所在调用名（如 `Text.of`、`.displayName`、`format.green`），字符串是赋值右侧时为 `=<变量名最后一段>`（如 `=displayName`），都不是时为 None
  - `scriptlex.scan(text: str, js: bool) -> list[StrLit]`：跳过 `//`、`/* */` 注释（ZS 额外跳过 `#` 行注释）；JS 中跳过正则字面量；含 `${...}` 的模板字符串不产出；不完整的字符串（行尾未闭合）跳过
  - `scriptlex.encode(value: str, quote: str) -> str`：生成带引号的字面量，转义反斜杠、该引号字符、换行、回车、制表符

- [ ] **Step 1: 写失败测试 `tests/test_scriptlex.py`**

```python
from mctrans.scriptlex import encode, scan

JS = r"""
// Text.of('in a comment')
/* .displayName('block comment') */
ItemEvents.modifyTooltips(event => {
    event.add('mekanism:meka_tool', [
        Text.red('Increased Energy Consumption!'),
        Text.of("§7It's \"quoted\"\n2nd")
    ])
    let re = /mekanism:(meka|tool)'s/g
    let half = total / 2 / count
    let tpl = `plain template`
    let expr = `has ${name} inside`
    event.add(/mekanism:mekasuit_/, [Text.translatable('tooltip.key')])
})
StartupEvents.registry('item', e => {
    e.create('magical_soil').displayName('§bMagical Soil').texture('kubejs:item/x')
})
"""


def lits(text, js=True):
    return [(l.value, l.callee) for l in scan(text, js)]


def test_js_strings_and_callees():
    assert lits(JS) == [
        ("mekanism:meka_tool", "event.add"),
        ("Increased Energy Consumption!", "Text.red"),
        ('§7It\'s "quoted"\n2nd', "Text.of"),
        ("plain template", "=tpl"),
        ("tooltip.key", "Text.translatable"),
        ("item", "StartupEvents.registry"),
        ("magical_soil", "e.create"),
        ("§bMagical Soil", ".displayName"),
        ("kubejs:item/x", ".texture"),
    ]


def test_spans_and_quotes():
    found = scan(JS, True)
    lit = next(l for l in found if l.callee == "Text.of")
    assert JS[lit.start:lit.end] == '"§7It\'s \\"quoted\\"\\n2nd"' and lit.quote == '"'
    soil = next(l for l in found if l.callee == ".displayName")
    assert JS[soil.start:soil.end] == "'§bMagical Soil'" and soil.quote == "'"


def test_unterminated_string_is_skipped():
    assert lits("Text.of('broken\nText.of('ok')") == [("ok", "Text.of")]


ZS = """
#priority 100
# recipes.addShaped("in comment")
<minecraft:stone>.displayName = "Smooth Rock";
<roughtweaks:bandage>.addTooltip(format.green("Blast Resistance: Weak"));
<x:y>.addTooltip("Fertile Seasons:");
recipes.addShaped("lolarecipe6", <roughtweaks:bandage>, [[<minecraft:paper>]]);
var a = 10 / 2;
"""


def test_zs_strings_and_callees():
    assert lits(ZS, js=False) == [
        ("Smooth Rock", "=displayName"),
        ("Blast Resistance: Weak", "format.green"),
        ("Fertile Seasons:", ".addTooltip"),
        ("lolarecipe6", "recipes.addShaped"),
    ]


def test_encode():
    assert encode("它's \"好\"\n", '"') == r'"它' + "'" + r's \"好\"\n"'
    assert encode("it's", "'") == r"'it\'s'"
    assert encode("a`b", "`") == r"`a\`b`"
    assert encode("back\\slash", '"') == r'"back\\slash"'
    for value in ["it's", 'say "hi"', "two\nlines", "tab\there", "中文 §a颜色"]:
        for q in "'\"":
            assert [l.value for l in scan("Text.of(" + encode(value, q) + ")", True)] == [value]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_scriptlex.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 `src/mctrans/scriptlex.py`**

```python
"""JS（KubeJS）/ ZS（CraftTweaker）脚本的轻量字符串扫描。

不做完整语法分析，只追踪括号嵌套以确定每个字符串字面量所在的调用，
并跳过注释、JS 正则字面量和含插值的模板字符串。
"""
from __future__ import annotations

from dataclasses import dataclass

IDENT_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$.")
REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")
REGEX_KEYWORDS = {"return", "typeof", "case", "in", "of"}
SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
ENCODE_ESCAPES = {"\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


@dataclass
class StrLit:
    value: str
    start: int
    end: int
    quote: str
    callee: str | None = None


def _ident_before(text: str, i: int) -> str:
    j = i - 1
    while j >= 0 and text[j] in " \t\r\n":
        j -= 1
    k = j
    while k >= 0 and text[k] in IDENT_CHARS:
        k -= 1
    return text[k + 1:j + 1]


def _callee(text: str, start: int, stack: list[tuple[str, str]]) -> str | None:
    j = start - 1
    while j >= 0 and text[j] in " \t\r\n":
        j -= 1
    if j >= 0 and text[j] == "=" and (j == 0 or text[j - 1] not in "=!<>+-*/"):
        name = _ident_before(text, j)
        if name:
            return "=" + name.rsplit(".", 1)[-1]
    if stack and stack[-1][0] == "(":
        return stack[-1][1] or None
    return None


def _read_string(text: str, i: int, quote: str) -> tuple[StrLit | None, int]:
    """从 text[i]（引号）开始读取字符串，返回 (字面量或 None, 结束位置)。"""
    n = len(text)
    j = i + 1
    buf: list[str] = []
    has_expr = False
    while j < n:
        c = text[j]
        if c == "\\" and j + 1 < n:
            e = text[j + 1]
            if e in SIMPLE_ESCAPES:
                buf.append(SIMPLE_ESCAPES[e])
                j += 2
            elif e == "x" and j + 3 < n:
                buf.append(chr(int(text[j + 2:j + 4], 16)))
                j += 4
            elif e == "u" and j + 5 < n and text[j + 2] != "{":
                buf.append(chr(int(text[j + 2:j + 6], 16)))
                j += 6
            elif e == "\n":
                j += 2
            else:
                buf.append(e)
                j += 2
            continue
        if c == quote:
            return (None if has_expr else StrLit("".join(buf), i, j + 1, quote)), j + 1
        if c == "\n" and quote != "`":
            return None, j  # 未闭合，跳过
        if quote == "`" and c == "$" and j + 1 < n and text[j + 1] == "{":
            has_expr = True
            depth = 0
            j += 1
            while j < n:
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            j += 1
            continue
        buf.append(c)
        j += 1
    return None, n


def _skip_regex(text: str, i: int) -> int:
    n = len(text)
    j = i + 1
    in_class = False
    while j < n:
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "\n":
            return j
        if c == "[":
            in_class = True
        elif c == "]":
            in_class = False
        elif c == "/" and not in_class:
            j += 1
            while j < n and text[j].isalpha():
                j += 1
            return j
        j += 1
    return n


def scan(text: str, js: bool) -> list[StrLit]:
    out: list[StrLit] = []
    stack: list[tuple[str, str]] = []
    n = len(text)
    i = 0
    prev = ""
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if text.startswith("//", i) or (not js and c == "#"):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "\"'" or (js and c == "`"):
            lit, i = _read_string(text, i, c)
            if lit is not None:
                lit.callee = _callee(text, lit.start, stack)
                out.append(lit)
            prev = c
            continue
        if js and c == "/" and (prev == "" or prev in REGEX_PREV or _ident_before(text, i) in REGEX_KEYWORDS):
            i = _skip_regex(text, i)
            prev = "/"
            continue
        if c == "(":
            stack.append(("(", _ident_before(text, i)))
        elif c in "[{":
            stack.append((c, ""))
        elif c in ")]}" and stack:
            stack.pop()
        prev = c
        i += 1
    return out


def encode(value: str, quote: str) -> str:
    body = "".join(ENCODE_ESCAPES.get(ch, "\\" + ch if ch == quote else ch) for ch in value)
    return quote + body + quote
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_scriptlex.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/scriptlex.py tests/test_scriptlex.py
git commit -m "feat: JS/ZS 脚本字符串扫描器"
```

---

### Task 2: KubeJS 与 CraftTweaker 处理器

**Files:**
- Create: `src/mctrans/handlers/scripts.py`
- Test: `tests/test_scripts.py`

**Interfaces:**
- Consumes: `scriptlex.scan/encode/StrLit`, `applied.read_original`, `Handler`, `OutputSink(root)`
- Produces:
  - `scripts.kubejs_wants(callee: str | None) -> bool`：最后一段为 `displayName`/`tooltip`；或倒数第二段为 `Text`/`Component` 且最后一段是 `of`、`literal`、`string` 或颜色/样式名（`black darkBlue darkGreen darkAqua darkRed darkPurple gold gray grey darkGray darkGrey blue green aqua red lightPurple yellow white bold italic underlined strikethrough obfuscated`）
  - `scripts.zs_wants(callee: str | None) -> bool`：`=displayName`；最后一段为 `addTooltip`、`addShiftTooltip`、`addDescription`、`setDisplayName`、`displayName`；或倒数第二段为 `format`
  - `scripts.targets(text: str, js: bool, wants) -> list[StrLit]`：扫描后按白名单筛选，并去掉空白字符串和资源 ID（`^[a-z0-9_.-]+:[a-z0-9_/.-]+$`）
  - `scripts.KubeJsHandler`（`name="kubejs"`，文件 `kubejs/{client,server,startup}_scripts/**/*.js`）
  - `scripts.CraftTweakerHandler`（`name="crafttweaker"`，文件 `scripts/**/*.zs`）
  - 条目：`source=<相对路径>`，`locator=<在 targets 列表中的序号>`，`context=<文件名> <callee>`
  - 写出：重新扫描原始文件，序号对应且原文一致时替换为 `encode(译文, 原引号)`

- [ ] **Step 1: 写失败测试 `tests/test_scripts.py`**

```python
import pytest

from mctrans.handlers.base import OutputSink
from mctrans.handlers.scripts import CraftTweakerHandler, KubeJsHandler, kubejs_wants, targets, zs_wants
from mctrans.instance import load_instance
from mctrans.models import Entry, TranslatedItem

TOOLTIPS = """ItemEvents.modifyTooltips(event => {
    event.add('allthemodium:teleport_pad', [Text.of("Mining Dimension Disabled")])
    event.add('mekanism:meka_tool', [Text.red('Increased Energy!'), Text.translatable('tip.key')])
    event.add('x:y', Text.of('minecraft:stone'))
})
"""
STARTUP = "\ufeffStartupEvents.registry('item', e => {\n    e.create('soil').displayName('§bMagical Soil')\n})\n"
ZS = """<x:bandage>.addTooltip(format.green("Blast Resistance: Weak"));
<minecraft:stone>.displayName = "Smooth Rock";
recipes.addShaped("lolarecipe6", <x:y>, [[<minecraft:paper>]]);
"""


def put(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))


@pytest.fixture
def scripted(instance_dir):
    put(instance_dir, "kubejs/client_scripts/tooltips.js", TOOLTIPS)
    put(instance_dir, "kubejs/startup_scripts/items/custom.js", STARTUP)
    put(instance_dir, "kubejs/README.txt", "Text.of('not a script')")
    put(instance_dir, "scripts/tweaks.zs", ZS)
    return instance_dir


@pytest.mark.parametrize("callee, ok", [
    ("Text.of", True), ("Text.red", True), ("Component.literal", True), (".displayName", True),
    ("item.tooltip", True), ("Text.translatable", False), ("event.add", False), (None, False),
])
def test_kubejs_wants(callee, ok):
    assert kubejs_wants(callee) is ok


@pytest.mark.parametrize("callee, ok", [
    ("=displayName", True), ("format.green", True), (".addTooltip", True), ("mods.jei.JEI.addDescription", True),
    ("recipes.addShaped", False), (None, False),
])
def test_zs_wants(callee, ok):
    assert zs_wants(callee) is ok


def test_targets_skip_ids():
    assert [t.value for t in targets(TOOLTIPS, True, kubejs_wants)] == ["Mining Dimension Disabled", "Increased Energy!"]


def test_kubejs_extract(scripted):
    h = KubeJsHandler()
    inst = load_instance(scripted)
    assert h.detect(inst)
    got = [(e.source, e.locator, e.original, e.context) for e in h.extract(inst)]
    assert got == [
        ("kubejs/client_scripts/tooltips.js", "0", "Mining Dimension Disabled", "tooltips.js Text.of"),
        ("kubejs/client_scripts/tooltips.js", "1", "Increased Energy!", "tooltips.js Text.red"),
        ("kubejs/startup_scripts/items/custom.js", "0", "§bMagical Soil", "custom.js .displayName"),
    ]


def test_crafttweaker_extract(scripted):
    got = [(e.locator, e.original) for e in CraftTweakerHandler().extract(load_instance(scripted))]
    assert got == [("0", "Blast Resistance: Weak"), ("1", "Smooth Rock")]


def test_detect_without_scripts(instance_dir):
    inst = load_instance(instance_dir)
    assert not KubeJsHandler().detect(inst) and not CraftTweakerHandler().detect(inst)


def item(handler, source, locator, original, text):
    return TranslatedItem(Entry(handler, source, locator, handler, original), text, "llm", "done")


def test_write_kubejs_keeps_bom_and_quotes(scripted):
    sink = OutputSink(scripted)
    KubeJsHandler().write([
        item("kubejs", "kubejs/startup_scripts/items/custom.js", "0", "§bMagical Soil", "§b魔法土壤"),
        item("kubejs", "kubejs/client_scripts/tooltips.js", "1", "Increased Energy!", "能量提升！"),
        item("kubejs", "kubejs/client_scripts/tooltips.js", "0", "Something else", "不匹配，跳过"),
    ], sink)
    startup = sink.instance_files["kubejs/startup_scripts/items/custom.js"].decode("utf-8")
    assert startup == STARTUP.replace("'§bMagical Soil'", "'§b魔法土壤'")
    tooltips = sink.instance_files["kubejs/client_scripts/tooltips.js"].decode("utf-8")
    assert tooltips == TOOLTIPS.replace("'Increased Energy!'", "'能量提升！'")


def test_write_crafttweaker(scripted):
    sink = OutputSink(scripted)
    CraftTweakerHandler().write([item("crafttweaker", "scripts/tweaks.zs", "1", "Smooth Rock", "光滑的\"岩石\"")], sink)
    assert sink.instance_files["scripts/tweaks.zs"].decode("utf-8") == ZS.replace('"Smooth Rock"', r'"光滑的\"岩石\""')
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_scripts.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 `src/mctrans/handlers/scripts.py`**

```python
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Iterator

from ..applied import read_original
from ..instance import Instance
from ..models import Entry, TranslatedItem
from ..scriptlex import StrLit, encode, scan
from .base import Handler, OutputSink

RESOURCE_ID_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_/.-]+$")
TEXT_STYLES = frozenset({
    "of", "literal", "string", "black", "darkBlue", "darkGreen", "darkAqua", "darkRed", "darkPurple", "gold",
    "gray", "grey", "darkGray", "darkGrey", "blue", "green", "aqua", "red", "lightPurple", "yellow", "white",
    "bold", "italic", "underlined", "strikethrough", "obfuscated",
})
ZS_METHODS = frozenset({"addTooltip", "addShiftTooltip", "addDescription", "setDisplayName", "displayName"})
BOM = "\ufeff"


def _parts(callee: str) -> list[str]:
    return callee.strip(".").split(".")


def kubejs_wants(callee: str | None) -> bool:
    if not callee or callee.startswith("="):
        return False
    parts = _parts(callee)
    if parts[-1] in ("displayName", "tooltip"):
        return True
    return len(parts) >= 2 and parts[-2] in ("Text", "Component") and parts[-1] in TEXT_STYLES


def zs_wants(callee: str | None) -> bool:
    if not callee:
        return False
    if callee == "=displayName":
        return True
    if callee.startswith("="):
        return False
    parts = _parts(callee)
    return parts[-1] in ZS_METHODS or (len(parts) >= 2 and parts[-2] == "format")


def targets(text: str, js: bool, wants: Callable[[str | None], bool]) -> list[StrLit]:
    return [
        lit for lit in scan(text, js)
        if wants(lit.callee) and lit.value.strip() and not RESOURCE_ID_RE.match(lit.value.strip())
    ]


class ScriptHandler(Handler):
    output = "instance"
    js: bool = True
    patterns: tuple[str, ...] = ()
    namespace: str = ""

    def wants(self, callee: str | None) -> bool:
        raise NotImplementedError

    def files(self, root: Path) -> list[str]:
        found: set[str] = set()
        for pattern in self.patterns:
            found.update(p.relative_to(root).as_posix() for p in root.glob(pattern) if p.is_file())
        return sorted(found)

    def detect(self, instance: Instance) -> bool:
        return bool(self.files(instance.root))

    def _read(self, root: Path, rel: str) -> tuple[str, bool] | None:
        data = read_original(root, rel)
        if data is None:
            return None
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            self.warnings.append(f"脚本不是 UTF-8 编码，已跳过：{rel}")
            return None
        has_bom = text.startswith(BOM)
        return (text[1:] if has_bom else text), has_bom

    def extract(self, instance: Instance) -> Iterator[Entry]:
        self.warnings = []
        for rel in self.files(instance.root):
            read = self._read(instance.root, rel)
            if read is None:
                continue
            name = rel.rsplit("/", 1)[-1]
            for index, lit in enumerate(targets(read[0], self.js, self.wants)):
                yield Entry(handler=self.name, source=rel, locator=str(index), namespace=self.namespace,
                            original=lit.value, context=f"{name} {lit.callee}")

    def write(self, items: list[TranslatedItem], sink: OutputSink) -> None:
        if sink.root is None:
            raise ValueError("脚本处理器写出时需要实例根目录")
        by_source: dict[str, list[TranslatedItem]] = {}
        for it in items:
            by_source.setdefault(it.entry.source, []).append(it)
        for rel, group in sorted(by_source.items()):
            read = self._read(sink.root, rel)
            if read is None:
                continue
            text, has_bom = read
            found = targets(text, self.js, self.wants)
            replacements: list[tuple[int, int, str]] = []
            for it in group:
                index = int(it.entry.locator)
                if index >= len(found):
                    continue
                lit = found[index]
                if lit.value != it.entry.original or it.text == lit.value:
                    continue
                replacements.append((lit.start, lit.end, encode(it.text, lit.quote)))
            if not replacements:
                continue
            for start, end, literal in sorted(replacements, reverse=True):
                text = text[:start] + literal + text[end:]
            sink.add_instance_file(rel, ((BOM if has_bom else "") + text).encode("utf-8"))


class KubeJsHandler(ScriptHandler):
    name = "kubejs"
    kind_label = "KubeJS 脚本中的展示文本（物品提示、显示名称）"
    js = True
    namespace = "kubejs"
    patterns = ("kubejs/client_scripts/**/*.js", "kubejs/server_scripts/**/*.js", "kubejs/startup_scripts/**/*.js")

    def wants(self, callee: str | None) -> bool:
        return kubejs_wants(callee)


class CraftTweakerHandler(ScriptHandler):
    name = "crafttweaker"
    kind_label = "CraftTweaker 脚本中的展示文本（物品提示、显示名称）"
    js = False
    namespace = "crafttweaker"
    patterns = ("scripts/**/*.zs",)

    def wants(self, callee: str | None) -> bool:
        return zs_wants(callee)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_scripts.py -v`
Expected: 20 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/handlers/scripts.py tests/test_scripts.py
git commit -m "feat: KubeJS 与 CraftTweaker 脚本处理器"
```

---

### Task 3: 配置文本处理器（用户规则）

**Files:**
- Create: `src/mctrans/handlers/config_text.py`
- Test: `tests/test_config_text.py`

**Interfaces:**
- Consumes: `config.home_dir`, `applied.read_original`
- Produces:
  - 规则文件：`home_dir() / "config_rules.toml"` 与 `<实例>/.mctrans/config_rules.toml`（都存在时合并），格式：

    ```toml
    [[rule]]
    glob = "config/loadingtips/*.txt"
    pattern = '^tip=(?P<text>.+)$'
    ```

    `pattern` 以多行模式编译，必须包含命名组 `text`
  - `config_text.ConfigRule(glob: str, pattern: re.Pattern)`
  - `config_text.load_rules(root: Path) -> tuple[list[ConfigRule], list[str]]`（第二项为警告：TOML 错误、缺字段、正则错误、缺 `text` 组）
  - `config_text.ConfigTextHandler`（`name="config_text"`、`output="instance"`）；没有规则时 `detect` 返回 False
  - 条目：`source=<相对路径>`，`locator=<规则序号>:<匹配序号>`，`namespace="config"`，`context=<文件名>`；原文按原样提取（不做转义处理）
  - 写出：按原始文件重新匹配，替换 `text` 组区间（原文一致时）

- [ ] **Step 1: 写失败测试 `tests/test_config_text.py`**

```python
from mctrans.handlers.base import OutputSink
from mctrans.handlers.config_text import ConfigTextHandler, load_rules
from mctrans.instance import load_instance
from mctrans.models import Entry, TranslatedItem

TIPS = "# tips\ntip=Use a §aWrench§r to rotate.\ntip=Press F3 for debug info.\nother=ignored\n"
RULES = """
[[rule]]
glob = "config/tips/*.txt"
pattern = '^tip=(?P<text>.+)$'
"""


def setup(instance_dir, mctrans_home, rules=RULES):
    (instance_dir / "config" / "tips").mkdir(parents=True)
    (instance_dir / "config" / "tips" / "tips.txt").write_bytes(TIPS.encode("utf-8"))
    mctrans_home.mkdir(parents=True, exist_ok=True)
    (mctrans_home / "config_rules.toml").write_text(rules, encoding="utf-8")


def test_load_rules_merges_instance_rules(instance_dir, mctrans_home):
    setup(instance_dir, mctrans_home)
    (instance_dir / ".mctrans").mkdir()
    (instance_dir / ".mctrans" / "config_rules.toml").write_text(
        '[[rule]]\nglob = "config/a.json"\npattern = \'"title": "(?P<text>[^"]+)"\'\n', encoding="utf-8")
    rules, warnings = load_rules(instance_dir)
    assert [r.glob for r in rules] == ["config/tips/*.txt", "config/a.json"] and warnings == []


def test_load_rules_reports_bad_rules(instance_dir, mctrans_home):
    setup(instance_dir, mctrans_home, rules='[[rule]]\nglob = "x"\npattern = "no group"\n[[rule]]\nglob = "y"\npattern = "(?P<text>["\n')
    rules, warnings = load_rules(instance_dir)
    assert rules == [] and len(warnings) == 2


def test_extract_and_write(instance_dir, mctrans_home):
    setup(instance_dir, mctrans_home)
    h = ConfigTextHandler()
    inst = load_instance(instance_dir)
    assert h.detect(inst)
    entries = list(h.extract(inst))
    assert [(e.source, e.locator, e.original) for e in entries] == [
        ("config/tips/tips.txt", "0:0", "Use a §aWrench§r to rotate."),
        ("config/tips/tips.txt", "0:1", "Press F3 for debug info."),
    ]
    sink = OutputSink(instance_dir)
    h.write([TranslatedItem(entries[1], "按 F3 查看调试信息。", "llm", "done")], sink)
    assert sink.instance_files["config/tips/tips.txt"].decode("utf-8") == TIPS.replace(
        "Press F3 for debug info.", "按 F3 查看调试信息。")


def test_no_rules_no_detect(instance_dir):
    assert not ConfigTextHandler().detect(load_instance(instance_dir))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_config_text.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 `src/mctrans/handlers/config_text.py`**

```python
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..applied import read_original
from ..config import home_dir
from ..instance import Instance
from ..models import Entry, TranslatedItem
from .base import Handler, OutputSink

RULES_FILE = "config_rules.toml"


@dataclass
class ConfigRule:
    glob: str
    pattern: re.Pattern


def _rule_files(root: Path) -> list[Path]:
    return [p for p in (home_dir() / RULES_FILE, Path(root) / ".mctrans" / RULES_FILE) if p.is_file()]


def load_rules(root: Path) -> tuple[list[ConfigRule], list[str]]:
    rules: list[ConfigRule] = []
    warnings: list[str] = []
    for path in _rule_files(root):
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            warnings.append(f"规则文件格式错误，已跳过：{path}（{exc}）")
            continue
        for i, raw in enumerate(data.get("rule", [])):
            glob, pattern = raw.get("glob"), raw.get("pattern")
            if not isinstance(glob, str) or not isinstance(pattern, str):
                warnings.append(f"{path} 第 {i + 1} 条规则缺少 glob 或 pattern")
                continue
            try:
                compiled = re.compile(pattern, re.MULTILINE)
            except re.error as exc:
                warnings.append(f"{path} 第 {i + 1} 条规则的正则有误：{exc}")
                continue
            if "text" not in compiled.groupindex:
                warnings.append(f"{path} 第 {i + 1} 条规则的正则缺少命名组 (?P<text>...)")
                continue
            rules.append(ConfigRule(glob, compiled))
    return rules, warnings


def _matches(root: Path, rules: list[ConfigRule]) -> dict[str, list[tuple[str, re.Match]]]:
    """返回 {相对路径: [(locator, match), ...]}，基于 apply 之前的原始内容。"""
    out: dict[str, list[tuple[str, re.Match]]] = {}
    for ri, rule in enumerate(rules):
        for path in sorted(Path(root).glob(rule.glob)):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            data = read_original(root, rel)
            if data is None:
                continue
            text = data.decode("utf-8", errors="replace")
            for mi, m in enumerate(rule.pattern.finditer(text)):
                out.setdefault(rel, []).append((f"{ri}:{mi}", m))
    return out


class ConfigTextHandler(Handler):
    name = "config_text"
    kind_label = "配置文件中的展示文本（按用户规则提取）"
    output = "instance"

    def detect(self, instance: Instance) -> bool:
        return bool(load_rules(instance.root)[0])

    def extract(self, instance: Instance) -> Iterator[Entry]:
        rules, self.warnings = load_rules(instance.root)
        for rel, found in _matches(instance.root, rules).items():
            name = rel.rsplit("/", 1)[-1]
            for locator, m in found:
                text = m.group("text")
                if text and text.strip():
                    yield Entry(handler=self.name, source=rel, locator=locator, namespace="config",
                                original=text, context=name)

    def write(self, items: list[TranslatedItem], sink: OutputSink) -> None:
        if sink.root is None:
            raise ValueError("配置文本处理器写出时需要实例根目录")
        rules, _ = load_rules(sink.root)
        found = _matches(sink.root, rules)
        wanted: dict[str, dict[str, TranslatedItem]] = {}
        for it in items:
            wanted.setdefault(it.entry.source, {})[it.entry.locator] = it
        for rel, by_locator in sorted(wanted.items()):
            data = read_original(sink.root, rel)
            if data is None or rel not in found:
                continue
            text = data.decode("utf-8", errors="replace")
            replacements = []
            for locator, m in found[rel]:
                it = by_locator.get(locator)
                if it is not None and m.group("text") == it.entry.original and it.text != it.entry.original:
                    replacements.append((m.start("text"), m.end("text"), it.text))
            if not replacements:
                continue
            for start, end, value in sorted(replacements, reverse=True):
                text = text[:start] + value + text[end:]
            sink.add_instance_file(rel, text.encode("utf-8"))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_config_text.py -v`
Expected: 4 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/handlers/config_text.py tests/test_config_text.py
git commit -m "feat: 配置文本处理器（用户自定义规则）"
```

---

### Task 4: 注册处理器、端到端测试与文档

**Files:**
- Modify: `src/mctrans/handlers/__init__.py`, `src/mctrans/cli.py`, `tests/test_lang_json.py`, `tests/test_cli.py`, `README.md`

**Interfaces:**
- Produces: `all_handlers()` 返回 `[LangJsonHandler(), FtbQuestsHandler(), PatchouliHandler(), KubeJsHandler(), CraftTweakerHandler(), ConfigTextHandler()]`

- [ ] **Step 1: 修改测试**

`tests/test_lang_json.py` 中注册表断言改为：

```python
    assert [h.name for h in all_handlers()] == [
        "lang_json", "ftbquests", "patchouli", "kubejs", "crafttweaker", "config_text"]
```

在 `tests/test_cli.py` 末尾追加：

```python
def test_scripts_end_to_end(pack, mctrans_home):
    root, fake = pack
    js = root / "kubejs" / "client_scripts" / "tooltips.js"
    js.parent.mkdir(parents=True)
    original_js = "ItemEvents.modifyTooltips(e => {\n  e.add('a:b', [Text.of('Mining Dimension Disabled')])\n})\n"
    js.write_bytes(original_js.encode("utf-8"))
    (root / "config" / "tips").mkdir(parents=True)
    (root / "config" / "tips" / "t.txt").write_bytes(b"tip=Press F3\n")
    mctrans_home.mkdir(parents=True, exist_ok=True)
    (mctrans_home / "config_rules.toml").write_text(
        "[[rule]]\nglob = \"config/tips/*.txt\"\npattern = '^tip=(?P<text>.+)$'\n", encoding="utf-8")
    invoke("run", root, "--yes")
    assert js.read_text(encoding="utf-8") == original_js.replace("'Mining Dimension Disabled'", "'译Mining Dimension Disabled'")
    assert (root / "config" / "tips" / "t.txt").read_bytes() == "tip=译Press F3\n".encode("utf-8")
    invoke("restore", root)
    assert js.read_bytes() == original_js.encode("utf-8")
    assert (root / "config" / "tips" / "t.txt").read_bytes() == b"tip=Press F3\n"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_lang_json.py tests/test_cli.py -v`
Expected: FAIL（注册表断言、脚本未被翻译）

- [ ] **Step 3: 修改 `src/mctrans/handlers/__init__.py`**

```python
from __future__ import annotations

from .base import Handler, OutputSink
from .config_text import ConfigTextHandler
from .ftbquests import FtbQuestsHandler
from .lang_json import LangJsonHandler
from .patchouli import PatchouliHandler
from .scripts import CraftTweakerHandler, KubeJsHandler

__all__ = ["Handler", "OutputSink", "all_handlers", "get_handler"]


def all_handlers() -> list[Handler]:
    return [LangJsonHandler(), FtbQuestsHandler(), PatchouliHandler(),
            KubeJsHandler(), CraftTweakerHandler(), ConfigTextHandler()]


def get_handler(name: str) -> Handler:
    for handler in all_handlers():
        if handler.name == name:
            return handler
    raise KeyError(f"未知的处理器：{name}")
```

- [ ] **Step 4: 修改 `src/mctrans/cli.py` 中 `OnlyOpt` 的帮助文本**

```python
OnlyOpt = Annotated[Optional[list[str]], typer.Option(
    "--only", help="只翻译指定类别，可重复：lang_json / ftbquests / patchouli / kubejs / crafttweaker / config_text")]
```

- [ ] **Step 5: 更新 `README.md`**

开头一句改为：

```markdown
把英文整合包（已安装的游戏实例目录）自动翻译成简体中文。当前支持 mod 语言文件、FTB Quests 任务书、Patchouli 手册、KubeJS 与 CraftTweaker 脚本中的提示文本，以及按规则指定的配置文本，MC 1.16 及以上。
```

在"### 实例目录"之前加入：

````markdown
### 脚本与配置文本

- **KubeJS**：翻译 `kubejs/*_scripts/` 中 `Text.of(...)`、`Text.red(...)` 等、`.displayName(...)`、`.tooltip(...)` 里的文字；`Text.translatable(...)` 与资源 ID 不会被改动
- **CraftTweaker**：翻译 `scripts/*.zs` 中 `format.xxx(...)`、`.addTooltip(...)`、`.addShiftTooltip(...)`、`.displayName = ...` 等处的文字
- **配置文本**：默认不处理。需要时在 `~/.mctrans/config_rules.toml`（全局）或 `<实例目录>/.mctrans/config_rules.toml`（单个整合包）中添加规则：

```toml
[[rule]]
glob = "config/loadingtips/*.txt"      # 相对实例目录的文件匹配
pattern = '^tip=(?P<text>.+)$'          # 多行正则，命名组 text 是要翻译的部分
```

以上文件修改前都会自动备份，可以用 `restore` 还原。
````

- [ ] **Step 6: 运行全部测试**

Run: `uv run pytest -q`
Expected: 全部通过

- [ ] **Step 7: 提交**

```bash
git add src/mctrans/handlers/__init__.py src/mctrans/cli.py tests/test_lang_json.py tests/test_cli.py README.md
git commit -m "feat: 注册脚本与配置文本处理器，补充说明文档"
```

---

### Task 5: 真实整合包验证

- [ ] **Step 1: 只读扫描与预估**

```
uv run mctrans scan "D:\我的世界\KCL\.minecraft\versions\All the Mods 10 8.6"
uv run mctrans translate "<同上>" --dry-run --only kubejs
uv run mctrans scan "D:\我的世界\KCL\.minecraft\versions\RLCraft 2.9.3" --no-cfpa
uv run mctrans translate "<同上>" --dry-run --only crafttweaker
```

记录 KubeJS / CraftTweaker 条目数与警告，抽查若干条原文确认都是展示文本（没有 ID、配方名等）。

- [ ] **Step 2: 在副本上验证写出**

复制 ATM10 的 `kubejs/` 与版本 json 到 scratchpad 下的新实例，执行 `scan`、`translate --only kubejs --limit 30 --yes`、`apply`。用 `scriptlex.scan` 对比修改前后：除被翻译字符串外，所有其他字符串与非字符串内容完全一致；随后 `restore` 恢复。

- [ ] **Step 3: 记录发现的问题，修复并补测试后提交**
