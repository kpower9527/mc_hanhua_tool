# 计划 3：FTB Quests 任务书与 Patchouli 手册 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 汉化 FTB Quests 任务书（新版语言文件与旧版内嵌文本两种格式）和 Patchouli 手册（jar 内与 KubeJS 资源目录中的 `assets/` 手册）。

**Architecture:** 新增与 FTB Library 行为一致的 SNBT 解析器（记录字符串位置，支持定点替换与生成语言文件）。FTB Quests 处理器直接写实例内文件（新版生成 `lang/zh_cn.snbt`，旧版定点替换章节文件）；Patchouli 处理器按文件生成 `zh_cn/` 下的 JSON 写入资源包。扫描时通过 apply 清单读取文件被修改前的原始内容，避免把自己写出的译文当作整合包自带中文。

**Tech Stack:** 同计划 1、2，无新增依赖。

**Spec:** `docs/superpowers/specs/2026-11-21-mc-modpack-translator-design.md`（第 6.2、6.3 节）

## 实现前验证结论（2026-11-21 调研）

**FTB Quests（1.21.1，2101.x）**
- 1.20.1 及更早版本没有翻译系统，文本直接写在 `config/ftbquests/quests/chapters/*.snbt` 等文件里
- 1.21.1 的 `TranslationManager` 只加载 `config/ftbquests/quests/lang/<locale>.snbt`（单文件，键值对，值为字符串或字符串列表）；某语言缺少的键会回退到默认语言（`en_us`）
- 常见附属 mod **FTB Quests Lang Splitter** 会改写加载逻辑：先读 `<locale>.snbt`，再把 `lang/<locale>/**/*.snbt`（排除 `recovery` 目录）合并进去，拆分文件的值优先；合并后拆分文件被改名为 `.snbt_merged`，结果写回 `<locale>.snbt`
- 实测 ATM10 8.6：只有 `lang/<locale>/` 目录形式，含 `zh_cn`（en_us 9236 个键，zh_cn 缺 1057 个）；ATM10SKY 2.4.0：`en_us.snbt` 与 `en_us/` 目录并存，没有 zh_cn
- FTB Library SNBT 的字符串转义只认 `\" \\ \t \b \n \r \f`，**未知转义会连同反斜杠一起丢弃**；写出时也只转义这几个字符
- 任务文本大量使用紧贴单词的 `&` 颜色码（如 `Plough&r`）以及 `{@pagebreak}`、`{image:...}` 等标记

**Patchouli（1.20.1 / 1.21.x）**
- 手册内容（`assets/<ns>/patchouli_books/<book>/<lang>/...`）通过客户端资源管理器加载，资源包可以覆盖
- 每个文件先找 `zh_cn` 同路径文件，找不到再用 `en_us`（逐文件回退）
- 实测：ATM10 有 30 本手册、ATM10SKY 24 本，几乎都在 `assets/` 下；少数在 `data/` 下（资源包无法覆盖，跳过并提示）

**设计决定**
1. FTB Quests 新版格式：读取时合并单文件和未合并的拆分文件（与 Lang Splitter 一致），写出统一的 `lang/zh_cn.snbt` 单文件（无论是否安装 Lang Splitter 都会被加载）。输出内容 = 原有中文 + 新译文
2. FTB Quests 旧版格式：解析章节等文件，只定点替换文本字段的字符串，文件其余内容保持字节级一致
3. Patchouli：只处理 jar 中没有对应 `zh_cn` 文件的 `en_us` 条目/分类文件（有的视为 mod 自带）；输出为整文件，因此在配合模式下全部放入补全包（位于 CFPA 包之下，CFPA 有该文件时以 CFPA 为准）
4. 扫描读取实例内文件时一律经过 `read_original`：如果文件被 apply 修改过，读备份；如果是 apply 新建的，视为不存在

## Global Constraints

- 计划 1、2 的全局约束继续有效
- FTB Quests 条目：`handler="ftbquests"`，`namespace="ftbquests"`；处理器 `output="instance"`
- Patchouli 条目：`handler="patchouli"`，`namespace=<手册所属命名空间>`；处理器 `output="pack"`、`whole_file=True`
- 实例内路径一律使用相对实例根目录的 POSIX 风格路径（`config/ftbquests/quests/...`）
- 测试不得读取真实整合包；真实整合包只在 Task 8 中只读使用，或复制到 scratchpad 后再写入

## 文件结构

```
src/mctrans/
├── snbt.py                 # 新增：SNBT 解析（带位置）、转义、生成语言文件、定点替换
├── applied.py              # 新增：pointer_path、read_original（读取 apply 之前的原始文件）
├── handlers/
│   ├── base.py             # 修改：OutputSink(root)、Handler.whole_file
│   ├── __init__.py         # 修改：注册 ftbquests、patchouli
│   ├── ftbquests.py        # 新增
│   └── patchouli.py        # 新增
├── translate/protect.py    # 修改：& 颜色码与花括号标记规则
├── translate/engine.py     # 修改：select_entries、run_translation(entries=...)
├── output/apply.py         # 修改：整文件处理器分配、OutputSink 带 root、使用 applied.pointer_path
└── cli.py                  # 修改：translate/run 增加 --only、--limit
```

---

### Task 1: SNBT 解析与生成

**Files:**
- Create: `src/mctrans/snbt.py`
- Test: `tests/test_snbt.py`

**Interfaces:**
- Produces:
  - `snbt.SNBTError(ValueError)`
  - `snbt.SString(value: str, start: int, end: int)`（`start`/`end` 为包含引号的字符区间）
  - `snbt.Scalar(raw: str)`（数字、布尔、无引号字符串等）
  - `snbt.parse(text: str) -> dict | list | SString | Scalar`（复合标签为 `dict[str, 节点]`，列表为 `list[节点]`）
  - `snbt.quote(value: str) -> str`（按 FTB 规则加引号并转义）
  - `snbt.to_lang_value(node) -> str | list[str] | None`（字符串节点或全为字符串的列表转为 Python 值，其他返回 None）
  - `snbt.dump_lang(table: dict[str, str | list[str]]) -> str`（生成 FTB 风格的语言文件文本，制表符缩进）
  - `snbt.replace_spans(text: str, replacements: list[tuple[int, int, str]]) -> str`（把区间替换为 `quote(新值)`）

- [ ] **Step 1: 写失败测试 `tests/test_snbt.py`**

