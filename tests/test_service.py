import hashlib

import pytest

from helpers import FakeClient, jar_bytes, lang, make_jar
from mctrans import service
from mctrans.config import ProviderConfig
from mctrans.service import Reporter, ServiceError

CFPA_ZIP = jar_bytes({"assets/create/lang/zh_cn.json": lang({"item.create.shaft": "传动杆"})})


def fake_fetch(url):
    name = url.rsplit("/", 1)[1]
    if name == "1.20.md5":
        return hashlib.md5(CFPA_ZIP).hexdigest().encode()
    if name == "Minecraft-Mod-Language-Modpack-1-20.zip":
        return CFPA_ZIP
    raise OSError(f"404 {name}")


class Recorder(Reporter):
    def __init__(self, stop_after=None):
        self.logs, self.progress_calls, self.stop_after = [], [], stop_after

    def log(self, text, level="info"):
        self.logs.append((level, text))

    def progress(self, done, total):
        self.progress_calls.append((done, total))

    def cancelled(self):
        return self.stop_after is not None and len(self.progress_calls) >= self.stop_after


@pytest.fixture
def pack(instance_dir, monkeypatch):
    make_jar(instance_dir / "mods" / "create.jar", {
        "META-INF/mods.toml": 'modId="create"',
        "assets/create/lang/en_us.json": lang({
            "item.create.gear": "Gear", "item.create.shaft": "Shaft", "tip.create.x": "Use the Gear %s",
            "tip.create.y": "Belt", "tip.create.z": "Cog",
        }),
        "assets/create/lang/zh_cn.json": lang({"item.create.gear": "齿轮"}),
    })
    fake = FakeClient()
    monkeypatch.setattr(service, "client_from_config", lambda p: fake)
    monkeypatch.setattr(service, "http_fetch", fake_fetch)
    return instance_dir, fake


def test_summary_before_scan_does_not_create_workspace(instance_dir):
    s = service.summary(instance_dir)
    assert s.scanned is False
    assert not (instance_dir / ".mctrans").exists()


def test_full_flow(pack):
    root, fake = pack
    rec = Recorder()
    result = service.scan(root, reporter=rec)
    assert result.mc_version == "1.20.1" and result.match.cfpa == 1 and result.match.builtin == 1
    assert [h.name for h in result.handlers] == ["lang_json", "patchouli"]
    assert any("CFPA" in text for _, text in rec.logs)
    plan = service.plan_translation(root)
    assert plan.estimate.entries == 3
    report = service.translate(root, reporter=rec)
    assert report.translated == 3 and rec.progress_calls[-1] == (3, 3)
    s = service.summary(root)
    assert s.scanned and s.total == 5 and s.finished == 5
    assert s.by_handler["lang_json"] == {"done": 5}
    assert s.origins == {"cfpa": 1, "llm": 3, "mod_builtin": 1}
    applied = service.apply(root)
    assert applied.packs == {"mctrans-zh_cn.zip": 1}
    service.restore(root)


def test_errors(instance_dir, tmp_path):
    with pytest.raises(ServiceError, match="mods"):
        service.scan(tmp_path)
    with pytest.raises(ServiceError, match="扫描"):
        service.apply(instance_dir)


def test_cancel(pack, monkeypatch):
    root, _ = pack
    service.scan(root)
    monkeypatch.setattr(service, "load_settings", lambda: _settings(batch_size=1, concurrency=1))
    report = service.translate(root, reporter=Recorder(stop_after=1))
    assert report.cancelled and report.translated == 1
    assert service.summary(root).finished == 3


def _settings(**kw):
    from mctrans.config import load_settings
    s = load_settings()
    for k, v in kw.items():
        setattr(s, k, v)
    s.requests_per_minute = 0
    return s


def test_review_edits_confirm_and_requeue(pack):
    root, fake = pack
    service.scan(root)
    service.translate(root)
    rows = {r.locator: r for r in service.review_rows(root)}
    assert len(rows) == 5
    rejected = service.save_edits(root, {rows["tip.create.x"].id: "使用齿轮", rows["tip.create.y"].id: "传送带"})
    assert list(rejected) == [rows["tip.create.x"].id] and "格式码" in rejected[rows["tip.create.x"].id]
    assert service.confirm_entries(root, [rows["tip.create.z"].id]) == 1
    rows = {r.locator: r for r in service.review_rows(root)}
    assert (rows["tip.create.y"].text, rows["tip.create.y"].origin, rows["tip.create.y"].status) == ("传送带", "human", "confirmed")
    assert rows["tip.create.z"].status == "confirmed"
    from mctrans.memory import Memory
    with Memory.default() as memory:
        assert memory.lookup("create", "Belt").confirmed is True
    calls_before = len(fake.calls)
    assert service.requeue(root, [rows["tip.create.x"].id]) == 1
    report = service.translate(root, ids=[rows["tip.create.x"].id])
    assert report.translated == 1 and len(fake.calls) == calls_before + 1


def test_test_provider(monkeypatch):
    monkeypatch.setattr(service, "client_from_config", lambda p: FakeClient(lambda t: "齿轮"))
    assert service.test_provider(ProviderConfig("x", "http://x", "m", api_key="k")) == "齿轮"
    monkeypatch.setattr(service, "client_from_config", lambda p: FakeClient(fail_times=1))
    with pytest.raises(ServiceError, match="测试失败"):
        service.test_provider(ProviderConfig("x", "http://x", "m", api_key="k"))
