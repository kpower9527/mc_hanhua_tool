import io
import json
import zipfile

import pytest
from PySide6.QtCore import Qt

from helpers import FakeClient, lang, make_jar
from mctrans import service
from mctrans.config import load_settings
from mctrans.gui.main_window import MainWindow
from mctrans.gui.review import ReviewFilterProxy, ReviewTableModel, TEXT_COL
from mctrans.gui.settings import SettingsDialog
from mctrans.store import ReviewRow
from mctrans.translate.glossary import load_user_glossary


def rows():
    return [
        ReviewRow(1, "lang_json", "jar:assets/create/lang/en_us.json", "k1", "Use %s", "使用 %s", "llm", "done", None),
        ReviewRow(2, "ftbquests", "ftbquests:lang", "q1", "Quest\nline", None, None, "pending", None),
        ReviewRow(3, "lang_json", "jar:assets/create/lang/en_us.json", "k2", "Gear", "齿轮", "mod_builtin", "done", None),
        ReviewRow(4, "patchouli", "mods/a.jar!assets/x", "name", "Book", "书", "human", "confirmed", None),
    ]


def test_review_model_display_and_edit(qtbot):
    model = ReviewTableModel(rows())
    assert model.rowCount() == 4 and model.columnCount() == 6
    assert model.data(model.index(0, 0)) == "语言文件"
    assert model.data(model.index(0, 1)) == "create"
    assert model.data(model.index(1, 2)) == "Quest ⏎ line"
    assert model.data(model.index(1, 2), Qt.EditRole) == "Quest\nline"
    assert model.flags(model.index(0, TEXT_COL)) & Qt.ItemIsEditable
    assert not model.flags(model.index(0, 2)) & Qt.ItemIsEditable
    with qtbot.waitSignal(model.rejected) as blocker:
        assert model.setData(model.index(0, TEXT_COL), "使用") is False
    assert "%s" in blocker.args[1]
    with qtbot.waitSignal(model.edited) as blocker:
        assert model.setData(model.index(0, TEXT_COL), "用 %s") is True
    assert blocker.args == [1, "用 %s"]
    assert (model.rows[0].origin, model.rows[0].status) == ("human", "confirmed")


def test_review_filter_proxy():
    model = ReviewTableModel(rows())
    proxy = ReviewFilterProxy()
    proxy.setSourceModel(model)
    proxy.set_filters(status="需审校")
    assert proxy.rowCount() == 2
    proxy.set_filters(status="全部")
    assert proxy.rowCount() == 4
    proxy.set_filters(handler="lang_json", status="全部")
    assert proxy.rowCount() == 2
    proxy.set_filters(status="全部", text="齿轮")
    assert proxy.rowCount() == 1
    proxy.set_filters(origin="human", status="已确认")
    assert proxy.rowCount() == 1


def test_settings_dialog_save(qtbot, mctrans_home):
    dialog = SettingsDialog()
    qtbot.addWidget(dialog)
    dialog.default_box.setCurrentIndex(dialog.default_box.findData("qwen"))
    dialog.edit_box.setCurrentIndex(dialog.edit_box.findData("qwen"))
    dialog.api_key.setText("sk-test")
    dialog.model_edit.setText("qwen3.7-flash")
    dialog.concurrency.setValue(6)
    dialog.add_glossary_row("AllTheModium", "ATM 合金")
    dialog.add_provider("local")
    dialog.base_url.setText("http://localhost:11434/v1")
    dialog.model_edit.setText("qwen2.5")
    assert dialog.save() == (True, "设置已保存")
    s = load_settings()
    assert s.default_provider == "qwen" and s.concurrency == 6
    assert (s.providers["qwen"].api_key, s.providers["qwen"].model) == ("sk-test", "qwen3.7-flash")
    assert s.providers["local"].model == "qwen2.5"
    assert load_user_glossary(mctrans_home / "glossary.tsv") == [("AllTheModium", "ATM 合金")]
    dialog.rules_edit.setPlainText('[[rule]]\nglob = "x"\npattern = "no group"\n')
    ok, message = dialog.save()
    assert not ok and "text" in message


