import pytest

from app.llm import LLMClient


@pytest.mark.parametrize(
    "raw, is_spam, conf",
    [
        ('{"is_spam": true, "confidence": 0.9, "reason": "реклама"}', True, 0.9),
        ('```json\n{"is_spam": false, "confidence": 0.1, "reason": "ок"}\n```', False, 0.1),
        ('текст до {"is_spam": true, "confidence": 1.5} хвост', True, 1.0),  # clamp
        ('{"is_spam": false, "confidence": -3}', False, 0.0),  # clamp
    ],
)
def test_parse_ok(raw, is_spam, conf):
    v = LLMClient._parse(raw)
    assert v is not None
    assert v.is_spam is is_spam
    assert v.confidence == conf


@pytest.mark.parametrize("raw", ["", "не json вовсе", "{нет валидного объекта", "{}"])
def test_parse_bad(raw):
    # "{}" не содержит is_spam -> провал валидации; остальное не парсится.
    assert LLMClient._parse(raw) is None
