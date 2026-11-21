import io
import json
import zipfile

import pytest

from mctrans.handlers.base import Handler
from mctrans.models import Entry
from mctrans.output import apply as apply_mod
from mctrans.output.apply import (
    FILL_FILE, FIX_FILE, MODE_I18N, PACK_ID, ApplyError, apply, i18n_pack_id, is_applied, resolve_mode, restore,
)
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
    assert report.entries == 1 and report.packs == {"mctrans-zh_cn.zip": 1} and report.mode == "standalone"
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
    output = "instance"

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
