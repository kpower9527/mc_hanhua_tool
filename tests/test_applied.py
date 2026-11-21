import json

from mctrans.applied import pointer_path, read_original


def write_manifest(root, modified):
    run = root / ".mctrans" / "backups" / "r1"
    (run / "files" / "config").mkdir(parents=True)
    (run / "files" / "config" / "a.snbt").write_bytes(b"original a")
    manifest = {"created": [], "modified": modified, "options": {"packs": [], "lang_set": False, "lang_before": None}}
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    pointer_path(root).write_text(json.dumps({"manifest": ".mctrans/backups/r1/manifest.json"}), encoding="utf-8")


def test_read_original_without_apply(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "a.snbt").write_bytes(b"now")
    assert read_original(tmp_path, "config/a.snbt") == b"now"
    assert read_original(tmp_path, "config/missing.snbt") is None


def test_read_original_after_apply(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "a.snbt").write_bytes(b"translated a")
    (tmp_path / "config" / "new.snbt").write_bytes(b"created by apply")
    (tmp_path / "config" / "untouched.snbt").write_bytes(b"same")
    write_manifest(tmp_path, [
        {"path": "config/a.snbt", "backup": ".mctrans/backups/r1/files/config/a.snbt", "hash": "x"},
        {"path": "config/new.snbt", "backup": None, "hash": "y"},
    ])
    assert read_original(tmp_path, "config/a.snbt") == b"original a"
    assert read_original(tmp_path, "config/new.snbt") is None
    assert read_original(tmp_path, "config/untouched.snbt") == b"same"
