import pytest

from antispam_bot.runtime_settings import Settings


async def test_defaults_seeded(settings, config):
    assert settings.get("enabled") is True
    assert settings.get("action_mode") == "ban"
    assert settings.get("language") == config.bot_language
    assert settings.get("model") == config.llm_model
    assert settings.get("llm_daily_limit") == 0
    assert set(settings.all()) == set(Settings.SPEC) == set(settings.describe())


@pytest.mark.parametrize(
    "key, value, expected",
    [
        ("spam_confidence_threshold", "0.95", 0.95),
        ("action_mode", "mute", "mute"),
        ("language", "ru", "ru"),
        ("enabled", "off", False),
        ("enabled", "on", True),
        ("llm_daily_limit", "100", 100),
    ],
)
async def test_set_and_persist(storage, config, key, value, expected):
    s = Settings(storage, config)
    await s.load()
    assert await s.set(key, value) == expected
    reloaded = Settings(storage, config)
    await reloaded.load()
    assert reloaded.get(key) == expected


@pytest.mark.parametrize(
    "key, value",
    [
        ("spam_confidence_threshold", "5"),
        ("action_mode", "explode"),
        ("min_chars_for_llm", "0"),
        ("language", "fr"),
        ("model", "  "),
        ("llm_daily_limit", "-1"),
        ("enabled", "maybe"),
    ],
)
async def test_invalid_values(settings, key, value):
    with pytest.raises(ValueError):
        await settings.set(key, value)


async def test_unknown_key(settings):
    with pytest.raises(KeyError):
        await settings.set("nonexistent", "1")


async def test_corrupted_value_falls_back(storage, config):
    await storage.set_setting(0, "spam_confidence_threshold", "not-a-number")
    s = Settings(storage, config)
    await s.load()
    assert s.get("spam_confidence_threshold") == config.spam_confidence_threshold


async def test_per_chat_override(settings):
    await settings.set("spam_confidence_threshold", "0.5")  # global default
    await settings.set("spam_confidence_threshold", "0.9", chat_id=-100)  # chat override
    assert settings.get("spam_confidence_threshold") == 0.5  # global
    assert settings.get("spam_confidence_threshold", -100) == 0.9  # overridden chat
    assert settings.get("spam_confidence_threshold", -200) == 0.5  # other chat -> global
    assert settings.all(-100)["spam_confidence_threshold"] == 0.9


async def test_ensure_loaded_reads_persisted_override(storage, config):
    await storage.set_setting(-100, "action_mode", "mute")  # persisted by "another session"
    s = Settings(storage, config)
    await s.load()
    assert s.get("action_mode", -100) == "ban"  # not loaded yet -> global default
    await s.ensure_loaded(-100)
    assert s.get("action_mode", -100) == "mute"
    assert s.get("action_mode", -200) == "ban"  # unrelated chat stays global


async def test_corrupted_chat_override_ignored(storage, config):
    await storage.set_setting(-100, "spam_confidence_threshold", "bad")
    s = Settings(storage, config)
    await s.load()
    await s.ensure_loaded(-100)
    assert s.get("spam_confidence_threshold", -100) == config.spam_confidence_threshold
