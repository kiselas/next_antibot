from antispam_bot.classifiers.base import ClassifyResult, Verdict
from antispam_bot.pipeline import Decision, MessageMeta, evaluate
from tests.conftest import FakeClassifier

CLEAN_META = MessageMeta(has_links=False, has_mentions=False, is_forward=False)


async def test_spam_above_threshold(settings):
    clf = FakeClassifier(
        ClassifyResult(Verdict(is_spam=True, confidence=0.95, reason="ad"), "m", None)
    )
    res = await evaluate("buy crypto now", CLEAN_META, settings=settings, classifier=clf)
    assert res.decision is Decision.SPAM
    assert res.confidence == 0.95


async def test_spam_below_threshold_is_clean(settings):
    await settings.set("spam_confidence_threshold", "0.9")
    clf = FakeClassifier(
        ClassifyResult(Verdict(is_spam=True, confidence=0.5, reason="?"), "m", None)
    )
    res = await evaluate("maybe ad", CLEAN_META, settings=settings, classifier=clf)
    assert res.decision is Decision.CLEAN


async def test_clean(settings):
    clf = FakeClassifier(ClassifyResult(Verdict(is_spam=False, confidence=0.1), "m", None))
    res = await evaluate("hi everyone", CLEAN_META, settings=settings, classifier=clf)
    assert res.decision is Decision.CLEAN


async def test_error_failsafe(settings):
    clf = FakeClassifier(ClassifyResult(None, None, "out_of_credits"))
    res = await evaluate("some longer text", CLEAN_META, settings=settings, classifier=clf)
    assert res.decision is Decision.ERROR
    assert res.error == "out_of_credits"


async def test_prefilter_skips_short(settings):
    clf = FakeClassifier(ClassifyResult(Verdict(is_spam=True, confidence=1.0), "m", None))
    res = await evaluate("+", CLEAN_META, settings=settings, classifier=clf)
    assert res.decision is Decision.SKIP
    assert clf.calls == 0


async def test_short_with_link_is_checked(settings):
    meta = MessageMeta(has_links=True, has_mentions=False, is_forward=False)
    clf = FakeClassifier(ClassifyResult(Verdict(is_spam=True, confidence=0.99), "m", None))
    res = await evaluate("t.me/x", meta, settings=settings, classifier=clf)
    assert res.decision is Decision.SPAM
    assert clf.calls == 1


async def test_context_carries_settings(settings):
    await settings.set("language", "ru")
    await settings.set("group_topic", "cats")
    clf = FakeClassifier(ClassifyResult(Verdict(is_spam=False, confidence=0.0), "m", None))
    await evaluate("some text here", CLEAN_META, settings=settings, classifier=clf)
    assert clf.last_ctx.lang == "ru"
    assert clf.last_ctx.group_topic == "cats"
