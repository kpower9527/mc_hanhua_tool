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
BOM = "﻿"


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