```python
import pytest

from mctrans.snbt import Scalar, SNBTError, SString, dump_lang, parse, quote, replace_spans, to_lang_value

SAMPLE = r'''{
	default_quest_shape: "gear"
	filename: "create"
	id: "100C477F4E63F20A"
	order_index: 3
	x: -6.0d
	enabled: true
	ints: [I; 1, 2, 3]
	quests: [
		{
			title: "&6Mechanical Plough"
			description: [
				"The &6Plough&r will plough!"
				""
				"Say \"hi\"\\n\\nok"
			]
			"quoted key": 'single \'q\''
		}
	]
}
'''


def test_parse_structure():
    tree = parse(SAMPLE)
    assert isinstance(tree, dict)
    assert tree["filename"].value == "create"
    assert isinstance(tree["order_index"], Scalar) and tree["order_index"].raw == "3"
    assert tree["x"].raw == "-6.0d" and tree["enabled"].raw == "true"
    assert [s.raw for s in tree["ints"]] == ["1", "2", "3"]
    quest = tree["quests"][0]
    assert quest["title"].value == "&6Mechanical Plough"
    assert [s.value for s in quest["description"]] == ["The &6Plough&r will plough!", "", 'Say "hi"\\n\\nok']
    # FTB 规则：单引号字符串里的 \' 属于未知转义，连同反斜杠一起被丢弃
    assert quest["quoted key"].value == "single q"


def test_spans_point_at_literals():
    tree = parse(SAMPLE)
    s = tree["quests"][0]["title"]
    assert SAMPLE[s.start:s.end] == '"&6Mechanical Plough"'


def test_commas_and_compact_syntax():
    tree = parse('{a:"x",b:[ "y" , "z" ],c:{d:1b}}')
    assert tree["a"].value == "x" and [n.value for n in tree["b"]] == ["y", "z"]
    assert tree["c"]["d"].raw == "1b"


@pytest.mark.parametrize("bad", ['{a: "unterminated}', '{a: "x"', '{a "x"}', '{a: "line\nbreak"}', '{} trailing'])
def test_parse_errors(bad):
    with pytest.raises(SNBTError):
        parse(bad)


def test_quote_escapes_like_ftb():
    assert quote('a"b\\c') == r'"a\"b\\c"'
    assert quote("tab\there\nnl") == r'"tab\there\nnl"'
    assert quote("it's") == '"it\'s"'


def test_quote_round_trip():
    for value in ['plain', 'with "quotes"', 'back\\slash', 'lit\\n', 'real\nnewline', '中文 &6颜色&r']:
        assert parse("{k: " + quote(value) + "}")["k"].value == value


def test_to_lang_value():
    tree = parse('{a: "x", b: ["1", "2"], c: 5, d: [1, 2], e: []}')
    assert to_lang_value(tree["a"]) == "x"
    assert to_lang_value(tree["b"]) == ["1", "2"]
    assert to_lang_value(tree["c"]) is None
    assert to_lang_value(tree["d"]) is None
    assert to_lang_value(tree["e"]) == []


def test_dump_lang_round_trip():
    table = {"quest.0A.title": "标题", "quest.0A.quest_desc": ["第一行", "", 'say "hi"'], "weird key": "v", "empty": []}
    text = dump_lang(table)
    assert text.startswith("{\n\tquest.0A.title: \"标题\"\n")
    tree = parse(text)
    assert {k: to_lang_value(v) for k, v in tree.items()} == table


def test_replace_spans_keeps_everything_else():
    tree = parse(SAMPLE)
    quest = tree["quests"][0]
    t, d0 = quest["title"], quest["description"][0]
    out = replace_spans(SAMPLE, [(t.start, t.end, "&6动力犁"), (d0.start, d0.end, "这个&6犁&r会犁地！")])
    assert '"&6动力犁"' in out and '"这个&6犁&r会犁地！"' in out
    assert out.replace('"&6动力犁"', '"&6Mechanical Plough"').replace(
        '"这个&6犁&r会犁地！"', '"The &6Plough&r will plough!"') == SAMPLE
    assert replace_spans(SAMPLE, []) == SAMPLE
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_snbt.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'mctrans.snbt'`

- [ ] **Step 3: 实现 `src/mctrans/snbt.py`**

```python
"""FTB Library 风格 SNBT 的解析与生成。

解析结果记录每个字符串字面量在原文中的位置，便于只替换文本、保持文件其余部分不变。
转义规则与 FTB Library 的 SNBTParser 一致：只认 \\" \\\\ \\t \\b \\n \\r \\f，未知转义连同反斜杠一起丢弃。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

DECODE = {'"': '"', "\\": "\\", "t": "\t", "b": "\b", "n": "\n", "r": "\r", "f": "\f"}
ENCODE = {'"': '"', "\\": "\\", "\t": "t", "\b": "b", "\n": "n", "\r": "r", "\f": "f"}
BARE_STOP = set(" \t\r\n,:{}[]")
SIMPLE_KEY_RE = re.compile(r"^[A-Za-z0-9._+-]+$")


class SNBTError(ValueError):
    pass


@dataclass
class SString:
    value: str
    start: int
    end: int


@dataclass
class Scalar:
    raw: str


class _Parser:
    def __init__(self, text: str):
        self.s = text
        self.i = 0
        self.n = len(text)

    def error(self, message: str) -> SNBTError:
        line = self.s.count("\n", 0, self.i) + 1
        return SNBTError(f"SNBT 格式错误（第 {line} 行）：{message}")

    def skip(self) -> None:
        while self.i < self.n and (self.s[self.i].isspace() or self.s[self.i] == ","):
            self.i += 1

    def peek(self) -> str:
        if self.i >= self.n:
            raise self.error("意外的文件结尾")
        return self.s[self.i]

    def parse(self):
        self.skip()
        value = self.value()
        self.skip()
        if self.i != self.n:
            raise self.error("多余的内容")
        return value

    def value(self):
        c = self.peek()
        if c == "{":
            return self.compound()
        if c == "[":
            return self.list()
        if c in "\"'":
            return self.string()
        return Scalar(self.bare())

    def compound(self) -> dict:
        self.i += 1
        out: dict = {}
        while True:
            self.skip()
            if self.peek() == "}":
                self.i += 1
                return out
            key = self.string().value if self.peek() in "\"'" else self.bare()
            self.skip()
            if self.peek() != ":":
                raise self.error(f"键 {key} 后缺少冒号")
            self.i += 1
            self.skip()
            out[key] = self.value()

    def list(self) -> list:
        self.i += 1
        self.skip()
        if self.i + 1 < self.n and self.s[self.i] in "BILbil" and self.s[self.i + 1] == ";":
            self.i += 2
        out: list = []
        while True:
            self.skip()
            if self.peek() == "]":
                self.i += 1
                return out
            out.append(self.value())

    def string(self) -> SString:
        quote_char = self.s[self.i]
        start = self.i
        self.i += 1
        buf: list[str] = []
        while True:
            if self.i >= self.n:
                raise self.error("字符串没有闭合")
            c = self.s[self.i]
            self.i += 1
            if c == "\n":
                raise self.error("字符串中出现换行")
            if c == "\\":
                if self.i >= self.n:
                    raise self.error("字符串没有闭合")
                e = self.s[self.i]
                self.i += 1
                if e in DECODE:
                    buf.append(DECODE[e])
                continue
            if c == quote_char:
                return SString("".join(buf), start, self.i)
            buf.append(c)

    def bare(self) -> str:
        start = self.i
        while self.i < self.n and self.s[self.i] not in BARE_STOP:
            self.i += 1
        if self.i == start:
            raise self.error(f"无法识别的字符 {self.s[self.i]!r}" if self.i < self.n else "意外的文件结尾")
        return self.s[start:self.i]


def parse(text: str):
    return _Parser(text).parse()


def quote(value: str) -> str:
    return '"' + "".join("\\" + ENCODE[ch] if ch in ENCODE else ch for ch in value) + '"'


def to_lang_value(node) -> str | list[str] | None:
    if isinstance(node, SString):
        return node.value
    if isinstance(node, list) and all(isinstance(x, SString) for x in node):
        return [x.value for x in node]
    return None


def dump_lang(table: dict[str, str | list[str]]) -> str:
    lines = ["{"]
    for key, value in table.items():
        k = key if SIMPLE_KEY_RE.match(key) else quote(key)
        if isinstance(value, list):
            if not value:
                lines.append(f"\t{k}: []")
            else:
                lines.append(f"\t{k}: [")
                lines.extend(f"\t\t{quote(v)}" for v in value)
                lines.append("\t]")
        else:
            lines.append(f"\t{k}: {quote(value)}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def replace_spans(text: str, replacements: list[tuple[int, int, str]]) -> str:
    out = text
    for start, end, value in sorted(replacements, key=lambda r: r[0], reverse=True):
        out = out[:start] + quote(value) + out[end:]
    return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_snbt.py -v`
Expected: 13 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/snbt.py tests/test_snbt.py
git commit -m "feat: FTB 风格 SNBT 解析与生成"
```

---

### Task 2: 基础设施（原始文件读取、整文件输出、格式码规则）

**Files:**
- Create: `src/mctrans/applied.py`
- Modify: `src/mctrans/handlers/base.py`, `src/mctrans/output/apply.py`, `src/mctrans/translate/protect.py`
- Test: `tests/test_applied.py`，追加到 `tests/test_protect.py`、`tests/test_apply.py`

**Interfaces:**
- Produces:
  - `applied.pointer_path(root: Path) -> Path`（`<root>/.mctrans/applied.json`）
  - `applied.read_original(root: Path, rel: str) -> bytes | None`：文件被 apply 修改过则返回备份内容；是 apply 新建的则返回 None；否则返回当前内容（不存在为 None）
  - `OutputSink(root: Path | None = None)`，新增属性 `root`
  - `Handler.whole_file: bool = False`：为 True 的资源包处理器一次性拿到所有非 `mod_builtin` 条目，独立模式写入主资源包，配合模式写入补全包
  - `apply.build_outputs(ws, mode, root: Path | None = None) -> Outputs`（所有 sink 都带上 `root`）
  - 格式码保护变化：`&` 颜色码改为"后面不是小写字母"即保护（`Plough&r is` 中的 `&r` 被保护，`Rock&roll` 不保护）；花括号标记扩展为任意不含花括号的 `{...}`（覆盖 `{@pagebreak}`、`{image:...}`）

- [ ] **Step 1: 写失败测试**

`tests/test_applied.py`：

```python
import json

