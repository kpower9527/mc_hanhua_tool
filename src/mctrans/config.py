from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

DEFAULT_PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "price_input": 2.0,
        "price_output": 8.0,
    },
    "qwen": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "api_key_env": "DASHSCOPE_API_KEY",
        "price_input": 0.8,
        "price_output": 2.0,
    },
}

SETTING_KEYS = (
    "default_provider",
    "concurrency",
    "requests_per_minute",
    "batch_size",
    "batch_max_chars",
    "max_retries",
)


class ConfigError(Exception):
    pass


@dataclass
class ProviderConfig:
    name: str
    base_url: str
    model: str
    api_key_env: str = ""
    api_key: str = ""
    price_input: float = 0.0  # 元 / 百万 token，仅用于费用预估
    price_output: float = 0.0

    def resolve_api_key(self) -> str:
        key = self.api_key or (os.environ.get(self.api_key_env, "") if self.api_key_env else "")
        if not key:
            hint = f"请设置环境变量 {self.api_key_env} 或" if self.api_key_env else "请"
            raise ConfigError(f"未找到 {self.name} 的 API Key：{hint}在配置文件中填写 api_key")
        return key


@dataclass
class Settings:
    default_provider: str = "deepseek"
    concurrency: int = 4
    requests_per_minute: int = 60
    batch_size: int = 40
    batch_max_chars: int = 6000
    max_retries: int = 3
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    def provider(self, name: str | None = None) -> ProviderConfig:
        n = name or self.default_provider
        if n not in self.providers:
            raise ConfigError(f"未知的翻译服务：{n}（可选：{', '.join(sorted(self.providers))}）")
        return self.providers[n]


def home_dir() -> Path:
    env = os.environ.get("MCTRANS_HOME")
    return Path(env) if env else Path.home() / ".mctrans"


def load_settings(path: Path | None = None) -> Settings:
    path = path or home_dir() / "config.toml"
    data: dict = {}
    if path.exists():
        try:
            with path.open("rb") as f:
                data = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"配置文件格式错误：{path}（{exc}）") from exc

    merged = {name: dict(values) for name, values in DEFAULT_PROVIDERS.items()}
    for name, override in data.get("provider", {}).items():
        merged.setdefault(name, {}).update(override)

    allowed = {f.name for f in fields(ProviderConfig)} - {"name"}
    providers: dict[str, ProviderConfig] = {}
    for name, values in merged.items():
        unknown = set(values) - allowed
        if unknown:
            raise ConfigError(f"翻译服务 {name} 的配置中有未知字段：{', '.join(sorted(unknown))}")
        if "base_url" not in values or "model" not in values:
            raise ConfigError(f"翻译服务 {name} 缺少 base_url 或 model")
        providers[name] = ProviderConfig(name=name, **values)

    settings = Settings(providers=providers)
    for key in SETTING_KEYS:
        if key in data:
            setattr(settings, key, data[key])
    return settings
