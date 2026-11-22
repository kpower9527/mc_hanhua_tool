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

LANG_RE = re.compile(r"^assets/([^/]+)/lang/([A-Za-z_]+)\.(json|lang)$", re.IGNORECASE)
VANILLA_NAMESPACES = frozenset({"minecraft", "realms"})
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


def parse_lang_legacy(data: bytes) -> dict[str, str]:
    """1.12 及更早的 .lang 格式：key=value，# 开头为注释，值按原样保留。"""
    out: dict[str, str] = {}
    for line in data.decode("utf-8-sig", errors="replace").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip():
            out[key.strip()] = value
    return out


def dump_lang_legacy(mapping: dict[str, str]) -> bytes:
    lines = ["#PARSE_ESCAPES"] if any("\\" in v for v in mapping.values()) else []
    lines += [f"{k}={v}" for k, v in mapping.items()]
    return ("\n".join(lines) + "\n").encode("utf-8")


def parse_any(data: bytes, ext: str) -> dict[str, str]:
    return parse_lang_legacy(data) if ext.lower() == "lang" else parse_lang(data)


class LangJsonHandler(Handler):
    name = "lang_json"
    kind_label = "模组语言文件（物品/方块名称、界面与提示文本）"

    def detect(self, instance: Instance) -> bool:
        return True

    def extract(self, instance: Instance) -> Iterator[Entry]:
        from ..respacks import enabled_packs

        self.warnings = []
        seen: set[tuple[str, str]] = set()
        # 整合包自带的资源包（resourcepacks 下已启用的、Paxi 强制加载的）里的英文：常有作者写的提示文本，
        # 也常用来改写模组的英文（如加图标）。语言键全局生效、资源包优先于模组：
        # - 资源包改写了模组的英文：以资源包的为准，模组里的这一条不再提取（它自带的中文是旧英文的翻译）
        # - 与模组英文相同：用模组的条目，保留模组自带中文与 CFPA 译文
        pack_entries: dict[str, Entry] = {}
        for path in enabled_packs(instance.root):
            for e in self._from_pack(path, seen):
                pack_entries.setdefault(e.locator, e)  # 多个包定义同一个键：优先级高的（先扫到的）为准
        overridden = {k: e.original for k, e in pack_entries.items()}
        for jar in instance.mod_jars:
            try:
                data = jar.read_bytes()
            except OSError as exc:
                self.warnings.append(f"无法读取 {jar.name}：{exc}")
                continue
            yield from self._from_jar(data, jar.name, 0, seen, overridden=overridden)
        yield from (e for k, e in pack_entries.items() if k in overridden)

        # 实例内的资源目录：KubeJS 的 kubejs/assets/，以及 1.12 ResourceLoader 的 resources/
        for base in ("kubejs/assets", "resources"):
            res_dir = instance.root / base
            if not res_dir.is_dir():
                continue
            for lang_dir in sorted(res_dir.glob("*/lang")):
                files = {(p.stem.lower(), p.suffix[1:].lower()): p for p in lang_dir.iterdir()
                         if p.suffix.lower() in (".json", ".lang")}
                ext = "json" if ("en_us", "json") in files else "lang"
                en = files.get(("en_us", ext))
                if en is None:
                    continue
                ns = lang_dir.parent.name
                zh = files.get(("zh_cn", ext))
                yield from self._entries(
                    f"{base}/{ns}/lang/en_us.{ext}", ns, en.read_bytes(), zh.read_bytes() if zh else None,
                    seen, en.relative_to(instance.root).as_posix(), ext,
                )

    def _from_pack(self, path, seen: set) -> Iterator[Entry]:
        prefix = f"pack:{path.name}!"
        if path.is_dir():
            files = {}
            for f in path.glob("assets/*/lang/*"):
                files[f.relative_to(path).as_posix()] = f
            yield from self._from_files(files, lambda f: f.read_bytes(), prefix, path.name, seen, pack=True)
            return
        try:
            data = path.read_bytes()
        except OSError as exc:
            self.warnings.append(f"无法读取资源包 {path.name}：{exc}")
            return
        yield from self._from_jar(data, path.name, MAX_NESTING, seen, prefix=prefix, pack=True)

    def _from_jar(self, data: bytes, label: str, depth: int, seen: set, prefix: str = "jar:",
                  overridden: dict[str, str] | None = None, pack: bool = False) -> Iterator[Entry]:
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            self.warnings.append(f"无法打开 {label}，已跳过")
            return
        with z:
            names = z.namelist()
            yield from self._from_files({n: n for n in names}, z.read, prefix, label, seen, overridden, pack)
            if depth < MAX_NESTING:
                for name in names:
                    if NESTED_JAR_RE.match(name):
                        yield from self._from_jar(z.read(name), f"{label}!{name}", depth + 1, seen,
                                                  overridden=overridden)

    def _from_files(self, files: dict, read, prefix: str, label: str, seen: set,
                    overridden: dict[str, str] | None = None, pack: bool = False) -> Iterator[Entry]:
        langs: dict[str, dict[tuple[str, str], object]] = {}
        for name, handle in files.items():
            m = LANG_RE.match(name)
            if m:
                langs.setdefault(m.group(1), {})[(m.group(2).lower(), m.group(3).lower())] = (name, handle)
        for ns, found in sorted(langs.items()):
            if pack and ns in VANILLA_NAMESPACES:
                continue  # 资源包里的原版英文（纹理包常整份附带）：游戏自带官方中文，不翻译
            ext = "json" if ("en_us", "json") in found else "lang"
            en = found.get(("en_us", ext))
            if en is None:
                continue
            zh = found.get(("zh_cn", ext))
            yield from self._entries(
                f"{prefix}assets/{ns}/lang/en_us.{ext}", ns, read(en[1]),
                read(zh[1]) if zh else None, seen, f"{label}!{en[0]}", ext, overridden,
            )

    def _entries(self, source: str, ns: str, en_data: bytes, zh_data: bytes | None,
                 seen: set, label: str, ext: str = "json",
                 overridden: dict[str, str] | None = None) -> Iterator[Entry]:
        try:
            en = parse_any(en_data, ext)
        except ValueError as exc:
            self.warnings.append(f"语言文件解析失败，已跳过：{label}（{exc}）")
            return
        zh: dict[str, str] = {}
        if zh_data is not None:
            try:
                zh = parse_any(zh_data, ext)
            except ValueError as exc:
                self.warnings.append(f"自带中文解析失败，已忽略：{label}（{exc}）")
        file_hash = hashlib.sha1(en_data).hexdigest()
        for key, text in en.items():
            if (source, key) in seen or not text.strip():
                continue
            if overridden and key in overridden:
                if overridden[key] != text:
                    continue  # 被资源包改写：以资源包的英文为准
                del overridden[key]  # 与资源包相同：用模组的条目
            seen.add((source, key))
            yield Entry(
                handler=self.name, source=source, locator=key, namespace=ns,
                original=text, context=key, existing_zh=zh.get(key), file_hash=file_hash,
            )

    def write(self, items: list[TranslatedItem], sink: OutputSink) -> None:
        groups: dict[tuple[str, str], dict[str, str]] = {}
        for it in items:
            ext = "lang" if it.entry.source.endswith(".lang") else "json"
            groups.setdefault((it.entry.namespace, ext), {})[it.entry.locator] = it.text
        for (ns, ext), mapping in sorted(groups.items()):
            if ext == "lang":
                sink.add_pack_file(f"assets/{ns}/lang/zh_cn.lang", dump_lang_legacy(mapping))
            else:
                data = json.dumps(mapping, ensure_ascii=False, indent=2) + "\n"
                sink.add_pack_file(f"assets/{ns}/lang/zh_cn.json", data.encode("utf-8"))
