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
