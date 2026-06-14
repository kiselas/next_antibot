from antispam_bot.classifiers.base import ClassificationContext
from antispam_bot.classifiers.prompt import system_prompt, user_prompt


def test_system_prompt_langs():
    assert "Telegram" in system_prompt("en")
    assert system_prompt("ru") != system_prompt("en")
    assert system_prompt("fr") == system_prompt("en")  # unknown -> fallback


def test_user_prompt_all_flags_en():
    ctx = ClassificationContext(
        text="hello",
        has_links=True,
        has_mentions=True,
        is_forward=True,
        group_topic="cats",
        allowed_domains="ok.com",
        lang="en",
    )
    p = user_prompt(ctx)
    assert "cats" in p and "ok.com" in p
    assert "links" in p and "@mentions" in p and "forwarded" in p
    assert "<<<MESSAGE>>>" in p and "hello" in p


def test_user_prompt_minimal_ru():
    p = user_prompt(ClassificationContext(text="привет", lang="ru"))
    assert "привет" in p
    assert "<<<MESSAGE>>>" in p
