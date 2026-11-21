# 计划 1：核心流水线 MVP 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 做出能端到端运行的 CLI：扫描实例中的 mod 语言文件 → 采用 mod 自带中文 → 用 DeepSeek/通义千问翻译其余条目 → 生成资源包并启用 → 可一键还原。

**Architecture:** 分阶段流水线 + SQLite 中间层。格式处理器（本计划只有 `lang_json`）负责"文件 ↔ 条目"；翻译模块只处理条目（格式码保护、术语注入、分批、校验、重试）；输出层把译文交给处理器写入 `OutputSink`，再打包成资源包、修改 `options.txt`，并记录清单用于还原。

**Tech Stack:** Python 3.12（uv 管理）、typer、openai SDK、rich、pytest；标准库 sqlite3 / zipfile / tomllib。

**Spec:** `docs/superpowers/specs/2026-11-21-mc-modpack-translator-design.md`

## 计划拆分

规格较大，拆成 6 个计划，每个计划完成后都能独立运行和测试：

1. **本计划**：核心流水线 MVP（配置、实例扫描、数据库、`lang_json`、LLM 翻译、资源包输出、apply/restore、CLI）
2. CFPA 集成 + 全局记忆库 + I18nUpdateMod 双模式
3. FTB Quests + Patchouli
4. KubeJS / CraftTweaker / 配置文本
5. Excel 审校 + 补丁导出
6. 1.12.2 支持（`.lang`、Better Questing、旧版 FTB Quests）

本计划中暂不实现、留给后续计划的规格内容：CFPA 匹配与术语表（计划 2）、全局记忆库（计划 2）、I18nUpdateMod 双模式（计划 2；本计划只检测并记录，输出固定为独立模式）、补丁导出与审校（计划 5）。

## Global Constraints

- Python ≥ 3.11（开发使用 3.12，`.python-version` 固定为 `3.12`），所有命令通过 `uv run` 执行
- 所有面向用户的输出（CLI 文本、错误信息、警告）使用中文
- 工作区目录：`<实例目录>/.mctrans/`；全局目录：`~/.mctrans/`，可用环境变量 `MCTRANS_HOME` 覆盖（测试依赖此变量）
- 译文来源（origin）取值：`mod_builtin` / `cfpa` / `memory` / `llm` / `human`
- 状态（status）取值：`pending` / `done` / `failed` / `confirmed` / `skipped`（`skipped` 为本计划新增：不含可翻译字母的条目，如纯符号/纯格式码，不需要翻译，也不写入输出）
- `confirmed` 状态的译文不得被任何非 `human` 来源覆盖
- 模型预设：deepseek → `https://api.deepseek.com` / `deepseek-chat` / `DEEPSEEK_API_KEY`；qwen → `https://dashscope.aliyuncs.com/compatible-mode/v1` / `qwen-plus` / `DASHSCOPE_API_KEY`
- 第一阶段只支持 MC 1.16 及以上
- 测试不得发起真实网络请求，LLM 一律用 `FakeClient`

## 文件结构

```
pyproject.toml
.python-version
.gitignore
README.md
src/mctrans/
├── __init__.py
├── models.py              # Entry、TranslatedItem
├── config.py              # Settings、ProviderConfig、load_settings、home_dir
├── instance.py            # Instance、load_instance、版本/加载器/I18nUpdateMod 检测
├── store.py               # Workspace（SQLite）、SyncStats、TranslationUpdate
├── handlers/
│   ├── __init__.py        # all_handlers、get_handler
│   ├── base.py            # Handler 抽象类、OutputSink
│   └── lang_json.py       # LangJsonHandler、parse_lang
├── translate/
│   ├── __init__.py
│   ├── protect.py         # 格式码保护与还原
│   ├── glossary.py        # Glossary、build_glossary、load_user_glossary
│   ├── prompt.py          # SYSTEM_PROMPT、build_user_prompt、parse_response
│   ├── client.py          # LLMClient 协议、OpenAICompatClient
│   ├── batch.py           # Unit、UnitResult、translate_batch
│   └── engine.py          # 分组/分批、RateLimiter、run_translation、estimate
├── output/
│   ├── __init__.py
│   ├── resourcepack.py    # pack_format_for、pack_mcmeta、build_pack_zip
│   ├── options.py         # options.txt 读写
│   └── apply.py           # apply、restore
└── cli.py                 # typer 命令
tests/
├── conftest.py
├── helpers.py             # make_jar、jar_bytes、lang、FakeClient
└── test_*.py
```

---

### Task 1: 项目脚手架、数据模型与配置

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `src/mctrans/__init__.py`, `src/mctrans/models.py`, `src/mctrans/config.py`, `tests/conftest.py`, `tests/helpers.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `models.Entry(handler, source, locator, namespace, original, context="", existing_zh=None, file_hash="", id=None)`
  - `models.TranslatedItem(entry: Entry, text: str, origin: str, status: str)`
  - `config.home_dir() -> Path`
  - `config.load_settings(path: Path | None = None) -> Settings`
  - `config.Settings`：`default_provider, concurrency, requests_per_minute, batch_size, batch_max_chars, max_retries, providers: dict[str, ProviderConfig]`，方法 `provider(name: str | None = None) -> ProviderConfig`
  - `config.ProviderConfig`：`name, base_url, model, api_key_env, api_key, price_input, price_output`，方法 `resolve_api_key() -> str`
  - `config.ConfigError(Exception)`
  - `tests/helpers.py`：`make_jar(path, files) -> Path`、`jar_bytes(files) -> bytes`、`lang(d) -> str`（`FakeClient` 在 Task 7 加入）

- [ ] **Step 1: 创建项目文件**

`pyproject.toml`：

```toml
[project]
name = "mctrans"
version = "0.1.0"
description = "Minecraft 整合包自动汉化工具"
requires-python = ">=3.11"
dependencies = [
    "typer>=0.12",
    "openai>=1.40",
    "rich>=13",
]

[project.scripts]
mctrans = "mctrans.cli:app"

