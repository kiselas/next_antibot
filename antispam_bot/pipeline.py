"""Message evaluation pipeline: pre-filter -> classifier -> decision."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .classifiers import ClassificationContext, Classifier
from .runtime_settings import Settings


class Decision(Enum):
    SKIP = "skip"  # filtered out before the classifier
    CLEAN = "clean"  # classifier: not spam
    SPAM = "spam"  # classifier: spam with confidence >= threshold
    ERROR = "error"  # classifier unavailable -> fail-safe, no action


@dataclass(frozen=True)
class MessageMeta:
    has_links: bool
    has_mentions: bool
    is_forward: bool


@dataclass(frozen=True)
class Result:
    decision: Decision
    confidence: float = 0.0
    reason: str = ""
    model: str | None = None
    error: str | None = None


async def evaluate(
    text: str,
    meta: MessageMeta,
    *,
    settings: Settings,
    classifier: Classifier,
    chat_id: int = 0,
) -> Result:
    stripped = text.strip()

    # Pre-filter: very short messages without links don't consume the classifier.
    min_chars = int(settings.get("min_chars_for_llm", chat_id))
    if len(stripped) < min_chars and not meta.has_links:
        return Result(Decision.SKIP)

    max_chars = int(settings.get("max_chars_to_llm", chat_id))
    ctx = ClassificationContext(
        text=stripped[:max_chars],
        has_links=meta.has_links,
        has_mentions=meta.has_mentions,
        is_forward=meta.is_forward,
        group_topic=str(settings.get("group_topic", chat_id)),
        allowed_domains=str(settings.get("allowed_domains", chat_id)),
        lang=str(settings.get("language")),  # language is global
    )

    res = await classifier.classify(ctx)
    if res.verdict is None:
        return Result(Decision.ERROR, error=res.error)

    threshold = float(settings.get("spam_confidence_threshold", chat_id))
    if res.verdict.is_spam and res.verdict.confidence >= threshold:
        return Result(Decision.SPAM, res.verdict.confidence, res.verdict.reason, res.model)
    return Result(Decision.CLEAN, res.verdict.confidence, res.verdict.reason, res.model)
