import pytest

from antispam_bot.runtime_settings import Settings


async def test_defaults_seeded(settings, config):
    assert settings.get("enabled") is True
    assert settings.get("action_mode") == "ban"
    assert settings.get("language") == config.bot_language
    assert settings.get("model") == config.llm_model
    assert settings.get("llm_daily_limit") == 0
    # all() and describe() cover every key
    assert set(settings.all()) == set(Settings.SPEC)
    assert set(settings.describe()) == set(Settings.SPEC)


async def test_set_and_persist(storage, config):
    s = Settings(storage, config)
    await s.load()
    await s.set("spam_confidence_threshold", "0.95")
    await s.set("action_mode", "mute")
    await s.set("language", "ru")
    s2 = Settings(storage, config)
    await s2.load()
    assert s2.get("spam_confidence_threshold") == 0.95
    assert s2.get("action_mode") == "mute"
    assert s2.get("language") == "ru"


async def test_validation(settings):
    with pytest.raises(ValueError):
        await settings.set("spam_confidence_threshold", "5")
    with pytest.raises(ValueError):
        await settings.set("action_mode", "explode")
    with pytest.raises(ValueError):
        await settings.set("min_chars_for_llm", "0")
    with pytest.raises(ValueError):
        await settings.set("language", "fr")
    with pytest.raises(ValueError):
        await settings.set("model", "  ")
    with pytest.raises(ValueError):
        await settings.set("llm_daily_limit", "-1")
    with pytest.raises(ValueError):
        await settings.set("enabled", "maybe")
    with pytest.raises(KeyError):
        await settings.set("nonexistent", "1")


async def test_bool_parsing(settings):
    await settings.set("enabled", "off")
    assert settings.get("enabled") is False
    await settings.set("enabled", "on")
    assert settings.get("enabled") is True


async def test_corrupted_value_falls_back(storage, config):
    await storage.set_setting("spam_confidence_threshold", "not-a-number")
    s = Settings(storage, config)
    await s.load()
    assert s.get("spam_confidence_threshold") == config.spam_confidence_threshold
