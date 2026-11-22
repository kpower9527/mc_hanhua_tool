import hashlib
import io
import json
import zipfile

import pytest
from typer.testing import CliRunner

from helpers import FakeClient, jar_bytes, lang, make_jar
from mctrans import cli, service
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
    monkeypatch.setattr(service, "http_fetch", offline)


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
    monkeypatch.setattr(service, "client_from_config", lambda p: fake)
    monkeypatch.setattr(service, "http_fetch", fake_cfpa_fetch)
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
    zpath = root / "resourcepacks" / "maikuai-zh_cn.zip"
    with zipfile.ZipFile(io.BytesIO(zpath.read_bytes())) as z:
        data = json.loads(z.read("assets/create/lang/zh_cn.json"))
    assert data == {"item.create.shaft": "传动杆", "tip.create.x": "译Use the Gear %s"}
    text = (root / "options.txt").read_text(encoding="utf-8")
    assert read_packs(text)[-1] == "file/maikuai-zh_cn.zip" and "lang:zh_cn" in text
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
    assert (root / "resourcepacks" / "maikuai-zh_cn-fill.zip").exists()
    assert not (root / "resourcepacks" / "maikuai-zh_cn.zip").exists()
    packs = read_packs((root / "options.txt").read_text(encoding="utf-8"))
    assert packs[-2:] == ["file/maikuai-zh_cn-fill.zip", "file/Minecraft-Mod-Language-Modpack-Converted-1.20.1.zip"]


def test_scan_without_network_warns(instance_dir):
    make_jar(instance_dir / "mods" / "a.jar", {"assets/a/lang/en_us.json": lang({"k": "Hello"})})
    out = invoke("scan", instance_dir).output
    assert "CFPA" in out and "禁止联网" in out


def test_quests_and_patchouli_end_to_end(pack):
    root, fake = pack
    lang_dir = root / "config" / "ftbquests" / "quests" / "lang"
    (lang_dir / "en_us").mkdir(parents=True)
    (lang_dir / "en_us" / "chapter.snbt").write_text(
        '{\n\tchapter.01.title: "Getting Started"\n\tquest.02.quest_desc: ["Craft a &6Gear&r." ""]\n}\n',
        encoding="utf-8")
    (lang_dir / "zh_cn.snbt").write_bytes('{\n\tchapter.01.title: "新手入门"\n}\n'.encode("utf-8"))
    make_jar(root / "mods" / "guide.jar", {
        "assets/guide/patchouli_books/book/en_us/entries/intro.json": json.dumps(
            {"name": "Intro", "pages": [{"type": "patchouli:text", "text": "Welcome to the pack."}]}),
    })
    invoke("scan", root)
    out = invoke("translate", root, "--yes", "--only", "ftbquests", "--limit", "5").output
    assert "完成：翻译 1 条" in out
    invoke("translate", root, "--yes")
    invoke("apply", root)
    zh = (lang_dir / "zh_cn.snbt").read_text(encoding="utf-8")
    assert '"新手入门"' in zh and '"译Craft a &6Gear&r."' in zh
    with zipfile.ZipFile(io.BytesIO((root / "resourcepacks" / "maikuai-zh_cn.zip").read_bytes())) as z:
        page = json.loads(z.read("assets/guide/patchouli_books/book/zh_cn/entries/intro.json"))
    assert page["name"] == "译Intro"
    # apply 之后重新扫描：不能把自己写出的译文当成整合包自带中文
    invoke("scan", root)
    from mctrans.store import Workspace
    with Workspace.for_instance(root) as ws:
        origins = {i.entry.locator: i.origin for i in ws.translated_items("ftbquests")}
    assert origins == {"chapter.01.title": "mod_builtin", "quest.02.quest_desc[0]": "llm"}
    invoke("restore", root)
    assert (lang_dir / "zh_cn.snbt").read_bytes() == '{\n\tchapter.01.title: "新手入门"\n}\n'.encode("utf-8")


def test_scripts_end_to_end(pack, mctrans_home):
    root, fake = pack
    js = root / "kubejs" / "client_scripts" / "tooltips.js"
    js.parent.mkdir(parents=True)
    original_js = "ItemEvents.modifyTooltips(e => {\n  e.add('a:b', [Text.of('Mining Dimension Disabled')])\n})\n"
    js.write_bytes(original_js.encode("utf-8"))
    (root / "config" / "tips").mkdir(parents=True)
    (root / "config" / "tips" / "t.txt").write_bytes(b"tip=Press F3\n")
    mctrans_home.mkdir(parents=True, exist_ok=True)
    (mctrans_home / "config_rules.toml").write_text(
        "[[rule]]\nglob = \"config/tips/*.txt\"\npattern = '^tip=(?P<text>.+)$'\n", encoding="utf-8")
    invoke("run", root, "--yes")
    assert js.read_text(encoding="utf-8") == original_js.replace("'Mining Dimension Disabled'", "'译Mining Dimension Disabled'")
    assert (root / "config" / "tips" / "t.txt").read_bytes() == "tip=译Press F3\n".encode("utf-8")
    invoke("restore", root)
    assert js.read_bytes() == original_js.encode("utf-8")
    assert (root / "config" / "tips" / "t.txt").read_bytes() == b"tip=Press F3\n"


def test_review_round_trip_and_patch(pack, tmp_path):
    from openpyxl import load_workbook
    root, _ = pack
    invoke("run", root, "--yes")
    xlsx = tmp_path / "review.xlsx"
    out = invoke("review", "export", root, xlsx).output
    assert "导出 1 条" in out
    wb = load_workbook(xlsx)
    sheet = wb["审校"]
    sheet["F2"].value = "使用齿轮 %s"
    wb.save(xlsx)
    out = invoke("review", "import", root, xlsx).output
    assert "修改 1 条" in out
    with zipfile.ZipFile(io.BytesIO((root / "resourcepacks" / "maikuai-zh_cn.zip").read_bytes())) as z:
        assert json.loads(z.read("assets/create/lang/zh_cn.json"))["tip.create.x"] == "译Use the Gear %s"
    invoke("apply", root)
    with zipfile.ZipFile(io.BytesIO((root / "resourcepacks" / "maikuai-zh_cn.zip").read_bytes())) as z:
        assert json.loads(z.read("assets/create/lang/zh_cn.json"))["tip.create.x"] == "使用齿轮 %s"
    from mctrans.memory import Memory
    with Memory.default() as memory:
        assert memory.lookup("create", "Use the Gear %s").confirmed is True
    patch_dir = tmp_path / "patch"
    out = invoke("export-patch", root, patch_dir).output
    assert "CC BY-NC-SA" in out
    assert (patch_dir / "resourcepacks" / "maikuai-zh_cn.zip").exists()
    result = runner.invoke(cli.app, ["export-patch", str(root), str(patch_dir)])
    assert result.exit_code == 1 and "不为空" in result.output
