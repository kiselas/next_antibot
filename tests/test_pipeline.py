import pytest

from app.llm import ClassifyResult, Verdict
from app.pipeline import Decision, MessageMeta, evaluate

CLEAN_META = MessageMeta(has_links=False, has_mentions=False, is_forward=False)


class FakeLLM:
    """Подменяет LLMClient: возвращает заранее заданный результат."""

    def __init__(self, result: ClassifyResult):
        self.result = result
        self.calls = 0

    async def classify(self, system, user, *, models, use_json_format=True):
        self.calls += 1
        self.last_models = models
        return self.result


async def test_spam_above_threshold(settings, config):
    llm = FakeLLM(ClassifyResult(Verdict(is_spam=True, confidence=0.95, reason="ad"), "m", None))
    res = await evaluate("купи крипту сейчас", CLEAN_META, settings=settings, llm=llm, config=config)
    assert res.decision is Decision.SPAM
    assert res.confidence == 0.95


async def test_spam_below_threshold_is_clean(settings, config):
    await settings.set("spam_confidence_threshold", "0.9")
    llm = FakeLLM(ClassifyResult(Verdict(is_spam=True, confidence=0.5, reason="?"), "m", None))
    res = await evaluate("возможно реклама", CLEAN_META, settings=settings, llm=llm, config=config)
    assert res.decision is Decision.CLEAN


async def test_clean(settings, config):
    llm = FakeLLM(ClassifyResult(Verdict(is_spam=False, confidence=0.1, reason="ok"), "m", None))
    res = await evaluate("привет всем, как дела", CLEAN_META, settings=settings, llm=llm, config=config)
    assert res.decision is Decision.CLEAN


async def test_llm_error_failsafe(settings, config):
    llm = FakeLLM(ClassifyResult(None, None, "out_of_credits"))
    res = await evaluate("любой текст подлиннее", CLEAN_META, settings=settings, llm=llm, config=config)
    assert res.decision is Decision.ERROR
    assert res.error == "out_of_credits"


async def test_prefilter_skips_short(settings, config):
    llm = FakeLLM(ClassifyResult(Verdict(is_spam=True, confidence=1.0, reason="x"), "m", None))
    res = await evaluate("+", CLEAN_META, settings=settings, llm=llm, config=config)
    assert res.decision is Decision.SKIP
    assert llm.calls == 0  # LLM не вызывался


async def test_short_with_link_is_checked(settings, config):
    meta = MessageMeta(has_links=True, has_mentions=False, is_forward=False)
    llm = FakeLLM(ClassifyResult(Verdict(is_spam=True, confidence=0.99, reason="link"), "m", None))
    res = await evaluate("t.me/x", meta, settings=settings, llm=llm, config=config)
    assert res.decision is Decision.SPAM
    assert llm.calls == 1


async def test_model_from_settings_first(settings, config):
    await settings.set("model", "custom/model")
    llm = FakeLLM(ClassifyResult(Verdict(is_spam=False, confidence=0.0, reason=""), "m", None))
    await evaluate("текст для проверки модели", CLEAN_META, settings=settings, llm=llm, config=config)
    assert llm.last_models[0] == "custom/model"
    assert config.openrouter_fallback_models[0] in llm.last_models
