"""Vault Hunters（the_vault）的配置文本：任务书、技能/能力描述、物品提示等。

the_vault 按客户端语言读取 config/the_vault/lang/<locale>/ 下与主配置同名的文件，覆盖其中的展示文字
（任务按 id 取名称与描述）。整合包自带的 zh_cn 往往落后于英文配置，缺的条目会显示英文。

注意：the_vault 用 FileReader 读这些 json，Java 17 在中文 Windows 上按 GBK 解码，UTF-8 中文会变乱码。
因此写出的文件一律用 \\uXXXX 转义（纯 ASCII），与系统编码无关。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Callable, Iterator

from ..applied import read_original
from ..instance import Instance
from ..models import Entry, TranslatedItem
from .base import Handler, OutputSink

BASE = "config/the_vault"
LANG_DIR = f"{BASE}/lang/zh_cn"
NAMESPACE = "the_vault"
KIND_STR = "str"
KIND_RICH = "rich"
MARK_RE = re.compile(r"<(/?)s(\d+)>")

Unit = tuple[list, str]  # (路径, 类型)


def _quest_units(obj) -> dict[str, Unit]:
    out: dict[str, Unit] = {}
    for i, q in enumerate(obj.get("quests") or []):
        if not isinstance(q, dict) or not isinstance(q.get("id"), str):
            continue
        qid = q["id"]
        if isinstance(q.get("name"), str):
            out[f"{qid}.name"] = (["quests", i, "name"], KIND_STR)
        if isinstance((q.get("descriptionData") or {}).get("description"), list):
            out[f"{qid}.description"] = (["quests", i, "descriptionData", "description"], KIND_RICH)
    return out


def _quest_main_units(obj) -> dict[str, Unit]:
    """主任务文件中没有按语言覆盖的展示文字：任务目标、章节与小节标题。"""
    out: dict[str, Unit] = {}
    for i, q in enumerate(obj.get("quests") or []):
        if isinstance(q, dict) and isinstance(q.get("id"), str) \
                and isinstance((q.get("presentation") or {}).get("goal"), str):
            out[f"{q['id']}.goal"] = (["quests", i, "presentation", "goal"], KIND_STR)
    for ci, ch in enumerate(obj.get("chapters") or []):
        if not isinstance(ch, dict):
            continue
        cid = ch.get("id", ci)
        for f in ("title", "tab"):
            if isinstance(ch.get(f), str):
                out[f"chapter:{cid}.{f}"] = (["chapters", ci, f], KIND_STR)
        for si, sub in enumerate(ch.get("subjects") or []):
            if isinstance(sub, dict) and isinstance(sub.get("title"), str):
                out[f"chapter:{cid}/{sub.get('id', si)}.title"] = (["chapters", ci, "subjects", si, "title"], KIND_STR)
    return out


def _tooltip_list_units(obj) -> dict[str, Unit]:
    out: dict[str, Unit] = {}
    seen: Counter = Counter()
    for i, t in enumerate(obj.get("tooltips") or []):
        if isinstance(t, dict) and isinstance(t.get("value"), str):
            item = str(t.get("item", i))
            seen[item] += 1
            key = item if seen[item] == 1 else f"{item}#{seen[item]}"
            out[key] = (["tooltips", i, "value"], KIND_STR)
    return out


def _dict_rich_units(top: str, sub: str | None) -> Callable:
    def units(obj) -> dict[str, Unit]:
        out: dict[str, Unit] = {}
        table = obj.get(top)
        if not isinstance(table, dict):
            return out
        for k, v in table.items():
            if sub is None and isinstance(v, list):
                out[k] = ([top, k], KIND_RICH)
            elif sub is not None and isinstance(v, dict) and isinstance(v.get(sub), list):
                out[k] = ([top, k, sub], KIND_RICH)
        return out
    return units


def _string_leaf_units(obj) -> dict[str, Unit]:
    out: dict[str, Unit] = {}

    def walk(node, path: list) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, path + [k])
        elif isinstance(node, str) and path:
            out[".".join(str(p) for p in path)] = (path, KIND_STR)

    walk(obj, [])
    return out


# (主配置相对路径, 提取函数)：这些文件 the_vault 会按语言读取 lang/<locale>/ 下的同名文件
LOCALIZED: list[tuple[str, Callable]] = [
    ("quest/quests.json", _quest_units),
    ("quest/sky_quests.json", _quest_units),
    ("tooltip.json", _tooltip_list_units),
    ("skill_descriptions.json", _dict_rich_units("descriptions", None)),
    ("abilities_descriptions.json", _dict_rich_units("data", "description")),
    ("menu_player_stat_description.json", _string_leaf_units),
    ("gear/modifier_tooltips.json", _string_leaf_units),
]
# 主配置中原地翻译的文件（没有语言覆盖机制的展示文字）
IN_PLACE: list[tuple[str, Callable]] = [
    ("quest/quests.json", _quest_main_units),
    ("quest/sky_quests.json", _quest_main_units),
]


def _source_localized(rel: str) -> str:
    return f"{LANG_DIR}/{rel}"


def _source_main(rel: str) -> str:
    return f"{BASE}/{rel}"


# ---- 富文本（[{text, color...}, ...]）与带标记字符串互转 ----

def _style(seg: dict) -> str:
    return json.dumps({k: v for k, v in seg.items() if k != "text"}, sort_keys=True, ensure_ascii=False)


def rich_styles(segments) -> tuple[str, list[str]] | None:
    """返回 (基础样式, 其他样式列表)；不是纯文本片段数组时返回 None。"""
    if not isinstance(segments, list) or not segments:
        return None
    if not all(isinstance(s, dict) and isinstance(s.get("text"), str) for s in segments):
        return None
    counts = Counter(_style(s) for s in segments)
    base = max(counts, key=lambda st: (counts[st], -[_style(s) for s in segments].index(st)))
    others: list[str] = []
    for s in segments:
        st = _style(s)
        if st != base and st not in others:
            others.append(st)
    return base, others


def encode_rich(segments, base: str, others: list[str]) -> str | None:
    """基础样式的片段直接拼接，其他样式用 <sN>…</sN> 包住，便于整段翻译、调整语序。"""
    parts: list[str] = []
    for s in segments:
        if not isinstance(s, dict) or not isinstance(s.get("text"), str):
            return None
        st = _style(s)
        if st == base:
            parts.append(s["text"])
        elif st in others:
            n = others.index(st) + 1
            parts.append(f"<s{n}>{s['text']}</s{n}>")
        else:
            return None
    return "".join(parts)


def decode_rich(text: str, base: str, others: list[str]) -> list[dict]:
    out: list[dict] = []
    style = base
    pos = 0

    def flush(chunk: str, st: str) -> None:
        if chunk:
            out.append({"text": chunk, **json.loads(st)})

    for m in MARK_RE.finditer(text):
        flush(text[pos:m.start()], style)
        n = int(m.group(2))
        style = others[n - 1] if not m.group(1) and 1 <= n <= len(others) else base
        pos = m.end()
    flush(text[pos:], style)
    return out


# ---- json 工具 ----

def _get(obj, path: list):
    for p in path:
        obj = obj[p]
    return obj


def _set(obj, path: list, value) -> None:
    _get(obj, path[:-1])[path[-1]] = value


def _load(data: bytes | None):
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8-sig"), strict=False)
    except (ValueError, UnicodeDecodeError):
        return None


def dump_ascii(obj) -> bytes:
    return (json.dumps(obj, ensure_ascii=True, indent=2) + "\n").encode("ascii")


def _unit_text(obj, unit: Unit, ref_styles=None) -> str | None:
    """取出单元的可翻译字符串；ref_styles 指定富文本的样式表（用英文原文的，保证标记一致）。"""
    path, kind = unit
    try:
        value = _get(obj, path)
    except (KeyError, IndexError, TypeError):
        return None
    if kind == KIND_STR:
        return value if isinstance(value, str) else None
    styles = ref_styles or rich_styles(value)
    return encode_rich(value, *styles) if styles else None


class VaultHuntersHandler(Handler):
    name = "vaulthunters"
    kind_label = "Vault Hunters 配置文本（任务书、技能与能力描述、物品提示）"
    output = "instance"

    def detect(self, instance: Instance) -> bool:
        return (instance.root / BASE).is_dir()

    def extract(self, instance: Instance) -> Iterator[Entry]:
        self.warnings = []
        root = instance.root
        for rel, units_fn in LOCALIZED:
            en = _load(read_original(root, _source_main(rel)))
            if en is None:
                continue
            zh = _load(read_original(root, _source_localized(rel)))
            zh_units = units_fn(zh) if isinstance(zh, dict) else {}
            for locator, unit in units_fn(en).items():
                styles = rich_styles(_get(en, unit[0])) if unit[1] == KIND_RICH else None
                text = _unit_text(en, unit, styles)
                if not text or not text.strip():
                    continue
                existing = None
                if locator in zh_units and zh_units[locator][1] == unit[1]:
                    existing = _unit_text(zh, zh_units[locator], styles)
                    if existing is not None and not _same_marks(text, existing):
                        existing = None  # 片段结构不同：多半是旧版译文，重新翻译
                yield self._entry(_source_localized(rel), locator, text, rel, existing)
        for rel, units_fn in IN_PLACE:
            en = _load(read_original(root, _source_main(rel)))
            if en is None:
                continue
            for locator, unit in units_fn(en).items():
                text = _unit_text(en, unit)
                if text and text.strip():
                    yield self._entry(_source_main(rel), locator, text, rel, None)

    def _entry(self, source: str, locator: str, text: str, rel: str, existing: str | None) -> Entry:
        return Entry(handler=self.name, source=source, locator=locator, namespace=NAMESPACE,
                     original=text, context=f"Vault Hunters {rel} · {locator}", existing_zh=existing)

    def write(self, items: list[TranslatedItem], sink: OutputSink) -> None:
        if sink.root is None:
            raise ValueError("Vault Hunters 处理器写出时需要实例根目录")
        root = Path(sink.root)
        by_source: dict[str, dict[str, TranslatedItem]] = {}
        for it in items:
            by_source.setdefault(it.entry.source, {})[it.entry.locator] = it
        for rel, units_fn in LOCALIZED:
            en = _load(read_original(root, _source_main(rel)))
            if en is None:
                continue
            zh = _load(read_original(root, _source_localized(rel)))
            wanted = by_source.get(_source_localized(rel), {})
            if not wanted and zh is None:
                continue
            zh_units = units_fn(zh) if isinstance(zh, dict) else {}
            out = json.loads(json.dumps(en))
            for locator, unit in units_fn(en).items():
                path, kind = unit
                styles = rich_styles(_get(en, path)) if kind == KIND_RICH else None
                original = _unit_text(en, unit, styles)
                if original is None:
                    continue
                it = wanted.get(locator)
                text = it.text if it is not None and it.entry.original == original else None
                if text is None and locator in zh_units and zh_units[locator][1] == kind:
                    existing = _unit_text(zh, zh_units[locator], styles)
                    if existing is not None and _same_marks(original, existing):
                        text = existing  # 整合包自带的中文
                if text is None or text == original:
                    continue
                _set(out, path, decode_rich(text, *styles) if kind == KIND_RICH else text)
            sink.add_instance_file(_source_localized(rel), dump_ascii(out))
        for rel, units_fn in IN_PLACE:
            wanted = by_source.get(_source_main(rel), {})
            if not wanted:
                continue
            en = _load(read_original(root, _source_main(rel)))
            if en is None:
                continue
            out = json.loads(json.dumps(en))
            changed = False
            for locator, unit in units_fn(en).items():
                it = wanted.get(locator)
                if it is not None and it.entry.original == _unit_text(en, unit) and it.text != it.entry.original:
                    _set(out, unit[0], it.text)
                    changed = True
            if changed:
                sink.add_instance_file(_source_main(rel), dump_ascii(out))
        self._escape_rest(root, sink)

    @staticmethod
    def _escape_rest(root: Path, sink: OutputSink) -> None:
        """其余 zh_cn 文件（整合包自带）也转成 \\uXXXX，避免在中文 Windows 上乱码。"""
        lang = root / LANG_DIR
        if not lang.is_dir():
            return
        for path in sorted(lang.rglob("*.json")):
            rel = path.relative_to(root).as_posix()
            if rel in sink.instance_files:
                continue
            data = read_original(root, rel)
            obj = _load(data)
            if obj is None or data is None:
                continue
            try:
                data.decode("ascii")
                continue  # 已是纯 ASCII
            except UnicodeDecodeError:
                pass
            sink.add_instance_file(rel, dump_ascii(obj))


def _same_marks(a: str, b: str) -> bool:
    return Counter(m.group(0) for m in MARK_RE.finditer(a)) == Counter(m.group(0) for m in MARK_RE.finditer(b))
