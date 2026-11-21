from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

from ..applied import read_original
from ..instance import Instance
from ..models import Entry, TranslatedItem
from ..snbt import SNBTError, SString, dump_lang, parse, replace_spans, to_lang_value
from .base import Handler, OutputSink

QUESTS_DIR = "config/ftbquests/quests"
LEGACY_DIR = "config/ftbquests/normal"
LANG_DIR = f"{QUESTS_DIR}/lang"
ZH_FILE = f"{LANG_DIR}/zh_cn.snbt"
LANG_SOURCE = "ftbquests:lang"
NAMESPACE = "ftbquests"
LINE_RE = re.compile(r"^(.*)\[(\d+)\]$")
INLINE_KEYS = frozenset({"title", "subtitle", "description", "hover"})
LANG_KEY_RE = re.compile(r"^\{[A-Za-z0-9_.:-]+\}$")


def inline_files(root: Path) -> list[str]:
    quests = Path(root) / QUESTS_DIR
    files = [quests / "data.snbt", quests / "chapter_groups.snbt"]
    files += sorted((quests / "chapters").glob("*.snbt"))
    files += sorted((quests / "reward_tables").glob("*.snbt"))
    files += sorted((Path(root) / LEGACY_DIR).rglob("*.snbt"))
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
        return (instance.root / QUESTS_DIR).is_dir() or (instance.root / LEGACY_DIR).is_dir()

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

    # ---- 旧版：文本内嵌在章节文件中 ----
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
