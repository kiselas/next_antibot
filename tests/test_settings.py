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
    await storage.set_setting("spam_confidence_threshold", "not-a-number")
    s = Settings(storage, config)
    await s.load()
    assert s.get("spam_confidence_threshold") == config.spam_confidence_threshold
