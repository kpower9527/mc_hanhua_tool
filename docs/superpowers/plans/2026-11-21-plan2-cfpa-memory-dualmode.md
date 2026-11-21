# 计划 2：CFPA 集成、全局记忆库与 I18nUpdateMod 双模式 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 扫描时自动下载 CFPA 汉化包并采用其中的人工译文；跨整合包复用翻译记忆；根据是否安装 I18nUpdateMod，自动在"独立模式"和"配合模式（补全包 + 修正包）"之间切换输出。

**Architecture:** 新增 `cfpa/`（版本映射、下载缓存、索引）与 `memory.py`（全局记忆库）。`match.py` 按优先级把 mod 自带 → CFPA → 记忆库中的译文填入工作区（`Workspace.fill_from` 通用填充）。输出层改为按模式把条目分配到不同资源包，并按 I18nUpdateMod 的包 ID 排列加载顺序。

**Tech Stack:** 同计划 1；新增依赖 `httpx`（下载 CFPA，支持 `HTTPS_PROXY` 等代理环境变量）。

**Spec:** `docs/superpowers/specs/2026-11-21-mc-modpack-translator-design.md`（第 4、7.1 节）

## 实现前验证结论（2026-11-21 调研）

**CFPA 发布**
- `autobuild` 是滚动发布，下载地址固定：`https://github.com/CFPAOrg/Minecraft-Mod-Language-Package/releases/download/autobuild/<文件名>`
- 资源包文件名：`Minecraft-Mod-Language-Modpack-<版本，点换成横线>[-Fabric].zip`，例如 `...-1-20.zip`、`...-1-20-Fabric.zip`、`...-26-1.zip`
- 校验文件：`<版本>[-fabric].md5`，例如 `1.20.md5`、`1.20-fabric.md5`，内容是 32 位大写十六进制 md5
- 现有版本线：1.16、1.18、1.19（无 Fabric）、1.20、1.21、26.1，均有 Forge 包和 Fabric 包（1.19 除外）
- 包内只有 `assets/<ns>/lang/zh_cn.json`（另有少量 `.json5`/`.lang`、Patchouli 手册等），**没有 en_us 原文**
- Fabric 包只含 Fabric 版翻译（1.20：Forge 包 606 个命名空间，Fabric 包 258 个）

**I18nUpdateMod（CFPAOrg/I18nUpdateMod3）**
- 生成的资源包：`resourcepacks/Minecraft-Mod-Language-Modpack-Converted-<MC版本>.zip`，在 options.txt 中的 ID 为 `file/Minecraft-Mod-Language-Modpack-Converted-<MC版本>.zip`
- `GameConfig.addResourcePack`：列表中已有该 ID 时不做任何修改；否则移除名字含 `Minecraft-Mod-Language-Modpack` 的旧项，并把新 ID 追加到末尾（最高优先级）
- NeoForge 运行时激活：仅在列表中没有 I18n 包（首次注册）时把它移到末尾

**对设计的调整**
1. CFPA 包没有英文原文，无法比对"英文是否变化"。改为比对参数签名（`%s`、`%1$d`、`{0}` 等的类型与数量）：不一致视为过时译文，不采用，交给 LLM 翻译
2. 无法从 CFPA 构建"全量中英术语表"。术语表沿用计划 1 的方式，来源于当前整合包已有译文（现在包含 CFPA 匹配到的物品名）
3. 配合模式下预先把 I18n 包 ID 写入 options.txt，补全包放在它之下、修正包放在它之上；I18nUpdateMod 看到 ID 已存在便不会调整顺序

## Global Constraints

- 计划 1 的全局约束继续有效（Python ≥ 3.11、`uv run`、中文输出、`MCTRANS_HOME`、origin/status 取值、`confirmed` 保护、测试不发真实网络请求）
- 译文优先级：`human` > `mod_builtin` > `cfpa` > `memory` > `llm`
- CFPA 缓存目录：`home_dir() / "cfpa"`；全局记忆库：`home_dir() / "memory.db"`
- 配置新增：`cfpa_base_url`（默认上面的 autobuild 地址，可改为镜像）、`cfpa_check_hours`（默认 6，两次检查更新的最小间隔）
- 资源包文件名：独立模式 `mctrans-zh_cn.zip`；配合模式补全包 `mctrans-zh_cn-fill.zip`、修正包 `mctrans-zh_cn-fix.zip`
- 网络失败不得中断扫描：有缓存用缓存，没有缓存则跳过 CFPA 并给出警告

## 文件结构

```
src/mctrans/
├── mcversion.py           # 新增：parse_version（从 resourcepack 移出，供多处使用）
├── cfpa/
│   ├── __init__.py
│   ├── versions.py        # CfpaAsset、cfpa_assets
│   ├── index.py           # CfpaIndex、compatible
│   └── cache.py           # CfpaCache、CacheResult、http_fetch
├── memory.py              # Memory、MemoryRecord
├── match.py               # run_matching、remember_translations、MatchStats
├── store.py               # 修改：fill_from、origin_counts
├── config.py              # 修改：cfpa_base_url、cfpa_check_hours
├── handlers/base.py       # 修改：Handler.output
├── output/options.py      # 修改：place_around
├── output/apply.py        # 修改：模式、多资源包
├── output/resourcepack.py # 修改：改用 mcversion.parse_version
└── cli.py                 # 修改：scan 接入 CFPA/记忆库，translate 写回记忆库，apply --mode，status 显示来源
```

---

### Task 1: 版本解析抽取与 CFPA 版本映射

**Files:**
- Create: `src/mctrans/mcversion.py`, `src/mctrans/cfpa/__init__.py`（空）, `src/mctrans/cfpa/versions.py`
- Modify: `src/mctrans/output/resourcepack.py`
- Test: `tests/test_cfpa_versions.py`

**Interfaces:**
- Produces:
  - `mcversion.parse_version(version: str) -> tuple[int, int, int]`
  - `cfpa.versions.CfpaAsset(key: str, zip_name: str, md5_name: str)`（frozen dataclass）
  - `cfpa.versions.cfpa_assets(mc_version: str, loader: str) -> list[CfpaAsset]`：按优先级排列（排在前面的优先）；Fabric/Quilt 加载器 Fabric 包在前，其他加载器 Forge 包在前；MC 低于 1.16 返回空列表

- [ ] **Step 1: 写失败测试 `tests/test_cfpa_versions.py`**

```python
import pytest

from mctrans.cfpa.versions import cfpa_assets
from mctrans.mcversion import parse_version


def test_parse_version():
    assert parse_version("1.20.1") == (1, 20, 1)
    assert parse_version("1.21") == (1, 21, 0)
    assert parse_version("26.1-pre2") == (26, 1, 2)


def keys(mc, loader):
    return [a.key for a in cfpa_assets(mc, loader)]


@pytest.mark.parametrize("mc, loader, expected", [
    ("1.20.1", "forge", ["1.20", "1.20-fabric"]),
    ("1.20.1", "neoforge", ["1.20", "1.20-fabric"]),
    ("1.20.1", "fabric", ["1.20-fabric", "1.20"]),
    ("1.21.1", "unknown", ["1.21", "1.21-fabric"]),
    ("1.19.2", "fabric", ["1.19"]),
    ("1.17.1", "forge", ["1.16", "1.16-fabric"]),
    ("26.1", "fabric", ["26.1-fabric", "26.1"]),
    ("1.12.2", "forge", []),
])
def test_cfpa_assets_selection(mc, loader, expected):
    assert keys(mc, loader) == expected


def test_asset_file_names():
    forge, fabric = cfpa_assets("1.20.1", "forge")
    assert forge.zip_name == "Minecraft-Mod-Language-Modpack-1-20.zip"
    assert forge.md5_name == "1.20.md5"
    assert fabric.zip_name == "Minecraft-Mod-Language-Modpack-1-20-Fabric.zip"
    assert fabric.md5_name == "1.20-fabric.md5"
    assert cfpa_assets("26.1", "forge")[0].zip_name == "Minecraft-Mod-Language-Modpack-26-1.zip"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_cfpa_versions.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 `src/mctrans/mcversion.py`**

```python
from __future__ import annotations

import re


def parse_version(version: str) -> tuple[int, int, int]:
    """把 "1.20.1"、"1.21"、"26.1-pre2" 之类的版本号解析成三元组，用于比较大小。"""
    nums = [int(n) for n in re.findall(r"\d+", version)[:3]]
    while len(nums) < 3:
        nums.append(0)
    return nums[0], nums[1], nums[2]
```

- [ ] **Step 4: 修改 `src/mctrans/output/resourcepack.py`**

删除其中的 `_parse_version` 函数和 `import re`，在文件头部加入：

```python
from ..mcversion import parse_version
```

并把 `pack_format_for` 中的 `v = _parse_version(mc_version)` 改为 `v = parse_version(mc_version)`。

- [ ] **Step 5: 实现 `src/mctrans/cfpa/versions.py`（并创建空的 `src/mctrans/cfpa/__init__.py`）**

```python
from __future__ import annotations

from dataclasses import dataclass

from ..mcversion import parse_version

