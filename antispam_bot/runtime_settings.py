"""Dynamic settings changeable at runtime via /set and /setchat.

Defaults are seeded from Config (.env) and stored under chat_id 0; each chat may
override any key. ``get(key, chat_id)`` returns the chat override if present, else
the global default. ``language`` is resolved globally (chat_id 0) by the core.
Each setting has its own parser/validator.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from .config import Config
from .i18n import SUPPORTED_LANGS
from .storage import Storage


def _to_bool(v: object) -> bool:
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    raise ValueError("expected true/false")


def _to_unit_float(v: object) -> float:
    f = float(str(v).replace(",", "."))
    if not 0.0 <= f <= 1.0:
        raise ValueError("expected a number between 0.0 and 1.0")
    return f


def _to_nonneg_int(v: object) -> int:
    i = int(str(v).strip())
    if i < 0:
        raise ValueError("expected an integer >= 0")
    return i


def _to_pos_int(v: object) -> int:
    i = int(str(v).strip())
    if i <= 0:
        raise ValueError("expected an integer > 0")
    return i


def _to_str(v: object) -> str:
    return str(v).strip()


def _to_action(v: object) -> str:
    s = str(v).strip().lower()
    if s in ("ban", "mute", "report"):
        return s
    raise ValueError("expected ban / mute / report")


def _to_model(v: object) -> str:
    s = str(v).strip()
    if not s:
        raise ValueError("model name cannot be empty")
    return s


def _to_lang(v: object) -> str:
    s = str(v).strip().lower()
    if s in SUPPORTED_LANGS:
        return s
    raise ValueError("supported: " + ", ".join(SUPPORTED_LANGS))


class Settings:
    # key -> (parser, description)
    SPEC: dict[str, tuple[Callable[[object], object], str]] = {
        "enabled": (_to_bool, "Enable/disable moderation (true/false)"),
        "language": (_to_lang, "Language of bot messages (en/ru)"),
        "action_mode": (_to_action, "Reaction to spam: ban / mute / report"),
        "spam_confidence_threshold": (_to_unit_float, "Confidence threshold to act (0.0..1.0)"),
        "trust_after_clean_msgs": (_to_nonneg_int, "Clean messages until 'trusted' (0=off)"),
        "trust_after_hours": (_to_nonneg_int, "Hours in the group until 'trusted' (0=off)"),
        "min_chars_for_llm": (_to_pos_int, "Minimum characters to call the classifier"),
        "max_chars_to_llm": (_to_pos_int, "Maximum message characters sent to the classifier"),
        "llm_daily_limit": (
            _to_nonneg_int,
            "Daily classifier-call limit, anti-flood (0=unlimited)",
        ),
        "group_topic": (_to_str, "Group topic for classifier context (empty = none)"),
        "allowed_domains": (_to_str, "Domains whose links are acceptable (comma-separated)"),
        "model": (_to_model, "Primary model id"),
        "use_json_format": (_to_bool, "Request strict JSON from the model (true/false)"),
    }

    def __init__(self, storage: Storage, config: Config) -> None:
        self.storage = storage
        self.config = config
        self._cache: dict[str, object] = {}  # global defaults (chat_id 0)
        self._chat_cache: dict[int, dict[str, object]] = {}  # per-chat overrides

    def _default(self, key: str) -> object:
        return {
            "enabled": True,
            "language": self.config.bot_language,
            "action_mode": "ban",
            "spam_confidence_threshold": self.config.spam_confidence_threshold,
            "trust_after_clean_msgs": self.config.trust_after_clean_msgs,
            "trust_after_hours": self.config.trust_after_hours,
            "min_chars_for_llm": self.config.min_chars_for_llm,
            "max_chars_to_llm": self.config.max_chars_to_llm,
            "llm_daily_limit": 0,
            "group_topic": self.config.group_topic,
            "allowed_domains": "",
            "model": self.config.llm_model,
            "use_json_format": True,
        }[key]

    @staticmethod
    def _serialize(v: object) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    def _parse_rows(self, rows: dict[str, str]) -> dict[str, object]:
        parsed: dict[str, object] = {}
        for key, (parse, _desc) in self.SPEC.items():
            if key in rows:
                with contextlib.suppress(ValueError):  # corrupted value -> fall back to default
                    parsed[key] = parse(rows[key])
        return parsed

    async def load(self) -> None:
        """Load global defaults (chat_id 0), seeding any that are missing."""
        stored = await self.storage.all_settings(0)
        for key in self.SPEC:
            if key in stored:
                try:
                    self._cache[key] = self.SPEC[key][0](stored[key])
                    continue
                except ValueError:
                    pass
            value = self._default(key)
            self._cache[key] = value
            await self.storage.set_setting(0, key, self._serialize(value))

    async def ensure_loaded(self, chat_id: int) -> None:
        """Lazily load a chat's overrides into the cache."""
        if not chat_id or chat_id in self._chat_cache:
            return
        self._chat_cache[chat_id] = self._parse_rows(await self.storage.all_settings(chat_id))

    def get(self, key: str, chat_id: int = 0) -> Any:
        if chat_id:
            override = self._chat_cache.get(chat_id)
            if override is not None and key in override:
                return override[key]
        return self._cache[key]

    def all(self, chat_id: int = 0) -> dict[str, object]:
        merged = dict(self._cache)
        if chat_id:
            merged.update(self._chat_cache.get(chat_id, {}))
        return merged

    def describe(self) -> dict[str, str]:
        return {k: desc for k, (_p, desc) in self.SPEC.items()}

    async def set(self, key: str, raw: object, chat_id: int = 0) -> object:
        if key not in self.SPEC:
            raise KeyError(key)
        if chat_id:
            await self.ensure_loaded(chat_id)
        value = self.SPEC[key][0](raw)  # raises ValueError on invalid input
        if chat_id:
            self._chat_cache[chat_id][key] = value
        else:
            self._cache[key] = value
        await self.storage.set_setting(chat_id, key, self._serialize(value))
        return value
