"""Клиент OpenRouter: классификация сообщения с fallback по моделям."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, ValidationError, field_validator

from .config import Config

log = logging.getLogger(__name__)

# Человекочитаемые ошибки для уведомлений админу.
ERROR_LABELS = {
    "out_of_credits": "закончились кредиты OpenRouter (402)",
    "rate_limited": "превышен лимит запросов OpenRouter (429)",
    "unavailable": "OpenRouter недоступен",
    "bad_response": "модель возвращает некорректный ответ",
}


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
class ClassifyResult:
    verdict: Verdict | None
    model: str | None
    error: str | None  # None при успехе; иначе ключ из ERROR_LABELS


class LLMClient:
    def __init__(self, config: Config) -> None:
        self.cfg = config
        self.client = httpx.AsyncClient(
            base_url=config.openrouter_base_url,
            timeout=config.llm_timeout_seconds,
            headers={
                "Authorization": f"Bearer {config.openrouter_api_key}",
                "HTTP-Referer": config.http_referer,
                "X-Title": config.app_title,
            },
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def classify(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        models: list[str],
        use_json_format: bool = True,
    ) -> ClassifyResult:
        last_error: str | None = None
        for model in list(dict.fromkeys(models)):  # порядок + дедуп
            try:
                verdict = await self._call(model, system_prompt, user_prompt, use_json_format)
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                last_error = {402: "out_of_credits", 429: "rate_limited"}.get(
                    code, "unavailable"
                )
                log.warning("Модель %s: HTTP %s (%s)", model, code, last_error)
                continue
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                last_error = "unavailable"
                log.warning("Модель %s недоступна: %s", model, exc)
                continue
            if verdict is not None:
                return ClassifyResult(verdict, model, None)
            last_error = "bad_response"
            log.warning("Модель %s вернула неразборчивый ответ", model)
        return ClassifyResult(None, None, last_error or "unavailable")

    async def _call(
        self, model: str, system_prompt: str, user_prompt: str, use_json_format: bool
    ) -> Verdict | None:
        payload: dict[str, object] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0,
            "max_tokens": 200,
        }
        if use_json_format:
            payload["response_format"] = {"type": "json_object"}
        resp = await self.client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return self._parse(content)

    @staticmethod
    def _parse(content: str) -> Verdict | None:
        if not content:
            return None
        content = content.strip()
        # Снимаем возможные ```json ... ``` обёртки
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
