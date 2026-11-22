"""config 目录下建筑模板（.snbt / .nbt）中的可见文字。

例如 Skyblock Builder 的出生岛模板：告示牌文字、RFTools 屏幕文本模块。
模板只在生成结构时读取，所以直接修改实例内的文件（应用前自动备份）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from .. import nbt, snbt
from ..applied import read_original
from ..instance import Instance
from ..models import Entry, TranslatedItem
from ..snbt import SString
from .base import Handler, OutputSink

SCAN_DIRS = ("config",)
SKIP_PREFIXES = ("config/ftbquests/",)
MARKERS = (b"messages", b"module_text_data", b"Text1")
SNBT_ESCAPES = {**snbt.DECODE, "'": "'"}
SIGN_FACES = {"front_text", "back_text"}
LEGACY_SIGN_KEYS = {"Text1", "Text2", "Text3", "Text4"}
NAMESPACE = "structure"


def candidate_files(root: Path) -> list[str]:
    out = []
    for d in SCAN_DIRS:
        base = Path(root) / d
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if p.suffix.lower() not in (".snbt", ".nbt") or not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            if not rel.startswith(SKIP_PREFIXES):
                out.append(rel)
    return out


# ---- 文本组件 ----

def component_text(raw: str) -> str | None:
    """告示牌上一行的文本组件（JSON 字符串）→ 可翻译的纯文本；结构复杂的组件不处理。"""
    try:
        obj = json.loads(raw)
    except ValueError:
        return raw  # 1.21.5 起可能直接存纯文本
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict) and isinstance(obj.get("text"), str) and not obj.get("extra"):
        return obj["text"]
    return None


def replace_component(raw: str, text: str) -> str:
    try:
        obj = json.loads(raw)
    except ValueError:
        return text
    if isinstance(obj, str):
        return json.dumps(text, ensure_ascii=False)
    obj["text"] = text
    return json.dumps(obj, ensure_ascii=False)


# ---- 遍历 ----

def walk(node, path: str = "", parent: str = "") -> Iterator[tuple[str, SString, bool]]:
    """产出 (定位路径, 字符串节点, 是否为文本组件 JSON)。"""
    if isinstance(node, dict):
        for key, value in node.items():
            p = f"{path}.{key}" if path else key
            if key == "line" and isinstance(value, SString) and "module_text_data" in parent:
                yield p, value, False
            elif key == "messages" and parent in SIGN_FACES and isinstance(value, list):
                for i, line in enumerate(value):
                    if isinstance(line, SString):
                        yield f"{p}[{i}]", line, True
                    elif isinstance(line, dict) and isinstance(line.get("text"), SString):
                        yield f"{p}[{i}].text", line["text"], False
            elif key in LEGACY_SIGN_KEYS and isinstance(value, SString):
                yield p, value, True
            else:
                yield from walk(value, p, key)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from walk(value, f"{path}[{i}]", parent)


def _texts(node) -> Iterator[tuple[str, SString, bool, str]]:
    for locator, s, is_json in walk(node):
        text = component_text(s.value) if is_json else s.value
        if text and any(c.isalpha() for c in text):
            yield locator, s, is_json, text


BLOCK_RE = re.compile(r"^[^.\[]*\[\d+\]")


def _block_of(locator: str) -> str:
    """定位路径所属的方块（如 blocks[12]）；无法判断时整文件视为一组之外的单独一组。"""
    m = BLOCK_RE.match(locator)
    return m.group(0) if m else locator


class _Loaded:
    def __init__(self, rel: str, data: bytes):
        self.rel = rel
        self.binary = rel.lower().endswith(".nbt")
        if self.binary:
            self.raw, self.method = nbt.decompress(data)
            self.tree = nbt.parse(self.raw) if any(m in self.raw for m in MARKERS) else None
        else:
            self.text = data.decode("utf-8")
            self.tree = snbt.parse(self.text, SNBT_ESCAPES) if any(
                m.decode() in self.text for m in MARKERS) else None

    def render(self, replacements: list[tuple[int, int, str]]) -> bytes:
        if self.binary:
            return nbt.compress(nbt.replace_strings(self.raw, replacements), self.method)
        return snbt.replace_spans(self.text, replacements).encode("utf-8")


def _load(root: Path, rel: str) -> _Loaded | None:
    data = read_original(root, rel)
    if data is None:
        return None
    return _Loaded(rel, data)


class StructureTextHandler(Handler):
    name = "structures"
    kind_label = "建筑模板中的文字（告示牌、RFTools 屏幕）"
    output = "instance"

    def detect(self, instance: Instance) -> bool:
        return bool(candidate_files(instance.root))

    def extract(self, instance: Instance) -> Iterator[Entry]:
        self.warnings = []
        for rel in candidate_files(instance.root):
            try:
                loaded = _load(instance.root, rel)
            except Exception as exc:  # 损坏或不是 NBT 的文件：跳过
                self.warnings.append(f"无法解析建筑模板，已跳过：{rel}（{exc}）")
                continue
            if loaded is None or loaded.tree is None:
                continue
            name = rel.rsplit("/", 1)[-1]
            found = list(_texts(loaded.tree))
            # 告示牌、屏幕上的一句话常被拆成几行（如 "No Place" / "Like Home!"），把同一方块上的各行一并作为上下文
            lines: dict[str, list[str]] = {}
            for locator, _, _, text in found:
                lines.setdefault(_block_of(locator), []).append(text)
            for locator, _, _, text in found:
                group = lines[_block_of(locator)]
                context = f"建筑模板 {name} 中告示牌/屏幕上的一行，译文需简短"
                if len(group) > 1:
                    context += "；同一块上的各行依次为：" + " / ".join(group) + "，请按整句意思翻译这一行"
                yield Entry(handler=self.name, source=rel, locator=locator, namespace=NAMESPACE,
                            original=text, context=context)

    def write(self, items: list[TranslatedItem], sink: OutputSink) -> None:
        if sink.root is None:
            raise ValueError("建筑模板处理器写出时需要实例根目录")
        wanted: dict[str, dict[str, TranslatedItem]] = {}
        for it in items:
            wanted.setdefault(it.entry.source, {})[it.entry.locator] = it
        for rel, by_locator in sorted(wanted.items()):
            loaded = _load(sink.root, rel)
            if loaded is None or loaded.tree is None:
                continue
            replacements = []
            for locator, s, is_json, text in _texts(loaded.tree):
                it = by_locator.get(locator)
                if it is None or text != it.entry.original or it.text == text:
                    continue
                value = replace_component(s.value, it.text) if is_json else it.text
                replacements.append((s.start, s.end, value))
            if replacements:
                sink.add_instance_file(rel, loaded.render(replacements))
