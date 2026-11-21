from __future__ import annotations

import json

PACKS_KEY = "resourcePacks"
INCOMPATIBLE_KEY = "incompatibleResourcePacks"


def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def get_option(text: str, key: str) -> str | None:
    prefix = key + ":"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):]
    return None


def set_option(text: str, key: str, value: str) -> str:
    nl = _newline(text)
    prefix = key + ":"
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = prefix + value
            break
    else:
        lines.append(prefix + value)
    return nl.join(lines) + nl


def _parse_list(value: str | None) -> list[str] | None:
    if value is None:
        return None
    try:
        data = json.loads(value)
    except ValueError:
        return []
    return [p for p in data if isinstance(p, str)] if isinstance(data, list) else []


def _dump_list(packs: list[str]) -> str:
    return json.dumps(packs, ensure_ascii=False, separators=(",", ":"))


def read_packs(text: str) -> list[str] | None:
    return _parse_list(get_option(text, PACKS_KEY))


def _current_packs(text: str, default_packs: list[str] | None) -> list[str]:
    packs = read_packs(text)
    if packs is None:
        packs = ["vanilla"] if default_packs is None else list(default_packs)
    return packs


def add_pack_top(text: str, pack_id: str, default_packs: list[str] | None = None) -> str:
    packs = [p for p in _current_packs(text, default_packs) if p != pack_id] + [pack_id]
    return set_option(text, PACKS_KEY, _dump_list(packs))


def remove_packs(text: str, pack_ids: list[str]) -> str:
    for key in (PACKS_KEY, INCOMPATIBLE_KEY):
        packs = _parse_list(get_option(text, key))
        if packs is None:
            continue
        text = set_option(text, key, _dump_list([p for p in packs if p not in pack_ids]))
    return text


def place_around(text: str, anchor: str, below: list[str], above: list[str],
                 default_packs: list[str] | None = None) -> str:
    """把 below 放在 anchor 紧下方、above 放在紧上方（列表越靠后优先级越高）。"""
    ours = set(below) | set(above)
    packs = [p for p in _current_packs(text, default_packs) if p not in ours]
    if anchor not in packs:
        packs.append(anchor)
    i = packs.index(anchor)
    packs = packs[:i] + list(below) + [anchor] + list(above) + packs[i + 1:]
    return set_option(text, PACKS_KEY, _dump_list(packs))
