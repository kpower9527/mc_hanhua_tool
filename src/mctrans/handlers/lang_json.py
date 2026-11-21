from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from typing import Iterator

from ..instance import Instance
from ..models import Entry, TranslatedItem
from .base import Handler, OutputSink

LANG_RE = re.compile(r"^assets/([^/]+)/lang/([A-Za-z_]+)\.json$")
NESTED_JAR_RE = re.compile(r"^META-INF/(?:jars|jarjar)/[^/]+\.jar$")
MAX_NESTING = 2


def parse_lang(data: bytes) -> dict[str, str]:
    text = data.decode("utf-8-sig")
    try:
        obj = json.loads(text, strict=False)
    except json.JSONDecodeError:
        cleaned = re.sub(r"^\s*//.*$", "", text, flags=re.M)
        cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
        obj = json.loads(cleaned, strict=False)
    if not isinstance(obj, dict):
        raise ValueError("语言文件顶层不是 JSON 对象")
    return {k: v for k, v in obj.items() if isinstance(k, str) and isinstance(v, str)}


class LangJsonHandler(Handler):
    name = "lang_json"
    kind_label = "模组语言文件（物品/方块名称、界面与提示文本）"

    def detect(self, instance: Instance) -> bool:
        return True

    def extract(self, instance: Instance) -> Iterator[Entry]:
        self.warnings = []
        seen: set[tuple[str, str]] = set()
        for jar in instance.mod_jars:
            try:
                data = jar.read_bytes()
            except OSError as exc:
                self.warnings.append(f"无法读取 {jar.name}：{exc}")
                continue
            yield from self._from_jar(data, jar.name, 0, seen)

        kubejs_assets = instance.root / "kubejs" / "assets"
        if kubejs_assets.is_dir():
            for lang_dir in sorted(kubejs_assets.glob("*/lang")):
                files = {p.stem.lower(): p for p in lang_dir.glob("*.json")}
                en = files.get("en_us")
                if en is None:
                    continue
                ns = lang_dir.parent.name
                zh = files.get("zh_cn")
                source = f"kubejs/assets/{ns}/lang/en_us.json"
                yield from self._entries(
                    source, ns, en.read_bytes(), zh.read_bytes() if zh else None, seen,
                    en.relative_to(instance.root).as_posix(),
                )

    def _from_jar(self, data: bytes, label: str, depth: int, seen: set) -> Iterator[Entry]:
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            self.warnings.append(f"无法打开 jar，已跳过：{label}")
            return
        with z:
            names = z.namelist()
            langs: dict[str, dict[str, str]] = {}
            for name in names:
                m = LANG_RE.match(name)
                if m:
                    langs.setdefault(m.group(1), {})[m.group(2).lower()] = name
            for ns, files in sorted(langs.items()):
                en = files.get("en_us")
                if en is None:
                    continue
                zh = files.get("zh_cn")
                yield from self._entries(
                    f"jar:assets/{ns}/lang/en_us.json", ns, z.read(en),
                    z.read(zh) if zh else None, seen, f"{label}!{en}",
                )
            if depth < MAX_NESTING:
                for name in names:
                    if NESTED_JAR_RE.match(name):
                        yield from self._from_jar(z.read(name), f"{label}!{name}", depth + 1, seen)

    def _entries(self, source: str, ns: str, en_data: bytes, zh_data: bytes | None,
                 seen: set, label: str) -> Iterator[Entry]:
        try:
            en = parse_lang(en_data)
        except ValueError as exc:
            self.warnings.append(f"语言文件解析失败，已跳过：{label}（{exc}）")
            return
        zh: dict[str, str] = {}
        if zh_data is not None:
            try:
                zh = parse_lang(zh_data)
            except ValueError as exc:
                self.warnings.append(f"自带中文解析失败，已忽略：{label}（{exc}）")
        file_hash = hashlib.sha1(en_data).hexdigest()
        for key, text in en.items():
            if (source, key) in seen or not text.strip():
                continue
            seen.add((source, key))
            yield Entry(
                handler=self.name, source=source, locator=key, namespace=ns,
                original=text, context=key, existing_zh=zh.get(key), file_hash=file_hash,
            )

    def write(self, items: list[TranslatedItem], sink: OutputSink) -> None:
        by_ns: dict[str, dict[str, str]] = {}
        for it in items:
            by_ns.setdefault(it.entry.namespace, {})[it.entry.locator] = it.text
        for ns, mapping in sorted(by_ns.items()):
            data = json.dumps(mapping, ensure_ascii=False, indent=2) + "\n"
            sink.add_pack_file(f"assets/{ns}/lang/zh_cn.json", data.encode("utf-8"))
