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
                yield from self._from_files(files, z.read, f"mods/{jar.name}!")
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
            m = BOOK_FILE_RE.match(asset_path)
            if data is None or m is None:
                continue
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
