"""Динамические настройки, изменяемые на лету командой /set.

Дефолты берутся из Config (.env), значения хранятся в таблице settings
и кешируются в памяти. Каждая настройка имеет свой парсер/валидатор.
"""
from __future__ import annotations

from collections.abc import Callable

from .config import Config
from .storage import Storage


def _to_bool(v: object) -> bool:
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "on", "да", "вкл"):
        return True
    if s in ("0", "false", "no", "off", "нет", "выкл"):
        return False
    raise ValueError("ожидается true/false")


def _to_unit_float(v: object) -> float:
    f = float(str(v).replace(",", "."))
    if not 0.0 <= f <= 1.0:
        raise ValueError("ожидается число от 0.0 до 1.0")
    return f


def _to_nonneg_int(v: object) -> int:
    i = int(str(v).strip())
    if i < 0:
        raise ValueError("ожидается целое >= 0")
    return i


def _to_pos_int(v: object) -> int:
    i = int(str(v).strip())
    if i <= 0:
        raise ValueError("ожидается целое > 0")
    return i


def _to_str(v: object) -> str:
    return str(v).strip()


def _to_action(v: object) -> str:
    s = str(v).strip().lower()
    if s in ("ban", "mute", "report"):
        return s
    raise ValueError("ожидается ban / mute / report")


def _to_model(v: object) -> str:
    s = str(v).strip()
    if not s:
        raise ValueError("имя модели не может быть пустым")
    return s


class Settings:
    # key -> (парсер, описание)
    SPEC: dict[str, tuple[Callable[[object], object], str]] = {
        "enabled": (_to_bool, "Вкл/выкл модерацию (true/false)"),
        "action_mode": (_to_action, "Реакция на спам: ban / mute / report"),
        "spam_confidence_threshold": (_to_unit_float, "Порог уверенности для бана (0.0..1.0)"),
        "trust_after_clean_msgs": (_to_nonneg_int, "Чистых сообщений до 'доверенного' (0=выкл)"),
        "trust_after_hours": (_to_nonneg_int, "Часов в группе до 'доверенного' (0=выкл)"),
        "min_chars_for_llm": (_to_pos_int, "Минимум символов для вызова LLM"),
        "max_chars_to_llm": (_to_pos_int, "Максимум символов сообщения для LLM"),
        "llm_daily_limit": (_to_nonneg_int, "Дневной лимит обращений к LLM, анти-флуд (0=без лимита)"),
        "group_topic": (_to_str, "Тематика группы для контекста LLM (пусто = не задана)"),
        "allowed_domains": (_to_str, "Домены, ссылки на которые допустимы (через запятую)"),
        "model": (_to_model, "Основная модель OpenRouter"),
        "use_json_format": (_to_bool, "Запрашивать у модели строгий JSON (true/false)"),
    }

    def __init__(self, storage: Storage, config: Config) -> None:
        self.storage = storage
        self.config = config
        self._cache: dict[str, object] = {}

    def _default(self, key: str) -> object:
        return {
            "enabled": True,
            "action_mode": "ban",
            "spam_confidence_threshold": self.config.spam_confidence_threshold,
            "trust_after_clean_msgs": self.config.trust_after_clean_msgs,
            "trust_after_hours": self.config.trust_after_hours,
            "min_chars_for_llm": self.config.min_chars_for_llm,
            "max_chars_to_llm": self.config.max_chars_to_llm,
            "llm_daily_limit": 0,
            "group_topic": self.config.group_topic,
            "allowed_domains": "",
            "model": self.config.openrouter_model,
            "use_json_format": True,
        }[key]

    @staticmethod
    def _serialize(v: object) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    async def load(self) -> None:
        stored = await self.storage.all_settings()
        for key, (parse, _desc) in self.SPEC.items():
            if key in stored:
                try:
                    self._cache[key] = parse(stored[key])
                    continue
                except ValueError:
                    pass  # повреждённое значение — откатываемся к дефолту
            value = self._default(key)
            self._cache[key] = value
            await self.storage.set_setting(key, self._serialize(value))

    def get(self, key: str) -> object:
        return self._cache[key]

    def all(self) -> dict[str, object]:
        return dict(self._cache)

    def describe(self) -> dict[str, str]:
        return {k: desc for k, (_p, desc) in self.SPEC.items()}

    async def set(self, key: str, raw: object) -> object:
        if key not in self.SPEC:
            raise KeyError(key)
        parse, _desc = self.SPEC[key]
        value = parse(raw)  # бросит ValueError при неверном значении
        self._cache[key] = value
        await self.storage.set_setting(key, self._serialize(value))
        return value
