"""Classifier for the native Anthropic Messages API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx

from ..config import Config
from .base import (
    ClassificationContext,
    Classifier,
    ClassifyResult,
    Verdict,
    http_error_code,
    parse_verdict_json,
)
from .prompt import system_prompt, user_prompt

if TYPE_CHECKING:
    from ..runtime_settings import Settings

log = logging.getLogger(__name__)

_DEFAULT_BASE = "https://api.anthropic.com"


class AnthropicClassifier(Classifier):
    name = "anthropic"

    def __init__(self, config: Config, settings: Settings) -> None:
        super().__init__(config, settings)
        base = config.llm_base_url
        if "openrouter" in base:  # default placeholder — use the real Anthropic host
            base = _DEFAULT_BASE
        self.client = httpx.AsyncClient(
            base_url=base,
            timeout=config.llm_timeout_seconds,
            headers={
                "x-api-key": config.llm_api_key,
                "anthropic-version": config.anthropic_version,
                "content-type": "application/json",
            },
        )

    async def close(self) -> None:
        await self.client.aclose()

    def _models(self) -> list[str]:
        return [str(self.settings.get("model")), *self.config.llm_fallback_models]

    async def classify(self, ctx: ClassificationContext) -> ClassifyResult:
        sysp, usrp = system_prompt(ctx.lang), user_prompt(ctx)
        last_error: str | None = None
        for model in dict.fromkeys(self._models()):
            try:
                verdict = await self._call(model, sysp, usrp)
            except httpx.HTTPStatusError as exc:
                last_error = http_error_code(exc)
                log.warning("Model %s: HTTP %s (%s)", model, exc.response.status_code, last_error)
                continue
            except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
                last_error = "unavailable"
                log.warning("Model %s unavailable: %s", model, exc)
                continue
            if verdict is not None:
                return ClassifyResult(verdict, model, None)
            last_error = "bad_response"
        return ClassifyResult(None, None, last_error or "unavailable")

    async def _call(self, model: str, system: str, user: str) -> Verdict | None:
        payload = {
            "model": model,
            "max_tokens": 200,
            "temperature": 0,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        resp = await self.client.post("/v1/messages", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return parse_verdict_json(data["content"][0]["text"])