@pytest.fixture
def pack(instance_dir, monkeypatch):
    make_jar(instance_dir / "mods" / "create.jar", {
        "META-INF/mods.toml": 'modId="create"',
        "assets/create/lang/en_us.json": lang({"item.create.gear": "Gear", "tip.create.x": "Use the Gear %s",
                                               "tip.create.y": "Belt"}),
        "assets/create/lang/zh_cn.json": lang({"item.create.gear": "齿轮"}),
    })
    monkeypatch.setattr(service, "client_from_config", lambda p: FakeClient())

    def offline(url):
        raise OSError("测试中禁止联网")
    monkeypatch.setattr(service, "http_fetch", offline)
    return instance_dir


def wait_task(qtbot, window, name):
    with qtbot.waitSignal(window.overview.task_done, timeout=20000) as blocker:
        pass
    assert blocker.args == [name]


def test_main_window_flow(qtbot, pack, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.ask = False
    window.overview.ask = False
    with qtbot.waitSignal(window.overview.task_done, timeout=20000) as blocker:
        assert window.add_instance_path(pack)
    assert blocker.args == ["扫描"]
    assert window.overview.stats.rowCount() >= 1
    assert "未扫描" not in window.instance_list.currentItem().text()

    with qtbot.waitSignal(window.overview.task_done, timeout=20000) as blocker:
        assert window.overview.translate()
    assert blocker.args == ["翻译"]
    assert service.summary(pack).finished == 3

    window.tabs.setCurrentWidget(window.review)
    review = window.review
    review.status_box.setCurrentText("全部")
    assert review.model.rowCount() == 3
    row = next(r for r, x in enumerate(review.model.rows) if x.original == "Belt")
    assert review.model.setData(review.model.index(row, TEXT_COL), "传送带")
    assert review.unapplied == 1 and "1 处修改" in review.unapplied_label.text()
    assert service.summary(pack).origins.get("human") == 1

    with qtbot.waitSignal(window.overview.task_done, timeout=20000) as blocker:
        review.apply_requested.emit()
    assert blocker.args == ["应用"]
    assert review.unapplied == 0
    with zipfile.ZipFile(io.BytesIO((pack / "resourcepacks" / "mctrans-zh_cn.zip").read_bytes())) as z:
        data = json.loads(z.read("assets/create/lang/zh_cn.json"))
    assert data["tip.create.y"] == "传送带"

    with qtbot.waitSignal(window.overview.task_done, timeout=20000) as blocker:
        assert window.overview.restore()
    assert blocker.args == ["还原"]
    assert not (pack / "resourcepacks" / "mctrans-zh_cn.zip").exists()

    patch_dir = tmp_path / "patch"
    with qtbot.waitSignal(window.overview.task_done, timeout=20000):
        assert window.overview.export_patch(str(patch_dir))
    assert (patch_dir / "resourcepacks" / "mctrans-zh_cn.zip").exists()


def test_retranslate_from_review(qtbot, pack):
    window = MainWindow()
    qtbot.addWidget(window)
    window.ask = False
    window.overview.ask = False
    with qtbot.waitSignal(window.overview.task_done, timeout=20000):
        window.add_instance_path(pack)
    with qtbot.waitSignal(window.overview.task_done, timeout=20000):
        window.overview.translate()
    target = next(r for r in service.review_rows(pack) if r.original == "Belt")
    service.save_edits(pack, {target.id: "旧译文"})
    with qtbot.waitSignal(window.overview.task_done, timeout=20000) as blocker:
        window.review.retranslate_requested.emit([target.id])
    assert blocker.args == ["翻译"]
    updated = next(r for r in service.review_rows(pack) if r.id == target.id)
    assert (updated.text, updated.origin) == ("译Belt", "llm")


def test_add_invalid_instance(qtbot, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.ask = False
    assert window.add_instance_path(tmp_path) is False
    assert window.instance_list.count() == 0