[dependency-groups]
dev = ["pytest>=8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/mctrans"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["tests"]
```

`.python-version`：

```
3.12
```

`.gitignore`：

```
.venv/
__pycache__/
*.egg-info/
.pytest_cache/
dist/
build/
.mctrans/
```

`src/mctrans/__init__.py`：

```python
__version__ = "0.1.0"
```

`src/mctrans/models.py`：

```python
from __future__ import annotations

from dataclasses import dataclass

ORIGINS = ("mod_builtin", "cfpa", "memory", "llm", "human")
STATUSES = ("pending", "done", "failed", "confirmed", "skipped")


@dataclass
class Entry:
    """从整合包中提取的一条待翻译文本。"""

    handler: str
    source: str
    locator: str
    namespace: str
    original: str
    context: str = ""
    existing_zh: str | None = None
    file_hash: str = ""
    id: int | None = None


@dataclass
class TranslatedItem:
    """条目及其当前采用的译文。"""

    entry: Entry
    text: str
    origin: str
    status: str
```

`tests/helpers.py`：

```python
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path


def jar_bytes(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def make_jar(path: Path, files: dict[str, str | bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jar_bytes(files))
    return path


def lang(d: dict[str, str]) -> str:
    return json.dumps(d, ensure_ascii=False)
```

`tests/conftest.py`：

```python
from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def mctrans_home(tmp_path, monkeypatch):
    home = tmp_path / "_home"
    monkeypatch.setenv("MCTRANS_HOME", str(home))
    for env in ("DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.delenv(env, raising=False)
    return home


@pytest.fixture
def instance_dir(tmp_path):
    root = tmp_path / "pack"
    (root / "mods").mkdir(parents=True)
    (root / "minecraftinstance.json").write_text(
        json.dumps({"gameVersion": "1.20.1"}), encoding="utf-8"
    )
    return root
```

- [ ] **Step 2: 安装依赖**

Run: `uv sync`
Expected: 创建 `.venv`，安装 typer、openai、rich、pytest，无报错。

- [ ] **Step 3: 写失败测试 `tests/test_config.py`**

```python
import pytest

from mctrans.config import ConfigError, home_dir, load_settings


def test_home_dir_uses_env(mctrans_home):
    assert home_dir() == mctrans_home


def test_defaults_without_file():
    s = load_settings()
    assert s.default_provider == "deepseek"
    assert s.provider().model == "deepseek-chat"
    assert s.provider("qwen").base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert s.batch_size == 40


def test_file_overrides_and_custom_provider(mctrans_home):
    mctrans_home.mkdir(parents=True)
    (mctrans_home / "config.toml").write_text(
        'default_provider = "qwen"\n'
        "concurrency = 8\n"
        "[provider.qwen]\n"
        'model = "qwen-max"\n'
        "[provider.local]\n"
        'base_url = "http://localhost:11434/v1"\n'
        'model = "qwen2.5"\n'
        'api_key = "x"\n',
        encoding="utf-8",
    )
    s = load_settings()
    assert s.default_provider == "qwen"
    assert s.concurrency == 8
    assert s.provider().model == "qwen-max"
    assert s.provider().api_key_env == "DASHSCOPE_API_KEY"
    assert s.provider("local").resolve_api_key() == "x"


def test_unknown_provider_key_rejected(mctrans_home):
    mctrans_home.mkdir(parents=True)
    (mctrans_home / "config.toml").write_text(
        '[provider.deepseek]\nmodle = "typo"\n', encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="modle"):
        load_settings()


def test_custom_provider_requires_base_url(mctrans_home):
    mctrans_home.mkdir(parents=True)
    (mctrans_home / "config.toml").write_text('[provider.x]\nmodel = "m"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="base_url"):
        load_settings()


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert load_settings().provider("deepseek").resolve_api_key() == "sk-test"


def test_api_key_missing():
    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        load_settings().provider("deepseek").resolve_api_key()


def test_unknown_provider():
    with pytest.raises(ConfigError, match="未知的翻译服务"):
        load_settings().provider("nope")
```

- [ ] **Step 4: 运行测试确认失败**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'mctrans.config'`

- [ ] **Step 5: 实现 `src/mctrans/config.py`**

```python
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
```

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/test_config.py -v`
Expected: 8 passed

- [ ] **Step 7: 提交**

```bash
git add pyproject.toml .python-version .gitignore uv.lock src tests
git commit -m "feat: 项目脚手架、数据模型与配置加载"
```

---

### Task 2: 实例扫描

**Files:**
- Create: `src/mctrans/instance.py`
- Test: `tests/test_instance.py`

**Interfaces:**
- Consumes: `tests/helpers.make_jar`
- Produces:
  - `instance.Instance(root: Path, mc_version: str, loader: str, mod_jars: list[Path], has_i18nupdatemod: bool)`
  - `instance.load_instance(root: Path, mc_version: str | None = None) -> Instance`（失败抛 `InstanceError`）
  - `instance.detect_mc_version(root: Path) -> str | None`
  - `instance.inspect_jars(jars: list[Path]) -> tuple[str, bool]`（加载器名：`forge` / `neoforge` / `fabric` / `unknown`；是否有 I18nUpdateMod）
  - `instance.InstanceError(Exception)`

- [ ] **Step 1: 写失败测试 `tests/test_instance.py`**

```python
import json

import pytest

from helpers import make_jar
from mctrans.instance import InstanceError, detect_mc_version, inspect_jars, load_instance


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_version_from_curseforge(instance_dir):
    assert detect_mc_version(instance_dir) == "1.20.1"


def test_version_from_mmc_pack(tmp_path):
    inst = tmp_path / "Prism" / "MyPack"
    root = inst / "minecraft"
    root.mkdir(parents=True)
    write_json(inst / "mmc-pack.json", {"components": [
        {"uid": "net.minecraftforge", "version": "47.2.0"},
        {"uid": "net.minecraft", "version": "1.20.1"},
    ]})
    assert detect_mc_version(root) == "1.20.1"


def test_version_from_hmcl_patches(tmp_path):
    root = tmp_path / "versions" / "ATM9"
    write_json(root / "ATM9.json", {"id": "ATM9", "patches": [{"id": "game", "version": "1.20.1"}]})
    assert detect_mc_version(root) == "1.20.1"


def test_version_from_fml_argument(tmp_path):
    root = tmp_path / "versions" / "pack"
    write_json(root / "pack.json", {"id": "pack", "arguments": {"game": [
        "--fml.forgeVersion", "47.2.0", "--fml.mcVersion", "1.20.1"]}})
    assert detect_mc_version(root) == "1.20.1"


def test_version_from_fabric_intermediary(tmp_path):
    root = tmp_path / "versions" / "fab"
    write_json(root / "fab.json", {"id": "fab", "libraries": [
        {"name": "net.fabricmc:intermediary:1.21.1"}]})
    assert detect_mc_version(root) == "1.21.1"


def test_version_from_inherits_from(tmp_path):
    root = tmp_path / "versions" / "x"
    write_json(root / "x.json", {"id": "x", "inheritsFrom": "1.18.2"})
    assert detect_mc_version(root) == "1.18.2"


def test_version_unknown(tmp_path):
    (tmp_path / "mods").mkdir()
    assert detect_mc_version(tmp_path) is None


def test_inspect_jars_loader_and_i18n(tmp_path):
    jars = [
        make_jar(tmp_path / "a.jar", {"META-INF/mods.toml": 'modId="a"'}),
        make_jar(tmp_path / "b.jar", {"META-INF/mods.toml": 'modId="b"'}),
        make_jar(tmp_path / "c.jar", {"fabric.mod.json": '{"id": "c"}'}),
        make_jar(tmp_path / "I18nUpdateMod-3.6.0.jar", {"META-INF/mods.toml": 'modId="i18nupdatemod"'}),
    ]
    assert inspect_jars(jars) == ("forge", True)


def test_inspect_jars_neoforge_by_modid(tmp_path):
    jars = [
        make_jar(tmp_path / "a.jar", {"META-INF/neoforge.mods.toml": 'modId="a"'}),
        make_jar(tmp_path / "x.jar", {"META-INF/neoforge.mods.toml": 'modId = "i18nupdatemod"'}),
    ]
    assert inspect_jars(jars) == ("neoforge", True)


def test_inspect_jars_skips_broken(tmp_path):
    bad = tmp_path / "bad.jar"
    bad.write_bytes(b"not a zip")
    assert inspect_jars([bad]) == ("unknown", False)


def test_load_instance(instance_dir):
    make_jar(instance_dir / "mods" / "a.jar", {"fabric.mod.json": '{"id": "a"}'})
    (instance_dir / "mods" / "old.jar.disabled").write_bytes(b"")
    inst = load_instance(instance_dir)
    assert inst.mc_version == "1.20.1"
    assert inst.loader == "fabric"
    assert [p.name for p in inst.mod_jars] == ["a.jar"]
    assert inst.has_i18nupdatemod is False


def test_load_instance_override_version(instance_dir):
    assert load_instance(instance_dir, mc_version="1.19.2").mc_version == "1.19.2"


def test_load_instance_requires_mods_dir(tmp_path):
    with pytest.raises(InstanceError, match="mods"):
        load_instance(tmp_path)


def test_load_instance_unknown_version(tmp_path):
    (tmp_path / "mods").mkdir()
    with pytest.raises(InstanceError, match="--mc-version"):
        load_instance(tmp_path)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_instance.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'mctrans.instance'`

- [ ] **Step 3: 实现 `src/mctrans/instance.py`**

```python
from __future__ import annotations

import json
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

VERSION_RE = re.compile(r"^\d+\.\d+(?:\.\d+)?$")
I18N_MODID_RE = re.compile(r'(?:modId\s*=\s*"i18nupdatemod"|"id"\s*:\s*"i18nupdatemod")')


class InstanceError(Exception):
    pass


@dataclass
class Instance:
    root: Path
    mc_version: str
    loader: str
    mod_jars: list[Path] = field(default_factory=list)
    has_i18nupdatemod: bool = False


def _read_json(path: Path):
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _from_mmc_pack(root: Path) -> str | None:
    for path in (root / "mmc-pack.json", root.parent / "mmc-pack.json"):
        data = _read_json(path)
        if isinstance(data, dict):
            for comp in data.get("components", []) or []:
                if isinstance(comp, dict) and comp.get("uid") == "net.minecraft" and comp.get("version"):
                    return str(comp["version"])
    return None


def _from_curseforge(root: Path) -> str | None:
    data = _read_json(root / "minecraftinstance.json")
    if isinstance(data, dict) and isinstance(data.get("gameVersion"), str) and data["gameVersion"]:
        return data["gameVersion"]
    return None


def _from_launcher_json(root: Path) -> str | None:
    data = _read_json(root / f"{root.name}.json")
    if not isinstance(data, dict):
        return None
    for patch in data.get("patches", []) or []:
        if isinstance(patch, dict) and patch.get("id") == "game" and patch.get("version"):
            return str(patch["version"])
    game_args = (data.get("arguments") or {}).get("game") or []
    for i, arg in enumerate(game_args):
        if arg == "--fml.mcVersion" and i + 1 < len(game_args) and isinstance(game_args[i + 1], str):
            return game_args[i + 1]
    for lib in data.get("libraries", []) or []:
        name = lib.get("name", "") if isinstance(lib, dict) else ""
        if name.startswith("net.fabricmc:intermediary:"):
            return name.rsplit(":", 1)[1]
    for key in ("clientVersion", "inheritsFrom", "id"):
        value = data.get(key)
        if isinstance(value, str) and VERSION_RE.match(value):
            return value
    return None


def detect_mc_version(root: Path) -> str | None:
    for finder in (_from_mmc_pack, _from_curseforge, _from_launcher_json):
        version = finder(root)
        if version:
            return version
    return None


def inspect_jars(jars: list[Path]) -> tuple[str, bool]:
    votes: Counter[str] = Counter()
    has_i18n = False
    for jar in jars:
        if "i18nupdate" in jar.name.lower():
            has_i18n = True
        try:
            with zipfile.ZipFile(jar) as z:
                names = set(z.namelist())
                meta = ""
                for candidate in ("META-INF/neoforge.mods.toml", "META-INF/mods.toml", "fabric.mod.json", "quilt.mod.json"):
                    if candidate in names:
                        meta = z.read(candidate).decode("utf-8", errors="replace")
                        break
        except (zipfile.BadZipFile, OSError):
            continue
        if I18N_MODID_RE.search(meta):
            has_i18n = True
        if "META-INF/neoforge.mods.toml" in names:
            votes["neoforge"] += 1
        elif "fabric.mod.json" in names or "quilt.mod.json" in names:
            votes["fabric"] += 1
        elif "META-INF/mods.toml" in names:
            votes["forge"] += 1
    loader = votes.most_common(1)[0][0] if votes else "unknown"
    return loader, has_i18n


def load_instance(root: Path, mc_version: str | None = None) -> Instance:
    root = Path(root).resolve()
    if not (root / "mods").is_dir():
        raise InstanceError(f"{root} 下没有 mods 目录，请指定游戏实例目录（含 mods/、config/ 的那一层）")
    version = mc_version or detect_mc_version(root)
    if not version:
        raise InstanceError("无法自动识别 Minecraft 版本，请用 --mc-version 指定，例如 --mc-version 1.20.1")
    jars = sorted(p for p in (root / "mods").glob("*.jar") if p.is_file())
    loader, has_i18n = inspect_jars(jars)
    return Instance(root=root, mc_version=version, loader=loader, mod_jars=jars, has_i18nupdatemod=has_i18n)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_instance.py -v`
Expected: 14 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/instance.py tests/test_instance.py
git commit -m "feat: 实例扫描（MC 版本、加载器、I18nUpdateMod 检测）"
```

---

### Task 3: 工作区数据库

**Files:**
- Create: `src/mctrans/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `models.Entry`, `models.TranslatedItem`
- Produces:
  - `store.Workspace(db_path: Path)`；`Workspace.for_instance(root: Path) -> Workspace`（路径 `<root>/.mctrans/workspace.db`）；支持 `with` 语句；`close()`
  - `set_meta(key: str, value: str)`、`get_meta(key: str) -> str | None`
  - `sync_entries(handler: str, entries: Iterable[Entry]) -> SyncStats`
  - `apply_builtin() -> int`
  - `pending_entries() -> list[Entry]`（状态 `pending`/`failed`、未过时，按 id 排序，`Entry.id` 已填充）
  - `save_translations(updates: list[TranslationUpdate]) -> int`（返回实际写入条数；跳过被 `confirmed` 保护的条目）
  - `translated_items(handler: str | None = None) -> list[TranslatedItem]`（状态 `done`/`confirmed`、文本非空、未过时）
  - `glossary_pairs() -> list[tuple[str, str]]`
  - `status_counts() -> dict[str, int]`
  - `start_run(command: str, params: dict) -> int`、`finish_run(run_id: int, stats: dict) -> None`
  - `store.SyncStats(added, changed, unchanged, stale)`
  - `store.TranslationUpdate(entry_id: int, text: str | None, origin: str | None, status: str, model: str | None = None, note: str | None = None)`

- [ ] **Step 1: 写失败测试 `tests/test_store.py`**

```python
import pytest

from mctrans.models import Entry
from mctrans.store import TranslationUpdate, Workspace


def e(locator, original, existing_zh=None, source="jar:assets/a/lang/en_us.json"):
    return Entry(handler="lang_json", source=source, locator=locator, namespace="a",
                 original=original, context=locator, existing_zh=existing_zh)


@pytest.fixture
def ws(tmp_path):
    w = Workspace.for_instance(tmp_path)
    yield w
    w.close()


def test_for_instance_path(tmp_path):
    with Workspace.for_instance(tmp_path) as w:
        assert w.path == tmp_path / ".mctrans" / "workspace.db"
    assert w.path.exists()


def test_meta(ws):
    assert ws.get_meta("mc_version") is None
    ws.set_meta("mc_version", "1.20.1")
    ws.set_meta("mc_version", "1.20.4")
    assert ws.get_meta("mc_version") == "1.20.4"


def test_sync_add_and_unchanged(ws):
    s = ws.sync_entries("lang_json", [e("item.a.x", "Gear"), e("item.a.y", "Shaft")])
    assert (s.added, s.changed, s.unchanged, s.stale) == (2, 0, 0, 0)
    s = ws.sync_entries("lang_json", [e("item.a.x", "Gear"), e("item.a.y", "Shaft")])
    assert (s.added, s.changed, s.unchanged, s.stale) == (0, 0, 2, 0)
    pending = ws.pending_entries()
    assert [p.locator for p in pending] == ["item.a.x", "item.a.y"]
    assert all(p.id is not None for p in pending)


def test_sync_changed_resets_and_flags_confirmed(ws):
    ws.sync_entries("lang_json", [e("k1", "Old"), e("k2", "Old2")])
    ids = {p.locator: p.id for p in ws.pending_entries()}
    ws.save_translations([
        TranslationUpdate(ids["k1"], "旧", "human", "confirmed"),
        TranslationUpdate(ids["k2"], "旧2", "llm", "done", "m"),
    ])
    s = ws.sync_entries("lang_json", [e("k1", "New"), e("k2", "New2")])
    assert s.changed == 2
    rows = {r["locator"]: r for r in ws.conn.execute(
        "SELECT e.locator, t.status, t.note FROM entries e JOIN translations t ON t.entry_id=e.id")}
    assert rows["k1"]["status"] == "pending" and rows["k1"]["note"] == "原文已变更，需复核"
    assert rows["k2"]["status"] == "pending" and rows["k2"]["note"] is None


def test_sync_marks_stale_and_revives(ws):
    ws.sync_entries("lang_json", [e("k1", "A"), e("k2", "B")])
    s = ws.sync_entries("lang_json", [e("k1", "A")])
    assert s.stale == 1
    assert [p.locator for p in ws.pending_entries()] == ["k1"]
    ws.sync_entries("lang_json", [e("k1", "A"), e("k2", "B")])
    assert len(ws.pending_entries()) == 2


def test_sync_stale_scoped_to_handler(ws):
    ws.sync_entries("lang_json", [e("k1", "A")])
    ws.sync_entries("other", [])
    assert len(ws.pending_entries()) == 1


def test_apply_builtin(ws):
    ws.sync_entries("lang_json", [
        e("k1", "Gear", existing_zh="齿轮"),
        e("k2", "Shaft", existing_zh="Shaft"),  # 自带 zh_cn 只是照抄英文
        e("k3", "Belt"),
    ])
    assert ws.apply_builtin() == 1
    assert ws.apply_builtin() == 0  # 幂等
    items = ws.translated_items()
    assert [(i.entry.locator, i.text, i.origin) for i in items] == [("k1", "齿轮", "mod_builtin")]
    assert [p.locator for p in ws.pending_entries()] == ["k2", "k3"]


def test_apply_builtin_overrides_llm_but_not_human(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear"), e("k2", "Shaft")])
    ids = {p.locator: p.id for p in ws.pending_entries()}
    ws.save_translations([
        TranslationUpdate(ids["k1"], "机翻", "llm", "done", "m"),
        TranslationUpdate(ids["k2"], "人工", "human", "confirmed"),
    ])
    ws.sync_entries("lang_json", [e("k1", "Gear", "齿轮"), e("k2", "Shaft", "传动杆")])
    assert ws.apply_builtin() == 1
    texts = {i.entry.locator: i.text for i in ws.translated_items()}
    assert texts == {"k1": "齿轮", "k2": "人工"}


def test_save_translations_protects_confirmed(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear")])
    (eid,) = [p.id for p in ws.pending_entries()]
    assert ws.save_translations([TranslationUpdate(eid, "齿轮", "human", "confirmed")]) == 1
    assert ws.save_translations([TranslationUpdate(eid, "机翻", "llm", "done", "m")]) == 0
    assert ws.translated_items()[0].text == "齿轮"


def test_pending_includes_failed(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear")])
    (eid,) = [p.id for p in ws.pending_entries()]
    ws.save_translations([TranslationUpdate(eid, None, None, "failed", note="x")])
    assert [p.id for p in ws.pending_entries()] == [eid]


def test_translated_items_filter_by_handler(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear", "齿轮")])
    ws.apply_builtin()
    assert len(ws.translated_items("lang_json")) == 1
    assert ws.translated_items("ftbquests") == []


def test_glossary_pairs(ws):
    ws.sync_entries("lang_json", [
        e("item.a.gear", "Gear", "齿轮"),
        e("block.a.casing", "Andesite Casing", "安山机壳"),
        e("tooltip.a.hint", "Right click", "右键"),
    ])
    ws.apply_builtin()
    assert sorted(ws.glossary_pairs()) == [("Andesite Casing", "安山机壳"), ("Gear", "齿轮")]


def test_status_counts(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear", "齿轮"), e("k2", "Shaft")])
    ws.apply_builtin()
    assert ws.status_counts() == {"done": 1, "pending": 1}


def test_runs(ws):
    run_id = ws.start_run("scan", {"a": 1})
    ws.finish_run(run_id, {"added": 2})
    row = ws.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    assert row["command"] == "scan" and row["finished_at"] is not None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'mctrans.store'`

- [ ] **Step 3: 实现 `src/mctrans/store.py`**

```python
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .models import Entry, TranslatedItem

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handler TEXT NOT NULL,
    source TEXT NOT NULL,
    locator TEXT NOT NULL,
    namespace TEXT NOT NULL,
    original TEXT NOT NULL,
    context TEXT NOT NULL DEFAULT '',
    existing_zh TEXT,
    file_hash TEXT NOT NULL DEFAULT '',
    stale INTEGER NOT NULL DEFAULT 0,
    UNIQUE(handler, source, locator)
);
CREATE TABLE IF NOT EXISTS translations (
    entry_id INTEGER PRIMARY KEY REFERENCES entries(id),
    text TEXT,
    origin TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    model TEXT,
    note TEXT,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    params TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    stats TEXT
);
"""

GLOSSARY_PREFIXES = ("item.", "block.", "entity.", "fluid.", "effect.", "enchantment.", "biome.")
CHANGED_NOTE = "原文已变更，需复核"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SyncStats:
    added: int = 0
    changed: int = 0
    unchanged: int = 0
    stale: int = 0


@dataclass
class TranslationUpdate:
    entry_id: int
    text: str | None
    origin: str | None
    status: str
    model: str | None = None
    note: str | None = None


class Workspace:
    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    @classmethod
    def for_instance(cls, root: Path) -> "Workspace":
        return cls(Path(root) / ".mctrans" / "workspace.db")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Workspace":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- meta ----
    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    # ---- entries ----
    def sync_entries(self, handler: str, entries: Iterable[Entry]) -> SyncStats:
        stats = SyncStats()
        seen: set[int] = set()
        with self.conn:
            for e in entries:
                row = self.conn.execute(
                    "SELECT id, original FROM entries WHERE handler=? AND source=? AND locator=?",
                    (handler, e.source, e.locator),
                ).fetchone()
                if row is None:
                    cur = self.conn.execute(
                        "INSERT INTO entries(handler, source, locator, namespace, original, context, existing_zh, file_hash) "
                        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                        (handler, e.source, e.locator, e.namespace, e.original, e.context, e.existing_zh, e.file_hash),
                    )
                    eid = cur.lastrowid
                    self.conn.execute(
                        "INSERT INTO translations(entry_id, status, updated_at) VALUES(?, 'pending', ?)",
                        (eid, _now()),
                    )
                    stats.added += 1
                else:
                    eid = row["id"]
                    self.conn.execute(
                        "UPDATE entries SET namespace=?, original=?, context=?, existing_zh=?, file_hash=?, stale=0 WHERE id=?",
                        (e.namespace, e.original, e.context, e.existing_zh, e.file_hash, eid),
                    )
                    if row["original"] != e.original:
                        tr = self.conn.execute(
                            "SELECT status, origin FROM translations WHERE entry_id=?", (eid,)
                        ).fetchone()
                        reviewed = tr is not None and (tr["status"] == "confirmed" or tr["origin"] == "human")
                        self.conn.execute(
                            "UPDATE translations SET status='pending', note=?, updated_at=? WHERE entry_id=?",
                            (CHANGED_NOTE if reviewed else None, _now(), eid),
                        )
                        stats.changed += 1
                    else:
                        stats.unchanged += 1
                seen.add(eid)
            active = [r["id"] for r in self.conn.execute(
                "SELECT id FROM entries WHERE handler=? AND stale=0", (handler,))]
            gone = [i for i in active if i not in seen]
            self.conn.executemany("UPDATE entries SET stale=1 WHERE id=?", [(i,) for i in gone])
            stats.stale = len(gone)
        return stats

    def apply_builtin(self) -> int:
        """把 mod 自带的中文作为译文（优先级高于机翻，低于人工）。"""
        with self.conn:
            cur = self.conn.execute(
                """
                UPDATE translations
                SET text=(SELECT existing_zh FROM entries e WHERE e.id=translations.entry_id),
                    origin='mod_builtin', status='done', model=NULL, note=NULL, updated_at=?
                WHERE entry_id IN (
                    SELECT e.id FROM entries e JOIN translations t ON t.entry_id=e.id
                    WHERE e.stale=0
                      AND e.existing_zh IS NOT NULL AND e.existing_zh != ''
                      AND e.existing_zh != e.original
                      AND t.status != 'confirmed'
                      AND COALESCE(t.origin, '') != 'human'
                      AND NOT (COALESCE(t.origin, '')='mod_builtin' AND t.status='done'
                               AND COALESCE(t.text, '')=e.existing_zh)
                )
                """,
                (_now(),),
            )
            return cur.rowcount

    @staticmethod
    def _entry(row: sqlite3.Row) -> Entry:
        return Entry(
            handler=row["handler"], source=row["source"], locator=row["locator"],
            namespace=row["namespace"], original=row["original"], context=row["context"],
            existing_zh=row["existing_zh"], file_hash=row["file_hash"], id=row["id"],
        )

    def pending_entries(self) -> list[Entry]:
        rows = self.conn.execute(
            "SELECT e.* FROM entries e JOIN translations t ON t.entry_id=e.id "
            "WHERE e.stale=0 AND t.status IN ('pending', 'failed') ORDER BY e.id"
        )
        return [self._entry(r) for r in rows]

    # ---- translations ----
    def save_translations(self, updates: list[TranslationUpdate]) -> int:
        written = 0
        with self.conn:
            for u in updates:
                tr = self.conn.execute(
                    "SELECT status FROM translations WHERE entry_id=?", (u.entry_id,)
                ).fetchone()
                if tr is None:
                    raise KeyError(f"条目不存在：{u.entry_id}")
                if tr["status"] == "confirmed" and u.origin != "human":
                    continue
                self.conn.execute(
                    "UPDATE translations SET text=?, origin=?, status=?, model=?, note=?, updated_at=? WHERE entry_id=?",
                    (u.text, u.origin, u.status, u.model, u.note, _now(), u.entry_id),
                )
                written += 1
        return written

    def translated_items(self, handler: str | None = None) -> list[TranslatedItem]:
        sql = (
            "SELECT e.*, t.text AS t_text, t.origin AS t_origin, t.status AS t_status "
            "FROM entries e JOIN translations t ON t.entry_id=e.id "
            "WHERE e.stale=0 AND t.status IN ('done', 'confirmed') AND t.text IS NOT NULL"
        )
        params: tuple = ()
        if handler:
            sql += " AND e.handler=?"
            params = (handler,)
        sql += " ORDER BY e.id"
        return [
            TranslatedItem(entry=self._entry(r), text=r["t_text"], origin=r["t_origin"], status=r["t_status"])
            for r in self.conn.execute(sql, params)
        ]

    def glossary_pairs(self) -> list[tuple[str, str]]:
        return [
            (item.entry.original, item.text)
            for item in self.translated_items("lang_json")
            if item.entry.locator.startswith(GLOSSARY_PREFIXES)
        ]

    def status_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT t.status, COUNT(*) AS n FROM entries e JOIN translations t ON t.entry_id=e.id "
            "WHERE e.stale=0 GROUP BY t.status"
        )
        return {r["status"]: r["n"] for r in rows}

    # ---- runs ----
    def start_run(self, command: str, params: dict) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO runs(command, params, started_at) VALUES(?, ?, ?)",
                (command, json.dumps(params, ensure_ascii=False, default=str), _now()),
            )
            return cur.lastrowid

    def finish_run(self, run_id: int, stats: dict) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET finished_at=?, stats=? WHERE id=?",
                (_now(), json.dumps(stats, ensure_ascii=False, default=str), run_id),
            )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_store.py -v`
Expected: 14 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/store.py tests/test_store.py
git commit -m "feat: 工作区数据库（条目同步、增量更新、译文保护）"
```

---

### Task 4: 处理器基类与 mod 语言文件处理器

**Files:**
- Create: `src/mctrans/handlers/__init__.py`, `src/mctrans/handlers/base.py`, `src/mctrans/handlers/lang_json.py`
- Test: `tests/test_lang_json.py`

**Interfaces:**
- Consumes: `instance.Instance`, `models.Entry`, `models.TranslatedItem`, `tests/helpers.make_jar/jar_bytes/lang`
- Produces:
  - `handlers.base.OutputSink`：属性 `pack_files: dict[str, bytes]`、`instance_files: dict[str, bytes]`；方法 `add_pack_file(relpath, data)`、`add_instance_file(relpath, data)`
  - `handlers.base.Handler`（抽象）：类属性 `name: str`、`kind_label: str`；实例属性 `warnings: list[str]`；方法 `detect(instance) -> bool`、`extract(instance) -> Iterator[Entry]`、`write(items: list[TranslatedItem], sink: OutputSink) -> None`
  - `handlers.all_handlers() -> list[Handler]`、`handlers.get_handler(name: str) -> Handler`
  - `handlers.lang_json.LangJsonHandler`（`name = "lang_json"`）、`parse_lang(data: bytes) -> dict[str, str]`
  - 条目约定：jar 内条目 `source = "jar:assets/<ns>/lang/en_us.json"`（与 jar 文件名无关，mod 升级后条目仍能对应）；KubeJS 条目 `source = "kubejs/assets/<ns>/lang/en_us.json"`；`locator` = 语言键；`context` = 语言键

- [ ] **Step 1: 写失败测试 `tests/test_lang_json.py`**

```python
import json

import pytest

from helpers import jar_bytes, lang, make_jar
from mctrans.handlers import all_handlers, get_handler
from mctrans.handlers.base import OutputSink
from mctrans.handlers.lang_json import LangJsonHandler, parse_lang
from mctrans.instance import load_instance
from mctrans.models import Entry, TranslatedItem


def extract(root):
    h = LangJsonHandler()
    return h, list(h.extract(load_instance(root)))


def test_registry():
    assert [h.name for h in all_handlers()] == ["lang_json"]
    assert isinstance(get_handler("lang_json"), LangJsonHandler)
    with pytest.raises(KeyError):
        get_handler("nope")


def test_parse_lang_lenient():
    data = '﻿{\n  // comment\n  "a": "A",\n  "b": 1,\n  "c": "C",\n}'.encode("utf-8")
    assert parse_lang(data) == {"a": "A", "c": "C"}


def test_parse_lang_rejects_non_object():
    with pytest.raises(ValueError):
        parse_lang(b"[1, 2]")


def test_extract_from_jar_with_builtin_zh(instance_dir):
    make_jar(instance_dir / "mods" / "create.jar", {
        "assets/create/lang/en_us.json": lang({"item.create.gear": "Gear", "item.create.shaft": "Shaft", "blank": " "}),
        "assets/create/lang/zh_cn.json": lang({"item.create.gear": "齿轮"}),
        "assets/create/lang/de_de.json": lang({"item.create.gear": "Zahnrad"}),
    })
    h, entries = extract(instance_dir)
    assert [(x.locator, x.original, x.existing_zh) for x in entries] == [
        ("item.create.gear", "Gear", "齿轮"),
        ("item.create.shaft", "Shaft", None),
    ]
    first = entries[0]
    assert first.handler == "lang_json"
    assert first.source == "jar:assets/create/lang/en_us.json"
    assert first.namespace == "create"
    assert first.context == "item.create.gear"
    assert len(first.file_hash) == 40
    assert h.warnings == []


def test_extract_case_insensitive_filenames(instance_dir):
    make_jar(instance_dir / "mods" / "old.jar", {
        "assets/old/lang/en_US.json": lang({"k": "Hello"}),
        "assets/old/lang/zh_CN.json": lang({"k": "你好"}),
    })
    _, entries = extract(instance_dir)
    assert [(x.locator, x.existing_zh) for x in entries] == [("k", "你好")]


def test_extract_nested_jar(instance_dir):
    inner = jar_bytes({"assets/lib/lang/en_us.json": lang({"lib.k": "Library"})})
    make_jar(instance_dir / "mods" / "outer.jar", {
        "assets/outer/lang/en_us.json": lang({"outer.k": "Outer"}),
        "META-INF/jars/lib.jar": inner,
    })
    _, entries = extract(instance_dir)
    assert sorted(x.namespace for x in entries) == ["lib", "outer"]


def test_extract_dedupes_same_key_across_jars(instance_dir):
    make_jar(instance_dir / "mods" / "a.jar", {"assets/shared/lang/en_us.json": lang({"k": "One"})})
    make_jar(instance_dir / "mods" / "b.jar", {"assets/shared/lang/en_us.json": lang({"k": "Two"})})
    _, entries = extract(instance_dir)
    assert [(x.locator, x.original) for x in entries] == [("k", "One")]


def test_extract_kubejs_assets(instance_dir):
    d = instance_dir / "kubejs" / "assets" / "kubejs" / "lang"
    d.mkdir(parents=True)
    (d / "en_us.json").write_text(lang({"item.kubejs.dust": "Magic Dust"}), encoding="utf-8")
    (d / "zh_cn.json").write_text(lang({"item.kubejs.dust": "魔法粉"}), encoding="utf-8")
    _, entries = extract(instance_dir)
    assert [(x.source, x.locator, x.existing_zh) for x in entries] == [
        ("kubejs/assets/kubejs/lang/en_us.json", "item.kubejs.dust", "魔法粉")
    ]


def test_extract_broken_files_warn(instance_dir):
    make_jar(instance_dir / "mods" / "bad.jar", {"assets/bad/lang/en_us.json": "{not json"})
    (instance_dir / "mods" / "corrupt.jar").write_bytes(b"garbage")
    h, entries = extract(instance_dir)
    assert entries == []
    assert any("bad.jar" in w for w in h.warnings)
    assert any("corrupt.jar" in w for w in h.warnings)


def item(ns, key, text):
    entry = Entry(handler="lang_json", source=f"jar:assets/{ns}/lang/en_us.json",
                  locator=key, namespace=ns, original="x")
    return TranslatedItem(entry=entry, text=text, origin="llm", status="done")


def test_write_groups_by_namespace():
    sink = OutputSink()
    LangJsonHandler().write([item("a", "k1", "一"), item("b", "k2", "二"), item("a", "k3", "三")], sink)
    assert sorted(sink.pack_files) == ["assets/a/lang/zh_cn.json", "assets/b/lang/zh_cn.json"]
    assert json.loads(sink.pack_files["assets/a/lang/zh_cn.json"].decode("utf-8")) == {"k1": "一", "k3": "三"}
    assert "一" in sink.pack_files["assets/a/lang/zh_cn.json"].decode("utf-8")  # 不转义中文
    assert sink.instance_files == {}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_lang_json.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'mctrans.handlers'`

- [ ] **Step 3: 实现 `src/mctrans/handlers/base.py`**

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from ..instance import Instance
from ..models import Entry, TranslatedItem


class OutputSink:
    """收集处理器写出的文件：资源包内的文件和实例目录内的文件。"""

    def __init__(self) -> None:
        self.pack_files: dict[str, bytes] = {}
        self.instance_files: dict[str, bytes] = {}

    def add_pack_file(self, relpath: str, data: bytes) -> None:
        self.pack_files[relpath] = data

    def add_instance_file(self, relpath: str, data: bytes) -> None:
        self.instance_files[relpath] = data


class Handler(ABC):
    name: str = ""
    kind_label: str = ""

    def __init__(self) -> None:
        self.warnings: list[str] = []

    @abstractmethod
    def detect(self, instance: Instance) -> bool: ...

    @abstractmethod
    def extract(self, instance: Instance) -> Iterator[Entry]: ...

    @abstractmethod
    def write(self, items: list[TranslatedItem], sink: OutputSink) -> None: ...
```

- [ ] **Step 4: 实现 `src/mctrans/handlers/lang_json.py`**

```python
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
```

注意：`json.JSONDecodeError` 和 `UnicodeDecodeError` 都是 `ValueError` 的子类，所以 `_entries` 里捕获 `ValueError` 即可。

- [ ] **Step 5: 实现 `src/mctrans/handlers/__init__.py`**

```python
from __future__ import annotations

from .base import Handler, OutputSink
from .lang_json import LangJsonHandler

__all__ = ["Handler", "OutputSink", "all_handlers", "get_handler"]


def all_handlers() -> list[Handler]:
    return [LangJsonHandler()]


def get_handler(name: str) -> Handler:
    for handler in all_handlers():
        if handler.name == name:
            return handler
    raise KeyError(f"未知的处理器：{name}")
```

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/test_lang_json.py -v`
Expected: 10 passed

- [ ] **Step 7: 提交**

```bash
git add src/mctrans/handlers tests/test_lang_json.py
git commit -m "feat: 处理器接口与 mod 语言文件处理器"
```

---

### Task 5: 格式码保护

**Files:**
- Create: `src/mctrans/translate/__init__.py`（空文件）, `src/mctrans/translate/protect.py`
- Test: `tests/test_protect.py`

**Interfaces:**
- Produces:
  - `protect.Masked(text: str, tokens: list[str])`
  - `protect.protect(text: str) -> Masked`：把格式码替换为 `⟦0⟧`、`⟦1⟧`…
  - `protect.restore(translated: str, masked: Masked) -> str`：占位符多重集合与原文不一致时抛 `TokenMismatch`
  - `protect.TokenMismatch(ValueError)`
  - `protect.has_translatable_text(masked_text: str) -> bool`：去掉占位符后是否还含英文字母

- [ ] **Step 1: 写失败测试 `tests/test_protect.py`**

```python
import pytest

from mctrans.translate.protect import TokenMismatch, has_translatable_text, protect, restore


@pytest.mark.parametrize("text, tokens", [
    ("§aGreen §lBold§r", ["§a", "§l", "§r"]),
    ("Has %s items and %1$s or %d%%", ["%s", "%1$s", "%d", "%%"]),
    ("Speed: %.2f", ["%.2f"]),
    ("Hello {0}, {player}!", ["{0}", "{player}"]),
    ("Use $(item)Wrench$() here$(br)", ["$(item)", "$()", "$(br)"]),
    ("Line1\nLine2", ["\n"]),
    ("Line1\\nLine2", ["\\n"]),
    ("&aColored", ["&a"]),
])
def test_protect_tokens(text, tokens):
    assert protect(text).tokens == tokens


def test_protect_leaves_plain_text():
    for text in ("Rock&roll", "50% of them", "R & D", "100% Accurate"):
        assert protect(text).tokens == []


def test_round_trip_with_reorder():
    m = protect("§a%s§r killed %s")
    assert m.text == "⟦0⟧⟦1⟧⟦2⟧ killed ⟦3⟧"
    assert restore("⟦3⟧ 杀死了 ⟦0⟧⟦1⟧⟦2⟧", m) == "%s 杀死了 §a%s§r"


def test_restore_rejects_missing_token():
    m = protect("Hello %s")
    with pytest.raises(TokenMismatch):
        restore("你好", m)


def test_restore_rejects_duplicate_token():
    m = protect("Hello %s")
    with pytest.raises(TokenMismatch):
        restore("你好 ⟦0⟧ ⟦0⟧", m)


def test_has_translatable_text():
    assert has_translatable_text(protect("§a%s").text) is False
    assert has_translatable_text(protect("123 / 456").text) is False
    assert has_translatable_text(protect("§aHello").text) is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_protect.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 `src/mctrans/translate/protect.py`（并创建空的 `src/mctrans/translate/__init__.py`）**

```python
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

TOKEN_FMT = "⟦{}⟧"
TOKEN_RE = re.compile(r"⟦(\d+)⟧")

PROTECT_RE = re.compile("|".join([
    r"§[0-9a-fk-orA-FK-OR]",                       # 颜色/样式码
    r"(?<![A-Za-z0-9])&[0-9a-fk-or]",              # & 颜色码（前面不能紧跟字母数字）
    r"%(?:\d+\$)?[-#+0,(]*\d*(?:\.\d+)?[a-zA-Z%]", # printf 参数
    r"\{[A-Za-z0-9_.:-]*\}",                       # {0} {player}
    r"\$\([^)]*\)",                                # Patchouli 宏
    r"\\n",                                        # 字面量 \n
    r"\n",                                         # 真实换行
]))


class TokenMismatch(ValueError):
    pass


@dataclass
class Masked:
    text: str
    tokens: list[str]


def protect(text: str) -> Masked:
    tokens: list[str] = []

    def repl(m: re.Match) -> str:
        tokens.append(m.group(0))
        return TOKEN_FMT.format(len(tokens) - 1)

    return Masked(PROTECT_RE.sub(repl, text), tokens)


def restore(translated: str, masked: Masked) -> str:
    found = Counter(TOKEN_RE.findall(translated))
    expected = Counter(str(i) for i in range(len(masked.tokens)))
    if found != expected:
        raise TokenMismatch(
            f"占位符不一致：期望 {sorted(expected.elements())}，实际 {sorted(found.elements())}"
        )
    return TOKEN_RE.sub(lambda m: masked.tokens[int(m.group(1))], translated)


def has_translatable_text(masked_text: str) -> bool:
    return re.search(r"[A-Za-z]", TOKEN_RE.sub("", masked_text)) is not None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_protect.py -v`
Expected: 13 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/translate tests/test_protect.py
git commit -m "feat: 格式码保护与还原"
```

---

### Task 6: 术语表

**Files:**
- Create: `src/mctrans/translate/glossary.py`
- Test: `tests/test_glossary.py`

**Interfaces:**
- Produces:
  - `glossary.Glossary`：`add(en: str, zh: str, override: bool = False) -> None`、`find(texts: Iterable[str], limit: int = 60) -> list[tuple[str, str]]`（长词优先）、`__len__`
  - `glossary.build_glossary(workspace_pairs: list[tuple[str, str]], user_pairs: list[tuple[str, str]]) -> Glossary`（用户术语优先）
  - `glossary.load_user_glossary(path: Path) -> list[tuple[str, str]]`（每行 `英文<Tab>中文`，`#` 开头为注释；文件不存在返回空列表）
  - 用户术语表默认路径：`home_dir() / "glossary.tsv"`（在 CLI 中拼接）

- [ ] **Step 1: 写失败测试 `tests/test_glossary.py`**

```python
from mctrans.translate.glossary import Glossary, build_glossary, load_user_glossary


def test_find_whole_words_longest_first():
    g = Glossary()
    g.add("Andesite", "安山岩")
    g.add("Andesite Alloy", "安山合金")
    g.add("Alloy", "合金")
    hits = g.find(["Craft an andesite alloy first."])
    assert hits == [("Andesite Alloy", "安山合金"), ("Alloy", "合金"), ("Andesite", "安山岩")]


def test_find_requires_word_boundary():
    g = Glossary()
    g.add("Gear", "齿轮")
    assert g.find(["Gearbox"]) == []
    assert g.find(["A gear."]) == [("Gear", "齿轮")]


def test_add_filters_bad_terms():
    g = Glossary()
    g.add("ab", "短")              # 太短
    g.add("Speed %s", "速度 %s")   # 含格式码
    g.add("Gear", "")              # 空译文
    g.add("Gear", "Gear")          # 未翻译
    assert len(g) == 0


def test_find_limit():
    g = Glossary()
    for i in range(10):
        g.add(f"Term{i}", f"术语{i}")
    assert len(g.find([" ".join(f"term{i}" for i in range(10))], limit=3)) == 3


def test_user_terms_take_priority():
    g = build_glossary([("Gear", "齿轮")], [("Gear", "传动齿轮")])
    assert g.find(["Gear"]) == [("Gear", "传动齿轮")]


def test_load_user_glossary(tmp_path):
    p = tmp_path / "glossary.tsv"
    p.write_text("# 注释\nGear\t齿轮\n\nbad line\nShaft\t传动杆\n", encoding="utf-8")
    assert load_user_glossary(p) == [("Gear", "齿轮"), ("Shaft", "传动杆")]
    assert load_user_glossary(tmp_path / "missing.tsv") == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_glossary.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 `src/mctrans/translate/glossary.py`**

```python
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

WORD_RE = re.compile(r"[a-z0-9']+")
MIN_TERM_LEN = 3
MAX_TERM_WORDS = 6


class Glossary:
    """英文术语 → 中文译名，按整词序列匹配。"""

    def __init__(self) -> None:
        self._terms: dict[tuple[str, ...], tuple[str, str]] = {}
        self._index: dict[str, set[tuple[str, ...]]] = {}

    def __len__(self) -> int:
        return len(self._terms)

    def add(self, en: str, zh: str, override: bool = False) -> None:
        en, zh = en.strip(), zh.strip()
        if len(en) < MIN_TERM_LEN or not zh or zh == en:
            return
        if any(c in en or c in zh for c in ("§", "%", "{", "$(")):
            return
        words = tuple(WORD_RE.findall(en.lower()))
        if not words or len(words) > MAX_TERM_WORDS:
            return
        if words in self._terms and not override:
            return
        self._terms[words] = (en, zh)
        self._index.setdefault(words[0], set()).add(words)

    def find(self, texts: Iterable[str], limit: int = 60) -> list[tuple[str, str]]:
        hits: set[tuple[str, ...]] = set()
        for text in texts:
            words = WORD_RE.findall(text.lower())
            for i, word in enumerate(words):
                for cand in self._index.get(word, ()):
                    if tuple(words[i:i + len(cand)]) == cand:
                        hits.add(cand)
        ordered = sorted(hits, key=lambda c: (-len(c), c))[:limit]
        return [self._terms[c] for c in ordered]


def build_glossary(workspace_pairs: list[tuple[str, str]], user_pairs: list[tuple[str, str]]) -> Glossary:
    g = Glossary()
    for en, zh in user_pairs:
        g.add(en, zh, override=True)
    for en, zh in workspace_pairs:
        g.add(en, zh)
    return g


def load_user_glossary(path: Path) -> list[tuple[str, str]]:
    if not path.is_file():
        return []
    pairs: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#") or "\t" not in line:
            continue
        en, zh = line.split("\t", 1)
        if en.strip() and zh.strip():
            pairs.append((en.strip(), zh.strip()))
    return pairs
```

注意：`test_find_requires_word_boundary` 中 "Gearbox" 被切成单词 `gearbox`，与 `gear` 不匹配，这正是整词匹配的效果。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_glossary.py -v`
Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/translate/glossary.py tests/test_glossary.py
git commit -m "feat: 术语表构建与整词匹配"
```

---

### Task 7: 提示词、LLM 客户端与单批翻译

**Files:**
- Create: `src/mctrans/translate/prompt.py`, `src/mctrans/translate/client.py`, `src/mctrans/translate/batch.py`
- Modify: `tests/helpers.py`（追加 `FakeClient`）
- Test: `tests/test_batch.py`

**Interfaces:**
- Consumes: `protect.protect/restore/TokenMismatch`, `glossary.Glossary`, `config.ProviderConfig`
- Produces:
  - `prompt.SYSTEM_PROMPT: str`、`prompt.INPUT_MARKER = "待翻译："`
  - `prompt.build_user_prompt(namespace: str, kind: str, terms: list[tuple[str, str]], items: dict[str, dict[str, str]]) -> str`（`items` 形如 `{"1": {"text": ..., "hint": ...}}`，JSON 放在 `INPUT_MARKER` 那一行之后、位于消息末尾）
  - `prompt.parse_response(raw: str) -> dict`（失败抛 `ValueError`）
  - `client.LLMClient`（Protocol）：属性 `model: str`，方法 `complete(system: str, user: str) -> str`
  - `client.OpenAICompatClient(base_url, api_key, model, timeout=120.0, max_retries=5, temperature=0.2)`
  - `client.client_from_config(p: ProviderConfig) -> OpenAICompatClient`
  - `batch.Unit(original: str, hint: str, entry_ids: list[int])`
  - `batch.UnitResult(text: str | None, error: str | None = None, note: str | None = None)`
  - `batch.translate_batch(client: LLMClient, units: list[Unit], namespace: str, kind: str, glossary: Glossary) -> list[UnitResult]`（结果与 `units` 一一对应；不抛异常）
  - `tests/helpers.FakeClient(fn=None)`：`fn(masked_text) -> str | None`，返回 `None` 表示漏掉该条；属性 `calls: list[str]`（每次请求的 user 消息）；`fail_times: int` 前若干次调用抛 `RuntimeError`

- [ ] **Step 1: 在 `tests/helpers.py` 末尾追加 `FakeClient`**

```python
class FakeClient:
    """模拟 LLM：解析 user 消息中 INPUT_MARKER 之后的 JSON，逐条调用 fn。"""

    model = "fake-model"

    def __init__(self, fn=None, fail_times: int = 0):
        self.fn = fn or (lambda text: "译" + text)
        self.fail_times = fail_times
        self.calls: list[str] = []

    def complete(self, system: str, user: str) -> str:
        from mctrans.translate.prompt import INPUT_MARKER

        self.calls.append(user)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("模拟 API 错误")
        items = json.loads(user.split(INPUT_MARKER, 1)[1])
        out = {}
        for key, value in items.items():
            result = self.fn(value["text"])
            if result is not None:
                out[key] = result
        return json.dumps(out, ensure_ascii=False)
```

- [ ] **Step 2: 写失败测试 `tests/test_batch.py`**

```python
import json

import pytest

from helpers import FakeClient
from mctrans.config import ProviderConfig
from mctrans.translate.batch import Unit, translate_batch
from mctrans.translate.client import OpenAICompatClient, client_from_config
from mctrans.translate.glossary import Glossary
from mctrans.translate.prompt import INPUT_MARKER, build_user_prompt, parse_response


def units(*texts):
    return [Unit(t, f"key.{i}", [i]) for i, t in enumerate(texts)]


def test_build_user_prompt_layout():
    p = build_user_prompt("create", "模组语言文件", [("Gear", "齿轮")], {"1": {"text": "A gear", "hint": "k"}})
    assert "模组：create" in p and "- Gear → 齿轮" in p
    assert json.loads(p.split(INPUT_MARKER, 1)[1]) == {"1": {"text": "A gear", "hint": "k"}}


def test_parse_response_variants():
    assert parse_response('{"1": "一"}') == {"1": "一"}
    assert parse_response('```json\n{"1": "一"}\n```') == {"1": "一"}
    assert parse_response('好的：{"1": "一"} 完成') == {"1": "一"}
    with pytest.raises(ValueError):
        parse_response("没有 JSON")
    with pytest.raises(ValueError):
        parse_response("[1]")


def test_success_restores_tokens():
    client = FakeClient(lambda t: t.replace("Hello", "你好"))
    res = translate_batch(client, units("§aHello %s"), "ns", "kind", Glossary())
    assert res[0].text == "§a你好 %s" and res[0].error is None


def test_prompt_contains_masked_text_and_glossary():
    g = Glossary()
    g.add("Gear", "齿轮")
    client = FakeClient()
    translate_batch(client, units("§aGear"), "create", "kind", g)
    sent = json.loads(client.calls[0].split(INPUT_MARKER, 1)[1])
    assert sent == {"1": {"text": "⟦0⟧Gear", "hint": "key.0"}}
    assert "- Gear → 齿轮" in client.calls[0]


def test_missing_key_and_token_loss():
    def fn(text):
        if text == "Skip me":
            return None
        if text.startswith("Lose"):
            return "丢了占位符"
        return "好"
    res = translate_batch(FakeClient(fn), units("Skip me", "Lose %s", "Fine"), "ns", "k", Glossary())
    assert res[0].text is None and "缺少译文" in res[0].error
    assert res[1].text is None and "占位符" in res[1].error
    assert res[2].text == "好"


def test_client_error_fails_all():
    res = translate_batch(FakeClient(fail_times=1), units("A", "B"), "ns", "k", Glossary())
    assert all(r.text is None and "请求失败" in r.error for r in res)


def test_whitespace_edges_preserved():
    res = translate_batch(FakeClient(lambda t: "  好  "), units("  Hello "), "ns", "k", Glossary())
    assert res[0].text == "  好 "


def test_same_as_original_is_noted():
    res = translate_batch(FakeClient(lambda t: t), units("Create"), "ns", "k", Glossary())
    assert res[0].text == "Create" and "相同" in res[0].note


def test_client_from_config():
    c = client_from_config(ProviderConfig("deepseek", "https://api.deepseek.com", "deepseek-chat", api_key="sk-x"))
    assert isinstance(c, OpenAICompatClient) and c.model == "deepseek-chat"
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/test_batch.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'mctrans.translate.batch'`

- [ ] **Step 4: 实现 `src/mctrans/translate/prompt.py`**

```python
from __future__ import annotations

import json
import re

SYSTEM_PROMPT = """你是专业的 Minecraft 模组本地化译者，负责把英文翻译成简体中文。
规则：
1. 输入是一个 JSON 对象，键是编号，值包含 text（原文）和可选的 hint（文本所在位置的键名，用来判断它是物品名、提示还是说明）。
2. 输出一个 JSON 对象，键与输入完全相同，值是对应 text 的中文译文字符串。不得增加或遗漏任何键。
3. 形如 ⟦0⟧、⟦1⟧ 的标记是占位符，必须原样保留在译文中，数量不变，可以按中文语序调整位置。
4. 必须遵循给出的术语表。物品、方块、生物名称要简洁，符合 Minecraft 官方简体中文和中文社区的习惯译法。
5. 模组名、人名等专有名词可以保留英文。
6. 只输出 JSON，不要输出任何解释。"""

INPUT_MARKER = "待翻译："


def build_user_prompt(namespace: str, kind: str, terms: list[tuple[str, str]],
                      items: dict[str, dict[str, str]]) -> str:
    lines = [f"模组：{namespace}", f"内容类型：{kind}"]
    if terms:
        lines.append("术语表：")
        lines.extend(f"- {en} → {zh}" for en, zh in terms)
    lines.append(INPUT_MARKER)
    lines.append(json.dumps(items, ensure_ascii=False, indent=1))
    return "\n".join(lines)


def parse_response(raw: str) -> dict:
    s = raw.strip()
    s = re.sub(r"^```[A-Za-z]*\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end < start:
        raise ValueError("响应中没有 JSON 对象")
    obj = json.loads(s[start:end + 1])
    if not isinstance(obj, dict):
        raise ValueError("响应 JSON 不是对象")
    return obj
```

- [ ] **Step 5: 实现 `src/mctrans/translate/client.py`**

```python
from __future__ import annotations

from typing import Protocol

from ..config import ProviderConfig


class LLMClient(Protocol):
    model: str

    def complete(self, system: str, user: str) -> str: ...


class OpenAICompatClient:
    """OpenAI 兼容接口客户端。429/5xx/超时由 openai SDK 自动指数退避重试。"""

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 120.0,
                 max_retries: int = 5, temperature: float = 0.2):
        from openai import OpenAI

        self.model = model
        self.temperature = temperature
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=max_retries)

    def complete(self, system: str, user: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=self.temperature,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        if not content:
            raise RuntimeError("模型返回了空内容")
        return content


def client_from_config(p: ProviderConfig) -> OpenAICompatClient:
    return OpenAICompatClient(base_url=p.base_url, api_key=p.resolve_api_key(), model=p.model)
```

- [ ] **Step 6: 实现 `src/mctrans/translate/batch.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field

from .client import LLMClient
from .glossary import Glossary
from .prompt import SYSTEM_PROMPT, build_user_prompt, parse_response
from .protect import TOKEN_RE, TokenMismatch, protect, restore

SAME_NOTE = "译文与原文相同，可能未翻译"


@dataclass
class Unit:
    """去重后的一条待翻译原文，对应一个或多个条目。"""

    original: str
    hint: str
    entry_ids: list[int] = field(default_factory=list)


@dataclass
class UnitResult:
    text: str | None
    error: str | None = None
    note: str | None = None


def _keep_edges(original: str, text: str) -> str:
    lead = original[: len(original) - len(original.lstrip())]
    trail = original[len(original.rstrip()):]
    return lead + text.strip() + trail


def translate_batch(client: LLMClient, units: list[Unit], namespace: str, kind: str,
                    glossary: Glossary) -> list[UnitResult]:
    masks = [protect(u.original) for u in units]
    items: dict[str, dict[str, str]] = {}
    for i, (u, m) in enumerate(zip(units, masks), 1):
        item = {"text": m.text.strip()}
        if u.hint:
            item["hint"] = u.hint
        items[str(i)] = item
    # 在去掉占位符的文本上匹配术语，避免 "§aGear" 被切成 "agear"
    terms = glossary.find(TOKEN_RE.sub(" ", m.text) for m in masks)
    user = build_user_prompt(namespace, kind, terms, items)
    try:
        obj = parse_response(client.complete(SYSTEM_PROMPT, user))
    except Exception as exc:  # API 错误或响应无法解析：整批失败，交给上层重试
        return [UnitResult(None, f"请求失败：{exc}") for _ in units]

    results: list[UnitResult] = []
    for i, (u, m) in enumerate(zip(units, masks), 1):
        value = obj.get(str(i))
        if not isinstance(value, str) or not value.strip():
            results.append(UnitResult(None, "缺少译文"))
            continue
        try:
            text = _keep_edges(u.original, restore(value.strip(), m))
        except TokenMismatch as exc:
            results.append(UnitResult(None, str(exc)))
            continue
        results.append(UnitResult(text, None, SAME_NOTE if text == u.original else None))
    return results
```

说明：发送给模型的是去掉首尾空白的文本，返回后由 `_keep_edges` 按原文补回首尾空白。`test_whitespace_edges_preserved` 验证了这一点。

- [ ] **Step 7: 运行测试确认通过**

Run: `uv run pytest tests/test_batch.py -v`
Expected: 9 passed

- [ ] **Step 8: 提交**

```bash
git add src/mctrans/translate tests/helpers.py tests/test_batch.py
git commit -m "feat: 提示词、OpenAI 兼容客户端与单批翻译校验"
```

---

### Task 8: 翻译引擎（分组、分批、并发、重试、成本预估）

**Files:**
- Create: `src/mctrans/translate/engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `store.Workspace/TranslationUpdate`, `handlers.get_handler`, `batch.Unit/translate_batch`, `protect.protect/has_translatable_text`, `glossary.Glossary`, `config.Settings`
- Produces:
  - `engine.EngineSettings(batch_size=40, batch_max_chars=6000, concurrency=4, requests_per_minute=60, max_retries=3)`；`EngineSettings.from_settings(s: Settings) -> EngineSettings`
  - `engine.Group(handler: str, namespace: str, units: list[Unit])`
  - `engine.group_units(entries: list[Entry]) -> tuple[list[Group], list[int]]`（第二项是无需翻译的条目 ID）
  - `engine.make_batches(units: list[Unit], batch_size: int, max_chars: int) -> list[list[Unit]]`
  - `engine.RateLimiter(per_minute: int)`，方法 `acquire()`；`per_minute <= 0` 表示不限速
  - `engine.TranslateReport(translated, failed, skipped, requests)`
  - `engine.run_translation(ws, client, glossary, settings: EngineSettings, on_progress: Callable[[int], None] | None = None) -> TranslateReport`
  - `engine.Estimate(entries, units, requests, input_tokens, output_tokens, cost)`
  - `engine.estimate(entries: list[Entry], settings: EngineSettings, price_input: float, price_output: float) -> Estimate`

- [ ] **Step 1: 写失败测试 `tests/test_engine.py`**

```python
import time

import pytest

from helpers import FakeClient
from mctrans.config import Settings
from mctrans.models import Entry
from mctrans.store import Workspace
from mctrans.translate.batch import Unit
from mctrans.translate.engine import (
    EngineSettings, RateLimiter, estimate, group_units, make_batches, run_translation,
)
from mctrans.translate.glossary import Glossary


def e(key, text, ns="a"):
    return Entry(handler="lang_json", source=f"jar:assets/{ns}/lang/en_us.json",
                 locator=key, namespace=ns, original=text, context=key)


@pytest.fixture
def ws(tmp_path):
    w = Workspace.for_instance(tmp_path)
    yield w
    w.close()


FAST = EngineSettings(batch_size=2, batch_max_chars=6000, concurrency=2, requests_per_minute=0, max_retries=2)


def test_from_settings():
    s = EngineSettings.from_settings(Settings(batch_size=10, concurrency=3))
    assert s.batch_size == 10 and s.concurrency == 3


def test_group_units_dedupes_and_skips():
    entries = [e("k1", "Gear"), e("k2", "Gear"), e("k3", "§a%s"), e("k4", "Gear", ns="b")]
    for i, x in enumerate(entries, 1):
        x.id = i
    groups, skipped = group_units(entries)
    assert skipped == [3]
    by_ns = {g.namespace: g for g in groups}
    assert [u.entry_ids for u in by_ns["a"].units] == [[1, 2]]
    assert [u.entry_ids for u in by_ns["b"].units] == [[4]]


def test_make_batches_by_count_and_chars():
    us = [Unit("x" * 10, "", [i]) for i in range(5)]
    assert [len(b) for b in make_batches(us, 2, 1000)] == [2, 2, 1]
    assert [len(b) for b in make_batches(us, 10, 25)] == [2, 2, 1]
    big = [Unit("x" * 100, "", [0])]
    assert [len(b) for b in make_batches(big, 10, 25)] == [1]


def test_run_translation_all_success(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear"), e("k2", "Gear"), e("k3", "Shaft"), e("k4", "%s")])
    client = FakeClient()
    progress = []
    report = run_translation(ws, client, Glossary(), FAST, on_progress=progress.append)
    assert (report.translated, report.failed, report.skipped) == (3, 0, 1)
    assert report.requests == 1  # 去重后 2 条，batch_size=2 → 1 次请求
    assert sum(progress) == 3
    texts = {i.entry.locator: (i.text, i.origin) for i in ws.translated_items()}
    assert texts == {"k1": ("译Gear", "llm"), "k2": ("译Gear", "llm"), "k3": ("译Shaft", "llm")}
    assert ws.status_counts() == {"done": 3, "skipped": 1}
    assert ws.pending_entries() == []


def test_run_translation_retries_then_succeeds(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear"), e("k2", "Shaft")])
    client = FakeClient(fail_times=1)
    report = run_translation(ws, client, Glossary(), FAST)
    assert report.translated == 2 and report.failed == 0
    assert report.requests >= 2


def test_run_translation_permanent_failure(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear"), e("k2", "Bad %s")])
    client = FakeClient(lambda t: "丢失" if "⟦" in t else "齿轮")
    report = run_translation(ws, client, Glossary(), FAST)
    assert report.translated == 1 and report.failed == 1
    row = ws.conn.execute(
        "SELECT t.status, t.note FROM entries e JOIN translations t ON t.entry_id=e.id WHERE e.locator='k2'"
    ).fetchone()
    assert row["status"] == "failed" and "占位符" in row["note"]
    # 再次运行只处理失败的条目
    client2 = FakeClient()
    report2 = run_translation(ws, client2, Glossary(), FAST)
    assert report2.translated == 1 and len(client2.calls) == 1


def test_rate_limiter_spacing():
    limiter = RateLimiter(per_minute=1200)  # 间隔 0.05 秒
    start = time.monotonic()
    for _ in range(3):
        limiter.acquire()
    assert time.monotonic() - start >= 0.09


def test_rate_limiter_disabled():
    limiter = RateLimiter(per_minute=0)
    start = time.monotonic()
    for _ in range(100):
        limiter.acquire()
    assert time.monotonic() - start < 0.05


def test_estimate():
    entries = [e("k1", "a" * 35), e("k2", "a" * 35), e("k3", "%s")]
    for i, x in enumerate(entries, 1):
        x.id = i
    est = estimate(entries, EngineSettings(batch_size=40), price_input=2.0, price_output=8.0)
    assert (est.entries, est.units, est.requests) == (2, 1, 1)
    assert est.input_tokens > 0 and est.output_tokens > 0
    assert est.cost == round(est.input_tokens / 1e6 * 2.0 + est.output_tokens / 1e6 * 8.0, 4)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_engine.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 `src/mctrans/translate/engine.py`**

```python
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

from ..config import Settings
from ..handlers import get_handler
from ..models import Entry
from ..store import TranslationUpdate, Workspace
from .batch import Unit, UnitResult, translate_batch
from .client import LLMClient
from .glossary import Glossary
from .protect import has_translatable_text, protect

PROMPT_OVERHEAD_TOKENS = 450  # 系统提示词 + 术语表的大致开销
CHARS_PER_TOKEN = 3.5
OUTPUT_RATIO = 1.3


@dataclass
class EngineSettings:
    batch_size: int = 40
    batch_max_chars: int = 6000
    concurrency: int = 4
    requests_per_minute: int = 60
    max_retries: int = 3

    @classmethod
    def from_settings(cls, s: Settings) -> "EngineSettings":
        return cls(s.batch_size, s.batch_max_chars, s.concurrency, s.requests_per_minute, s.max_retries)


@dataclass
class Group:
    handler: str
    namespace: str
    units: list[Unit]


@dataclass
class TranslateReport:
    translated: int = 0
    failed: int = 0
    skipped: int = 0
    requests: int = 0


@dataclass
class Estimate:
    entries: int
    units: int
    requests: int
    input_tokens: int
    output_tokens: int
    cost: float


def group_units(entries: list[Entry]) -> tuple[list[Group], list[int]]:
    groups: dict[tuple[str, str], dict[str, Unit]] = {}
    skipped: list[int] = []
    for e in entries:
        if not has_translatable_text(protect(e.original).text):
            skipped.append(e.id)
            continue
        units = groups.setdefault((e.handler, e.namespace), {})
        unit = units.get(e.original)
        if unit is None:
            units[e.original] = Unit(e.original, e.context, [e.id])
        else:
            unit.entry_ids.append(e.id)
    return [Group(h, ns, list(us.values())) for (h, ns), us in groups.items()], skipped


def make_batches(units: list[Unit], batch_size: int, max_chars: int) -> list[list[Unit]]:
    batches: list[list[Unit]] = []
    current: list[Unit] = []
    size = 0
    for u in units:
        n = len(u.original) + len(u.hint)
        if current and (len(current) >= batch_size or size + n > max_chars):
            batches.append(current)
            current, size = [], 0
        current.append(u)
        size += n
    if current:
        batches.append(current)
    return batches


class RateLimiter:
    def __init__(self, per_minute: int):
        self.interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self._lock = threading.Lock()
        self._next = 0.0

    def acquire(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            wait = self._next - now
            self._next = max(now, self._next) + self.interval
        if wait > 0:
            time.sleep(wait)


def run_translation(ws: Workspace, client: LLMClient, glossary: Glossary, settings: EngineSettings,
                    on_progress: Callable[[int], None] | None = None) -> TranslateReport:
    report = TranslateReport()
    groups, skipped = group_units(ws.pending_entries())
    if skipped:
        ws.save_translations([TranslationUpdate(i, None, None, "skipped") for i in skipped])
        report.skipped = len(skipped)

    limiter = RateLimiter(settings.requests_per_minute)
    kinds = {g.handler: get_handler(g.handler).kind_label for g in groups}
    last_error: dict[int, str] = {}
    batch_size = settings.batch_size
    pending = [(g, b) for g in groups for b in make_batches(g.units, batch_size, settings.batch_max_chars)]

    def job(group: Group, batch: list[Unit]) -> list[UnitResult]:
        limiter.acquire()
        return translate_batch(client, batch, group.namespace, kinds[group.handler], glossary)

    for _attempt in range(settings.max_retries + 1):
        if not pending:
            break
        failed: dict[int, tuple[Group, list[Unit]]] = {}
        with ThreadPoolExecutor(max_workers=max(1, settings.concurrency)) as pool:
            futures = {pool.submit(job, g, b): (g, b) for g, b in pending}
            for fut in as_completed(futures):
                group, batch = futures[fut]
                report.requests += 1
                updates: list[TranslationUpdate] = []
                done = 0
                for unit, result in zip(batch, fut.result()):
                    if result.text is not None:
                        updates.extend(
                            TranslationUpdate(i, result.text, "llm", "done", client.model, result.note)
                            for i in unit.entry_ids
                        )
                        done += len(unit.entry_ids)
                    else:
                        last_error[id(unit)] = result.error or "未知错误"
                        failed.setdefault(id(group), (group, []))[1].append(unit)
                if updates:
                    ws.save_translations(updates)
                report.translated += done
                if on_progress and done:
                    on_progress(done)
        batch_size = max(1, batch_size // 4)
        pending = [
            (g, b) for g, us in failed.values()
            for b in make_batches(us, batch_size, settings.batch_max_chars)
        ]

    final: list[TranslationUpdate] = []
    for _group, batch in pending:
        for unit in batch:
            final.extend(
                TranslationUpdate(i, None, None, "failed", None, last_error.get(id(unit)))
                for i in unit.entry_ids
            )
            report.failed += len(unit.entry_ids)
    if final:
        ws.save_translations(final)
    return report


def estimate(entries: list[Entry], settings: EngineSettings, price_input: float,
             price_output: float) -> Estimate:
    groups, skipped = group_units(entries)
    units = [u for g in groups for u in g.units]
    requests = sum(len(make_batches(g.units, settings.batch_size, settings.batch_max_chars)) for g in groups)
    in_chars = sum(len(u.original) + len(u.hint) for u in units)
    out_chars = sum(len(u.original) for u in units)
    input_tokens = int(in_chars / CHARS_PER_TOKEN) + requests * PROMPT_OVERHEAD_TOKENS
    output_tokens = int(out_chars / CHARS_PER_TOKEN * OUTPUT_RATIO) + requests * 10
    cost = round(input_tokens / 1e6 * price_input + output_tokens / 1e6 * price_output, 4)
    return Estimate(len(entries) - len(skipped), len(units), requests, input_tokens, output_tokens, cost)
```

说明：
- 第一轮按 `batch_size` 分批；失败的条目在下一轮用 `batch_size // 4` 的更小批次重试，最多重试 `max_retries` 轮，仍失败的标记为 `failed`，并在 `note` 中记录最后一次错误
- 数据库写入只在主线程（`as_completed` 循环）中进行，工作线程只负责调用 API
- `test_estimate` 中 `cost` 保留 4 位小数，测试里用同样的公式比较

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_engine.py -v`
Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/translate/engine.py tests/test_engine.py
git commit -m "feat: 翻译引擎（去重、分批、并发、重试、成本预估）"
```

---

### Task 9: 资源包与 options.txt

**Files:**
- Create: `src/mctrans/output/__init__.py`（空文件）, `src/mctrans/output/resourcepack.py`, `src/mctrans/output/options.py`
- Test: `tests/test_resourcepack.py`, `tests/test_options.py`

**Interfaces:**
- Produces:
  - `resourcepack.pack_format_for(mc_version: str) -> int`（低于 1.16 抛 `ValueError`）
  - `resourcepack.pack_mcmeta(mc_version: str, description: str) -> bytes`
  - `resourcepack.build_pack_zip(files: dict[str, bytes], mcmeta: bytes) -> bytes`（内容相同则字节相同）
  - `options.read_packs(text: str) -> list[str] | None`
  - `options.get_option(text: str, key: str) -> str | None`
  - `options.set_option(text: str, key: str, value: str) -> str`（保留原换行风格；键不存在则追加）
  - `options.add_pack_top(text: str, pack_id: str) -> str`
  - `options.remove_packs(text: str, pack_ids: list[str]) -> str`（同时从 `resourcePacks` 和 `incompatibleResourcePacks` 移除）

- [ ] **Step 1: 写失败测试 `tests/test_resourcepack.py`**

```python
import io
import json
import zipfile

import pytest

from mctrans.output.resourcepack import build_pack_zip, pack_format_for, pack_mcmeta


@pytest.mark.parametrize("version, fmt", [
    ("1.16.5", 6), ("1.16.1", 5), ("1.18.2", 8), ("1.19.2", 9), ("1.19.4", 13),
    ("1.20.1", 15), ("1.20.4", 22), ("1.21.1", 34), ("1.21.4", 46), ("1.21.8", 64),
])
def test_pack_format(version, fmt):
    assert pack_format_for(version) == fmt


def test_pack_format_too_old():
    with pytest.raises(ValueError, match="1.16"):
        pack_format_for("1.12.2")


def test_mcmeta_old_version():
    meta = json.loads(pack_mcmeta("1.20.1", "desc"))
    assert meta == {"pack": {"pack_format": 15, "description": "desc"}}


def test_mcmeta_new_version_has_ranges():
    pack = json.loads(pack_mcmeta("1.21.1", "desc"))["pack"]
    assert pack["pack_format"] == 34 and pack["supported_formats"] == [34, 9999]
    newest = json.loads(pack_mcmeta("1.21.10", "desc"))["pack"]
    assert newest["min_format"] == 69 and newest["max_format"] == 9999


def test_build_pack_zip_deterministic():
    files = {"assets/a/lang/zh_cn.json": b"{}", "assets/b/lang/zh_cn.json": b"{}"}
    meta = pack_mcmeta("1.20.1", "d")
    first = build_pack_zip(files, meta)
    assert first == build_pack_zip(dict(reversed(list(files.items()))), meta)
    with zipfile.ZipFile(io.BytesIO(first)) as z:
        assert z.namelist() == ["pack.mcmeta", "assets/a/lang/zh_cn.json", "assets/b/lang/zh_cn.json"]
```

- [ ] **Step 2: 写失败测试 `tests/test_options.py`**

```python
from mctrans.output.options import add_pack_top, get_option, read_packs, remove_packs, set_option

SAMPLE = 'version:3465\r\nresourcePacks:["vanilla","file/other.zip"]\r\nlang:en_us\r\n'


def test_read_packs():
    assert read_packs(SAMPLE) == ["vanilla", "file/other.zip"]
    assert read_packs("lang:en_us\n") is None
    assert read_packs("resourcePacks:broken\n") == []


def test_get_and_set_option_keeps_crlf():
    assert get_option(SAMPLE, "lang") == "en_us"
    out = set_option(SAMPLE, "lang", "zh_cn")
    assert out == SAMPLE.replace("lang:en_us", "lang:zh_cn")
    appended = set_option("version:1\n", "lang", "zh_cn")
    assert appended == "version:1\nlang:zh_cn\n"


def test_add_pack_top():
    out = add_pack_top(SAMPLE, "file/mctrans-zh_cn.zip")
    assert read_packs(out) == ["vanilla", "file/other.zip", "file/mctrans-zh_cn.zip"]
    assert read_packs(add_pack_top(out, "file/mctrans-zh_cn.zip")) == read_packs(out)  # 不重复


def test_add_pack_to_empty_options():
    out = add_pack_top("", "file/x.zip")
    assert read_packs(out) == ["vanilla", "file/x.zip"]


def test_remove_packs():
    text = add_pack_top(SAMPLE, "file/x.zip") + 'incompatibleResourcePacks:["file/x.zip"]\r\n'
    out = remove_packs(text, ["file/x.zip"])
    assert read_packs(out) == ["vanilla", "file/other.zip"]
    assert get_option(out, "incompatibleResourcePacks") == "[]"
    assert remove_packs("lang:en_us\n", ["file/x.zip"]) == "lang:en_us\n"
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/test_resourcepack.py tests/test_options.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 4: 实现 `src/mctrans/output/resourcepack.py`（并创建空的 `src/mctrans/output/__init__.py`）**

```python
from __future__ import annotations

import io
import json
import re
import zipfile

# (最低版本, pack_format)，参考 Minecraft Wiki 的 pack format 列表
PACK_FORMATS: list[tuple[tuple[int, int, int], int]] = [
    ((1, 16, 0), 5), ((1, 16, 2), 6), ((1, 17, 0), 7), ((1, 18, 0), 8),
    ((1, 19, 0), 9), ((1, 19, 3), 12), ((1, 19, 4), 13), ((1, 20, 0), 15),
    ((1, 20, 2), 18), ((1, 20, 3), 22), ((1, 20, 5), 32), ((1, 21, 0), 34),
    ((1, 21, 2), 42), ((1, 21, 4), 46), ((1, 21, 5), 55), ((1, 21, 6), 63),
    ((1, 21, 7), 64), ((1, 21, 9), 69),
]
MAX_FORMAT = 9999


def _parse_version(version: str) -> tuple[int, int, int]:
    nums = [int(n) for n in re.findall(r"\d+", version)[:3]]
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]


def pack_format_for(mc_version: str) -> int:
    v = _parse_version(mc_version)
    fmt = None
    for min_version, f in PACK_FORMATS:
        if v >= min_version:
            fmt = f
    if fmt is None:
        raise ValueError(f"不支持的 Minecraft 版本：{mc_version}（当前仅支持 1.16 及以上）")
    return fmt


def pack_mcmeta(mc_version: str, description: str) -> bytes:
    fmt = pack_format_for(mc_version)
    pack: dict = {"pack_format": fmt, "description": description}
    if fmt >= 18:  # 1.20.2+ 支持 supported_formats，声明兼容所有更高版本
        pack["supported_formats"] = [fmt, MAX_FORMAT]
    if fmt >= 65:  # 1.21.9+ 改用 min_format / max_format
        pack["min_format"] = fmt
        pack["max_format"] = MAX_FORMAT
    return json.dumps({"pack": pack}, ensure_ascii=False, indent=2).encode("utf-8")


def build_pack_zip(files: dict[str, bytes], mcmeta: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in [("pack.mcmeta", mcmeta)] + sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return buf.getvalue()
```

注意：1.21.9 之后 `pack.mcmeta` 的格式字段有变化，上面同时写了新旧字段。Task 12 的手动验证会在新版本游戏中确认能正常加载；如果不能，需要按实际报错调整此函数。

- [ ] **Step 5: 实现 `src/mctrans/output/options.py`**

```python
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


def add_pack_top(text: str, pack_id: str) -> str:
    packs = read_packs(text) or ["vanilla"]
    packs = [p for p in packs if p != pack_id] + [pack_id]
    return set_option(text, PACKS_KEY, _dump_list(packs))


def remove_packs(text: str, pack_ids: list[str]) -> str:
    for key in (PACKS_KEY, INCOMPATIBLE_KEY):
        packs = _parse_list(get_option(text, key))
        if packs is None:
            continue
        text = set_option(text, key, _dump_list([p for p in packs if p not in pack_ids]))
    return text
```

说明：`options.txt` 中 `resourcePacks` 列表越靠后优先级越高，所以"放在最上层"就是追加到列表末尾。

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/test_resourcepack.py tests/test_options.py -v`
Expected: 14 + 5 = 19 passed

- [ ] **Step 7: 提交**

```bash
git add src/mctrans/output tests/test_resourcepack.py tests/test_options.py
git commit -m "feat: 资源包打包与 options.txt 读写"
```

---

### Task 10: 应用与还原

**Files:**
- Create: `src/mctrans/output/apply.py`
- Test: `tests/test_apply.py`

**Interfaces:**
- Consumes: `store.Workspace`, `handlers.all_handlers`, `handlers.base.OutputSink`, `resourcepack.*`, `options.*`
- Produces:
  - `apply.PACK_FILE = "mctrans-zh_cn.zip"`、`apply.PACK_ID = "file/mctrans-zh_cn.zip"`
  - `apply.ApplyError(Exception)`
  - `apply.build_outputs(ws: Workspace) -> tuple[OutputSink, int]`（第二项是写入的条目数；不含 `mod_builtin` 来源）
  - `apply.ApplyReport(entries: int, pack_files: int, instance_files: list[str], pack_path: Path | None, dry_run: bool)`
  - `apply.apply(root: Path, ws: Workspace, mc_version: str, set_lang: bool = False, dry_run: bool = False) -> ApplyReport`（已应用过会先自动还原）
  - `apply.RestoreReport(restored: list[str], removed: list[str], skipped: list[str])`
  - `apply.restore(root: Path) -> RestoreReport`（没有应用记录时抛 `ApplyError`）
  - `apply.is_applied(root: Path) -> bool`
  - 文件布局：备份与清单在 `<root>/.mctrans/backups/<时间戳>/`（`manifest.json`、`files/<相对路径>`）；指针文件 `<root>/.mctrans/applied.json` 内容 `{"manifest": "<清单相对路径>"}`

- [ ] **Step 1: 写失败测试 `tests/test_apply.py`**

```python
import io
import json
import zipfile

import pytest

from mctrans.handlers.base import Handler
from mctrans.models import Entry
from mctrans.output import apply as apply_mod
from mctrans.output.apply import PACK_ID, ApplyError, apply, is_applied, restore
from mctrans.output.options import get_option, read_packs
from mctrans.store import TranslationUpdate, Workspace


def e(key, text, zh=None, ns="a"):
    return Entry(handler="lang_json", source=f"jar:assets/{ns}/lang/en_us.json",
                 locator=key, namespace=ns, original=text, context=key, existing_zh=zh)


@pytest.fixture
def ws(instance_dir):
    w = Workspace.for_instance(instance_dir)
    w.sync_entries("lang_json", [e("k1", "Gear", zh="齿轮"), e("k2", "Shaft"), e("k3", "Belt")])
    w.apply_builtin()
    ids = {p.locator: p.id for p in w.pending_entries()}
    w.save_translations([TranslationUpdate(ids["k2"], "传动杆", "llm", "done", "m")])
    yield w
    w.close()


def read_zip(path):
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as z:
        return {n: z.read(n) for n in z.namelist()}


def test_apply_creates_pack_and_enables_it(instance_dir, ws):
    (instance_dir / "options.txt").write_bytes(b'version:3465\r\nresourcePacks:["vanilla"]\r\nlang:en_us\r\n')
    report = apply(instance_dir, ws, "1.20.1", set_lang=True)
    assert report.entries == 1 and report.pack_files == 1
    files = read_zip(instance_dir / "resourcepacks" / "mctrans-zh_cn.zip")
    assert json.loads(files["assets/a/lang/zh_cn.json"]) == {"k2": "传动杆"}  # 不含 mod 自带的 k1
    assert json.loads(files["pack.mcmeta"])["pack"]["pack_format"] == 15
    text = (instance_dir / "options.txt").read_bytes().decode("utf-8")
    assert read_packs(text) == ["vanilla", PACK_ID]
    assert get_option(text, "lang") == "zh_cn"
    assert "\r\n" in text
    assert is_applied(instance_dir)


def test_apply_without_options_file(instance_dir, ws):
    apply(instance_dir, ws, "1.20.1")
    assert read_packs((instance_dir / "options.txt").read_text(encoding="utf-8")) == ["vanilla", PACK_ID]


def test_dry_run_writes_nothing(instance_dir, ws):
    report = apply(instance_dir, ws, "1.20.1", dry_run=True)
    assert report.dry_run and report.entries == 1
    assert not (instance_dir / "resourcepacks").exists()
    assert not is_applied(instance_dir)


def test_restore_round_trip(instance_dir, ws):
    original = b'resourcePacks:["vanilla"]\nlang:en_us\n'
    (instance_dir / "options.txt").write_bytes(original)
    apply(instance_dir, ws, "1.20.1", set_lang=True)
    report = restore(instance_dir)
    assert not (instance_dir / "resourcepacks" / "mctrans-zh_cn.zip").exists()
    assert (instance_dir / "options.txt").read_bytes() == original
    assert report.removed == ["resourcepacks/mctrans-zh_cn.zip"]
    assert not is_applied(instance_dir)
    with pytest.raises(ApplyError):
        restore(instance_dir)


def test_restore_survives_game_rewriting_options(instance_dir, ws):
    apply(instance_dir, ws, "1.20.1")
    opts = instance_dir / "options.txt"
    opts.write_text(opts.read_text(encoding="utf-8") + "fov:0.5\n", encoding="utf-8")
    restore(instance_dir)
    text = opts.read_text(encoding="utf-8")
    assert read_packs(text) == ["vanilla"] and "fov:0.5" in text


def test_restore_skips_modified_pack(instance_dir, ws):
    apply(instance_dir, ws, "1.20.1")
    (instance_dir / "resourcepacks" / "mctrans-zh_cn.zip").write_bytes(b"user changed")
    report = restore(instance_dir)
    assert report.skipped == ["resourcepacks/mctrans-zh_cn.zip"]
    assert (instance_dir / "resourcepacks" / "mctrans-zh_cn.zip").exists()


def test_apply_twice_is_idempotent(instance_dir, ws):
    apply(instance_dir, ws, "1.20.1")
    apply(instance_dir, ws, "1.20.1")
    assert read_packs((instance_dir / "options.txt").read_text(encoding="utf-8")) == ["vanilla", PACK_ID]


class FileHandler(Handler):
    """测试用：把译文直接写进实例内文件，模拟任务书类处理器。"""
    name = "lang_json"
    kind_label = "x"

    def detect(self, instance):
        return True

    def extract(self, instance):
        return iter(())

    def write(self, items, sink):
        sink.add_instance_file("config/quests.txt", "\n".join(i.text for i in items).encode("utf-8"))
        sink.add_instance_file("config/new.txt", b"new")


def test_instance_files_backup_and_restore(instance_dir, ws, monkeypatch):
    monkeypatch.setattr(apply_mod, "all_handlers", lambda: [FileHandler()])
    quests = instance_dir / "config" / "quests.txt"
    quests.parent.mkdir()
    quests.write_bytes(b"original quests")
    report = apply(instance_dir, ws, "1.20.1")
    assert sorted(report.instance_files) == ["config/new.txt", "config/quests.txt"]
    assert quests.read_bytes() == "传动杆".encode("utf-8")
    result = restore(instance_dir)
    assert quests.read_bytes() == b"original quests"
    assert not (instance_dir / "config" / "new.txt").exists()
    assert sorted(result.restored) == ["config/quests.txt"]
    assert sorted(result.removed) == ["config/new.txt"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_apply.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 `src/mctrans/output/apply.py`**

```python
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..handlers import all_handlers
from ..handlers.base import OutputSink
from ..store import Workspace
from .options import add_pack_top, get_option, remove_packs, set_option
from .resourcepack import build_pack_zip, pack_mcmeta

PACK_FILE = "mctrans-zh_cn.zip"
PACK_ID = f"file/{PACK_FILE}"
PACK_DESCRIPTION = "mctrans 自动汉化"
EXCLUDED_ORIGINS = {"mod_builtin"}  # jar 内已自带，无需重复写入


class ApplyError(Exception):
    pass


@dataclass
class ApplyReport:
    entries: int
    pack_files: int
    instance_files: list[str]
    pack_path: Path | None
    dry_run: bool


@dataclass
class RestoreReport:
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pointer(root: Path) -> Path:
    return root / ".mctrans" / "applied.json"


def is_applied(root: Path) -> bool:
    return _pointer(Path(root)).exists()


def build_outputs(ws: Workspace) -> tuple[OutputSink, int]:
    sink = OutputSink()
    count = 0
    for handler in all_handlers():
        items = [it for it in ws.translated_items(handler.name) if it.origin not in EXCLUDED_ORIGINS]
        if items:
            handler.write(items, sink)
            count += len(items)
    return sink, count


def apply(root: Path, ws: Workspace, mc_version: str, set_lang: bool = False,
          dry_run: bool = False) -> ApplyReport:
    root = Path(root)
    sink, count = build_outputs(ws)
    pack_path = root / "resourcepacks" / PACK_FILE if sink.pack_files else None
    report = ApplyReport(count, len(sink.pack_files), sorted(sink.instance_files), pack_path, dry_run)
    if dry_run:
        return report
    if is_applied(root):
        restore(root)

    run_dir = root / ".mctrans" / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir.mkdir(parents=True)
    manifest: dict = {"created": [], "modified": [], "options": {"packs": [], "lang_set": False, "lang_before": None}}

    for rel, data in sorted(sink.instance_files.items()):
        target = root / rel
        backup_rel = None
        if target.exists():
            backup = run_dir / "files" / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            backup_rel = backup.relative_to(root).as_posix()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        manifest["modified"].append({"path": rel, "backup": backup_rel, "hash": _sha256(data)})

    if pack_path is not None or set_lang:
        opts = root / "options.txt"
        text = opts.read_bytes().decode("utf-8") if opts.exists() else ""
        if pack_path is not None:
            data = build_pack_zip(sink.pack_files, pack_mcmeta(mc_version, PACK_DESCRIPTION))
            pack_path.parent.mkdir(parents=True, exist_ok=True)
            pack_path.write_bytes(data)
            manifest["created"].append({"path": f"resourcepacks/{PACK_FILE}", "hash": _sha256(data)})
            text = add_pack_top(text, PACK_ID)
            manifest["options"]["packs"].append(PACK_ID)
        if set_lang:
            manifest["options"]["lang_set"] = True
            manifest["options"]["lang_before"] = get_option(text, "lang")
            text = set_option(text, "lang", "zh_cn")
        opts.write_bytes(text.encode("utf-8"))

    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    _pointer(root).write_text(
        json.dumps({"manifest": manifest_path.relative_to(root).as_posix()}), encoding="utf-8"
    )
    return report


def restore(root: Path) -> RestoreReport:
    root = Path(root)
    pointer = _pointer(root)
    if not pointer.exists():
        raise ApplyError("没有找到已应用的汉化记录，无需还原")
    manifest = json.loads((root / json.loads(pointer.read_text(encoding="utf-8"))["manifest"]).read_text(encoding="utf-8"))
    report = RestoreReport()

    for item in manifest["modified"]:
        target = root / item["path"]
        if target.exists() and _sha256(target.read_bytes()) != item["hash"]:
            report.skipped.append(item["path"])
            continue
        if item["backup"]:
            shutil.copy2(root / item["backup"], target)
            report.restored.append(item["path"])
        else:
            target.unlink(missing_ok=True)
            report.removed.append(item["path"])

    for item in manifest["created"]:
        target = root / item["path"]
        if target.exists() and _sha256(target.read_bytes()) != item["hash"]:
            report.skipped.append(item["path"])
            continue
        target.unlink(missing_ok=True)
        report.removed.append(item["path"])

    opts_info = manifest["options"]
    opts = root / "options.txt"
    if opts.exists() and (opts_info["packs"] or opts_info["lang_set"]):
        text = opts.read_bytes().decode("utf-8")
        text = remove_packs(text, opts_info["packs"])
        if opts_info["lang_set"]:
            text = set_option(text, "lang", opts_info["lang_before"] or "en_us")
        opts.write_bytes(text.encode("utf-8"))

    pointer.unlink()
    return report
```

说明：
- `options.txt` 不按哈希整体还原，因为游戏每次退出都会重写它；还原时只移除我们添加的资源包并恢复 `lang`
- 被 skip 的资源包仍留在 `options.txt` 里会被移除引用，这是有意为之：用户改过的文件保留，但我们不再负责启用它
- `test_restore_round_trip` 中原文件末尾有换行且没有 `incompatibleResourcePacks` 行，还原后字节完全一致

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_apply.py -v`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/output/apply.py tests/test_apply.py
git commit -m "feat: 应用汉化（资源包、备份清单）与一键还原"
```

---

### Task 11: 命令行

**Files:**
- Create: `src/mctrans/cli.py`, `src/mctrans/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: 前面所有任务的接口
- Produces: 命令 `scan`、`translate`、`apply`、`restore`、`status`、`run`；模块级 `app: typer.Typer`；测试通过 monkeypatch `mctrans.cli.client_from_config` 注入 `FakeClient`

- [ ] **Step 1: 写失败测试 `tests/test_cli.py`**

```python
import io
import json
import zipfile

import pytest
from typer.testing import CliRunner

from helpers import FakeClient, lang, make_jar
from mctrans import cli
from mctrans.output.options import read_packs

runner = CliRunner()


@pytest.fixture
def pack(instance_dir, monkeypatch):
    make_jar(instance_dir / "mods" / "create.jar", {
        "META-INF/mods.toml": 'modId="create"',
        "assets/create/lang/en_us.json": lang({
            "item.create.gear": "Gear", "item.create.shaft": "Shaft", "tip.create.x": "Use the Gear %s",
        }),
        "assets/create/lang/zh_cn.json": lang({"item.create.gear": "齿轮"}),
    })
    fake = FakeClient()
    monkeypatch.setattr(cli, "client_from_config", lambda p: fake)
    return instance_dir, fake


def invoke(*args):
    result = runner.invoke(cli.app, [str(a) for a in args])
    assert result.exit_code == 0, result.output
    return result


def test_scan_then_status(pack):
    root, _ = pack
    out = invoke("scan", root).output
    assert "1.20.1" in out and "forge" in out
    out = invoke("status", root).output
    assert "done" in out and "pending" in out


def test_translate_requires_scan(pack):
    root, _ = pack
    result = runner.invoke(cli.app, ["translate", str(root)])
    assert result.exit_code == 1 and "scan" in result.output


def test_translate_dry_run_makes_no_calls(pack):
    root, fake = pack
    invoke("scan", root)
    out = invoke("translate", root, "--dry-run").output
    assert "预计" in out and fake.calls == []


def test_full_flow_and_restore(pack):
    root, fake = pack
    invoke("run", root, "--yes", "--set-lang")
    # 术语表来自 mod 自带译文：Gear → 齿轮
    assert any("- Gear → 齿轮" in call for call in fake.calls)
    zpath = root / "resourcepacks" / "mctrans-zh_cn.zip"
    with zipfile.ZipFile(io.BytesIO(zpath.read_bytes())) as z:
        data = json.loads(z.read("assets/create/lang/zh_cn.json"))
    assert data == {"item.create.shaft": "译Shaft", "tip.create.x": "译Use the Gear %s"}
    text = (root / "options.txt").read_text(encoding="utf-8")
    assert read_packs(text)[-1] == "file/mctrans-zh_cn.zip" and "lang:zh_cn" in text
    invoke("restore", root)
    assert not zpath.exists()


def test_missing_api_key_message(instance_dir):
    make_jar(instance_dir / "mods" / "a.jar", {"assets/a/lang/en_us.json": lang({"k": "Hello"})})
    invoke("scan", instance_dir)
    result = runner.invoke(cli.app, ["translate", str(instance_dir)])
    assert result.exit_code == 1 and "DEEPSEEK_API_KEY" in result.output


def test_bad_instance_dir(tmp_path):
    result = runner.invoke(cli.app, ["scan", str(tmp_path)])
    assert result.exit_code == 1 and "mods" in result.output
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL，`ImportError: cannot import name 'cli'`

- [ ] **Step 3: 实现 `src/mctrans/cli.py`**

```python
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table

from .config import ConfigError, home_dir, load_settings
from .handlers import all_handlers
from .instance import InstanceError, load_instance
from .output.apply import ApplyError, apply as apply_outputs, restore as restore_outputs
from .store import Workspace
from .translate.client import client_from_config
from .translate.engine import EngineSettings, estimate, run_translation
from .translate.glossary import build_glossary, load_user_glossary

app = typer.Typer(help="Minecraft 整合包自动汉化工具", no_args_is_help=True, add_completion=False)
console = Console()

MAX_WARNINGS_SHOWN = 20

InstanceArg = Annotated[
    Path, typer.Argument(help="游戏实例目录（含 mods/ 的那一层）", exists=True, file_okay=False, resolve_path=True)
]
ProviderOpt = Annotated[Optional[str], typer.Option("--provider", "-p", help="翻译服务：deepseek / qwen / 自定义")]


def _fail(message: str) -> None:
    console.print(f"[red]错误：[/red]{message}")
    raise typer.Exit(1)


def _open_scanned(root: Path) -> Workspace:
    ws = Workspace.for_instance(root)
    if ws.get_meta("mc_version") is None:
        ws.close()
        _fail("这个实例还没有扫描过，请先运行 mctrans scan")
    return ws


def _write_log(root: Path, name: str, lines: list[str]) -> Path:
    log_dir = root / ".mctrans" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"{name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def do_scan(root: Path, mc_version: str | None) -> None:
    try:
        inst = load_instance(root, mc_version)
    except InstanceError as exc:
        _fail(str(exc))
    flags = " · 检测到 I18nUpdateMod" if inst.has_i18nupdatemod else ""
    console.print(f"Minecraft {inst.mc_version} · 加载器 {inst.loader} · {len(inst.mod_jars)} 个 mod{flags}")

    table = Table("处理器", "新增", "变更", "未变", "过时")
    warnings: list[str] = []
    with Workspace.for_instance(inst.root) as ws:
        ws.set_meta("mc_version", inst.mc_version)
        ws.set_meta("loader", inst.loader)
        ws.set_meta("has_i18nupdatemod", "1" if inst.has_i18nupdatemod else "0")
        run_id = ws.start_run("scan", {"mc_version": inst.mc_version})
        totals: dict[str, int] = {}
        for handler in all_handlers():
            if not handler.detect(inst):
                continue
            s = ws.sync_entries(handler.name, handler.extract(inst))
            table.add_row(handler.name, str(s.added), str(s.changed), str(s.unchanged), str(s.stale))
            warnings.extend(handler.warnings)
            totals[handler.name] = s.added + s.changed + s.unchanged
        builtin = ws.apply_builtin()
        ws.finish_run(run_id, {"entries": totals, "builtin": builtin, "warnings": len(warnings)})
    console.print(table)
    console.print(f"采用 mod 自带中文：{builtin} 条")
    if warnings:
        for w in warnings[:MAX_WARNINGS_SHOWN]:
            console.print(f"[yellow]警告：[/yellow]{w}")
        log = _write_log(inst.root, "scan", warnings)
        console.print(f"共 {len(warnings)} 条警告，完整列表见 {log}")


def do_translate(root: Path, provider: str | None, dry_run: bool, yes: bool) -> None:
    try:
        settings = load_settings()
        pcfg = settings.provider(provider)
    except ConfigError as exc:
        _fail(str(exc))
    engine_settings = EngineSettings.from_settings(settings)
    ws = _open_scanned(root)
    with ws:
        ws.apply_builtin()
        entries = ws.pending_entries()
        est = estimate(entries, engine_settings, pcfg.price_input, pcfg.price_output)
        console.print(
            f"待翻译 {est.entries} 条（去重后 {est.units} 条），预计 {est.requests} 次请求，"
            f"输入约 {est.input_tokens:,} tokens、输出约 {est.output_tokens:,} tokens，"
            f"使用 {pcfg.name}/{pcfg.model} 预计费用约 ¥{est.cost:.2f}（按配置单价估算，以官网为准）"
        )
        if dry_run or est.entries == 0:
            return
        try:
            client = client_from_config(pcfg)  # 先检查 API Key，再请求确认
        except ConfigError as exc:
            _fail(str(exc))
        if not yes and not typer.confirm("开始翻译吗？", default=True):
            raise typer.Exit(0)
        glossary = build_glossary(ws.glossary_pairs(), load_user_glossary(home_dir() / "glossary.tsv"))
        run_id = ws.start_run("translate", {"provider": pcfg.name, "model": pcfg.model})
        with Progress(TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(),
                      TimeRemainingColumn(), console=console) as progress:
            task = progress.add_task("翻译中", total=est.entries)
            report = run_translation(ws, client, glossary, engine_settings,
                                     on_progress=lambda n: progress.advance(task, n))
        ws.finish_run(run_id, vars(report))
    console.print(
        f"完成：翻译 {report.translated} 条，失败 {report.failed} 条，"
        f"跳过（无需翻译）{report.skipped} 条，共 {report.requests} 次请求"
    )
    if report.failed:
        console.print("[yellow]有条目翻译失败，再次运行 translate 会只重试失败的条目[/yellow]")


def do_apply(root: Path, set_lang: bool, dry_run: bool) -> None:
    ws = _open_scanned(root)
    with ws:
        report = apply_outputs(root, ws, ws.get_meta("mc_version"), set_lang=set_lang, dry_run=dry_run)
    prefix = "（预演，未写入）" if dry_run else ""
    console.print(f"{prefix}写入译文 {report.entries} 条，资源包内文件 {report.pack_files} 个")
    if report.pack_path:
        console.print(f"资源包：{report.pack_path}")
    for rel in report.instance_files:
        console.print(f"修改文件：{rel}")
    if not dry_run:
        console.print("已自动备份，可用 mctrans restore 一键还原")


@app.command()
def scan(instance: InstanceArg,
         mc_version: Annotated[Optional[str], typer.Option("--mc-version", help="手动指定 Minecraft 版本")] = None):
    """扫描实例，提取待翻译文本。"""
    do_scan(instance, mc_version)


@app.command()
def translate(instance: InstanceArg, provider: ProviderOpt = None,
              dry_run: Annotated[bool, typer.Option("--dry-run", help="只预估费用，不调用 API")] = False,
              yes: Annotated[bool, typer.Option("--yes", "-y", help="跳过确认")] = False):
    """翻译待处理的条目（可中断，再次运行会续跑）。"""
    do_translate(instance, provider, dry_run, yes)


@app.command("apply")
def apply_cmd(instance: InstanceArg,
              set_lang: Annotated[bool, typer.Option("--set-lang", help="同时把游戏语言设为简体中文")] = False,
              dry_run: Annotated[bool, typer.Option("--dry-run", help="只显示将要写入的内容")] = False):
    """把译文写入实例（生成资源包并启用，修改前自动备份）。"""
    do_apply(instance, set_lang, dry_run)


@app.command("restore")
def restore_cmd(instance: InstanceArg):
    """撤销 apply 的所有修改。"""
    try:
        report = restore_outputs(instance)
    except ApplyError as exc:
        _fail(str(exc))
    console.print(f"已还原 {len(report.restored)} 个文件，删除 {len(report.removed)} 个生成文件")
    for rel in report.skipped:
        console.print(f"[yellow]跳过（应用后被改动过）：[/yellow]{rel}")


@app.command()
def status(instance: InstanceArg):
    """查看翻译进度。"""
    ws = _open_scanned(instance)
    with ws:
        console.print(f"Minecraft {ws.get_meta('mc_version')} · 加载器 {ws.get_meta('loader')}")
        table = Table("状态", "条目数")
        for name, n in sorted(ws.status_counts().items()):
            table.add_row(name, str(n))
    console.print(table)


@app.command()
def run(instance: InstanceArg,
        mc_version: Annotated[Optional[str], typer.Option("--mc-version", help="手动指定 Minecraft 版本")] = None,
        provider: ProviderOpt = None,
        set_lang: Annotated[bool, typer.Option("--set-lang", help="同时把游戏语言设为简体中文")] = False,
        yes: Annotated[bool, typer.Option("--yes", "-y", help="跳过确认")] = False):
    """一键执行：扫描 → 翻译 → 应用。"""
    do_scan(instance, mc_version)
    do_translate(instance, provider, dry_run=False, yes=yes)
    do_apply(instance, set_lang, dry_run=False)
```

`src/mctrans/__main__.py`：

```python
from .cli import app

app()
```

说明：`load_instance` 返回的 `inst.root` 是绝对路径；CLI 参数用了 `resolve_path=True`，所以工作区路径一致。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 6 passed

- [ ] **Step 5: 运行全部测试**

Run: `uv run pytest -q`
Expected: 116 passed

- [ ] **Step 6: 提交**

```bash
git add src/mctrans/cli.py src/mctrans/__main__.py tests/test_cli.py
git commit -m "feat: 命令行（scan/translate/apply/restore/status/run）"
```

---

### Task 12: 使用说明与真实环境验证

**Files:**
- Create: `README.md`

- [ ] **Step 1: 编写 `README.md`**

````markdown
# mctrans：Minecraft 整合包自动汉化工具

把英文整合包（已安装的游戏实例目录）自动翻译成简体中文。当前版本支持 mod 语言文件，MC 1.16 及以上。

## 安装

需要 [uv](https://docs.astral.sh/uv/)。

```
uv sync
```

## 配置 API Key

DeepSeek：设置环境变量 `DEEPSEEK_API_KEY`
通义千问（阿里云百炼）：设置环境变量 `DASHSCOPE_API_KEY`

也可以在 `~/.mctrans/config.toml` 中配置：

```toml
default_provider = "deepseek"   # 或 "qwen"
concurrency = 4                 # 并发请求数
requests_per_minute = 60        # 每分钟请求上限，0 为不限

[provider.deepseek]
api_key = "sk-..."
model = "deepseek-chat"

[provider.qwen]
model = "qwen-plus"
```

任何 OpenAI 兼容服务都可以加为自定义 provider（填写 `base_url`、`model`、`api_key`）。

## 使用

```
uv run mctrans run <实例目录> --set-lang     # 一键：扫描 → 翻译 → 应用
```

分步执行：

```
uv run mctrans scan <实例目录>               # 扫描，识别版本和 mod
uv run mctrans translate <实例目录> --dry-run # 预估费用
uv run mctrans translate <实例目录>           # 翻译（可中断，再次运行会续跑）
uv run mctrans apply <实例目录> --set-lang    # 生成资源包并启用
uv run mctrans restore <实例目录>             # 一键还原
uv run mctrans status <实例目录>              # 查看进度
```

实例目录是包含 `mods/` 的那一层，例如 HMCL/PCL 开启版本隔离时的 `.minecraft/versions/<版本名>/`，或 Prism 的 `instances/<实例名>/minecraft/`。版本识别失败时用 `--mc-version 1.20.1` 指定。

## 自定义术语表

在 `~/.mctrans/glossary.tsv` 中每行写一条 `英文<Tab>中文`，优先级高于自动提取的术语。

## 说明

- 工作数据保存在实例目录下的 `.mctrans/`，分发整合包时请排除这个目录
- mod 自带的中文会被直接采用，不会重复翻译
- 生成的资源包为 `resourcepacks/mctrans-zh_cn.zip`，放在资源包列表最上层
````

- [ ] **Step 2: 提交**

```bash
git add README.md
git commit -m "docs: 添加使用说明"
```

- [ ] **Step 3: 真实环境验证（需要用户配合）**

需要用户提供一个真实的整合包实例目录和 API Key。依次执行：

1. `uv run mctrans scan <实例目录>`：检查识别出的版本、加载器是否正确，警告是否合理
2. `uv run mctrans translate <实例目录> --dry-run`：检查条目数和预估费用是否合理
3. 为控制费用，可以先复制一份实例、只保留几个 mod，跑一次 `translate`，抽查数据库中的译文质量：
   `uv run python -c "import sqlite3;c=sqlite3.connect(r'<实例目录>/.mctrans/workspace.db');[print(r) for r in c.execute(\"select e.original,t.text from entries e join translations t on t.entry_id=e.id where t.origin='llm' limit 30\")]"`
4. `uv run mctrans apply <实例目录> --set-lang`，启动游戏确认：资源包已启用、无"版本不兼容"提示、物品名和提示文本显示为中文、格式码颜色正常
5. `uv run mctrans restore <实例目录>`，确认恢复原样

把发现的问题（错译类型、格式码损坏、资源包加载问题）记录下来，作为后续计划的输入。
