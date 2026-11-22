import io
import json
import shutil
import subprocess
import zipfile

import pytest

from mctrans.output.installer import BAT_NAME, PS1_NAME
from mctrans.output.options import get_option, read_packs

from mctrans.handlers.base import Handler
from mctrans.models import Entry
from mctrans.output import apply as apply_mod
from mctrans.output.apply import ApplyError, MODE_I18N, MODE_STANDALONE
from mctrans.output.patch import README_NAME, export_patch
from mctrans.store import TranslationUpdate, Workspace


def e(key, text):
    return Entry("lang_json", "jar:assets/a/lang/en_us.json", key, "a", text, context=key)


@pytest.fixture
def ws(instance_dir):
    w = Workspace.for_instance(instance_dir)
    w.sync_entries("lang_json", [e("k1", "Gear"), e("k2", "Shaft")])
    ids = {p.locator: p.id for p in w.pending_entries()}
    w.save_translations([
        TranslationUpdate(ids["k1"], "齿轮", "cfpa", "done"),
        TranslationUpdate(ids["k2"], "轴", "llm", "done", "m"),
    ])
    yield w
    w.close()


def test_export_standalone_with_cfpa_notice(instance_dir, ws, tmp_path):
    out = tmp_path / "patch"
    report = export_patch(instance_dir, ws, "1.20.1", out, MODE_STANDALONE)
    assert report.includes_cfpa and report.entries == 2
    assert report.files == ["resourcepacks/maikuai-zh_cn.zip", README_NAME, BAT_NAME, PS1_NAME]
    with zipfile.ZipFile(io.BytesIO((out / "resourcepacks" / "maikuai-zh_cn.zip").read_bytes())) as z:
        assert json.loads(z.read("assets/a/lang/zh_cn.json")) == {"k1": "齿轮", "k2": "轴"}
    readme = (out / README_NAME).read_text(encoding="utf-8")
    assert "CC BY-NC-SA 4.0" in readme and "1.20.1" in readme
    assert not (instance_dir / "resourcepacks").exists()  # 不修改实例


def test_export_i18n_mode_has_no_cfpa(instance_dir, ws, tmp_path):
    report = export_patch(instance_dir, ws, "1.20.1", tmp_path / "p", MODE_I18N)
    assert not report.includes_cfpa
    assert report.files == ["resourcepacks/maikuai-zh_cn-fill.zip", README_NAME, BAT_NAME, PS1_NAME]
    assert "CC BY-NC-SA" not in (tmp_path / "p" / README_NAME).read_text(encoding="utf-8")


def test_refuses_non_empty_dir(instance_dir, ws, tmp_path):
    out = tmp_path / "p"
    out.mkdir()
    (out / "x.txt").write_text("x")
    with pytest.raises(ApplyError):
        export_patch(instance_dir, ws, "1.20.1", out, MODE_STANDALONE)


class FileHandler(Handler):
    name = "lang_json"
    kind_label = "x"
    output = "instance"

    def detect(self, instance):
        return True

    def extract(self, instance):
        return iter(())

    def write(self, items, sink):
        sink.add_instance_file("config/quests.txt", "\n".join(i.text for i in items).encode("utf-8"))


def test_instance_files_are_copied(instance_dir, ws, tmp_path, monkeypatch):
    monkeypatch.setattr(apply_mod, "all_handlers", lambda: [FileHandler()])
    report = export_patch(instance_dir, ws, "1.20.1", tmp_path / "p", MODE_STANDALONE)
    assert report.files == ["config/quests.txt", README_NAME, BAT_NAME, PS1_NAME]
    assert (tmp_path / "p" / "config" / "quests.txt").read_text(encoding="utf-8") == "齿轮\n轴"


POWERSHELL = shutil.which("powershell")
needs_ps = pytest.mark.skipif(POWERSHELL is None, reason="需要 Windows PowerShell")


