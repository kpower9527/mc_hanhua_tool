import hashlib
import io
import json
import zipfile

import pytest
from typer.testing import CliRunner

from helpers import FakeClient, jar_bytes, lang, make_jar
from mctrans import cli
from mctrans.output.options import read_packs

runner = CliRunner()

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
    monkeypatch.setattr(cli, "http_fetch", fake_cfpa_fetch)
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


def test_missing_api_key_message(instance_dir):
    make_jar(instance_dir / "mods" / "a.jar", {"assets/a/lang/en_us.json": lang({"k": "Hello"})})
    invoke("scan", instance_dir)
    result = runner.invoke(cli.app, ["translate", str(instance_dir)])
    assert result.exit_code == 1 and "DEEPSEEK_API_KEY" in result.output


def test_bad_instance_dir(tmp_path):
    result = runner.invoke(cli.app, ["scan", str(tmp_path)])
    assert result.exit_code == 1 and "mods" in result.output


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