# (最低 MC 版本, CFPA 版本线, 是否有 Fabric 包)
CFPA_LINES: list[tuple[tuple[int, int], str, bool]] = [
    ((1, 16), "1.16", True),
    ((1, 18), "1.18", True),
    ((1, 19), "1.19", False),
    ((1, 20), "1.20", True),
    ((1, 21), "1.21", True),
    ((26, 1), "26.1", True),
]
FABRIC_LOADERS = ("fabric", "quilt")


@dataclass(frozen=True)
class CfpaAsset:
    key: str
    zip_name: str
    md5_name: str


def _asset(line: str, fabric: bool) -> CfpaAsset:
    dashed = line.replace(".", "-")
    if fabric:
        return CfpaAsset(f"{line}-fabric", f"Minecraft-Mod-Language-Modpack-{dashed}-Fabric.zip", f"{line}-fabric.md5")
    return CfpaAsset(line, f"Minecraft-Mod-Language-Modpack-{dashed}.zip", f"{line}.md5")


def cfpa_assets(mc_version: str, loader: str) -> list[CfpaAsset]:
    v = parse_version(mc_version)[:2]
    chosen: tuple[str, bool] | None = None
    for min_version, line, has_fabric in CFPA_LINES:
        if v >= min_version:
            chosen = (line, has_fabric)
    if chosen is None:
        return []
    line, has_fabric = chosen
    forge = _asset(line, fabric=False)
    if not has_fabric:
        return [forge]
    fabric = _asset(line, fabric=True)
    return [fabric, forge] if loader in FABRIC_LOADERS else [forge, fabric]
```

- [ ] **Step 6: 运行测试确认通过（含原有资源包测试）**

Run: `uv run pytest tests/test_cfpa_versions.py tests/test_resourcepack.py -v`
Expected: 10 + 14 = 24 passed

- [ ] **Step 7: 提交**

```bash
git add src/mctrans/mcversion.py src/mctrans/cfpa src/mctrans/output/resourcepack.py tests/test_cfpa_versions.py
git commit -m "feat: CFPA 版本映射，抽取版本号解析"
```

---

### Task 2: CFPA 索引与参数签名校验

**Files:**
- Create: `src/mctrans/cfpa/index.py`
- Test: `tests/test_cfpa_index.py`

**Interfaces:**
- Consumes: `handlers.lang_json.parse_lang`, `tests/helpers.jar_bytes/lang`
- Produces:
  - `cfpa.index.compatible(original: str, translated: str) -> bool`：参数签名（printf 参数按转换字符、`{x}` 按完整内容计数，忽略 `%%`）一致时返回 True
  - `cfpa.index.CfpaIndex`：`CfpaIndex.from_zips(zips: list[bytes]) -> CfpaIndex`（列表靠前的优先）；`lookup(namespace: str, key: str) -> str | None`；`__len__`（键总数）；`namespace_count: int` 属性；`warnings: list[str]`

- [ ] **Step 1: 写失败测试 `tests/test_cfpa_index.py`**

```python
import pytest

from helpers import jar_bytes, lang
from mctrans.cfpa.index import CfpaIndex, compatible


@pytest.mark.parametrize("original, translated, ok", [
    ("%s killed %s", "%2$s 被 %1$s 杀死", True),
    ("Speed: %.2f m/s", "速度：%.2f 米/秒", True),
    ("Hello {0}", "你好 {0}", True),
    ("100%% done", "已完成", True),
    ("%s items", "物品", False),
    ("%d items", "%s 个物品", False),
    ("Hi {player}", "你好 {name}", False),
    ("No args", "没有参数", True),
])
def test_compatible(original, translated, ok):
    assert compatible(original, translated) is ok


def test_index_lookup_and_priority():
    primary = jar_bytes({
        "pack.mcmeta": "{}",
        "assets/create/lang/zh_cn.json": lang({"item.create.shaft": "传动杆"}),
        "assets/create/lang/zh_cn.flatten.json5": "{}",
        "assets/lang/zh_cn.json": lang({"x": "y"}),  # 没有命名空间，忽略
    })
    secondary = jar_bytes({
        "assets/create/lang/zh_cn.json": lang({"item.create.shaft": "轴", "item.create.gear": "齿轮"}),
        "assets/other/lang/zh_cn.json": lang({"k": "值"}),
    })
    idx = CfpaIndex.from_zips([primary, secondary])
    assert idx.lookup("create", "item.create.shaft") == "传动杆"
    assert idx.lookup("create", "item.create.gear") == "齿轮"
    assert idx.lookup("other", "k") == "值"
    assert idx.lookup("create", "missing") is None
    assert idx.lookup("nope", "k") is None
    assert len(idx) == 3 and idx.namespace_count == 2


def test_index_skips_broken_files():
    data = jar_bytes({"assets/bad/lang/zh_cn.json": "{oops", "assets/ok/lang/zh_cn.json": lang({"k": "好"})})
    idx = CfpaIndex.from_zips([data])
    assert idx.lookup("ok", "k") == "好"
    assert any("bad" in w for w in idx.warnings)


def test_index_skips_corrupt_zip():
    idx = CfpaIndex.from_zips([b"not a zip"])
    assert len(idx) == 0 and idx.warnings
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_cfpa_index.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 `src/mctrans/cfpa/index.py`**

```python
from __future__ import annotations

import io
import re
import zipfile
from collections import Counter

from ..handlers.lang_json import parse_lang

ZH_LANG_RE = re.compile(r"^assets/([^/]+)/lang/zh_cn\.json$", re.IGNORECASE)
PRINTF_RE = re.compile(r"%(?:\d+\$)?[-#+0,(]*\d*(?:\.\d+)?([a-zA-Z])")
BRACE_RE = re.compile(r"\{[A-Za-z0-9_.:-]*\}")


def _signature(text: str) -> Counter:
    sig: Counter = Counter("%" + m.group(1) for m in PRINTF_RE.finditer(text.replace("%%", "")))
    sig.update(BRACE_RE.findall(text))
    return sig


def compatible(original: str, translated: str) -> bool:
    """原文与译文的参数（%s、%1$d、{0} 等）类型和数量一致。用于发现过时的 CFPA 译文。"""
    return _signature(original) == _signature(translated)


class CfpaIndex:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, str]] = {}
        self.warnings: list[str] = []

    @classmethod
    def from_zips(cls, zips: list[bytes]) -> "CfpaIndex":
        idx = cls()
        for data in zips:
            idx._add_zip(data)
        return idx

    def _add_zip(self, data: bytes) -> None:
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            self.warnings.append("CFPA 资源包已损坏，已跳过")
            return
        with z:
            for name in z.namelist():
                m = ZH_LANG_RE.match(name)
                if not m:
                    continue
                try:
                    entries = parse_lang(z.read(name))
                except ValueError as exc:
                    self.warnings.append(f"CFPA 语言文件解析失败，已跳过：{name}（{exc}）")
                    continue
                bucket = self._data.setdefault(m.group(1), {})
                for key, text in entries.items():
                    bucket.setdefault(key, text)

    def lookup(self, namespace: str, key: str) -> str | None:
        return self._data.get(namespace, {}).get(key)

    def __len__(self) -> int:
        return sum(len(b) for b in self._data.values())

    @property
    def namespace_count(self) -> int:
        return len(self._data)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_cfpa_index.py -v`
Expected: 11 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/cfpa/index.py tests/test_cfpa_index.py
git commit -m "feat: CFPA 索引与参数签名校验"
```

---

### Task 3: CFPA 下载缓存与配置项

**Files:**
- Create: `src/mctrans/cfpa/cache.py`
- Modify: `pyproject.toml`（依赖加 `httpx>=0.27`）, `src/mctrans/config.py`
- Test: `tests/test_cfpa_cache.py`, 追加到 `tests/test_config.py`

**Interfaces:**
- Consumes: `cfpa.versions.CfpaAsset`
- Produces:
  - `cfpa.cache.DEFAULT_BASE_URL = "https://github.com/CFPAOrg/Minecraft-Mod-Language-Package/releases/download/autobuild"`
  - `cfpa.cache.http_fetch(url: str) -> bytes`（httpx，跟随重定向，超时 120 秒，读取代理环境变量）
  - `cfpa.cache.CacheResult(data: bytes | None, status: str, message: str = "")`；`status` 取值 `updated`（新下载）/ `fresh`（远端未变化）/ `cached`（未检查或检查失败，用缓存）/ `missing`（无可用数据）
  - `cfpa.cache.CfpaCache(root: Path, base_url: str = DEFAULT_BASE_URL, fetch=http_fetch, check_hours: float = 6, clock=time.time)`；方法 `get(asset: CfpaAsset, offline: bool = False) -> CacheResult`
  - 缓存文件：`<root>/<zip_name>` 与 `<root>/<key>.state.json`（`{"md5": 小写, "checked_at": 时间戳}`）
  - `config.Settings` 新增字段 `cfpa_base_url: str = DEFAULT_BASE_URL`、`cfpa_check_hours: float = 6.0`，并加入 `SETTING_KEYS`

- [ ] **Step 1: 写失败测试 `tests/test_cfpa_cache.py`**

```python
import hashlib

