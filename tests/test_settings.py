import pytest

from app.runtime_settings import Settings


async def test_defaults_seeded(settings, config):
    assert settings.get("enabled") is True
    assert settings.get("action_mode") == "ban"
    assert settings.get("spam_confidence_threshold") == config.spam_confidence_threshold
    assert settings.get("model") == config.openrouter_model


async def test_set_and_persist(storage, config):
    s = Settings(storage, config)
    await s.load()
    await s.set("spam_confidence_threshold", "0.95")
    await s.set("action_mode", "mute")
    assert s.get("spam_confidence_threshold") == 0.95
    assert s.get("action_mode") == "mute"

    # Перечитываем из БД новым экземпляром.
    s2 = Settings(storage, config)
    await s2.load()
    assert s2.get("spam_confidence_threshold") == 0.95
    assert s2.get("action_mode") == "mute"


async def test_validation(settings):
    with pytest.raises(ValueError):
        await settings.set("spam_confidence_threshold", "5")
    with pytest.raises(ValueError):
        await settings.set("action_mode", "explode")
    with pytest.raises(ValueError):
        await settings.set("min_chars_for_llm", "0")
    with pytest.raises(KeyError):
        await settings.set("nonexistent", "1")


async def test_bool_parsing(settings):
    await settings.set("enabled", "off")
    assert settings.get("enabled") is False
    await settings.set("enabled", "да")
    assert settings.get("enabled") is True