def run_installer(patch_dir, *targets):
    args = [POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", str(patch_dir / PS1_NAME)]
    args += [str(t) for t in targets]
    proc = subprocess.run(args, capture_output=True, timeout=120)
    assert proc.returncode == 0, proc.stdout.decode("gbk", "replace") + proc.stderr.decode("gbk", "replace")


@needs_ps
def test_installer_copies_and_puts_pack_on_top(instance_dir, ws, tmp_path):
    patch = tmp_path / "补丁 目录"
    export_patch(instance_dir, ws, "1.20.1", patch, MODE_STANDALONE)
    game = tmp_path / "游戏 实例"
    (game / "mods").mkdir(parents=True)
    (game / "resourcepacks").mkdir()
    (game / "resourcepacks" / "maikuai-zh_cn.zip").write_bytes(b"old")
    (game / "options.txt").write_bytes(
        'version:3465\r\nresourcePacks:["vanilla","file/maikuai-zh_cn.zip","file/汉化补丁.zip"]\r\nlang:en_us\r\n'
        .encode("utf-8"))
    run_installer(patch, game)
    data = (game / "options.txt").read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8")
    assert "\r\n" in text and text.startswith("version:3465")
    assert read_packs(text) == ["vanilla", "file/汉化补丁.zip", "file/maikuai-zh_cn.zip"]
    assert get_option(text, "lang") == "zh_cn"
    assert (game / "resourcepacks" / "maikuai-zh_cn.zip").read_bytes() != b"old"
    backups = list((game / ".hanhua-backup").rglob("maikuai-zh_cn.zip"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"old"
    assert not (game / PS1_NAME).exists() and not (game / README_NAME).exists()


@needs_ps
def test_installer_in_place_i18n_without_options(instance_dir, ws, tmp_path):
    game = tmp_path / "game"
    export_patch(instance_dir, ws, "1.20.1", game, MODE_I18N)
    (game / "mods").mkdir()
    (game / "logs").mkdir()  # 玩过的整合包才有的目录，用来识别「补丁解压在整合包里」
    run_installer(game)
    text = (game / "options.txt").read_text(encoding="utf-8")
    assert read_packs(text) == ["vanilla", "file/maikuai-zh_cn-fill.zip",
                                "file/Minecraft-Mod-Language-Modpack-Converted-1.20.1.zip"]
    assert get_option(text, "lang") == "zh_cn"


def make_game(base, name, options=None):
    game = base / name
    (game / "mods").mkdir(parents=True)
    if options is not None:
        (game / "options.txt").write_text(options, encoding="utf-8")
    return game


@needs_ps
def test_installer_multiple_targets_and_repeat_runs(instance_dir, ws, tmp_path):
    patch = tmp_path / "补丁"
    export_patch(instance_dir, ws, "1.20.1", patch, MODE_STANDALONE)
    before = sorted(p.relative_to(patch) for p in patch.rglob("*"))
    a = make_game(tmp_path, "整合包 A", 'resourcePacks:["vanilla","file/x.zip"]\nlang:en_us\n')
    b = make_game(tmp_path, "整合包 B")
    run_installer(patch, a, b)
    run_installer(patch, a)  # 再装一次：不重复添加资源包
    assert read_packs((a / "options.txt").read_text(encoding="utf-8")) == [
        "vanilla", "file/x.zip", "file/maikuai-zh_cn.zip"]
    assert read_packs((b / "options.txt").read_text(encoding="utf-8"))[-1] == "file/maikuai-zh_cn.zip"
    assert sorted(p.relative_to(patch) for p in patch.rglob("*")) == before  # 补丁目录没有被改动


def test_export_with_extras(instance_dir, ws, tmp_path):
    extras = tmp_path / "extras"
    (extras / "mods").mkdir(parents=True)
    (extras / "mods" / "ad.jar").write_bytes(b"jar")
    (extras / "resourcepacks").mkdir()
    (extras / "resourcepacks" / "maikuai-zh_cn.zip").write_bytes(b"conflict")
    out = tmp_path / "p"
    report = export_patch(instance_dir, ws, "1.20.1", out, MODE_STANDALONE, extras)
    assert report.extras == ["mods/ad.jar"]
    assert report.extra_conflicts == ["resourcepacks/maikuai-zh_cn.zip"]
    assert "mods/ad.jar" in report.files
    assert (out / "mods" / "ad.jar").read_bytes() == b"jar"
    assert (out / "resourcepacks" / "maikuai-zh_cn.zip").read_bytes() != b"conflict"
    assert "mods/ad.jar（附加文件）" in (out / README_NAME).read_text(encoding="utf-8")
    assert "$ShippedJars = 1" in (out / PS1_NAME).read_text(encoding="utf-8-sig")


def test_export_missing_extras_dir(instance_dir, ws, tmp_path):
    with pytest.raises(ApplyError):
        export_patch(instance_dir, ws, "1.20.1", tmp_path / "p", MODE_STANDALONE, tmp_path / "nope")


@needs_ps
def test_installer_copies_extra_mod(instance_dir, ws, tmp_path):
    extras = tmp_path / "extras"
    (extras / "mods").mkdir(parents=True)
    (extras / "mods" / "ad.jar").write_bytes(b"jar")
    patch = tmp_path / "补丁"
    export_patch(instance_dir, ws, "1.20.1", patch, MODE_STANDALONE, extras)
    game = make_game(tmp_path, "game")
    run_installer(patch, game)
    assert (game / "mods" / "ad.jar").read_bytes() == b"jar"
    assert not (patch / "options.txt").exists()
