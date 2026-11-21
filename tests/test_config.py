import pytest

from mctrans.config import ConfigError, home_dir, load_settings


def test_home_dir_uses_env(mctrans_home):
    assert home_dir() == mctrans_home


def test_defaults_without_file():
    s = load_settings()
    assert s.default_provider == "deepseek"
    assert s.provider().model == "deepseek-chat"
    assert s.provider("qwen").base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert s.batch_size == 40


def test_file_overrides_and_custom_provider(mctrans_home):
    mctrans_home.mkdir(parents=True)
    (mctrans_home / "config.toml").write_text(
        'default_provider = "qwen"\n'
        "concurrency = 8\n"
        "[provider.qwen]\n"
        'model = "qwen-max"\n'
        "[provider.local]\n"
        'base_url = "http://localhost:11434/v1"\n'
        'model = "qwen2.5"\n'
        'api_key = "x"\n',
        encoding="utf-8",
    )
    s = load_settings()
    assert s.default_provider == "qwen"
    assert s.concurrency == 8
    assert s.provider().model == "qwen-max"
    assert s.provider().api_key_env == "DASHSCOPE_API_KEY"
    assert s.provider("local").resolve_api_key() == "x"


def test_unknown_provider_key_rejected(mctrans_home):
    mctrans_home.mkdir(parents=True)
    (mctrans_home / "config.toml").write_text(
        '[provider.deepseek]\nmodle = "typo"\n', encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="modle"):
        load_settings()


def test_custom_provider_requires_base_url(mctrans_home):
    mctrans_home.mkdir(parents=True)
    (mctrans_home / "config.toml").write_text('[provider.x]\nmodel = "m"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="base_url"):
        load_settings()


def test_api_key_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert load_settings().provider("deepseek").resolve_api_key() == "sk-test"


def test_api_key_missing():
    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        load_settings().provider("deepseek").resolve_api_key()


def test_unknown_provider():
    with pytest.raises(ConfigError, match="未知的翻译服务"):
        load_settings().provider("nope")
