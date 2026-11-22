"""写入配置文件与用户术语表（tomllib 只能读，这里手写简单的 TOML 输出）。"""
from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path

from .config import SETTING_KEYS, ProviderConfig, Settings, home_dir


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    # JSON 字符串的转义规则与 TOML 基本字符串兼容
    return json.dumps(str(value), ensure_ascii=False)


def _toml_key(key: str) -> str:
    return key if key.replace("_", "").replace("-", "").isalnum() and key.isascii() else json.dumps(key, ensure_ascii=False)


def dumps_settings(settings: Settings) -> str:
    lines = ["# mctrans 配置文件（由图形界面写入，也可以手动编辑）"]
    for key in SETTING_KEYS:
        lines.append(f"{key} = {_toml_value(getattr(settings, key))}")
    for rule in settings.patch_extras:
        lines += ["", "[[patch_extra]]  # 导出补丁时附加的文件（版本留空 = 所有版本）",
                  f"version = {_toml_value(rule.version)}", f"loader = {_toml_value(rule.loader)}",
                  f"dir = {_toml_value(rule.dir)}"]
    provider_fields = [f.name for f in fields(ProviderConfig) if f.name != "name"]
    for name, provider in settings.providers.items():
        lines.append("")
        lines.append(f"[provider.{_toml_key(name)}]")
        for key in provider_fields:
            lines.append(f"{key} = {_toml_value(getattr(provider, key))}")
    return "\n".join(lines) + "\n"


def save_settings(settings: Settings, path: Path | None = None) -> Path:
    path = path or home_dir() / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(dumps_settings(settings), encoding="utf-8")
    tmp.replace(path)
    return path


def save_user_glossary(path: Path, pairs: list[tuple[str, str]]) -> None:
    lines = ["# 自定义术语表：每行 英文<Tab>中文，优先级高于自动提取的术语"]
    for en, zh in pairs:
        en, zh = en.strip(), zh.strip()
        if en and zh and "\t" not in en and "\t" not in zh and "\n" not in en + zh:
            lines.append(f"{en}\t{zh}")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