from mctrans.applied import pointer_path, read_original


def write_manifest(root, modified):
    run = root / ".mctrans" / "backups" / "r1"
    (run / "files" / "config").mkdir(parents=True)
    (run / "files" / "config" / "a.snbt").write_bytes(b"original a")
    manifest = {"created": [], "modified": modified, "options": {"packs": [], "lang_set": False, "lang_before": None}}
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    pointer_path(root).write_text(json.dumps({"manifest": ".mctrans/backups/r1/manifest.json"}), encoding="utf-8")


def test_read_original_without_apply(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "a.snbt").write_bytes(b"now")
    assert read_original(tmp_path, "config/a.snbt") == b"now"
    assert read_original(tmp_path, "config/missing.snbt") is None


def test_read_original_after_apply(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "a.snbt").write_bytes(b"translated a")
    (tmp_path / "config" / "new.snbt").write_bytes(b"created by apply")
    (tmp_path / "config" / "untouched.snbt").write_bytes(b"same")
    write_manifest(tmp_path, [
        {"path": "config/a.snbt", "backup": ".mctrans/backups/r1/files/config/a.snbt", "hash": "x"},
        {"path": "config/new.snbt", "backup": None, "hash": "y"},
    ])
    assert read_original(tmp_path, "config/a.snbt") == b"original a"
    assert read_original(tmp_path, "config/new.snbt") is None
    assert read_original(tmp_path, "config/untouched.snbt") == b"same"
```

在 `tests/test_protect.py` 末尾追加：

```python
def test_ftb_style_tokens():
    assert protect("The &6Plough&r is &aGreen&r.").tokens == ["&6", "&r", "&a", "&r"]
    assert protect("{@pagebreak}").tokens == ["{@pagebreak}"]
    assert protect("See {image:atm:textures/a.png width:50}").tokens == ["{image:atm:textures/a.png width:50}"]
    assert protect("Rock&roll &amp").tokens == []
```

在 `tests/test_apply.py` 末尾追加：

```python
class WholeFileHandler(Handler):
    """测试用：整文件处理器，记录收到的条目与 sink 的 root。"""
    name = "lang_json"
    kind_label = "x"
    whole_file = True
    seen: list = []

    def detect(self, instance):
        return True

    def extract(self, instance):
        return iter(())

    def write(self, items, sink):
        WholeFileHandler.seen.append((sorted(i.entry.locator for i in items), sink.root))
        sink.add_pack_file("assets/a/whole.json", b"{}")


def test_whole_file_handler_goes_to_fill_pack(instance_dir, mixed_ws, monkeypatch):
    monkeypatch.setattr(apply_mod, "all_handlers", lambda: [WholeFileHandler()])
    WholeFileHandler.seen = []
    report = apply(instance_dir, mixed_ws, "1.20.1", mode=MODE_I18N)
    assert report.packs == {FILL_FILE: 1}
    assert WholeFileHandler.seen == [(["k.cfpa", "k.human", "k.llm", "k.mem"], instance_dir)]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_applied.py tests/test_protect.py tests/test_apply.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'mctrans.applied'` 等）

- [ ] **Step 3: 实现 `src/mctrans/applied.py`**

```python
"""读取 apply 之前的原始文件，避免把自己写出的译文当作整合包自带内容。"""
from __future__ import annotations

import json
from pathlib import Path


def pointer_path(root: Path) -> Path:
    return Path(root) / ".mctrans" / "applied.json"


