import json

import pytest

from mctrans import update


def release(tag="v0.2.0", body="修复了卡住的问题", url="https://github.com/x/y/releases/tag/v0.2.0"):
    return json.dumps({"tag_name": tag, "html_url": url, "body": body}).encode()


@pytest.mark.parametrize("a,b,newer", [
    ("0.2.0", "0.1.9", True), ("0.1.10", "0.1.9", True), ("0.1.9", "0.1.9", False),
    ("0.1.8", "0.1.9", False), ("1.0", "0.9.9", True), ("0.2", "0.2.0", False),
])
def test_is_newer(a, b, newer):
    assert update.is_newer(a, b) is newer


def test_check_finds_newer_release():
    info = update.check("0.1.9", fetch=lambda url: release())
    assert info.version == "0.2.0" and info.newer
    assert info.url.endswith("v0.2.0") and info.notes == "修复了卡住的问题"


def test_check_same_version_not_newer():
    assert not update.check("0.2.0", fetch=lambda url: release()).newer


def test_check_requests_latest_release_of_repo():
    urls = []
    update.check("0.1.9", fetch=lambda url: urls.append(url) or release())
    assert urls == [update.LATEST_API]


def test_check_bad_response_raises_update_error():
    with pytest.raises(update.UpdateError):
        update.check("0.1.9", fetch=lambda url: b"not json")
    with pytest.raises(update.UpdateError):
        update.check("0.1.9", fetch=lambda url: json.dumps({"message": "Not Found"}).encode())


def test_check_network_error_becomes_update_error():
    def boom(url):
        raise OSError("连不上")
    with pytest.raises(update.UpdateError, match="连不上"):
        update.check("0.1.9", fetch=boom)


def test_long_notes_truncated():
    info = update.check("0.1.9", fetch=lambda url: release(body="字" * 3000))
    assert len(info.notes) <= update.MAX_NOTES + 1 and info.notes.endswith("…")


def test_skip_version_persisted(tmp_path):
    assert update.skipped_version(tmp_path) is None
    update.skip_version(tmp_path, "0.2.0")
    assert update.skipped_version(tmp_path) == "0.2.0"


def test_skipped_version_tolerates_corrupt_file(tmp_path):
    (tmp_path / "update.json").write_text("{坏", encoding="utf-8")
    assert update.skipped_version(tmp_path) is None
