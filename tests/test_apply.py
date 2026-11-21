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