def _manifest(root: Path) -> dict | None:
    pointer = pointer_path(root)
    if not pointer.is_file():
        return None
    try:
        rel = json.loads(pointer.read_text(encoding="utf-8"))["manifest"]
        return json.loads((Path(root) / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return None


def read_original(root: Path, rel: str) -> bytes | None:
    root = Path(root)
    manifest = _manifest(root)
    if manifest:
        for item in manifest.get("modified", []):
            if item.get("path") == rel:
                backup = item.get("backup")
                return (root / backup).read_bytes() if backup else None
    path = root / rel
    return path.read_bytes() if path.is_file() else None
```

- [ ] **Step 4: 修改 `src/mctrans/handlers/base.py`**

`OutputSink` 的 `__init__` 改为：

```python
    def __init__(self, root: Path | None = None) -> None:
        self.root = root
        self.pack_files: dict[str, bytes] = {}
        self.instance_files: dict[str, bytes] = {}
```

文件头部加入 `from pathlib import Path`。`Handler` 类中 `output` 一行之后加入：

```python
    whole_file: bool = False  # True：输出整文件，同一文件的译文不能拆到不同资源包
```

- [ ] **Step 5: 修改 `src/mctrans/output/apply.py`**

1. 删除 `_pointer` 函数，文件头部加入 `from ..applied import pointer_path`，并把文件中所有 `_pointer(` 替换为 `pointer_path(`。
2. 在 `PACK_ORIGINS` 定义之后加入：

```python
# 整文件处理器的输出位置（配合模式下放在 CFPA 包之下，CFPA 有同名文件时以 CFPA 为准）
WHOLE_FILE_PACK = {MODE_STANDALONE: PACK_FILE, MODE_I18N: FILL_FILE}
```

3. 把 `build_outputs` 替换为：

```python
def build_outputs(ws: Workspace, mode: str, root: Path | None = None) -> Outputs:
    plan = PACK_ORIGINS[mode]
    packs = {name: OutputSink(root) for name in plan}
    instance = OutputSink(root)
    count = 0
    for handler in all_handlers():
        items = ws.translated_items(handler.name)
        if handler.output == "instance" or handler.whole_file:
            chosen = [it for it in items if it.origin not in INSTANCE_EXCLUDED]
            if chosen:
                target = instance if handler.output == "instance" else packs[WHOLE_FILE_PACK[mode]]
                handler.write(chosen, target)
                count += len(chosen)
            continue
        for name, origins in plan.items():
            chosen = [it for it in items if it.origin in origins]
            if chosen:
                handler.write(chosen, packs[name])
                count += len(chosen)
    return Outputs({n: s for n, s in packs.items() if s.pack_files}, instance, count)
```

4. 在 `apply` 函数中把 `outputs = build_outputs(ws, mode)` 改为 `outputs = build_outputs(ws, mode, root)`。

- [ ] **Step 6: 修改 `src/mctrans/translate/protect.py` 的 `PROTECT_RE`**

```python
PROTECT_RE = re.compile("|".join([
    r"§[0-9a-fk-orA-FK-OR]",                       # 颜色/样式码
    r"&[0-9a-fk-or](?![a-z])",                     # & 颜色码（后面紧跟小写字母时视为普通文字，如 Rock&roll）
    r"%(?:\d+\$)?[-#+0,(]*\d*(?:\.\d+)?[a-zA-Z%]", # printf 参数
    r"\{[^{}\n]*\}",                               # {0} {player} {@pagebreak} {image:...}
    r"\$\([^)]*\)",                                # Patchouli 宏
    r"\\n",                                        # 字面量 \n
    r"\n",                                         # 真实换行
]))
```

- [ ] **Step 7: 运行全部测试**

Run: `uv run pytest -q`
Expected: 全部通过（原有的 `test_protect_leaves_plain_text` 中 `Rock&roll` 仍不受保护）

- [ ] **Step 8: 提交**

```bash
git add src/mctrans/applied.py src/mctrans/handlers/base.py src/mctrans/output/apply.py src/mctrans/translate/protect.py tests/test_applied.py tests/test_protect.py tests/test_apply.py
git commit -m "feat: 读取 apply 前原始文件、整文件输出、补充 FTB 格式码保护"
```

---

### Task 3: FTB Quests 处理器（新版语言文件）

**Files:**
- Create: `src/mctrans/handlers/ftbquests.py`
- Test: `tests/test_ftbquests.py`

**Interfaces:**
- Consumes: `snbt.parse/to_lang_value/dump_lang/SNBTError`, `applied.read_original`, `Handler`, `OutputSink(root)`
- Produces:
  - `ftbquests.QUESTS_DIR = "config/ftbquests/quests"`、`LANG_DIR = QUESTS_DIR + "/lang"`、`ZH_FILE = LANG_DIR + "/zh_cn.snbt"`、`LANG_SOURCE = "ftbquests:lang"`
  - `ftbquests.read_table(root: Path, locale: str, warnings: list[str]) -> dict[str, str | list[str]]`：合并 `lang/<locale>.snbt` 与 `lang/<locale>/**/*.snbt`（拆分文件优先；跳过空字符串、空列表；经过 `read_original`）
  - `ftbquests.FtbQuestsHandler`（`name="ftbquests"`、`output="instance"`）
  - 新版条目：`source=LANG_SOURCE`，字符串值 `locator=<键>`，列表值每个非空行 `locator=<键>[<行号>]`；`context=<键>`；`existing_zh` 取 zh_cn 同键（列表要求行数一致，行数不一致时跳过整个键，保留原有中文）
  - 写出：`config/ftbquests/quests/lang/zh_cn.snbt` = 原有中文（`read_original`）合并新译文；列表在原有中文缺失或行数不一致时以英文为底逐行替换；键顺序按英文

- [ ] **Step 1: 写失败测试 `tests/test_ftbquests.py`**

```python
import pytest

from mctrans.applied import pointer_path
from mctrans.handlers.base import OutputSink
from mctrans.handlers.ftbquests import LANG_SOURCE, ZH_FILE, FtbQuestsHandler, read_table
from mctrans.instance import load_instance
from mctrans.models import Entry, TranslatedItem
from mctrans.snbt import parse, to_lang_value

LANG = "config/ftbquests/quests/lang"


def put(root, rel, text):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture
def lang_pack(instance_dir):
    put(instance_dir, f"{LANG}/en_us.snbt", '{\n\tfile.0000.title: "My Pack"\n\tquest.01.title: "Old title"\n}\n')
    put(instance_dir, f"{LANG}/en_us/chapters/create.snbt", "\n".join([
        "{",
        '\tquest.01.title: "&6Mechanical Plough"',
        '\tquest.01.quest_desc: ["The &6Plough&r works." "" "Second line"]',
        '\tquest.02.quest_desc: ["A" "B"]',
        '\timage.03.title: ""',
        "}",
    ]))
    put(instance_dir, f"{LANG}/en_us/chapters/old.snbt_merged", '{\n\tquest.99.title: "ignored"\n}\n')
    put(instance_dir, f"{LANG}/zh_cn/chapter.snbt", '{\n\tfile.0000.title: "我的整合包"\n\tquest.02.quest_desc: ["只有一行"]\n}\n')
    return instance_dir


def extract(root):
    h = FtbQuestsHandler()
    inst = load_instance(root)
    assert h.detect(inst)
    return h, list(h.extract(inst))


def test_read_table_merges_split_files(lang_pack):
    warnings = []
    en = read_table(lang_pack, "en_us", warnings)
    assert en == {
        "file.0000.title": "My Pack",
        "quest.01.title": "&6Mechanical Plough",  # 拆分文件优先
        "quest.01.quest_desc": ["The &6Plough&r works.", "", "Second line"],
        "quest.02.quest_desc": ["A", "B"],
    }
    assert warnings == []


def test_extract_lang_entries(lang_pack):
    h, entries = extract(lang_pack)
    got = [(e.locator, e.original, e.existing_zh) for e in entries]
    assert got == [
        ("file.0000.title", "My Pack", "我的整合包"),
        ("quest.01.title", "&6Mechanical Plough", None),
        ("quest.01.quest_desc[0]", "The &6Plough&r works.", None),
        ("quest.01.quest_desc[2]", "Second line", None),
        # quest.02 已有中文但行数不同：保持原样，不提取
    ]
    assert all(e.source == LANG_SOURCE and e.namespace == "ftbquests" for e in entries)
    assert entries[2].context == "quest.01.quest_desc"


def test_detect_requires_quests_dir(instance_dir):
    assert not FtbQuestsHandler().detect(load_instance(instance_dir))


def test_broken_lang_file_warns(lang_pack):
    put(lang_pack, f"{LANG}/en_us/chapters/broken.snbt", '{\n\tquest.x.title: "oops\n}')
    h, entries = extract(lang_pack)
    assert entries and any("broken.snbt" in w for w in h.warnings)


def item(locator, text, origin="llm"):
    entry = Entry("ftbquests", LANG_SOURCE, locator, "ftbquests", "x")
    return TranslatedItem(entry, text, origin, "done")


def test_write_merges_with_existing_chinese(lang_pack):
    sink = OutputSink(lang_pack)
    FtbQuestsHandler().write([
        item("quest.01.title", "&6动力犁"),
        item("quest.01.quest_desc[0]", "这个&6犁&r能用。"),
    ], sink)
    assert list(sink.instance_files) == [ZH_FILE]
    table = {k: to_lang_value(v) for k, v in parse(sink.instance_files[ZH_FILE].decode("utf-8")).items()}
    assert table == {
        "file.0000.title": "我的整合包",
        "quest.01.title": "&6动力犁",
        "quest.01.quest_desc": ["这个&6犁&r能用。", "", "Second line"],
        "quest.02.quest_desc": ["只有一行"],
    }
    assert list(table)[:2] == ["file.0000.title", "quest.01.title"]  # 按英文顺序


def test_rescan_after_apply_reads_original(lang_pack):
    # 模拟 apply 新建了 zh_cn.snbt：扫描时应视为不存在
    put(lang_pack, ZH_FILE, '{\n\tquest.01.title: "&6动力犁"\n}\n')
    run = lang_pack / ".mctrans" / "backups" / "r1"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(
        '{"created": [], "modified": [{"path": "%s", "backup": null, "hash": "x"}], '
        '"options": {"packs": [], "lang_set": false, "lang_before": null}}' % ZH_FILE, encoding="utf-8")
    pointer_path(lang_pack).write_text('{"manifest": ".mctrans/backups/r1/manifest.json"}', encoding="utf-8")
    _, entries = extract(lang_pack)
    assert next(e for e in entries if e.locator == "quest.01.title").existing_zh is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_ftbquests.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 `src/mctrans/handlers/ftbquests.py`**

```python
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from ..applied import read_original
from ..instance import Instance
from ..models import Entry, TranslatedItem
from ..snbt import SNBTError, dump_lang, parse, to_lang_value
from .base import Handler, OutputSink

QUESTS_DIR = "config/ftbquests/quests"
LANG_DIR = f"{QUESTS_DIR}/lang"
ZH_FILE = f"{LANG_DIR}/zh_cn.snbt"
LANG_SOURCE = "ftbquests:lang"
NAMESPACE = "ftbquests"
LINE_RE = re.compile(r"^(.*)\[(\d+)\]$")


def _parse_file(root: Path, rel: str, warnings: list[str]):
    data = read_original(root, rel)
    if data is None:
        return None
    try:
        return parse(data.decode("utf-8-sig"))
    except (SNBTError, UnicodeDecodeError) as exc:
        warnings.append(f"任务书文件解析失败，已跳过：{rel}（{exc}）")
        return None


def read_table(root: Path, locale: str, warnings: list[str]) -> dict[str, str | list[str]]:
    """合并读取 lang/<locale>.snbt 与 lang/<locale>/**/*.snbt（拆分文件优先，与 Lang Splitter 一致）。"""
    root = Path(root)
    files = [f"{LANG_DIR}/{locale}.snbt"]
    split_dir = root / LANG_DIR / locale
    if split_dir.is_dir():
        files += sorted(p.relative_to(root).as_posix() for p in split_dir.rglob("*.snbt"))
    table: dict[str, str | list[str]] = {}
    for rel in files:
        tree = _parse_file(root, rel, warnings)
        if not isinstance(tree, dict):
            continue
        for key, node in tree.items():
            value = to_lang_value(node)
            if value is None or value == "" or value == []:
                continue
            table[key] = value
    return table


def has_lang_files(root: Path) -> bool:
    lang = Path(root) / LANG_DIR
    return (lang / "en_us.snbt").is_file() or (lang / "en_us").is_dir()


class FtbQuestsHandler(Handler):
    name = "ftbquests"
    kind_label = "FTB Quests 任务书（任务标题、副标题与描述）"
    output = "instance"

    def detect(self, instance: Instance) -> bool:
        return (instance.root / QUESTS_DIR).is_dir()

    def extract(self, instance: Instance) -> Iterator[Entry]:
        self.warnings = []
        if has_lang_files(instance.root):
            yield from self._extract_lang(instance.root)
        else:
            yield from self._extract_inline(instance.root)

    def write(self, items: list[TranslatedItem], sink: OutputSink) -> None:
        if sink.root is None:
            raise ValueError("FTB Quests 处理器写出时需要实例根目录")
        lang_items = [it for it in items if it.entry.source == LANG_SOURCE]
        inline_items = [it for it in items if it.entry.source != LANG_SOURCE]
        if lang_items:
            self._write_lang(lang_items, sink)
        if inline_items:
            self._write_inline(inline_items, sink)

    # ---- 新版：lang/<locale>.snbt ----
    def _extract_lang(self, root: Path) -> Iterator[Entry]:
        en = read_table(root, "en_us", self.warnings)
        zh = read_table(root, "zh_cn", self.warnings)
        for key, value in en.items():
            zh_value = zh.get(key)
            if isinstance(value, str):
                if value.strip():
                    yield self._entry(key, key, value, zh_value if isinstance(zh_value, str) else None)
                continue
            same_shape = isinstance(zh_value, list) and len(zh_value) == len(value)
            if zh_value is not None and not same_shape:
                continue  # 已有中文但结构不同：保持原样
            for i, line in enumerate(value):
                if line.strip():
                    yield self._entry(f"{key}[{i}]", key, line, zh_value[i] if same_shape else None)

    def _entry(self, locator: str, key: str, original: str, existing_zh: str | None) -> Entry:
        return Entry(handler=self.name, source=LANG_SOURCE, locator=locator, namespace=NAMESPACE,
                     original=original, context=key, existing_zh=existing_zh)

    def _write_lang(self, items: list[TranslatedItem], sink: OutputSink) -> None:
        warnings: list[str] = []
        en = read_table(sink.root, "en_us", warnings)
        out: dict[str, str | list[str]] = {
            k: (list(v) if isinstance(v, list) else v) for k, v in read_table(sink.root, "zh_cn", warnings).items()
        }
        for it in items:
            m = LINE_RE.match(it.entry.locator)
            if m is None:
                out[it.entry.locator] = it.text
                continue
            key, index = m.group(1), int(m.group(2))
            english = en.get(key)
            base = out.get(key)
            if not isinstance(english, list):
                continue
            if not isinstance(base, list) or len(base) != len(english):
                base = list(english)
            if index < len(base):
                base[index] = it.text
            out[key] = base
        ordered = {k: out[k] for k in en if k in out}
        ordered.update({k: v for k, v in out.items() if k not in ordered})
        sink.add_instance_file(ZH_FILE, dump_lang(ordered).encode("utf-8"))

    # ---- 旧版：文本内嵌在章节文件中（Task 4 实现）----
    def _extract_inline(self, root: Path) -> Iterator[Entry]:
        return iter(())

    def _write_inline(self, items: list[TranslatedItem], sink: OutputSink) -> None:
        return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_ftbquests.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/handlers/ftbquests.py tests/test_ftbquests.py
git commit -m "feat: FTB Quests 处理器（新版语言文件，兼容 Lang Splitter）"
```

---

### Task 4: FTB Quests 处理器（旧版内嵌文本）

**Files:**
- Modify: `src/mctrans/handlers/ftbquests.py`
- Test: 追加到 `tests/test_ftbquests.py`

**Interfaces:**
- Produces:
  - 旧版条目：`source=<文件相对路径>`（如 `config/ftbquests/quests/chapters/create.snbt`），`locator=<结构路径>`（如 `quests[0].description[1]`、`title`），`context=<文件名>#<结构路径>`
  - 处理的文件：`data.snbt`、`chapter_groups.snbt`、`chapters/*.snbt`、`reward_tables/*.snbt`
  - 处理的字段：`title`、`subtitle`、`description`、`hover`（字符串或字符串列表，任意嵌套层级）；跳过空字符串和形如 `{some.lang.key}` 的翻译键引用
  - 写出：只替换有译文且与原文不同的字符串区间，其余字节不变

- [ ] **Step 1: 在 `tests/test_ftbquests.py` 末尾追加失败测试**

```python
INLINE_CHAPTER = """{
\tfilename: "create"
\ttitle: "Create"
\tquests: [
\t\t{
\t\t\tid: "01"
\t\t\ttitle: "&6Mechanical Plough"
\t\t\tsubtitle: "{atm9.quest.subtitle}"
\t\t\tdescription: [
\t\t\t\t"The &6Plough&r works."
\t\t\t\t""
\t\t\t\t"Second line"
\t\t\t]
\t\t\ttasks: [{ id: "02", type: "item", item: "create:shaft", title: "Get a Shaft" }]
\t\t\tx: 1.0d
\t\t}
\t]
}
"""


@pytest.fixture
def inline_pack(instance_dir):
    q = "config/ftbquests/quests"
    put(instance_dir, f"{q}/chapters/create.snbt", INLINE_CHAPTER)
    put(instance_dir, f"{q}/chapter_groups.snbt", '{\n\tchapter_groups: [{ id: "G1", title: "Tech" }]\n}\n')
    put(instance_dir, f"{q}/data.snbt", '{\n\ttitle: "My Quests"\n\tversion: 13\n}\n')
    return instance_dir


def test_extract_inline(inline_pack):
    _, entries = extract(inline_pack)
    got = [(e.source.rsplit("/", 1)[1], e.locator, e.original) for e in entries]
    assert got == [
        ("data.snbt", "title", "My Quests"),
        ("chapter_groups.snbt", "chapter_groups[0].title", "Tech"),
        ("create.snbt", "title", "Create"),
        ("create.snbt", "quests[0].title", "&6Mechanical Plough"),
        ("create.snbt", "quests[0].description[0]", "The &6Plough&r works."),
        ("create.snbt", "quests[0].description[2]", "Second line"),
        ("create.snbt", "quests[0].tasks[0].title", "Get a Shaft"),
    ]
    assert entries[3].context == "create.snbt#quests[0].title"


def test_write_inline_replaces_only_text(inline_pack):
    src = "config/ftbquests/quests/chapters/create.snbt"

    def it(locator, text):
        return TranslatedItem(Entry("ftbquests", src, locator, "ftbquests", "x"), text, "llm", "done")

    sink = OutputSink(inline_pack)
    FtbQuestsHandler().write([
        it("quests[0].title", "&6动力犁"),
        it("quests[0].description[2]", "第二行"),
        it("title", "Create"),  # 与原文相同，不替换
    ], sink)
    out = sink.instance_files[src].decode("utf-8")
    assert out == INLINE_CHAPTER.replace('"&6Mechanical Plough"', '"&6动力犁"').replace('"Second line"', '"第二行"')


def test_lang_format_takes_precedence_over_inline(inline_pack):
    put(inline_pack, f"{LANG}/en_us.snbt", '{\n\tquest.01.title: "From lang"\n}\n')
    _, entries = extract(inline_pack)
    assert [e.source for e in entries] == [LANG_SOURCE]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_ftbquests.py -v`
Expected: FAIL（新增的 3 个测试中前两个失败：旧版提取返回空）

- [ ] **Step 3: 修改 `src/mctrans/handlers/ftbquests.py`**

在文件头部导入中加入 `SString` 与 `replace_spans`：

```python
from ..snbt import SNBTError, SString, dump_lang, parse, replace_spans, to_lang_value
```

在 `LINE_RE` 之后加入：

```python
INLINE_KEYS = frozenset({"title", "subtitle", "description", "hover"})
LANG_KEY_RE = re.compile(r"^\{[A-Za-z0-9_.:-]+\}$")


def inline_files(root: Path) -> list[str]:
    quests = Path(root) / QUESTS_DIR
    files = [quests / "data.snbt", quests / "chapter_groups.snbt"]
    files += sorted((quests / "chapters").glob("*.snbt"))
    files += sorted((quests / "reward_tables").glob("*.snbt"))
    return [p.relative_to(root).as_posix() for p in files if p.is_file()]


def walk_texts(node, path: str = "") -> Iterator[tuple[str, SString]]:
    """遍历 SNBT 树，产出 (结构路径, 字符串节点)，只包含文本字段。"""
    if isinstance(node, dict):
        for key, value in node.items():
            p = f"{path}.{key}" if path else key
            if key in INLINE_KEYS:
                if isinstance(value, SString):
                    yield p, value
                elif isinstance(value, list):
                    for i, x in enumerate(value):
                        if isinstance(x, SString):
                            yield f"{p}[{i}]", x
            else:
                yield from walk_texts(value, p)
    elif isinstance(node, list):
        for i, x in enumerate(node):
            yield from walk_texts(x, f"{path}[{i}]")
```

把 `_extract_inline` 与 `_write_inline` 替换为：

```python
    def _extract_inline(self, root: Path) -> Iterator[Entry]:
        for rel in inline_files(root):
            tree = _parse_file(root, rel, self.warnings)
            if tree is None:
                continue
            filename = rel.rsplit("/", 1)[-1]
            for path, node in walk_texts(tree):
                text = node.value
                if not text.strip() or LANG_KEY_RE.match(text.strip()):
                    continue
                yield Entry(handler=self.name, source=rel, locator=path, namespace=NAMESPACE,
                            original=text, context=f"{filename}#{path}")

    def _write_inline(self, items: list[TranslatedItem], sink: OutputSink) -> None:
        by_source: dict[str, list[TranslatedItem]] = {}
        for it in items:
            by_source.setdefault(it.entry.source, []).append(it)
        for rel, group in sorted(by_source.items()):
            data = read_original(sink.root, rel)
            if data is None:
                continue
            text = data.decode("utf-8-sig")
            spans = dict(walk_texts(parse(text)))
            replacements = [
                (spans[it.entry.locator].start, spans[it.entry.locator].end, it.text)
                for it in group
                if it.entry.locator in spans and spans[it.entry.locator].value != it.text
            ]
            if replacements:
                sink.add_instance_file(rel, replace_spans(text, replacements).encode("utf-8"))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_ftbquests.py -v`
Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/handlers/ftbquests.py tests/test_ftbquests.py
git commit -m "feat: FTB Quests 处理器（旧版内嵌文本，定点替换）"
```

---

### Task 5: Patchouli 处理器

**Files:**
- Create: `src/mctrans/handlers/patchouli.py`
- Test: `tests/test_patchouli.py`

**Interfaces:**
- Consumes: `Handler`, `OutputSink(root)`, `tests/helpers.make_jar/jar_bytes`
- Produces:
  - `patchouli.PatchouliHandler`（`name="patchouli"`、`output="pack"`、`whole_file=True`）
  - `patchouli.text_fields(obj) -> list[tuple[str, str]]`：提取 `name`、`description`，以及 `pages[i]` 中的 `title`、`text`、`link_text`、`name`；跳过空字符串和形如翻译键的值（无空格、含点，如 `book.mod.entry.name`）
  - `patchouli.set_field(obj, path: str, value: str) -> None`
  - 条目：jar 内 `source="mods/<jar 文件名>!<assets 路径>"`，KubeJS 内 `source="kubejs/assets/<...>"`；`locator=<字段路径>`（如 `pages[1].text`）；`namespace=<手册命名空间>`；`context=<book>/<categories|entries 下相对路径>#<字段路径>`
  - 只处理 `assets/<ns>/patchouli_books/<book>/en_us/(categories|entries)/**/*.json`，且同一来源中不存在对应 `zh_cn` 文件的
  - `data/` 下的手册：每个 jar 只给出一条警告
  - 写出：读取原英文 JSON（`sink.root` + `source`），替换字段，写入资源包 `assets/<ns>/patchouli_books/<book>/zh_cn/<相对路径>`

- [ ] **Step 1: 写失败测试 `tests/test_patchouli.py`**

```python
import json

from helpers import make_jar
from mctrans.handlers.base import OutputSink
from mctrans.handlers.patchouli import PatchouliHandler, set_field, text_fields
from mctrans.instance import load_instance
from mctrans.models import Entry, TranslatedItem

BOOK = "assets/ars/patchouli_books/notebook"
ENTRY = {
    "name": "Spell Basics",
    "icon": "ars:spell_book",
    "category": "ars:spells",
    "pages": [
        {"type": "patchouli:text", "text": "Spells are $(item)powerful$(). Use them wisely."},
        {"type": "patchouli:spotlight", "item": "ars:wand", "title": "The Wand", "text": "A wand."},
        {"type": "patchouli:link", "url": "https://x", "link_text": "Wiki", "text": "book.ars.key"},
        {"type": "patchouli:entity", "entity": "minecraft:cow", "name": "Cow"},
    ],
}
CATEGORY = {"name": "Spells", "description": "All about spells.", "icon": "ars:spell_book"}


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False)


def test_text_fields():
    assert text_fields(ENTRY) == [
        ("name", "Spell Basics"),
        ("pages[0].text", "Spells are $(item)powerful$(). Use them wisely."),
        ("pages[1].title", "The Wand"),
        ("pages[1].text", "A wand."),
        ("pages[2].link_text", "Wiki"),
        ("pages[3].name", "Cow"),
    ]
    assert text_fields({"name": "book.ars.name"}) == []


def test_set_field():
    obj = json.loads(dumps(ENTRY))
    set_field(obj, "pages[1].title", "法杖")
    set_field(obj, "name", "法术基础")
    assert obj["pages"][1]["title"] == "法杖" and obj["name"] == "法术基础"


def make_book(instance_dir):
    return make_jar(instance_dir / "mods" / "ars-1.0.jar", {
        f"{BOOK}/en_us/entries/basics/spells.json": dumps(ENTRY),
        f"{BOOK}/en_us/categories/spells.json": dumps(CATEGORY),
        f"{BOOK}/en_us/entries/done.json": dumps({"name": "Done", "pages": []}),
        f"{BOOK}/zh_cn/entries/done.json": dumps({"name": "完成", "pages": []}),
        "data/ars/patchouli_books/old/en_us/entries/a.json": dumps({"name": "Old"}),
    })


def test_extract(instance_dir):
    make_book(instance_dir)
    h = PatchouliHandler()
    entries = list(h.extract(load_instance(instance_dir)))
    sources = {e.source for e in entries}
    assert sources == {
        f"mods/ars-1.0.jar!{BOOK}/en_us/entries/basics/spells.json",
        f"mods/ars-1.0.jar!{BOOK}/en_us/categories/spells.json",
    }  # done.json 已有中文，跳过
    spell = [e for e in entries if e.source.endswith("spells.json") and "entries" in e.source]
    assert [e.locator for e in spell] == ["name", "pages[0].text", "pages[1].title", "pages[1].text",
                                          "pages[2].link_text", "pages[3].name"]
    assert spell[0].namespace == "ars" and spell[0].context == "notebook/entries/basics/spells.json#name"
    assert len(h.warnings) == 1 and "data" in h.warnings[0]


def test_extract_kubejs_assets(instance_dir):
    d = instance_dir / "kubejs" / "assets" / "kubejs" / "patchouli_books" / "guide" / "en_us" / "entries"
    d.mkdir(parents=True)
    (d / "intro.json").write_text(dumps({"name": "Intro", "pages": [{"type": "patchouli:text", "text": "Hi there"}]}),
                                  encoding="utf-8")
    entries = list(PatchouliHandler().extract(load_instance(instance_dir)))
    assert [(e.source, e.locator) for e in entries] == [
        ("kubejs/assets/kubejs/patchouli_books/guide/en_us/entries/intro.json", "name"),
        ("kubejs/assets/kubejs/patchouli_books/guide/en_us/entries/intro.json", "pages[0].text"),
    ]


def test_write(instance_dir):
    make_book(instance_dir)
    src = f"mods/ars-1.0.jar!{BOOK}/en_us/entries/basics/spells.json"

    def it(locator, text):
        return TranslatedItem(Entry("patchouli", src, locator, "ars", "x"), text, "llm", "done")

    sink = OutputSink(instance_dir)
    PatchouliHandler().write([it("name", "法术基础"), it("pages[1].title", "法杖")], sink)
    out_path = f"{BOOK}/zh_cn/entries/basics/spells.json"
    assert list(sink.pack_files) == [out_path]
    out = json.loads(sink.pack_files[out_path].decode("utf-8"))
    assert out["name"] == "法术基础" and out["pages"][1]["title"] == "法杖"
    assert out["pages"][0]["text"] == ENTRY["pages"][0]["text"]  # 未翻译的保持英文
    assert out["pages"][1]["item"] == "ars:wand"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_patchouli.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 `src/mctrans/handlers/patchouli.py`**

```python
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from typing import Iterator

from ..instance import Instance
from ..models import Entry, TranslatedItem
from .base import Handler, OutputSink

BOOK_FILE_RE = re.compile(
    r"^(?P<prefix>assets/(?P<ns>[^/]+)/patchouli_books/(?P<book>[^/]+))/(?P<lang>[a-z0-9_]+)/"
    r"(?P<rel>(?:categories|entries)/.+\.json)$"
)
DATA_BOOK_RE = re.compile(r"^data/[^/]+/patchouli_books/[^/]+/en_us/")
KEY_LIKE_RE = re.compile(r"^[a-z0-9_:-]+(?:\.[a-z0-9_:-]+)+$")
TOP_FIELDS = ("name", "description")
PAGE_FIELDS = ("title", "text", "link_text", "name")
PATH_PART_RE = re.compile(r"([^.\[\]]+)|\[(\d+)\]")


def text_fields(obj) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if not isinstance(obj, dict):
        return out
    for f in TOP_FIELDS:
        if isinstance(obj.get(f), str):
            out.append((f, obj[f]))
    pages = obj.get("pages")
    if isinstance(pages, list):
        for i, page in enumerate(pages):
            if isinstance(page, dict):
                for f in PAGE_FIELDS:
                    if isinstance(page.get(f), str):
                        out.append((f"pages[{i}].{f}", page[f]))
    return [(p, v) for p, v in out if v.strip() and not KEY_LIKE_RE.match(v.strip())]


def set_field(obj, path: str, value: str) -> None:
    parts = [int(i) if i else k for k, i in PATH_PART_RE.findall(path)]
    target = obj
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value


def _load_json(data: bytes):
    return json.loads(data.decode("utf-8-sig"), strict=False)


class PatchouliHandler(Handler):
    name = "patchouli"
    kind_label = "Patchouli 手册（条目标题与正文）"
    output = "pack"
    whole_file = True

    def detect(self, instance: Instance) -> bool:
        return True

    def extract(self, instance: Instance) -> Iterator[Entry]:
        self.warnings = []
        for jar in instance.mod_jars:
            try:
                z = zipfile.ZipFile(jar)
            except (zipfile.BadZipFile, OSError):
                continue  # 损坏的 jar 已由 lang_json 处理器报告
            with z:
                files = {n: None for n in z.namelist()}
                if any(DATA_BOOK_RE.match(n) for n in files):
                    self.warnings.append(f"{jar.name} 中有放在 data/ 下的 Patchouli 手册，资源包无法覆盖，已跳过")
                yield from self._from_files(files, lambda n: z.read(n), f"mods/{jar.name}!")
        kubejs = instance.root / "kubejs"
        if (kubejs / "assets").is_dir():
            files = {p.relative_to(kubejs).as_posix(): p for p in (kubejs / "assets").rglob("*.json")}
            yield from self._from_files(files, lambda n: files[n].read_bytes(), "kubejs/")

    def _from_files(self, files: dict, read, source_prefix: str) -> Iterator[Entry]:
        for name in sorted(files):
            m = BOOK_FILE_RE.match(name)
            if not m or m.group("lang") != "en_us":
                continue
            if f"{m.group('prefix')}/zh_cn/{m.group('rel')}" in files:
                continue  # mod 自带中文
            try:
                obj = _load_json(read(name))
            except ValueError as exc:
                self.warnings.append(f"手册文件解析失败，已跳过：{source_prefix}{name}（{exc}）")
                continue
            for path, text in text_fields(obj):
                yield Entry(
                    handler=self.name, source=f"{source_prefix}{name}", locator=path,
                    namespace=m.group("ns"), original=text,
                    context=f"{m.group('book')}/{m.group('rel')}#{path}",
                )

    def write(self, items: list[TranslatedItem], sink: OutputSink) -> None:
        if sink.root is None:
            raise ValueError("Patchouli 处理器写出时需要实例根目录")
        by_source: dict[str, list[TranslatedItem]] = {}
        for it in items:
            by_source.setdefault(it.entry.source, []).append(it)
        for source, group in sorted(by_source.items()):
            asset_path, data = self._read_source(sink.root, source)
            if data is None:
                continue
            m = BOOK_FILE_RE.match(asset_path)
            obj = _load_json(data)
            for it in group:
                try:
                    set_field(obj, it.entry.locator, it.text)
                except (KeyError, IndexError, TypeError):
                    continue
            out_path = f"{m.group('prefix')}/zh_cn/{m.group('rel')}"
            sink.add_pack_file(out_path, (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

    @staticmethod
    def _read_source(root: Path, source: str) -> tuple[str, bytes | None]:
        if source.startswith("mods/") and "!" in source:
            jar_rel, inner = source.split("!", 1)
            try:
                with zipfile.ZipFile(root / jar_rel) as z:
                    return inner, z.read(inner)
            except (OSError, KeyError, zipfile.BadZipFile):
                return inner, None
        inner = source[len("kubejs/"):] if source.startswith("kubejs/") else source
        path = root / source
        return inner, path.read_bytes() if path.is_file() else None
```

说明：KubeJS 资源的 `files` 键是相对 `kubejs/` 的路径（以 `assets/` 开头），所以和 jar 内路径用同一个正则匹配。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_patchouli.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/handlers/patchouli.py tests/test_patchouli.py
git commit -m "feat: Patchouli 手册处理器"
```

---

### Task 6: 注册处理器、按类别与数量限制翻译

**Files:**
- Modify: `src/mctrans/handlers/__init__.py`, `src/mctrans/translate/engine.py`, `src/mctrans/cli.py`
- Test: `tests/test_lang_json.py`（注册表断言）, 追加到 `tests/test_engine.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `all_handlers()` 返回 `[LangJsonHandler(), FtbQuestsHandler(), PatchouliHandler()]`
  - `engine.select_entries(entries: list[Entry], only: set[str] | None, limit: int | None) -> list[Entry]`
  - `engine.run_translation(ws, client, glossary, settings, on_progress=None, entries: list[Entry] | None = None)`：`entries` 为 None 时使用 `ws.pending_entries()`
  - CLI `translate` 与 `run` 新增 `--only`（可重复，处理器名）与 `--limit`（最多翻译的条目数）

- [ ] **Step 1: 修改测试**

`tests/test_lang_json.py` 中 `test_registry` 的第一行断言改为：

```python
    assert [h.name for h in all_handlers()] == ["lang_json", "ftbquests", "patchouli"]
```

在 `tests/test_engine.py` 末尾追加：

```python
from mctrans.translate.engine import select_entries


def test_select_entries():
    entries = [e("k1", "A"), Entry("ftbquests", "s", "q1", "ftbquests", "Quest"), e("k2", "B")]
    assert select_entries(entries, None, None) == entries
    assert [x.locator for x in select_entries(entries, {"ftbquests"}, None)] == ["q1"]
    assert [x.locator for x in select_entries(entries, None, 2)] == ["k1", "q1"]


def test_run_translation_with_explicit_entries(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear"), e("k2", "Shaft")])
    subset = ws.pending_entries()[:1]
    report = run_translation(ws, FakeClient(), Glossary(), FAST, entries=subset)
    assert report.translated == 1
    assert [p.locator for p in ws.pending_entries()] == ["k2"]
```

在 `tests/test_cli.py` 末尾追加：

```python
def test_quests_and_patchouli_end_to_end(pack):
    root, fake = pack
    lang_dir = root / "config" / "ftbquests" / "quests" / "lang"
    (lang_dir / "en_us").mkdir(parents=True)
    (lang_dir / "en_us" / "chapter.snbt").write_text(
        '{\n\tchapter.01.title: "Getting Started"\n\tquest.02.quest_desc: ["Craft a &6Gear&r." ""]\n}\n',
        encoding="utf-8")
    (lang_dir / "zh_cn.snbt").write_text('{\n\tchapter.01.title: "新手入门"\n}\n', encoding="utf-8")
    make_jar(root / "mods" / "guide.jar", {
        "assets/guide/patchouli_books/book/en_us/entries/intro.json": json.dumps(
            {"name": "Intro", "pages": [{"type": "patchouli:text", "text": "Welcome to the pack."}]}),
    })
    invoke("scan", root)
    out = invoke("translate", root, "--yes", "--only", "ftbquests", "--limit", "5").output
    assert "完成：翻译 1 条" in out
    invoke("translate", root, "--yes")
    invoke("apply", root)
    zh = (lang_dir / "zh_cn.snbt").read_text(encoding="utf-8")
    assert '"新手入门"' in zh and '"译Craft a &6Gear&r."' in zh
    with zipfile.ZipFile(io.BytesIO((root / "resourcepacks" / "mctrans-zh_cn.zip").read_bytes())) as z:
        page = json.loads(z.read("assets/guide/patchouli_books/book/zh_cn/entries/intro.json"))
    assert page["name"] == "译Intro"
    # apply 之后重新扫描：不能把自己写出的译文当成整合包自带中文
    invoke("scan", root)
    from mctrans.store import Workspace
    with Workspace.for_instance(root) as ws:
        origins = {i.entry.locator: i.origin for i in ws.translated_items("ftbquests")}
    assert origins == {"chapter.01.title": "mod_builtin", "quest.02.quest_desc[0]": "llm"}
    invoke("restore", root)
    assert (lang_dir / "zh_cn.snbt").read_text(encoding="utf-8") == '{\n\tchapter.01.title: "新手入门"\n}\n'
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_lang_json.py tests/test_engine.py tests/test_cli.py -v`
Expected: FAIL（注册表断言、`select_entries` 不存在、CLI 无 `--only` 选项）

- [ ] **Step 3: 修改 `src/mctrans/handlers/__init__.py`**

```python
from __future__ import annotations

from .base import Handler, OutputSink
from .ftbquests import FtbQuestsHandler
from .lang_json import LangJsonHandler
from .patchouli import PatchouliHandler

__all__ = ["Handler", "OutputSink", "all_handlers", "get_handler"]


def all_handlers() -> list[Handler]:
    return [LangJsonHandler(), FtbQuestsHandler(), PatchouliHandler()]


def get_handler(name: str) -> Handler:
    for handler in all_handlers():
        if handler.name == name:
            return handler
    raise KeyError(f"未知的处理器：{name}")
```

- [ ] **Step 4: 修改 `src/mctrans/translate/engine.py`**

在 `make_batches` 之前加入：

```python
def select_entries(entries: list[Entry], only: set[str] | None, limit: int | None) -> list[Entry]:
    """按处理器筛选并限制条目数（用于抽样翻译、控制费用）。"""
    chosen = [e for e in entries if not only or e.handler in only]
    return chosen[:limit] if limit else chosen
```

把 `run_translation` 的签名改为：

```python
def run_translation(ws: Workspace, client: LLMClient, glossary: Glossary, settings: EngineSettings,
                    on_progress: Callable[[int], None] | None = None,
                    entries: list[Entry] | None = None) -> TranslateReport:
```

并把函数体第二行 `groups, skipped = group_units(ws.pending_entries())` 改为：

```python
    groups, skipped = group_units(ws.pending_entries() if entries is None else entries)
```

- [ ] **Step 5: 修改 `src/mctrans/cli.py`**

导入中把 `from .translate.engine import EngineSettings, estimate, run_translation` 改为：

```python
from .translate.engine import EngineSettings, estimate, run_translation, select_entries
```

在 `ModeOpt` 之后加入：

```python
OnlyOpt = Annotated[Optional[list[str]], typer.Option("--only", help="只翻译指定类别，可重复：lang_json / ftbquests / patchouli")]
LimitOpt = Annotated[Optional[int], typer.Option("--limit", help="最多翻译多少条（抽样试翻、控制费用）")]
```

`do_translate` 的签名改为 `def do_translate(root: Path, provider: str | None, dry_run: bool, yes: bool, only: list[str] | None = None, limit: int | None = None) -> None:`，把其中的 `entries = ws.pending_entries()` 改为：

```python
        entries = select_entries(ws.pending_entries(), set(only) if only else None, limit)
```

并把 `run_translation(...)` 调用加上 `entries=entries` 参数：

```python
            report = run_translation(ws, client, glossary, engine_settings,
                                     on_progress=lambda n: progress.advance(task, n), entries=entries)
```

`translate` 命令的参数末尾加上 `only: OnlyOpt = None, limit: LimitOpt = None`，调用改为 `do_translate(instance, provider, dry_run, yes, only, limit)`；`run` 命令同样加上这两个参数，调用改为 `do_translate(instance, provider, dry_run=False, yes=yes, only=only, limit=limit)`。

- [ ] **Step 6: 运行全部测试**

Run: `uv run pytest -q`
Expected: 全部通过

- [ ] **Step 7: 提交**

```bash
git add src/mctrans/handlers/__init__.py src/mctrans/translate/engine.py src/mctrans/cli.py tests/test_lang_json.py tests/test_engine.py tests/test_cli.py
git commit -m "feat: 注册任务书与手册处理器，translate 支持 --only 与 --limit"
```

---

### Task 7: 文档更新

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-11-21-mc-modpack-translator-design.md`

- [ ] **Step 1: 更新 `README.md`**

把开头第一段改为：

```markdown
把英文整合包（已安装的游戏实例目录）自动翻译成简体中文。当前支持 mod 语言文件、FTB Quests 任务书、Patchouli 手册，MC 1.16 及以上。
```

在"### 实例目录"之前加入：

````markdown
### 任务书与手册

- **FTB Quests**：1.21 及以上版本生成 `config/ftbquests/quests/lang/zh_cn.snbt`（保留整合包原有中文，只补缺失的部分，兼容 FTB Quests Lang Splitter）；1.20.1 及更早版本直接替换章节文件里的文本，修改前自动备份
- **Patchouli 手册**：为没有中文的条目生成 `zh_cn` 文件，写入资源包；放在 `data/` 下的旧式手册暂不支持

抽样试翻，先看看效果和费用：

```
uv run mctrans translate <实例目录> --only ftbquests --limit 50
```

注意：装有 Lang Splitter 时，游戏启动后会重写 `zh_cn.snbt`，此时 `restore` 会跳过这个文件（提示"应用后被改动过"），需要时手动删除即可。
````

- [ ] **Step 2: 在设计文档 9.1 节中更新第 2、3 条**

```markdown
2. **Patchouli**：1.20.1 与 1.21.x 均通过客户端资源管理器加载 `assets/` 下的手册内容，资源包可覆盖；每个文件先找当前语言、找不到回退 en_us。`data/` 下的手册无法用资源包覆盖，暂不支持
3. **FTB Quests**：1.20.1 及更早版本文本内嵌在章节文件中；1.21.1 使用 `lang/<locale>.snbt`，缺失键回退 en_us。附属 mod Lang Splitter 额外支持 `lang/<locale>/**/*.snbt` 拆分文件。实现见计划 3
```

- [ ] **Step 3: 提交**

```bash
git add README.md docs/superpowers/specs/2026-11-21-mc-modpack-translator-design.md
git commit -m "docs: 任务书与手册的使用说明，更新验证结论"
```

---

### Task 8: 真实整合包验证

只读扫描用户提供的真实整合包，并在复制出的副本上验证写出效果，不修改原整合包。

- [ ] **Step 1: 只读扫描与费用预估**

对 `D:\我的世界\KCL\.minecraft\versions\All the Mods 10 8.6` 与 `D:\我的世界\KCL\.minecraft\versions\ATM10SKY 2.4.0 (2)` 分别执行：

```
uv run mctrans scan <实例目录>
uv run mctrans translate <实例目录> --dry-run
uv run mctrans translate <实例目录> --dry-run --only ftbquests
uv run mctrans translate <实例目录> --dry-run --only patchouli
```

（`scan` 只会在实例下新建 `.mctrans/` 工作目录，不修改其他文件。）

记录：各处理器条目数、警告、CFPA/自带中文匹配数、预估费用。检查 ATM10 任务书的 mod 自带中文条数是否接近 8252，待翻译条数是否接近 1057。

- [ ] **Step 2: 在副本上抽样翻译并检查输出**

把 ATM10SKY 的 `config/ftbquests`、`kubejs/assets` 以及含 Patchouli 手册的几个 jar（如 `bigreactors`、`industrialforegoing`）复制到 scratchpad 下的新实例目录（附带 `ATM10SKY 2.4.0 (2).json` 以便识别版本），然后：

```
uv run mctrans scan <副本>
uv run mctrans translate <副本> --yes --only ftbquests --limit 30
uv run mctrans translate <副本> --yes --only patchouli --limit 30
uv run mctrans apply <副本>
```

检查：生成的 `lang/zh_cn.snbt` 能被本项目的 SNBT 解析器读取，格式码（`&6`、`&r`、`{@pagebreak}`、`$(item)`）完整；资源包中的 Patchouli JSON 有效；随后 `restore` 能完全恢复副本。

- [ ] **Step 3: 把发现的问题记录下来，修复后补充测试再提交**