import pytest

from mctrans.cfpa.cache import CfpaCache
from mctrans.cfpa.versions import CfpaAsset

ASSET = CfpaAsset("1.20", "Minecraft-Mod-Language-Modpack-1-20.zip", "1.20.md5")
BASE = "https://example.test/autobuild"


class FakeRemote:
    def __init__(self, data=b"zipdata"):
        self.data = data
        self.md5 = hashlib.md5(data).hexdigest().upper()
        self.calls = []
        self.fail = False

    def __call__(self, url):
        self.calls.append(url.rsplit("/", 1)[1])
        if self.fail:
            raise OSError("network down")
        if url == f"{BASE}/{ASSET.md5_name}":
            return (self.md5 + "\n").encode()
        if url == f"{BASE}/{ASSET.zip_name}":
            return self.data
        raise AssertionError(url)


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def remote():
    return FakeRemote()


@pytest.fixture
def clock():
    return Clock()


def make(tmp_path, remote, clock):
    return CfpaCache(tmp_path / "cfpa", BASE, fetch=remote, check_hours=6, clock=clock)


def test_first_download(tmp_path, remote, clock):
    r = make(tmp_path, remote, clock).get(ASSET)
    assert (r.status, r.data) == ("updated", b"zipdata")
    assert remote.calls == ["1.20.md5", "Minecraft-Mod-Language-Modpack-1-20.zip"]
    assert (tmp_path / "cfpa" / ASSET.zip_name).read_bytes() == b"zipdata"


def test_within_interval_uses_cache_without_network(tmp_path, remote, clock):
    cache = make(tmp_path, remote, clock)
    cache.get(ASSET)
    remote.calls.clear()
    clock.now += 3600
    r = cache.get(ASSET)
    assert (r.status, r.data, remote.calls) == ("cached", b"zipdata", [])


def test_after_interval_checks_md5_only(tmp_path, remote, clock):
    cache = make(tmp_path, remote, clock)
    cache.get(ASSET)
    remote.calls.clear()
    clock.now += 7 * 3600
    r = cache.get(ASSET)
    assert (r.status, remote.calls) == ("fresh", ["1.20.md5"])


def test_remote_changed_redownloads(tmp_path, remote, clock):
    cache = make(tmp_path, remote, clock)
    cache.get(ASSET)
    remote.data = b"newer"
    remote.md5 = hashlib.md5(b"newer").hexdigest()
    clock.now += 7 * 3600
    r = cache.get(ASSET)
    assert (r.status, r.data) == ("updated", b"newer")


def test_md5_mismatch_is_rejected(tmp_path, remote, clock):
    remote.md5 = "0" * 32
    r = make(tmp_path, remote, clock).get(ASSET)
    assert r.status == "missing" and r.data is None and "校验" in r.message
    assert not (tmp_path / "cfpa" / ASSET.zip_name).exists()


def test_network_failure_falls_back_to_cache(tmp_path, remote, clock):
    cache = make(tmp_path, remote, clock)
    cache.get(ASSET)
    remote.fail = True
    clock.now += 7 * 3600
    r = cache.get(ASSET)
    assert (r.status, r.data) == ("cached", b"zipdata") and "network down" in r.message


def test_network_failure_without_cache(tmp_path, remote, clock):
    remote.fail = True
    r = make(tmp_path, remote, clock).get(ASSET)
    assert (r.status, r.data) == ("missing", None) and "network down" in r.message


def test_offline_mode(tmp_path, remote, clock):
    cache = make(tmp_path, remote, clock)
    assert cache.get(ASSET, offline=True).status == "missing"
    cache.get(ASSET)
    remote.calls.clear()
    clock.now += 100 * 3600
    r = cache.get(ASSET, offline=True)
    assert (r.status, r.data, remote.calls) == ("cached", b"zipdata", [])
```

在 `tests/test_config.py` 末尾追加：

```python
def test_cfpa_settings(mctrans_home):
    s = load_settings()
    assert s.cfpa_base_url.endswith("/releases/download/autobuild")
    assert s.cfpa_check_hours == 6
    mctrans_home.mkdir(parents=True)
    (mctrans_home / "config.toml").write_text(
        'cfpa_base_url = "https://mirror.test/cfpa"\ncfpa_check_hours = 1\n', encoding="utf-8")
    s = load_settings()
    assert s.cfpa_base_url == "https://mirror.test/cfpa" and s.cfpa_check_hours == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_cfpa_cache.py tests/test_config.py -v`
Expected: FAIL（`ModuleNotFoundError` 与 `AttributeError: 'Settings' object has no attribute 'cfpa_base_url'`）

- [ ] **Step 3: 在 `pyproject.toml` 的 dependencies 中加入 `"httpx>=0.27",` 并运行 `uv sync`**

- [ ] **Step 4: 实现 `src/mctrans/cfpa/cache.py`**

```python
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .versions import CfpaAsset

DEFAULT_BASE_URL = "https://github.com/CFPAOrg/Minecraft-Mod-Language-Package/releases/download/autobuild"


