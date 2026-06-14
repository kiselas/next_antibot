import json

import httpx
import pytest

from antispam_bot.classifiers import AVAILABLE_BACKENDS, build_classifier
from antispam_bot.classifiers.anthropic import AnthropicClassifier
from antispam_bot.classifiers.base import (
    ClassificationContext,
    Verdict,
    parse_verdict_json,
)
from antispam_bot.classifiers.heuristic import HeuristicClassifier
from antispam_bot.classifiers.ollama import OllamaClassifier
from antispam_bot.classifiers.openai_compat import OpenAICompatClassifier

CTX = ClassificationContext(text="hello there friends", has_links=False)


# ---- parser ----
@pytest.mark.parametrize(
    "raw, is_spam, conf",
    [
        ('{"is_spam": true, "confidence": 0.9, "reason": "ad"}', True, 0.9),
        ('```json\n{"is_spam": false, "confidence": 0.1}\n```', False, 0.1),
        ('junk {"is_spam": true, "confidence": 1.5} tail', True, 1.0),
        ('{"is_spam": false, "confidence": -3}', False, 0.0),
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


# ---- factory ----
def test_factory(config, settings):
    for backend, cls in [
        ("openai_compat", OpenAICompatClassifier),
        ("anthropic", AnthropicClassifier),
        ("ollama", OllamaClassifier),
        ("heuristic", HeuristicClassifier),
    ]:
        cfg = config.model_copy(update={"classifier_backend": backend})
        assert isinstance(build_classifier(cfg, settings), cls)
    assert set(AVAILABLE_BACKENDS) == {"openai_compat", "anthropic", "ollama", "heuristic"}


def test_factory_unknown(config, settings):
    cfg = config.model_copy(update={"classifier_backend": "bogus"})
    with pytest.raises(ValueError):
        build_classifier(cfg, settings)


def _swap_transport(clf, handler):
    clf.client = httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(handler))


# ---- openai_compat ----
async def test_openai_success(config, settings):
    clf = OpenAICompatClassifier(config, settings)
    _swap_transport(
        clf,
        lambda r: httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"is_spam":true,"confidence":0.9,"reason":"ad"}'}}
                ]
            },
        ),
    )
    res = await clf.classify(CTX)
    assert res.verdict.is_spam and res.error is None
    await clf.close()


async def test_openai_fallback_on_402(config, settings):
    primary = str(settings.get("model"))

    def handler(req):
        body = json.loads(req.content)
        if body["model"] == primary:
            return httpx.Response(402, json={"error": "no credits"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"is_spam":false,"confidence":0.2}'}}]},
        )

    clf = OpenAICompatClassifier(config, settings)
    _swap_transport(clf, handler)
    res = await clf.classify(CTX)
    assert res.verdict is not None and res.verdict.is_spam is False
    assert res.model == config.llm_fallback_models[0]
    await clf.close()


async def test_openai_all_fail_out_of_credits(config, settings):
    clf = OpenAICompatClassifier(config, settings)
    _swap_transport(clf, lambda r: httpx.Response(402, json={}))
    res = await clf.classify(CTX)
    assert res.verdict is None and res.error == "out_of_credits"
    await clf.close()


async def test_openai_bad_response(config, settings):
    clf = OpenAICompatClassifier(config, settings)
    _swap_transport(
        clf,
        lambda r: httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}),
    )
    res = await clf.classify(CTX)
    assert res.verdict is None and res.error == "bad_response"
    await clf.close()


async def test_openai_rate_limited(config, settings):
    clf = OpenAICompatClassifier(config, settings)
    _swap_transport(clf, lambda r: httpx.Response(429, json={}))
    res = await clf.classify(CTX)
    assert res.error == "rate_limited"
    await clf.close()


async def test_openai_uses_json_format_toggle(config, settings):
    seen = {}

    def handler(req):
        seen["has_format"] = "response_format" in json.loads(req.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"is_spam":false,"confidence":0}'}}]}
        )

    await settings.set("use_json_format", "false")
    clf = OpenAICompatClassifier(config, settings)
    _swap_transport(clf, handler)
    await clf.classify(CTX)
    assert seen["has_format"] is False
    await clf.close()


