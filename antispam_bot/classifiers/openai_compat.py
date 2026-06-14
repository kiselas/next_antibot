"""Classifier for any OpenAI-compatible Chat Completions API.

Works with OpenRouter, OpenAI, Together, Groq, vLLM, LM Studio, Ollama's
``/v1`` endpoint, and similar — just point ``LLM_BASE_URL`` at the provider.
"""

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


class OpenAICompatClassifier(Classifier):
    name = "openai_compat"

    def __init__(self, config: Config, settings: Settings) -> None:
        super().__init__(config, settings)
        self.client = httpx.AsyncClient(
            base_url=config.llm_base_url,
            timeout=config.llm_timeout_seconds,
            headers={
                "Authorization": f"Bearer {config.llm_api_key}",
                "HTTP-Referer": config.http_referer,
                "X-Title": config.app_title,
            },
        )

    async def close(self) -> None:
        await self.client.aclose()

    def _models(self) -> list[str]:
        return [str(self.settings.get("model")), *self.config.llm_fallback_models]

    async def classify(self, ctx: ClassificationContext) -> ClassifyResult:
        use_json = bool(self.settings.get("use_json_format"))
        sysp, usrp = system_prompt(ctx.lang), user_prompt(ctx)
        last_error: str | None = None
        for model in dict.fromkeys(self._models()):
            try:
                verdict = await self._call(model, sysp, usrp, use_json)
            except httpx.HTTPStatusError as exc:
                last_error = http_error_code(exc)
                log.warning("Model %s: HTTP %s (%s)", model, exc.response.status_code, last_error)
                continue
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                last_error = "unavailable"
                log.warning("Model %s unavailable: %s", model, exc)
                continue
            if verdict is not None:
                return ClassifyResult(verdict, model, None)
            last_error = "bad_response"
            log.warning("Model %s returned an unparseable answer", model)
        return ClassifyResult(None, None, last_error or "unavailable")

    async def _call(self, model: str, system: str, user: str, use_json: bool) -> Verdict | None:
        payload: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": 200,
        }
        if use_json:
            payload["response_format"] = {"type": "json_object"}
        resp = await self.client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        return parse_verdict_json(data["choices"][0]["message"]["content"])
