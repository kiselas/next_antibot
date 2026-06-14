"""Classifier abstraction shared by all backends."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel, ValidationError, field_validator

from ..config import Config

if TYPE_CHECKING:
    from ..runtime_settings import Settings

# Error codes returned by classifiers; mapped to localized labels in i18n (err_*).
ERROR_CODES = ("out_of_credits", "rate_limited", "unavailable", "bad_response")


class Verdict(BaseModel):
    is_spam: bool
    confidence: float = 0.0
    reason: str = ""

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v: object) -> float:
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, f))


@dataclass(frozen=True)
class ClassificationContext:
    text: str
    has_links: bool = False
    has_mentions: bool = False
    is_forward: bool = False
    group_topic: str = ""
    allowed_domains: str = ""
    lang: str = "en"


@dataclass(frozen=True)
class ClassifyResult:
    verdict: Verdict | None
    model: str | None
    error: str | None  # None on success; otherwise one of ERROR_CODES


class Classifier(ABC):
    """A pluggable spam classifier backend."""

    name: str = "base"

    def __init__(self, config: Config, settings: Settings) -> None:
        self.config = config
        self.settings = settings

    @abstractmethod
    async def classify(self, ctx: ClassificationContext) -> ClassifyResult:
        """Return a verdict for the message described by ``ctx``."""

    async def close(self) -> None:  # noqa: B027 - optional no-op for stateless backends
        """Release resources (HTTP clients, etc.)."""


def http_error_code(exc: httpx.HTTPStatusError) -> str:
    """Map an HTTP status to a coarse error code."""
    return {402: "out_of_credits", 429: "rate_limited"}.get(exc.response.status_code, "unavailable")


def parse_verdict_json(content: str | None) -> Verdict | None:
    """Best-effort extraction of a ``Verdict`` from a model's text response."""
    if not content:
        return None
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        newline = content.find("\n")
        if newline != -1:
            content = content[newline + 1 :]
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    snippet = content[start : end + 1]
    try:
        return Verdict.model_validate_json(snippet)
    except ValidationError:
        try:
            return Verdict.model_validate(json.loads(snippet))
        except (ValidationError, json.JSONDecodeError):
            return None
