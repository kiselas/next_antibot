import pytest

from antispam_bot.classifiers.base import ClassifyResult, Verdict
from antispam_bot.pipeline import Decision, MessageMeta, evaluate
from tests.conftest import CLEAN, ERROR, SPAM, fake

CLEAN_META = MessageMeta(has_links=False, has_mentions=False, is_forward=False)
LINK_META = MessageMeta(has_links=True, has_mentions=False, is_forward=False)


@pytest.mark.parametrize(
    "result, text, meta, decision",
    [
        (SPAM, "buy crypto now", CLEAN_META, Decision.SPAM),
        (CLEAN, "hi everyone", CLEAN_META, Decision.CLEAN),
        (ERROR, "some longer text", CLEAN_META, Decision.ERROR),
        (SPAM, "+", CLEAN_META, Decision.SKIP),  # too short, no link -> pre-filter
        (SPAM, "t.me/x", LINK_META, Decision.SPAM),  # short but has a link -> checked
    ],
    ids=["spam", "clean", "error", "prefilter", "short_with_link"],
)
async def test_evaluate(settings, result, text, meta, decision):
    clf = fake(result)
    res = await evaluate(text, meta, settings=settings, classifier=clf)
    assert res.decision is decision
    assert clf.calls == (0 if decision is Decision.SKIP else 1)


async def test_below_threshold_is_clean(settings):
    await settings.set("spam_confidence_threshold", "0.9")
    clf = fake(ClassifyResult(Verdict(is_spam=True, confidence=0.5), "m", None))
    res = await evaluate("maybe ad", CLEAN_META, settings=settings, classifier=clf)
    assert res.decision is Decision.CLEAN


async def test_error_carries_code(settings):
    res = await evaluate("longer text", CLEAN_META, settings=settings, classifier=fake(ERROR))
    assert res.error == "out_of_credits"


async def test_context_carries_settings(settings):
    await settings.set("language", "ru")
    await settings.set("group_topic", "cats")
    clf = fake(CLEAN)
    await evaluate("some text here", CLEAN_META, settings=settings, classifier=clf)
    assert clf.last_ctx.lang == "ru"
    assert clf.last_ctx.group_topic == "cats"
