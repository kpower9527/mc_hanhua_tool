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
