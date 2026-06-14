"""Bot configuration loaded from environment variables (.env).

Provider-neutral ``LLM_*`` names are preferred; legacy ``OPENROUTER_*`` names are
accepted as aliases for backward compatibility.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import AliasChoices, Field, field_validator
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
    # Allowed chats. Empty = work everywhere (with a startup warning).
    allowed_chat_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)

    # --- Classifier backend ---
    # One of: openai_compat | anthropic | ollama | heuristic
    classifier_backend: str = "openai_compat"

    # --- LLM connection (provider-neutral; OPENROUTER_* kept as aliases) ---
    llm_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("LLM_API_KEY", "OPENROUTER_API_KEY"),
    )
    llm_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        validation_alias=AliasChoices("LLM_BASE_URL", "OPENROUTER_BASE_URL"),
    )
    llm_model: str = Field(
        default="google/gemini-2.0-flash-lite-001",
        validation_alias=AliasChoices("LLM_MODEL", "OPENROUTER_MODEL"),
    )
    llm_fallback_models: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["meta-llama/llama-3.3-70b-instruct:free"],
        validation_alias=AliasChoices("LLM_FALLBACK_MODELS", "OPENROUTER_FALLBACK_MODELS"),
    )
    anthropic_version: str = "2023-06-01"

    # --- Moderation defaults (seed values for dynamic settings) ---
    bot_language: str = "en"
    spam_confidence_threshold: float = 0.85
    trust_after_clean_msgs: int = 3
    trust_after_hours: int = 24
    max_chars_to_llm: int = 2000
    min_chars_for_llm: int = 2
    group_topic: str = ""

    # --- Misc ---
    llm_timeout_seconds: float = 8.0
    llm_error_alert_threshold: int = 5
    db_path: str = "/app/data/bot.db"
    log_level: str = "INFO"
    http_referer: str = "https://github.com/kiselas/next_antibot"
    app_title: str = "AntispamBot"

    @field_validator("admin_usernames", "llm_fallback_models", mode="before")
    @classmethod
    def _split_csv(cls, v: object) -> list[str]:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return list(v)  # type: ignore  # already a sequence at this point

    @field_validator("admin_user_ids", "allowed_chat_ids", mode="before")
    @classmethod
    def _split_int_csv(cls, v: object) -> list[int]:
        if v is None or v == "":
            return []
        if isinstance(v, str):
            return [int(item.strip()) for item in v.split(",") if item.strip()]
        return [int(x) for x in v]  # type: ignore  # already an iterable at this point

    @field_validator("admin_usernames", mode="after")
    @classmethod
    def _normalize_usernames(cls, v: list[str]) -> list[str]:
        return [u.lstrip("@").lower() for u in v]