def http_fetch(url: str) -> bytes:
    import httpx

    with httpx.Client(follow_redirects=True, timeout=120.0, trust_env=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


@dataclass
class CacheResult:
    data: bytes | None
    status: str
    message: str = ""


class CfpaCache:
    def __init__(self, root: Path, base_url: str = DEFAULT_BASE_URL,
                 fetch: Callable[[str], bytes] = http_fetch, check_hours: float = 6.0,
                 clock: Callable[[], float] = time.time):
        self.root = Path(root)
        self.base_url = base_url.rstrip("/")
        self.fetch = fetch
        self.check_seconds = check_hours * 3600
        self.clock = clock

    def _state_path(self, asset: CfpaAsset) -> Path:
        return self.root / f"{asset.key}.state.json"

    def _load_state(self, asset: CfpaAsset) -> dict:
        try:
            return json.loads(self._state_path(asset).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_state(self, asset: CfpaAsset, md5: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._state_path(asset).write_text(
            json.dumps({"md5": md5, "checked_at": self.clock()}), encoding="utf-8")

    def get(self, asset: CfpaAsset, offline: bool = False) -> CacheResult:
        zip_path = self.root / asset.zip_name
        cached = zip_path.read_bytes() if zip_path.is_file() else None
        if offline:
            return CacheResult(cached, "cached" if cached is not None else "missing")
        state = self._load_state(asset)
        if cached is not None and self.clock() - state.get("checked_at", 0) < self.check_seconds:
            return CacheResult(cached, "cached")
        try:
            remote_md5 = self.fetch(f"{self.base_url}/{asset.md5_name}").decode("ascii").strip().lower()
            if cached is not None and state.get("md5") == remote_md5:
                self._save_state(asset, remote_md5)
                return CacheResult(cached, "fresh")
            data = self.fetch(f"{self.base_url}/{asset.zip_name}")
            if hashlib.md5(data).hexdigest() != remote_md5:
                raise ValueError(f"{asset.zip_name} 校验失败，下载内容可能不完整")
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = zip_path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(zip_path)
            self._save_state(asset, remote_md5)
            return CacheResult(data, "updated")
        except Exception as exc:  # 网络或校验错误：尽量用缓存继续
            if cached is not None:
                return CacheResult(cached, "cached", f"检查更新失败，使用本地缓存：{exc}")
            return CacheResult(None, "missing", f"下载失败：{exc}")
```

- [ ] **Step 5: 修改 `src/mctrans/config.py`**

在文件头部导入：

```python
from .cfpa.cache import DEFAULT_BASE_URL as CFPA_DEFAULT_BASE_URL
```

`SETTING_KEYS` 末尾追加 `"cfpa_base_url", "cfpa_check_hours"`；`Settings` 中 `max_retries` 之后追加：

```python
    cfpa_base_url: str = CFPA_DEFAULT_BASE_URL
    cfpa_check_hours: float = 6.0
```

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/test_cfpa_cache.py tests/test_config.py -v`
Expected: 8 + 9 = 17 passed

- [ ] **Step 7: 提交**

```bash
git add pyproject.toml uv.lock src/mctrans/cfpa/cache.py src/mctrans/config.py tests/test_cfpa_cache.py tests/test_config.py
git commit -m "feat: CFPA 资源包下载缓存（md5 校验、检查间隔、离线回退）"
```

---

### Task 4: 全局记忆库与工作区通用填充

**Files:**
- Create: `src/mctrans/memory.py`
- Modify: `src/mctrans/store.py`（新增 `fill_from`、`origin_counts`）
- Test: `tests/test_memory.py`, 追加到 `tests/test_store.py`

**Interfaces:**
- Consumes: `config.home_dir`, `models.Entry`
- Produces:
  - `memory.MemoryRecord(namespace: str, original: str, text: str, origin: str, confirmed: bool = False, model: str | None = None)`
  - `memory.Memory(db_path: Path)`；`Memory.default() -> Memory`（`home_dir() / "memory.db"`）；支持 `with`；`remember(records: list[MemoryRecord]) -> int`（已确认的记录不会被未确认记录覆盖）；`lookup(namespace: str, original: str) -> MemoryRecord | None`；`__len__`
  - `Workspace.fill_from(origin: str, lookup: Callable[[Entry], str | None], overridable: frozenset[str] = frozenset(), handler: str | None = None) -> int`：候选为未过时、非 `confirmed`，且状态为 `pending`/`failed`，或状态为 `done` 且来源在 `overridable` 中的条目；`lookup` 返回非空文本且与当前不同则写入（`status="done"`）
  - `Workspace.origin_counts() -> dict[str, int]`（只统计 `done`/`confirmed`）

- [ ] **Step 1: 写失败测试 `tests/test_memory.py`**

```python
from mctrans.memory import Memory, MemoryRecord


def test_remember_and_lookup(tmp_path):
    with Memory(tmp_path / "m.db") as m:
        assert m.remember([MemoryRecord("create", "Shaft", "传动杆", "llm", model="x")]) == 1
        r = m.lookup("create", "Shaft")
        assert (r.text, r.origin, r.confirmed, r.model) == ("传动杆", "llm", False, "x")
        assert m.lookup("other", "Shaft") is None
        assert len(m) == 1


def test_confirmed_not_overwritten_by_unconfirmed(tmp_path):
    with Memory(tmp_path / "m.db") as m:
        m.remember([MemoryRecord("a", "Gear", "齿轮", "human", confirmed=True)])
        assert m.remember([MemoryRecord("a", "Gear", "机翻", "llm")]) == 0
        assert m.lookup("a", "Gear").text == "齿轮"
        assert m.remember([MemoryRecord("a", "Gear", "新齿轮", "human", confirmed=True)]) == 1
        assert m.lookup("a", "Gear").text == "新齿轮"


def test_default_location(mctrans_home):
    with Memory.default() as m:
        assert m.path == mctrans_home / "memory.db"
```

在 `tests/test_store.py` 末尾追加：

```python
def test_fill_from_pending_and_overridable(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear"), e("k2", "Shaft"), e("k3", "Belt", "传送带")])
    ws.apply_builtin()
    ids = {p.locator: p.id for p in ws.pending_entries()}
    ws.save_translations([TranslationUpdate(ids["k2"], "机翻", "llm", "done", "m")])
    table = {"Gear": "齿轮", "Shaft": "传动杆", "Belt": "皮带"}
    n = ws.fill_from("cfpa", lambda en: table.get(en.original), frozenset({"llm", "memory"}))
    assert n == 2  # k1 待翻译、k2 机翻可覆盖；k3 是 mod 自带，不可覆盖
    texts = {i.entry.locator: (i.text, i.origin) for i in ws.translated_items()}
    assert texts == {"k1": ("齿轮", "cfpa"), "k2": ("传动杆", "cfpa"), "k3": ("传送带", "mod_builtin")}
    assert ws.fill_from("cfpa", lambda en: table.get(en.original), frozenset({"llm"})) == 0  # 幂等


def test_fill_from_skips_confirmed_and_handler_filter(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear")])
    ws.sync_entries("other", [Entry("other", "f", "k9", "a", "Gear")])
    (eid,) = [p.id for p in ws.pending_entries() if p.locator == "k1"]
    ws.save_translations([TranslationUpdate(eid, "人工齿轮", "human", "confirmed")])
    assert ws.fill_from("cfpa", lambda en: "齿轮", frozenset({"human"}), handler="lang_json") == 0
    assert ws.fill_from("memory", lambda en: "齿轮", handler="other") == 1


def test_origin_counts(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear", "齿轮"), e("k2", "Shaft")])
    ws.apply_builtin()
    assert ws.origin_counts() == {"mod_builtin": 1}
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_memory.py tests/test_store.py -v`
Expected: FAIL（`ModuleNotFoundError` 与 `AttributeError: 'Workspace' object has no attribute 'fill_from'`）

- [ ] **Step 3: 实现 `src/mctrans/memory.py`**

```python
from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import home_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory (
    namespace TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    original TEXT NOT NULL,
    text TEXT NOT NULL,
    origin TEXT NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    updated_at TEXT,
    PRIMARY KEY (namespace, source_hash)
);
"""


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class MemoryRecord:
    namespace: str
    original: str
    text: str
    origin: str
    confirmed: bool = False
    model: str | None = None


class Memory:
    """跨整合包的全局翻译记忆库，以 (namespace, 原文哈希) 为键。"""

    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    @classmethod
    def default(cls) -> "Memory":
        return cls(home_dir() / "memory.db")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Memory":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def remember(self, records: list[MemoryRecord]) -> int:
        written = 0
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.conn:
            for r in records:
                key = (r.namespace, _hash(r.original))
                row = self.conn.execute(
                    "SELECT text, confirmed FROM memory WHERE namespace=? AND source_hash=?", key
                ).fetchone()
                if row is not None:
                    if row["confirmed"] and not r.confirmed:
                        continue
                    if row["text"] == r.text and bool(row["confirmed"]) == r.confirmed:
                        continue
                self.conn.execute(
                    "INSERT INTO memory(namespace, source_hash, original, text, origin, confirmed, model, updated_at) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(namespace, source_hash) DO UPDATE SET text=excluded.text, origin=excluded.origin, "
                    "confirmed=excluded.confirmed, model=excluded.model, updated_at=excluded.updated_at",
                    (*key, r.original, r.text, r.origin, int(r.confirmed), r.model, now),
                )
                written += 1
        return written

    def lookup(self, namespace: str, original: str) -> MemoryRecord | None:
        row = self.conn.execute(
            "SELECT * FROM memory WHERE namespace=? AND source_hash=?", (namespace, _hash(original))
        ).fetchone()
        if row is None:
            return None
        return MemoryRecord(row["namespace"], row["original"], row["text"], row["origin"],
                            bool(row["confirmed"]), row["model"])

    def __len__(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM memory").fetchone()[0]
```

- [ ] **Step 4: 在 `src/mctrans/store.py` 中新增方法**

文件头部的 `from typing import Iterable` 改为 `from typing import Callable, Iterable`。在 `glossary_pairs` 方法之前加入：

```python
    def fill_from(self, origin: str, lookup: Callable[[Entry], str | None],
                  overridable: frozenset[str] = frozenset(), handler: str | None = None) -> int:
        """用外部来源（CFPA、记忆库等）填充译文，遵守优先级：只覆盖待翻译条目和 overridable 中的来源。"""
        sql = (
            "SELECT e.*, t.text AS t_text, t.origin AS t_origin, t.status AS t_status "
            "FROM entries e JOIN translations t ON t.entry_id=e.id "
            "WHERE e.stale=0 AND t.status != 'confirmed'"
        )
        params: tuple = ()
        if handler:
            sql += " AND e.handler=?"
            params = (handler,)
        updates: list[TranslationUpdate] = []
        for r in self.conn.execute(sql, params).fetchall():
            status, current = r["t_status"], r["t_origin"]
            if not (status in ("pending", "failed") or (status == "done" and current in overridable)):
                continue
            text = lookup(self._entry(r))
            if not text or (status == "done" and current == origin and r["t_text"] == text):
                continue
            updates.append(TranslationUpdate(r["id"], text, origin, "done"))
        return self.save_translations(updates)
```

在 `status_counts` 方法之后加入：

```python
    def origin_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT t.origin, COUNT(*) AS n FROM entries e JOIN translations t ON t.entry_id=e.id "
            "WHERE e.stale=0 AND t.status IN ('done', 'confirmed') GROUP BY t.origin"
        )
        return {r["origin"]: r["n"] for r in rows}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_memory.py tests/test_store.py -v`
Expected: 3 + 17 = 20 passed

- [ ] **Step 6: 提交**

```bash
git add src/mctrans/memory.py src/mctrans/store.py tests/test_memory.py tests/test_store.py
git commit -m "feat: 全局翻译记忆库与工作区通用填充"
```

---

### Task 5: 译文匹配流程

**Files:**
- Create: `src/mctrans/match.py`
- Test: `tests/test_match.py`

**Interfaces:**
- Consumes: `Workspace.apply_builtin/fill_from/translated_items`, `CfpaIndex`, `compatible`, `Memory`, `MemoryRecord`
- Produces:
  - `match.MatchStats(builtin=0, cfpa=0, cfpa_rejected=0, human=0, memory=0)`
  - `match.run_matching(ws: Workspace, cfpa: CfpaIndex | None, memory: Memory | None) -> MatchStats`：依次执行 mod 自带 → CFPA（仅 `lang_json`，可覆盖 `llm`/`memory`）→ 记忆库中人工确认的译文（来源记为 `human`，可覆盖除人工外的所有来源）→ 记忆库中其他译文（来源 `memory`，只填待翻译条目）
  - `match.remember_translations(ws: Workspace, memory: Memory) -> int`：把工作区中 `llm` 和 `human` 来源的译文写入记忆库（`human` 记为已确认）

- [ ] **Step 1: 写失败测试 `tests/test_match.py`**

```python
import pytest

from helpers import jar_bytes, lang
from mctrans.cfpa.index import CfpaIndex
from mctrans.match import remember_translations, run_matching
from mctrans.memory import Memory, MemoryRecord
from mctrans.models import Entry
from mctrans.store import TranslationUpdate, Workspace


def e(key, text, zh=None, ns="create"):
    return Entry(handler="lang_json", source=f"jar:assets/{ns}/lang/en_us.json",
                 locator=key, namespace=ns, original=text, context=key, existing_zh=zh)


@pytest.fixture
def ws(tmp_path):
    w = Workspace.for_instance(tmp_path / "inst")
    yield w
    w.close()


@pytest.fixture
def memory(tmp_path):
    m = Memory(tmp_path / "memory.db")
    yield m
    m.close()


def cfpa(mapping):
    return CfpaIndex.from_zips([jar_bytes({"assets/create/lang/zh_cn.json": lang(mapping)})])


def texts(ws):
    return {i.entry.locator: (i.text, i.origin) for i in ws.translated_items()}


def test_priority_chain(ws, memory):
    ws.sync_entries("lang_json", [
        e("k.builtin", "Gear", zh="齿轮"),
        e("k.cfpa", "Shaft"),
        e("k.stale", "%s items"),
        e("k.mem", "Belt"),
        e("k.human", "Casing"),
        e("k.none", "Cog"),
    ])
    idx = cfpa({"k.builtin": "CFPA齿轮", "k.cfpa": "传动杆", "k.stale": "物品", "k.human": "CFPA机壳"})
    memory.remember([
        MemoryRecord("create", "Belt", "传送带", "llm"),
        MemoryRecord("create", "Casing", "人工机壳", "human", confirmed=True),
    ])
    stats = run_matching(ws, idx, memory)
    assert (stats.builtin, stats.cfpa, stats.cfpa_rejected, stats.human, stats.memory) == (1, 2, 1, 1, 1)
    assert texts(ws) == {
        "k.builtin": ("齿轮", "mod_builtin"),
        "k.cfpa": ("传动杆", "cfpa"),
        "k.mem": ("传送带", "memory"),
        "k.human": ("人工机壳", "human"),
    }
    assert [p.locator for p in ws.pending_entries()] == ["k.stale", "k.none"]


def test_cfpa_overrides_llm(ws):
    ws.sync_entries("lang_json", [e("k1", "Shaft")])
    (eid,) = [p.id for p in ws.pending_entries()]
    ws.save_translations([TranslationUpdate(eid, "机翻轴", "llm", "done", "m")])
    run_matching(ws, cfpa({"k1": "传动杆"}), None)
    assert texts(ws) == {"k1": ("传动杆", "cfpa")}


def test_cfpa_ignores_identical_english(ws):
    ws.sync_entries("lang_json", [e("k1", "Create")])
    assert run_matching(ws, cfpa({"k1": "Create"}), None).cfpa == 0


def test_without_sources_only_builtin(ws):
    ws.sync_entries("lang_json", [e("k1", "Gear", zh="齿轮"), e("k2", "Shaft")])
    stats = run_matching(ws, None, None)
    assert (stats.builtin, stats.cfpa, stats.memory) == (1, 0, 0)


def test_remember_translations(ws, memory):
    ws.sync_entries("lang_json", [e("k1", "Gear", zh="齿轮"), e("k2", "Shaft"), e("k3", "Belt")])
    ws.apply_builtin()
    ids = {p.locator: p.id for p in ws.pending_entries()}
    ws.save_translations([
        TranslationUpdate(ids["k2"], "轴", "llm", "done", "qwen"),
        TranslationUpdate(ids["k3"], "传送带", "human", "confirmed"),
    ])
    assert remember_translations(ws, memory) == 2
    assert memory.lookup("create", "Shaft").model == "qwen"
    assert memory.lookup("create", "Belt").confirmed is True
    assert memory.lookup("create", "Gear") is None  # mod 自带的不入库
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_match.py -v`
Expected: FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 `src/mctrans/match.py`**

```python
from __future__ import annotations

from dataclasses import dataclass

from .cfpa.index import CfpaIndex, compatible
from .memory import Memory, MemoryRecord
from .models import Entry
from .store import Workspace

NON_HUMAN = frozenset({"mod_builtin", "cfpa", "memory", "llm"})


@dataclass
class MatchStats:
    builtin: int = 0
    cfpa: int = 0
    cfpa_rejected: int = 0
    human: int = 0
    memory: int = 0


def run_matching(ws: Workspace, cfpa: CfpaIndex | None, memory: Memory | None) -> MatchStats:
    stats = MatchStats()
    stats.builtin = ws.apply_builtin()

    if cfpa is not None:
        rejected = 0

        def from_cfpa(e: Entry) -> str | None:
            nonlocal rejected
            zh = cfpa.lookup(e.namespace, e.locator)
            if not zh or zh == e.original:
                return None
            if not compatible(e.original, zh):
                rejected += 1
                return None
            return zh

        stats.cfpa = ws.fill_from("cfpa", from_cfpa, frozenset({"llm", "memory"}), handler="lang_json")
        stats.cfpa_rejected = rejected

    if memory is not None:
        def confirmed(e: Entry) -> str | None:
            r = memory.lookup(e.namespace, e.original)
            return r.text if r is not None and r.confirmed else None

        def unconfirmed(e: Entry) -> str | None:
            r = memory.lookup(e.namespace, e.original)
            return r.text if r is not None and not r.confirmed else None

        stats.human = ws.fill_from("human", confirmed, NON_HUMAN)
        stats.memory = ws.fill_from("memory", unconfirmed)
    return stats


def remember_translations(ws: Workspace, memory: Memory) -> int:
    """把工作区中机翻和人工译文写入全局记忆库（人工译文记为已确认）。"""
    rows = ws.conn.execute(
        "SELECT e.namespace, e.original, t.text, t.origin, t.model "
        "FROM entries e JOIN translations t ON t.entry_id=e.id "
        "WHERE e.stale=0 AND t.status IN ('done', 'confirmed') AND t.text IS NOT NULL "
        "AND t.origin IN ('llm', 'human')"
    )
    return memory.remember([
        MemoryRecord(r["namespace"], r["original"], r["text"], r["origin"], r["origin"] == "human", r["model"])
        for r in rows
    ])
```

说明：`k.stale` 的原文有 `%s`，CFPA 译文"物品"缺少参数，判定为过时译文，计入 `cfpa_rejected` 并留给 LLM 翻译。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_match.py -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/match.py tests/test_match.py
git commit -m "feat: 译文匹配流程（mod 自带、CFPA、记忆库）"
```

---

### Task 6: 输出双模式

**Files:**
- Modify: `src/mctrans/handlers/base.py`（`Handler.output`）, `src/mctrans/output/options.py`（`place_around`）, `src/mctrans/output/apply.py`
- Test: 追加到 `tests/test_options.py`，修改 `tests/test_apply.py`

**Interfaces:**
- Consumes: `Workspace.translated_items`, `handlers.all_handlers`
- Produces:
  - `Handler.output: str = "pack"`（类属性；写实例内文件的处理器设为 `"instance"`，会一次性拿到全部非 `mod_builtin` 的条目）
  - `options.place_around(text: str, anchor: str, below: list[str], above: list[str]) -> str`：确保 `anchor` 在列表中（不在则追加到末尾），把 `below` 放在它紧下方、`above` 放在它紧上方
  - `apply.MODE_STANDALONE = "standalone"`、`apply.MODE_I18N = "i18nupdate"`
  - `apply.PACK_FILE = "mctrans-zh_cn.zip"`、`apply.FILL_FILE = "mctrans-zh_cn-fill.zip"`、`apply.FIX_FILE = "mctrans-zh_cn-fix.zip"`
  - `apply.i18n_pack_id(mc_version: str) -> str` → `"file/Minecraft-Mod-Language-Modpack-Converted-<mc_version>.zip"`
  - `apply.resolve_mode(mode: str, has_i18nupdatemod: bool) -> str`（`mode` 为 `auto`/`standalone`/`i18nupdate`，非法值抛 `ApplyError`）
  - `apply.Outputs(packs: dict[str, OutputSink], instance: OutputSink, entries: int)`；`apply.build_outputs(ws, mode) -> Outputs`（只保留有文件的资源包）
  - `apply.ApplyReport(entries: int, mode: str, packs: dict[str, int], instance_files: list[str], dry_run: bool)`（`packs` 为文件名 → 包内文件数）
  - `apply.apply(root, ws, mc_version, mode=MODE_STANDALONE, set_lang=False, dry_run=False) -> ApplyReport`
  - `restore` 接口不变

- [ ] **Step 1: 在 `tests/test_options.py` 末尾追加失败测试**

```python
from mctrans.output.options import place_around

ANCHOR = "file/Minecraft-Mod-Language-Modpack-Converted-1.20.1.zip"


def test_place_around_adds_anchor():
    out = place_around('resourcePacks:["vanilla"]\n', ANCHOR, ["file/fill.zip"], ["file/fix.zip"])
    assert read_packs(out) == ["vanilla", "file/fill.zip", ANCHOR, "file/fix.zip"]


def test_place_around_existing_anchor_keeps_others():
    text = f'resourcePacks:["vanilla","file/fill.zip","{ANCHOR}","file/other.zip"]\n'
    out = place_around(text, ANCHOR, ["file/fill.zip"], [])
    assert read_packs(out) == ["vanilla", "file/fill.zip", ANCHOR, "file/other.zip"]
    out = place_around(text, ANCHOR, [], ["file/fix.zip"])
    assert read_packs(out) == ["vanilla", "file/fill.zip", ANCHOR, "file/fix.zip", "file/other.zip"]
```

- [ ] **Step 2: 修改 `tests/test_apply.py`**

把文件开头的导入改为：

```python
from mctrans.output.apply import (
    FILL_FILE, FIX_FILE, MODE_I18N, PACK_ID, ApplyError, apply, i18n_pack_id, is_applied, resolve_mode, restore,
)
```

把 `test_apply_creates_pack_and_enables_it` 中的 `assert report.entries == 1 and report.pack_files == 1` 改为：

```python
    assert report.entries == 1 and report.packs == {"mctrans-zh_cn.zip": 1} and report.mode == "standalone"
```

给 `FileHandler` 类加一行类属性 `output = "instance"`。

在文件末尾追加：

```python
def test_resolve_mode():
    assert resolve_mode("auto", True) == "i18nupdate"
    assert resolve_mode("auto", False) == "standalone"
    assert resolve_mode("standalone", True) == "standalone"
    with pytest.raises(ApplyError):
        resolve_mode("bogus", False)


@pytest.fixture
def mixed_ws(instance_dir):
    w = Workspace.for_instance(instance_dir)
    w.sync_entries("lang_json", [
        e("k.builtin", "Gear", zh="齿轮"), e("k.cfpa", "Shaft"), e("k.llm", "Belt"),
        e("k.mem", "Cog"), e("k.human", "Casing"),
    ])
    w.apply_builtin()
    ids = {p.locator: p.id for p in w.pending_entries()}
    w.save_translations([
        TranslationUpdate(ids["k.cfpa"], "传动杆", "cfpa", "done"),
        TranslationUpdate(ids["k.llm"], "传送带", "llm", "done", "m"),
        TranslationUpdate(ids["k.mem"], "嵌齿", "memory", "done"),
        TranslationUpdate(ids["k.human"], "机壳", "human", "confirmed"),
    ])
    yield w
    w.close()


def test_standalone_includes_cfpa(instance_dir, mixed_ws):
    apply(instance_dir, mixed_ws, "1.20.1")
    files = read_zip(instance_dir / "resourcepacks" / "mctrans-zh_cn.zip")
    assert set(json.loads(files["assets/a/lang/zh_cn.json"])) == {"k.cfpa", "k.llm", "k.mem", "k.human"}


def test_i18n_mode_splits_packs_and_orders_them(instance_dir, mixed_ws):
    (instance_dir / "options.txt").write_text('resourcePacks:["vanilla","file/other.zip"]\n', encoding="utf-8")
    report = apply(instance_dir, mixed_ws, "1.20.1", mode=MODE_I18N)
    assert report.mode == "i18nupdate" and report.packs == {FILL_FILE: 1, FIX_FILE: 1}
    fill = read_zip(instance_dir / "resourcepacks" / FILL_FILE)
    fix = read_zip(instance_dir / "resourcepacks" / FIX_FILE)
    assert set(json.loads(fill["assets/a/lang/zh_cn.json"])) == {"k.llm", "k.mem"}
    assert set(json.loads(fix["assets/a/lang/zh_cn.json"])) == {"k.human"}
    assert not (instance_dir / "resourcepacks" / "mctrans-zh_cn.zip").exists()
    packs = read_packs((instance_dir / "options.txt").read_text(encoding="utf-8"))
    assert packs == ["vanilla", "file/other.zip", f"file/{FILL_FILE}", i18n_pack_id("1.20.1"), f"file/{FIX_FILE}"]
    restore(instance_dir)
    packs = read_packs((instance_dir / "options.txt").read_text(encoding="utf-8"))
    assert packs == ["vanilla", "file/other.zip", i18n_pack_id("1.20.1")]
    assert not (instance_dir / "resourcepacks" / FILL_FILE).exists()


def test_i18n_mode_without_human_has_no_fix_pack(instance_dir, ws):
    report = apply(instance_dir, ws, "1.20.1", mode=MODE_I18N)
    assert report.packs == {FILL_FILE: 1}
    packs = read_packs((instance_dir / "options.txt").read_text(encoding="utf-8"))
    assert packs == ["vanilla", f"file/{FILL_FILE}", i18n_pack_id("1.20.1")]
```

- [ ] **Step 3: 运行测试确认失败**

Run: `uv run pytest tests/test_options.py tests/test_apply.py -v`
Expected: FAIL（`ImportError: cannot import name 'place_around'`、`cannot import name 'FILL_FILE'`）

- [ ] **Step 4: 修改 `src/mctrans/handlers/base.py`**

在 `Handler` 类中 `kind_label: str = ""` 之后加入：

```python
    output: str = "pack"  # "pack"：写入资源包；"instance"：直接修改实例内文件
```

- [ ] **Step 5: 在 `src/mctrans/output/options.py` 末尾加入**

```python
def place_around(text: str, anchor: str, below: list[str], above: list[str]) -> str:
    """把 below 放在 anchor 紧下方、above 放在紧上方（列表越靠后优先级越高）。"""
    ours = set(below) | set(above)
    packs = [p for p in (read_packs(text) or ["vanilla"]) if p not in ours]
    if anchor not in packs:
        packs.append(anchor)
    i = packs.index(anchor)
    packs = packs[:i] + list(below) + [anchor] + list(above) + packs[i + 1:]
    return set_option(text, PACKS_KEY, _dump_list(packs))
```

- [ ] **Step 6: 用以下内容替换 `src/mctrans/output/apply.py` 中从文件开头到 `restore` 函数之前的全部代码（`restore` 函数保持不变）**

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
from .options import add_pack_top, get_option, place_around, remove_packs, set_option
from .resourcepack import build_pack_zip, pack_mcmeta

MODE_STANDALONE = "standalone"
MODE_I18N = "i18nupdate"
MODES = (MODE_STANDALONE, MODE_I18N)

PACK_FILE = "mctrans-zh_cn.zip"
FILL_FILE = "mctrans-zh_cn-fill.zip"
FIX_FILE = "mctrans-zh_cn-fix.zip"
PACK_ID = f"file/{PACK_FILE}"
PACK_DESCRIPTION = "mctrans 自动汉化"

# 每种模式下各资源包收录哪些来源的译文
PACK_ORIGINS: dict[str, dict[str, frozenset[str]]] = {
    MODE_STANDALONE: {PACK_FILE: frozenset({"cfpa", "memory", "llm", "human"})},
    MODE_I18N: {FILL_FILE: frozenset({"memory", "llm"}), FIX_FILE: frozenset({"human"})},
}
INSTANCE_EXCLUDED = frozenset({"mod_builtin"})


class ApplyError(Exception):
    pass


@dataclass
class ApplyReport:
    entries: int
    mode: str
    packs: dict[str, int]
    instance_files: list[str]
    dry_run: bool


@dataclass
class RestoreReport:
    restored: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


@dataclass
class Outputs:
    packs: dict[str, OutputSink]
    instance: OutputSink
    entries: int


def i18n_pack_id(mc_version: str) -> str:
    return f"file/Minecraft-Mod-Language-Modpack-Converted-{mc_version}.zip"


def resolve_mode(mode: str, has_i18nupdatemod: bool) -> str:
    if mode == "auto":
        return MODE_I18N if has_i18nupdatemod else MODE_STANDALONE
    if mode not in MODES:
        raise ApplyError(f"未知的输出模式：{mode}（可选：auto、{'、'.join(MODES)}）")
    return mode


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pointer(root: Path) -> Path:
    return root / ".mctrans" / "applied.json"


def is_applied(root: Path) -> bool:
    return _pointer(Path(root)).exists()


def build_outputs(ws: Workspace, mode: str) -> Outputs:
    plan = PACK_ORIGINS[mode]
    packs = {name: OutputSink() for name in plan}
    instance = OutputSink()
    count = 0
    for handler in all_handlers():
        items = ws.translated_items(handler.name)
        if handler.output == "instance":
            chosen = [it for it in items if it.origin not in INSTANCE_EXCLUDED]
            if chosen:
                handler.write(chosen, instance)
                count += len(chosen)
            continue
        for name, origins in plan.items():
            chosen = [it for it in items if it.origin in origins]
            if chosen:
                handler.write(chosen, packs[name])
                count += len(chosen)
    return Outputs({n: s for n, s in packs.items() if s.pack_files}, instance, count)


def apply(root: Path, ws: Workspace, mc_version: str, mode: str = MODE_STANDALONE,
          set_lang: bool = False, dry_run: bool = False) -> ApplyReport:
    root = Path(root)
    outputs = build_outputs(ws, mode)
    report = ApplyReport(
        entries=outputs.entries, mode=mode,
        packs={name: len(sink.pack_files) for name, sink in outputs.packs.items()},
        instance_files=sorted(outputs.instance.instance_files), dry_run=dry_run,
    )
    if dry_run:
        return report
    if is_applied(root):
        restore(root)

    run_dir = root / ".mctrans" / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    run_dir.mkdir(parents=True)
    manifest: dict = {"created": [], "modified": [], "options": {"packs": [], "lang_set": False, "lang_before": None}}

    for rel, data in sorted(outputs.instance.instance_files.items()):
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

    if outputs.packs or set_lang:
        opts = root / "options.txt"
        text = opts.read_bytes().decode("utf-8") if opts.exists() else ""
        for name, sink in sorted(outputs.packs.items()):
            data = build_pack_zip(sink.pack_files, pack_mcmeta(mc_version, PACK_DESCRIPTION))
            path = root / "resourcepacks" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            manifest["created"].append({"path": f"resourcepacks/{name}", "hash": _sha256(data)})
            manifest["options"]["packs"].append(f"file/{name}")
        if mode == MODE_STANDALONE and PACK_FILE in outputs.packs:
            text = add_pack_top(text, PACK_ID)
        elif mode == MODE_I18N and outputs.packs:
            below = [f"file/{FILL_FILE}"] if FILL_FILE in outputs.packs else []
            above = [f"file/{FIX_FILE}"] if FIX_FILE in outputs.packs else []
            text = place_around(text, i18n_pack_id(mc_version), below, above)
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
```

说明：还原时只移除我们自己的资源包，`apply` 时写入的 I18n 包 ID 会保留。这不会造成问题，因为 I18nUpdateMod 自己也会添加它；如果用户后来卸载了 I18nUpdateMod，游戏会自动忽略不存在的资源包。

- [ ] **Step 7: 修改 `src/mctrans/cli.py` 中的 `do_apply` 以适配新的 `ApplyReport`（完整的 CLI 改动在 Task 7，这里先保证测试通过）**

```python
def do_apply(root: Path, set_lang: bool, dry_run: bool) -> None:
    ws = _open_scanned(root)
    with ws:
        report = apply_outputs(root, ws, ws.get_meta("mc_version"), set_lang=set_lang, dry_run=dry_run)
    prefix = "（预演，未写入）" if dry_run else ""
    console.print(f"{prefix}写入译文 {report.entries} 条")
    for name, n in report.packs.items():
        console.print(f"资源包：resourcepacks/{name}（{n} 个文件）")
    for rel in report.instance_files:
        console.print(f"修改文件：{rel}")
    if not dry_run:
        console.print("已自动备份，可用 mctrans restore 一键还原")
```

- [ ] **Step 8: 运行全部测试**

Run: `uv run pytest -q`
Expected: 全部通过

- [ ] **Step 9: 提交**

```bash
git add src/mctrans/handlers/base.py src/mctrans/output src/mctrans/cli.py tests/test_options.py tests/test_apply.py
git commit -m "feat: 输出双模式（独立资源包 / 配合 I18nUpdateMod 的补全包与修正包）"
```

---

### Task 7: CLI 集成

**Files:**
- Modify: `src/mctrans/cli.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `cfpa_assets`, `CfpaCache`, `http_fetch`, `CfpaIndex`, `Memory`, `run_matching`, `remember_translations`, `resolve_mode`, `Workspace.origin_counts`
- Produces:
  - `scan` 新增选项 `--no-cfpa`（不使用 CFPA）、`--offline`（只用本地缓存）
  - `apply`、`run` 新增选项 `--mode auto|standalone|i18nupdate`（默认 `auto`）
  - `status` 额外显示各来源条目数
  - 模块级名称 `http_fetch`（测试通过 monkeypatch `mctrans.cli.http_fetch` 替换网络请求）

- [ ] **Step 1: 修改 `tests/test_cli.py`**

在文件开头的导入中加入 `from helpers import jar_bytes`（与现有 helpers 导入合并为 `from helpers import FakeClient, jar_bytes, lang, make_jar`），并加入 `import hashlib`。在 `runner = CliRunner()` 之后加入：

```python
CFPA_ZIP = jar_bytes({"assets/create/lang/zh_cn.json": lang({"item.create.shaft": "传动杆"})})


def fake_cfpa_fetch(url):
    name = url.rsplit("/", 1)[1]
    if name == "1.20.md5":
        return hashlib.md5(CFPA_ZIP).hexdigest().encode()
    if name == "Minecraft-Mod-Language-Modpack-1-20.zip":
        return CFPA_ZIP
    raise OSError(f"404 {name}")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def offline(url):
        raise OSError("测试中禁止联网")
    monkeypatch.setattr(cli, "http_fetch", offline)
```

把 `pack` fixture 中的 `monkeypatch.setattr(cli, "client_from_config", lambda p: fake)` 之后加一行：

```python
    monkeypatch.setattr(cli, "http_fetch", fake_cfpa_fetch)
```

把 `test_full_flow_and_restore` 整个替换为：

```python
def test_full_flow_and_restore(pack):
    root, fake = pack
    out = invoke("run", root, "--yes", "--set-lang").output
    assert "CFPA" in out
    # 术语表来自 mod 自带译文：Gear → 齿轮
    assert any("- Gear → 齿轮" in call for call in fake.calls)
    # Shaft 由 CFPA 提供，不再发给模型
    assert not any('"Shaft"' in call for call in fake.calls)
    zpath = root / "resourcepacks" / "mctrans-zh_cn.zip"
    with zipfile.ZipFile(io.BytesIO(zpath.read_bytes())) as z:
        data = json.loads(z.read("assets/create/lang/zh_cn.json"))
    assert data == {"item.create.shaft": "传动杆", "tip.create.x": "译Use the Gear %s"}
    text = (root / "options.txt").read_text(encoding="utf-8")
    assert read_packs(text)[-1] == "file/mctrans-zh_cn.zip" and "lang:zh_cn" in text
    invoke("restore", root)
    assert not zpath.exists()
```

在文件末尾追加：

```python
def test_memory_reused_across_instances(pack, tmp_path, monkeypatch):
    root, fake = pack
    invoke("run", root, "--yes")
    calls_before = len(fake.calls)
    other = tmp_path / "other"
    (other / "mods").mkdir(parents=True)
    (other / "minecraftinstance.json").write_text('{"gameVersion": "1.20.1"}', encoding="utf-8")
    make_jar(other / "mods" / "create.jar", {
        "assets/create/lang/en_us.json": lang({"tip.create.x": "Use the Gear %s"}),
    })
    out = invoke("scan", other, "--no-cfpa").output
    assert "记忆库" in out
    out = invoke("translate", other, "--yes").output
    assert len(fake.calls) == calls_before  # 全部命中记忆库，没有新请求
    assert "memory" in invoke("status", other).output


def test_i18n_mode_auto_detected(pack):
    root, _ = pack
    make_jar(root / "mods" / "I18nUpdateMod-3.6.0.jar", {"META-INF/mods.toml": 'modId="i18nupdatemod"'})
    out = invoke("run", root, "--yes").output
    assert "i18nupdate" in out
    assert (root / "resourcepacks" / "mctrans-zh_cn-fill.zip").exists()
    assert not (root / "resourcepacks" / "mctrans-zh_cn.zip").exists()
    packs = read_packs((root / "options.txt").read_text(encoding="utf-8"))
    assert packs[-2:] == ["file/mctrans-zh_cn-fill.zip", "file/Minecraft-Mod-Language-Modpack-Converted-1.20.1.zip"]


def test_scan_without_network_warns(instance_dir):
    make_jar(instance_dir / "mods" / "a.jar", {"assets/a/lang/en_us.json": lang({"k": "Hello"})})
    out = invoke("scan", instance_dir).output
    assert "CFPA" in out and "禁止联网" in out
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL（`AttributeError: module 'mctrans.cli' has no attribute 'http_fetch'` 等）

- [ ] **Step 3: 修改 `src/mctrans/cli.py`**

导入区加入：

```python
from .cfpa.cache import CfpaCache, http_fetch
from .cfpa.index import CfpaIndex
from .cfpa.versions import cfpa_assets
from .match import MatchStats, remember_translations, run_matching
from .memory import Memory
from .output.apply import resolve_mode
```

并把 `from .output.apply import ApplyError, apply as apply_outputs, restore as restore_outputs` 保留。

在 `_write_log` 之后加入：

```python
def _load_cfpa(inst, settings, offline: bool) -> CfpaIndex | None:
    assets = cfpa_assets(inst.mc_version, inst.loader)
    if not assets:
        console.print(f"CFPA 暂不支持 Minecraft {inst.mc_version}，跳过")
        return None
    cache = CfpaCache(home_dir() / "cfpa", settings.cfpa_base_url, fetch=http_fetch,
                      check_hours=settings.cfpa_check_hours)
    zips: list[bytes] = []
    for asset in assets:
        result = cache.get(asset, offline=offline)
        label = {"updated": "已下载最新版", "fresh": "已是最新", "cached": "使用缓存", "missing": "不可用"}[result.status]
        console.print(f"CFPA 汉化包 {asset.key}：{label}")
        if result.message:
            console.print(f"[yellow]警告：[/yellow]{result.message}")
        if result.data is not None:
            zips.append(result.data)
    if not zips:
        console.print("[yellow]没有可用的 CFPA 汉化包，本次只使用 mod 自带中文和记忆库[/yellow]")
        return None
    index = CfpaIndex.from_zips(zips)
    for w in index.warnings[:MAX_WARNINGS_SHOWN]:
        console.print(f"[yellow]警告：[/yellow]{w}")
    return index


def _print_match(stats: MatchStats) -> None:
    console.print(
        f"采用 mod 自带中文 {stats.builtin} 条，CFPA 人工译文 {stats.cfpa} 条"
        f"（参数不一致、视为过时而跳过 {stats.cfpa_rejected} 条），"
        f"记忆库 {stats.memory} 条，记忆库中的人工译文 {stats.human} 条"
    )
```

把 `do_scan` 的签名改为 `def do_scan(root: Path, mc_version: str | None, use_cfpa: bool = True, offline: bool = False) -> None:`，在 `try: inst = load_instance(...)` 之前加入：

```python
    try:
        settings = load_settings()
    except ConfigError as exc:
        _fail(str(exc))
```

把 `with Workspace.for_instance(inst.root) as ws:` 块中 `builtin = ws.apply_builtin()` 一行替换为：

```python
        index = _load_cfpa(inst, settings, offline) if use_cfpa else None
        with Memory.default() as memory:
            stats = run_matching(ws, index, memory)
```

把 `ws.finish_run(run_id, {"entries": totals, "builtin": builtin, "warnings": len(warnings)})` 改为 `ws.finish_run(run_id, {"entries": totals, "match": vars(stats), "warnings": len(warnings)})`，把 `console.print(f"采用 mod 自带中文：{builtin} 条")` 替换为 `_print_match(stats)`。

在 `do_translate` 中，把 `ws.apply_builtin()` 一行替换为：

```python
        memory = Memory.default()
        try:
            run_matching(ws, None, memory)
        finally:
            memory.close()
```

并在 `ws.finish_run(run_id, vars(report))` 之后加入：

```python
        with Memory.default() as memory:
            remember_translations(ws, memory)
```

把 `do_apply` 替换为：

```python
def do_apply(root: Path, set_lang: bool, dry_run: bool, mode: str = "auto") -> None:
    ws = _open_scanned(root)
    with ws:
        try:
            chosen = resolve_mode(mode, ws.get_meta("has_i18nupdatemod") == "1")
        except ApplyError as exc:
            _fail(str(exc))
        report = apply_outputs(root, ws, ws.get_meta("mc_version"), mode=chosen,
                               set_lang=set_lang, dry_run=dry_run)
    prefix = "（预演，未写入）" if dry_run else ""
    mode_label = "配合 I18nUpdateMod" if report.mode == "i18nupdate" else "独立"
    console.print(f"{prefix}输出模式：{report.mode}（{mode_label}），写入译文 {report.entries} 条")
    for name, n in report.packs.items():
        console.print(f"资源包：resourcepacks/{name}（{n} 个文件）")
    for rel in report.instance_files:
        console.print(f"修改文件：{rel}")
    if not dry_run:
        console.print("已自动备份，可用 mctrans restore 一键还原")
```

在 `ProviderOpt` 定义之后加入：

```python
ModeOpt = Annotated[str, typer.Option("--mode", help="输出模式：auto / standalone / i18nupdate")]
```

把 `scan` 命令改为：

```python
@app.command()
def scan(instance: InstanceArg,
         mc_version: Annotated[Optional[str], typer.Option("--mc-version", help="手动指定 Minecraft 版本")] = None,
         no_cfpa: Annotated[bool, typer.Option("--no-cfpa", help="不使用 CFPA 汉化包")] = False,
         offline: Annotated[bool, typer.Option("--offline", help="不联网，只用本地缓存的 CFPA 汉化包")] = False):
    """扫描实例，提取待翻译文本并匹配已有译文。"""
    do_scan(instance, mc_version, use_cfpa=not no_cfpa, offline=offline)
```

把 `apply_cmd` 的参数列表末尾加上 `mode: ModeOpt = "auto"`，调用改为 `do_apply(instance, set_lang, dry_run, mode)`；`run` 命令参数列表末尾加上 `mode: ModeOpt = "auto"`，最后一行改为 `do_apply(instance, set_lang, dry_run=False, mode=mode)`。

在 `status` 命令中，`console.print(table)` 之前（仍在 `with ws:` 内）读取来源统计，并在打印表格后打印：

```python
@app.command()
def status(instance: InstanceArg):
    """查看翻译进度。"""
    ws = _open_scanned(instance)
    with ws:
        console.print(f"Minecraft {ws.get_meta('mc_version')} · 加载器 {ws.get_meta('loader')}")
        table = Table("状态", "条目数")
        for name, n in sorted(ws.status_counts().items()):
            table.add_row(name, str(n))
        origins = Table("译文来源", "条目数")
        for name, n in sorted(ws.origin_counts().items()):
            origins.add_row(name, str(n))
    console.print(table)
    console.print(origins)
```

- [ ] **Step 4: 运行全部测试**

Run: `uv run pytest -q`
Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
git add src/mctrans/cli.py tests/test_cli.py
git commit -m "feat: CLI 接入 CFPA、记忆库与输出模式选择"
```

---

### Task 8: 文档更新与真实环境验证

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-11-21-mc-modpack-translator-design.md`

- [ ] **Step 1: 更新 `README.md`**

在"## 使用"一节的分步命令块之后加入：

````markdown
### 译文来源与优先级

扫描时会按以下优先级为每条文本选择译文，只有都没有时才交给大模型翻译：

1. 人工确认的译文
2. mod 自带的中文
3. [CFPA](https://github.com/CFPAOrg/Minecraft-Mod-Language-Package) 社区人工译文（自动下载并缓存到 `~/.mctrans/cfpa/`，每 6 小时最多检查一次更新）
4. 翻译记忆库（`~/.mctrans/memory.db`，以前翻译过的文本跨整合包复用）
5. 大模型翻译

参数（`%s`、`{0}` 等）与原文不一致的 CFPA 译文会被视为过时并跳过。

```
uv run mctrans scan <实例目录> --offline   # 不联网，只用缓存的 CFPA 汉化包
uv run mctrans scan <实例目录> --no-cfpa   # 不使用 CFPA
```

GitHub 访问慢时，可以在配置中把 `cfpa_base_url` 改为镜像地址，或设置 `HTTPS_PROXY` 环境变量。

### 与 I18nUpdateMod 配合

如果整合包装了 I18nUpdateMod（自动汉化更新模组），工具会自动切换到配合模式：

- `mctrans-zh_cn-fill.zip`（补全包）：只包含 CFPA 没有的机翻，放在 I18nUpdateMod 资源包**之下**，以后 CFPA 补上人工译文时会自动覆盖机翻
- `mctrans-zh_cn-fix.zip`（修正包）：只包含人工确认的译文，放在 I18nUpdateMod 资源包**之上**

可以用 `--mode standalone` 或 `--mode i18nupdate` 强制指定模式。
````

并把"## 说明"一节的第三条改为：

```markdown
- 独立模式生成 `resourcepacks/mctrans-zh_cn.zip`，放在资源包列表最上层
```

- [ ] **Step 2: 在设计文档第 9 节"实现前的验证项"末尾追加验证结论**

```markdown

### 9.1 验证结论（2026-11-21）

1. **I18nUpdateMod**：生成 `resourcepacks/Minecraft-Mod-Language-Modpack-Converted-<MC版本>.zip`。options.txt 中已有该 ID 时不调整顺序；没有时追加到最上层。因此 `apply` 预先写入该 ID 并在其上下放置修正包和补全包即可稳定控制顺序（NeoForge 运行时激活仅在首次注册时调整顺序，需进游戏确认）
2. **Patchouli**：待计划 3 验证
3. **新版 FTB Quests 语言文件**：待计划 3 验证
4. **CFPA 发布**：`autobuild` 滚动发布，地址固定，资源包与 md5 文件命名见计划 2。包内只有 zh_cn，没有 en_us，因此第 4 节"英文原文不一致时不采用"改为"参数签名不一致时不采用"，"全量术语表"改为使用当前整合包已匹配到的译文
```

- [ ] **Step 3: 提交**

```bash
git add README.md docs/superpowers/specs/2026-11-21-mc-modpack-translator-design.md
git commit -m "docs: 更新 CFPA、记忆库与双模式说明，记录验证结论"
```

- [ ] **Step 4: 真实环境验证**

用计划 1 的合成实例（`scratchpad/smoke`）执行，确认真实 CFPA 下载与匹配可用：

1. `uv run mctrans scan <实例目录>`：应显示 CFPA 1.20 / 1.20-fabric 的下载状态，CFPA 匹配条数
2. 再次执行同一命令：应显示"使用缓存"，不再下载
3. `uv run mctrans translate <实例目录> --yes`，然后 `uv run mctrans status <实例目录>` 查看来源统计
4. 在实例的 `mods/` 中放入一个模拟的 I18nUpdateMod jar（`META-INF/mods.toml` 含 `modId="i18nupdatemod"`），重新 `scan` 后 `apply`，检查输出模式和 `options.txt` 中的顺序
5. `uv run mctrans restore <实例目录>`
