import json

import httpx
import pytest

from antispam_bot.classifiers import AVAILABLE_BACKENDS, build_classifier
from antispam_bot.classifiers.anthropic import AnthropicClassifier
from antispam_bot.classifiers.base import ClassificationContext, Verdict, parse_verdict_json
from antispam_bot.classifiers.heuristic import HeuristicClassifier
from antispam_bot.classifiers.ollama import OllamaClassifier
from antispam_bot.classifiers.openai_compat import OpenAICompatClassifier

CTX = ClassificationContext(text="hello there friends")

# backend -> (class, function wrapping a content string into that API's JSON shape)
BACKENDS = {
    "openai_compat": (OpenAICompatClassifier, lambda c: {"choices": [{"message": {"content": c}}]}),
    "anthropic": (AnthropicClassifier, lambda c: {"content": [{"text": c}]}),
    "ollama": (OllamaClassifier, lambda c: {"message": {"content": c}}),
}
HTTP_BACKENDS = [cls for cls, _ in BACKENDS.values()]
HTTP_IDS = list(BACKENDS)


def _swap(clf, handler):
    clf.client = httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw, is_spam, conf",
    [
        ('{"is_spam": true, "confidence": 0.9, "reason": "ad"}', True, 0.9),
        ('```json\n{"is_spam": false, "confidence": 0.1}\n```', False, 0.1),
        ('junk {"is_spam": true, "confidence": 1.5} tail', True, 1.0),  # clamp high
        ('{"is_spam": false, "confidence": -3}', False, 0.0),  # clamp low
    ],
)
def test_parse_ok(raw, is_spam, conf):
    v = parse_verdict_json(raw)
    assert v is not None and v.is_spam is is_spam and v.confidence == conf


@pytest.mark.parametrize("raw", ["", "no json", "{nope", "{}"])
def test_parse_bad(raw):
    assert parse_verdict_json(raw) is None


def test_verdict_clamp_non_numeric():
    assert Verdict(is_spam=True, confidence="oops").confidence == 0.0


# --------------------------------------------------------------------------- #
# factory
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "backend, cls",
    [(b, c) for b, (c, _) in BACKENDS.items()] + [("heuristic", HeuristicClassifier)],
)
def test_factory(config, settings, backend, cls):
    cfg = config.model_copy(update={"classifier_backend": backend})
    assert isinstance(build_classifier(cfg, settings), cls)


def test_factory_unknown(config, settings):
    with pytest.raises(ValueError):
        build_classifier(config.model_copy(update={"classifier_backend": "bogus"}), settings)


def test_available_backends():
    assert set(AVAILABLE_BACKENDS) == {"openai_compat", "anthropic", "ollama", "heuristic"}


# --------------------------------------------------------------------------- #
# HTTP backends — uniform behaviour across all three
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("cls, wrap", list(BACKENDS.values()), ids=HTTP_IDS)
async def test_backend_success(config, settings, cls, wrap):
    clf = cls(config, settings)
    _swap(
        clf,
        lambda r: httpx.Response(200, json=wrap('{"is_spam":true,"confidence":0.9,"reason":"x"}')),
    )
    res = await clf.classify(CTX)
    assert res.verdict.is_spam and res.error is None
    await clf.close()


@pytest.mark.parametrize("cls, wrap", list(BACKENDS.values()), ids=HTTP_IDS)
async def test_backend_bad_response(config, settings, cls, wrap):
    clf = cls(config, settings)
    _swap(clf, lambda r: httpx.Response(200, json=wrap("not json")))
    res = await clf.classify(CTX)
    assert res.verdict is None and res.error == "bad_response"
    await clf.close()


@pytest.mark.parametrize("cls", HTTP_BACKENDS, ids=HTTP_IDS)
async def test_backend_malformed_payload(config, settings, cls):
    clf = cls(config, settings)
    _swap(clf, lambda r: httpx.Response(200, json={}))  # missing keys -> error
    res = await clf.classify(CTX)
    assert res.verdict is None and res.error == "unavailable"
    await clf.close()


@pytest.mark.parametrize(
    "status, error", [(402, "out_of_credits"), (429, "rate_limited"), (500, "unavailable")]
)
@pytest.mark.parametrize("cls", HTTP_BACKENDS, ids=HTTP_IDS)
async def test_backend_http_error(config, settings, cls, status, error):
    clf = cls(config, settings)
    _swap(clf, lambda r: httpx.Response(status, json={}))
    res = await clf.classify(CTX)
    assert res.verdict is None and res.error == error
    await clf.close()


# --------------------------------------------------------------------------- #
# openai_compat specifics: model fallback and JSON-format toggle
# --------------------------------------------------------------------------- #
async def test_openai_fallback_on_402(config, settings):
    primary = str(settings.get("model"))

    def handler(req):
        body = json.loads(req.content)
        if body["model"] == primary:
            return httpx.Response(402, json={})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"is_spam":false,"confidence":0.2}'}}]}
        )

    clf = OpenAICompatClassifier(config, settings)
    _swap(clf, handler)
    res = await clf.classify(CTX)
    assert res.verdict is not None and res.model == config.llm_fallback_models[0]
    await clf.close()


@pytest.mark.parametrize("use_json, present", [("true", True), ("false", False)])
async def test_openai_json_format_toggle(config, settings, use_json, present):
    seen = {}

    def handler(req):
        seen["present"] = "response_format" in json.loads(req.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"is_spam":false,"confidence":0}'}}]}
        )

    await settings.set("use_json_format", use_json)
    clf = OpenAICompatClassifier(config, settings)
    _swap(clf, handler)
    await clf.classify(CTX)
    assert seen["present"] is present
    await clf.close()


# --------------------------------------------------------------------------- #
# heuristic backend
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "text, kw, is_spam, min_conf",
    [
        ("Заработок на крипте! пиши в личку https://t.me/scam", {"has_links": True}, True, 0.5),
        ("see https://example.com", {"is_forward": True}, False, 0.2),
        ("привет, как дела у всех?", {}, False, 0.0),
    ],
    ids=["spam", "forward_url", "clean"],
)
async def test_heuristic(config, settings, text, kw, is_spam, min_conf):
    clf = HeuristicClassifier(config, settings)
    res = await clf.classify(ClassificationContext(text=text, **kw))
    assert res.verdict.is_spam is is_spam
    assert res.verdict.confidence >= min_conf
    assert res.error is None
