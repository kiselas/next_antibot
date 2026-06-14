from antispam_bot.config import Config


def test_csv_and_int_csv(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAMES", "@Foo, bar")
    monkeypatch.setenv("ADMIN_USER_IDS", "111, 222")
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "-100, -200")
    monkeypatch.setenv("LLM_FALLBACK_MODELS", "a/b:free, c/d:free")
    cfg = Config(_env_file=None)
    assert cfg.admin_usernames == ["foo", "bar"]  # normalized
    assert cfg.admin_user_ids == [111, 222]
    assert cfg.allowed_chat_ids == [-100, -200]
    assert cfg.llm_fallback_models == ["a/b:free", "c/d:free"]


def test_empty_lists(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAMES", "")
    monkeypatch.setenv("ADMIN_USER_IDS", "")
    cfg = Config(_env_file=None)
    assert cfg.admin_usernames == []
    assert cfg.admin_user_ids == []


def test_legacy_openrouter_aliases(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-legacy")
    monkeypatch.setenv("OPENROUTER_MODEL", "legacy/model")
    cfg = Config(_env_file=None)
    assert cfg.llm_api_key == "sk-legacy"
    assert cfg.llm_model == "legacy/model"


def test_defaults():
    cfg = Config(_env_file=None)
    assert cfg.classifier_backend == "openai_compat"
    assert cfg.bot_language == "en"
    assert cfg.llm_base_url.startswith("https://")
