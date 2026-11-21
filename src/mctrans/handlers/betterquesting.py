from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator

from ..applied import read_original
from ..instance import Instance
from ..models import Entry, TranslatedItem
from .base import Handler, OutputSink

BQ_DIR = "config/betterquesting"
TEXT_KEYS = frozenset({"name:8", "desc:8"})
KEY_LIKE_RE = re.compile(r"^[a-z0-9_:-]+(?:\.[a-z0-9_:-]+)+$")


def _walk(node, path: list[str]) -> Iterator[tuple[list[str], str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in TEXT_KEYS and isinstance(value, str):
                yield path + [key], value
            else:
                yield from _walk(value, path + [key])
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _walk(value, path + [str(i)])


def _set(node, path: list[str], value: str) -> None:
    for key in path[:-1]:
        node = node[int(key)] if isinstance(node, list) else node[key]
    if isinstance(node, list):
        node[int(path[-1])] = value
    else:
        node[path[-1]] = value


class BetterQuestingHandler(Handler):
    name = "betterquesting"
    kind_label = "Better Questing 任务书（任务名称与描述）"
    output = "instance"

    def files(self, root: Path) -> list[str]:
        base = Path(root) / BQ_DIR
        found = [base / "DefaultQuests.json"] + sorted((base / "DefaultQuests").rglob("*.json"))
        return [p.relative_to(root).as_posix() for p in found if p.is_file()]

    def detect(self, instance: Instance) -> bool:
        return bool(self.files(instance.root))

    def _load(self, root: Path, rel: str):
        data = read_original(root, rel)
        if data is None:
            return None
        try:
            return json.loads(data.decode("utf-8-sig"), strict=False)
        except ValueError as exc:
            self.warnings.append(f"任务文件解析失败，已跳过：{rel}（{exc}）")
            return None

    def extract(self, instance: Instance) -> Iterator[Entry]:
        self.warnings = []
        for rel in self.files(instance.root):
            obj = self._load(instance.root, rel)
            if obj is None:
                continue
            name = rel.rsplit("/", 1)[-1]
            for path, text in _walk(obj, []):
                if not text.strip() or KEY_LIKE_RE.match(text.strip()):
                    continue
                yield Entry(handler=self.name, source=rel, locator=json.dumps(path, ensure_ascii=False),
                            namespace="betterquesting", original=text, context=f"{name}#{path[-1]}")

    def write(self, items: list[TranslatedItem], sink: OutputSink) -> None:
        if sink.root is None:
            raise ValueError("Better Questing 处理器写出时需要实例根目录")
        by_source: dict[str, list[TranslatedItem]] = {}
        for it in items:
            by_source.setdefault(it.entry.source, []).append(it)
        for rel, group in sorted(by_source.items()):
            obj = self._load(sink.root, rel)
            if obj is None:
                continue
            changed = False
            for it in group:
                if it.text == it.entry.original:
                    continue
                try:
                    _set(obj, json.loads(it.entry.locator), it.text)
                    changed = True
                except (KeyError, IndexError, ValueError, TypeError):
                    continue
            if changed:
                sink.add_instance_file(rel, (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
