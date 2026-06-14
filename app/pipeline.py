"""Пайплайн оценки сообщения: пре-фильтр -> LLM -> решение."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .config import Config
from .llm import LLMClient
from .prompts import SYSTEM_PROMPT, build_user_prompt
from .runtime_settings import Settings


class Decision(Enum):
    SKIP = "skip"    # отсеяно пре-фильтром, LLM не вызывался
    CLEAN = "clean"  # LLM: не спам
    SPAM = "spam"    # LLM: спам с уверенностью >= порога
    ERROR = "error"  # LLM недоступен -> fail-safe, никаких действий


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
    llm: LLMClient,
    config: Config,
) -> Result:
    stripped = text.strip()

    # Пре-фильтр: очень короткие сообщения без ссылок не тратят квоту LLM.
    min_chars = int(settings.get("min_chars_for_llm"))
    if len(stripped) < min_chars and not meta.has_links:
        return Result(Decision.SKIP)

    max_chars = int(settings.get("max_chars_to_llm"))
    snippet = stripped[:max_chars]
    user_prompt = build_user_prompt(
        snippet,
        has_links=meta.has_links,
        has_mentions=meta.has_mentions,
        is_forward=meta.is_forward,
        group_topic=str(settings.get("group_topic")),
        allowed_domains=str(settings.get("allowed_domains")),
    )

    models = [str(settings.get("model")), *config.openrouter_fallback_models]
    res = await llm.classify(
        SYSTEM_PROMPT,
        user_prompt,
        models=models,
        use_json_format=bool(settings.get("use_json_format")),
    )
    if res.verdict is None:
        return Result(Decision.ERROR, error=res.error)

    threshold = float(settings.get("spam_confidence_threshold"))
    if res.verdict.is_spam and res.verdict.confidence >= threshold:
        return Result(Decision.SPAM, res.verdict.confidence, res.verdict.reason, res.model)
    return Result(Decision.CLEAN, res.verdict.confidence, res.verdict.reason, res.model)