# ---- anthropic ----
async def test_anthropic_success(config, settings):
    cfg = config.model_copy(update={"classifier_backend": "anthropic"})
    clf = AnthropicClassifier(cfg, settings)
    _swap_transport(
        clf,
        lambda r: httpx.Response(
            200, json={"content": [{"text": '{"is_spam":true,"confidence":0.8,"reason":"x"}'}]}
        ),
    )
    res = await clf.classify(CTX)
    assert res.verdict.is_spam and res.error is None
    await clf.close()


async def test_anthropic_error(config, settings):
    clf = AnthropicClassifier(config, settings)
    _swap_transport(clf, lambda r: httpx.Response(500, json={}))
    res = await clf.classify(CTX)
    assert res.verdict is None and res.error == "unavailable"
    await clf.close()


# ---- ollama ----
async def test_ollama_success(config, settings):
    clf = OllamaClassifier(config, settings)
    _swap_transport(
        clf,
        lambda r: httpx.Response(
            200, json={"message": {"content": '{"is_spam":false,"confidence":0.0}'}}
        ),
    )
    res = await clf.classify(CTX)
    assert res.verdict is not None and res.verdict.is_spam is False
    await clf.close()


# ---- heuristic ----
async def test_heuristic_spam(config, settings):
    clf = HeuristicClassifier(config, settings)
    ctx = ClassificationContext(
        text="Заработок на крипте! пиши в личку https://t.me/scam", has_links=True
    )
    res = await clf.classify(ctx)
    assert res.verdict.is_spam is True
    assert res.verdict.confidence >= 0.5
    assert res.error is None


async def test_heuristic_clean(config, settings):
    clf = HeuristicClassifier(config, settings)
    res = await clf.classify(ClassificationContext(text="привет, как дела у всех?"))
    assert res.verdict.is_spam is False
    assert res.verdict.confidence == 0.0


async def test_heuristic_forward_with_url(config, settings):
    clf = HeuristicClassifier(config, settings)
    ctx = ClassificationContext(text="see https://example.com", is_forward=True, lang="xx")
    res = await clf.classify(ctx)
    assert res.verdict.confidence > 0.0  # mention/forward+url signal


# ---- extra error paths ----
async def test_openai_malformed_json(config, settings):
    clf = OpenAICompatClassifier(config, settings)
    _swap_transport(clf, lambda r: httpx.Response(200, json={}))  # missing 'choices'
    res = await clf.classify(CTX)
    assert res.verdict is None and res.error == "unavailable"
    await clf.close()


async def test_anthropic_bad_response(config, settings):
    clf = AnthropicClassifier(config, settings)
    _swap_transport(clf, lambda r: httpx.Response(200, json={"content": [{"text": "not json"}]}))
    res = await clf.classify(CTX)
    assert res.verdict is None and res.error == "bad_response"
    await clf.close()


async def test_anthropic_malformed(config, settings):
    clf = AnthropicClassifier(config, settings)
    _swap_transport(clf, lambda r: httpx.Response(200, json={}))  # missing 'content'
    res = await clf.classify(CTX)
    assert res.verdict is None and res.error == "unavailable"
    await clf.close()


async def test_ollama_error(config, settings):
    clf = OllamaClassifier(config, settings)
    _swap_transport(clf, lambda r: httpx.Response(500, json={}))
    res = await clf.classify(CTX)
    assert res.verdict is None and res.error == "unavailable"
    await clf.close()


async def test_ollama_bad_response(config, settings):
    clf = OllamaClassifier(config, settings)
    _swap_transport(clf, lambda r: httpx.Response(200, json={"message": {"content": "nope"}}))
    res = await clf.classify(CTX)
    assert res.verdict is None and res.error == "bad_response"
    await clf.close()


async def test_ollama_malformed(config, settings):
    clf = OllamaClassifier(config, settings)
    _swap_transport(clf, lambda r: httpx.Response(200, json={}))  # missing 'message'
    res = await clf.classify(CTX)
    assert res.verdict is None and res.error == "unavailable"
    await clf.close()
