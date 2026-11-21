import io
import json
import zipfile

import pytest

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
    assert report.files == ["resourcepacks/mctrans-zh_cn.zip", README_NAME]
    with zipfile.ZipFile(io.BytesIO((out / "resourcepacks" / "mctrans-zh_cn.zip").read_bytes())) as z:
        assert json.loads(z.read("assets/a/lang/zh_cn.json")) == {"k1": "齿轮", "k2": "轴"}
    readme = (out / README_NAME).read_text(encoding="utf-8")
    assert "CC BY-NC-SA 4.0" in readme and "1.20.1" in readme
    assert not (instance_dir / "resourcepacks").exists()  # 不修改实例


def test_export_i18n_mode_has_no_cfpa(instance_dir, ws, tmp_path):
    report = export_patch(instance_dir, ws, "1.20.1", tmp_path / "p", MODE_I18N)
    assert not report.includes_cfpa
    assert report.files == ["resourcepacks/mctrans-zh_cn-fill.zip", README_NAME]
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
    assert report.files == ["config/quests.txt", README_NAME]
    assert (tmp_path / "p" / "config" / "quests.txt").read_text(encoding="utf-8") == "齿轮\n轴"
