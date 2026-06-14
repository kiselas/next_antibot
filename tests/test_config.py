import pytest

from antispam_bot.config import Config


@pytest.mark.parametrize(
    "env, value, attr, expected",
    [
        ("ADMIN_USERNAMES", "@Foo, bar", "admin_usernames", ["foo", "bar"]),  # normalized
        ("ADMIN_USERNAMES", "", "admin_usernames", []),
        ("ADMIN_USER_IDS", "", "admin_user_ids", []),
        ("ADMIN_USER_IDS", "111, 222", "admin_user_ids", [111, 222]),
        ("ALLOWED_CHAT_IDS", "-100, -200", "allowed_chat_ids", [-100, -200]),
        (
            "LLM_FALLBACK_MODELS",
            "a/b:free, c/d:free",
            "llm_fallback_models",
            ["a/b:free", "c/d:free"],
        ),
    ],
)
def test_csv_parsing(monkeypatch, env, value, attr, expected):
    monkeypatch.setenv(env, value)
    assert getattr(Config(_env_file=None), attr) == expected


@pytest.mark.parametrize(
    "env, attr, value",
    [
        ("OPENROUTER_API_KEY", "llm_api_key", "sk-legacy"),
        ("OPENROUTER_MODEL", "llm_model", "legacy/model"),
    ],
)
def test_legacy_openrouter_aliases(monkeypatch, env, attr, value):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv(env, value)
    assert getattr(Config(_env_file=None), attr) == value


def test_defaults():
    cfg = Config(_env_file=None)
    assert cfg.classifier_backend == "openai_compat"
    assert cfg.bot_language == "en"
    assert cfg.llm_base_url.startswith("https://")
