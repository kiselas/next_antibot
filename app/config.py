"""Конфигурация бота из переменных окружения (.env)."""
from __future__ import annotations

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Telegram ---
    bot_token: str
    admin_usernames: Annotated[list[str], NoDecode] = Field(default_factory=list)
    admin_user_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    admin_chat_id: int | None = None
    # Список разрешённых чатов. Пусто = работать везде (с предупреждением в логах).
    allowed_chat_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    # --- OpenRouter ---
    openrouter_api_key: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "google/gemini-2.0-flash-lite-001"
    openrouter_fallback_models: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["meta-llama/llama-3.3-70b-instruct:free"]
    )

    # --- Поведение модерации (дефолты для динамических настроек) ---
    spam_confidence_threshold: float = 0.85
    trust_after_clean_msgs: int = 3
    trust_after_hours: int = 24
    max_chars_to_llm: int = 2000
    min_chars_for_llm: int = 2
    group_topic: str = ""

    # --- Прочее ---
    llm_timeout_seconds: float = 8.0
    # Сколько ошибок LLM подряд до уведомления админа.
    llm_error_alert_threshold: int = 5
    db_path: str = "/app/data/bot.db"
    log_level: str = "INFO"
    http_referer: str = "https://github.com/antispam-bot"
    app_title: str = "AntispamBot"

    @field_validator("admin_usernames", "openrouter_fallback_models", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> list[str]:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return list(v)  # type: ignore[arg-type]

    @field_validator("admin_user_ids", "allowed_chat_ids", mode="before")
    @classmethod
    def _split_int_csv(cls, v: object) -> list[int]:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(item.strip()) for item in v.split(",") if item.strip()]
        return [int(x) for x in v]  # type: ignore[union-attr]

    @field_validator("admin_usernames", mode="after")
    @classmethod
    def _normalize_usernames(cls, v: list[str]) -> list[str]:
        return [u.lstrip("@").lower() for u in v]
