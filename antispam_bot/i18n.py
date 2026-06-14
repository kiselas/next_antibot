"""Minimal i18n: message catalogs and a ``t()`` lookup helper.

English is the default/fallback language. User-facing strings go through ``t()``;
developer-facing log messages stay in English in the code.
"""

from __future__ import annotations

DEFAULT_LANG = "en"
SUPPORTED_LANGS = ("en", "ru")

_CATALOG: dict[str, dict[str, str]] = {
    "en": {
        "admin_only": "⛔ This command is for the bot administrator only.",
        "start_user": "Hi! I'm an antispam bot. Management is available to the administrator.",
        "help": (
            "🤖 Antispam bot — administrator commands:\n\n"
            "/stats — moderation statistics\n"
            "/recent [N] — last N actions (default 10)\n"
            "/test <text> — run the classifier with no side effects\n"
            "/config — current parameters\n"
            "/set <key> <value> — change a parameter\n"
            "/unban <user_id> [chat_id] — lift a ban\n"
            "/allow <@user|user_id> — add to the whitelist\n"
            "/unallow <@user|user_id> — remove from the whitelist\n"
            "/whitelist — show the whitelist\n"
            "/resetstats — reset statistics\n"
            "/help — this help\n\n"
            "Example: /set spam_confidence_threshold 0.9"
        ),
        "stats": (
            "📊 Antispam bot statistics\n\n"
            "Uptime: {uptime:.1f} h\n"
            "Messages checked (LLM): {checked}\n"
            "Skipped by pre-filter: {skipped}\n"
            "Spam detected: {spam}\n"
            "Sanctions applied (ban/mute): {banned}\n"
            "Actions in 24h: {actions_24h}\n"
            "LLM errors (fail-safe): {llm_errors}\n"
            "Skipped by daily limit: {budget_skipped}\n"
            "New members: {joined}\n\n"
            "Users: trusted {trusted} / pending {pending} / total {total}"
        ),
        "recent_empty": "The log is empty.",
        "recent_header": "🗒 Recent actions ({count}):\n",
        "recent_item": (
            "{when} [{action}] {uname} (id {user_id}) conf={conf:.2f}\n"
            "   reason: {reason}\n   text: {text}"
        ),
        "test_usage": "Usage: /test <message text>",
        "test_result": (
            "Decision: {decision}\nConfidence: {conf:.2f}\nReason: {reason}\nModel: {model}"
        ),
        "test_error_suffix": "\nError: {error}",
        "config_header": "⚙️ Parameters (change: /set <key> <value>):\n",
        "config_item": "• {key} = {value}\n   {desc}",
        "set_usage": "Usage: /set <key> <value>\nKeys: /config",
        "set_unknown": "Unknown key «{key}».\nAvailable: {known}",
        "set_invalid": "Invalid value: {error}",
        "set_ok": "✅ {key} = {value}",
        "unban_usage": "Usage: /unban <user_id> [chat_id]",
        "unban_user_id_num": "user_id must be a number.",
        "unban_chat_id_num": "chat_id must be a number.",
        "unban_no_chat": "I don't know which chat to unban in. Use: /unban <user_id> <chat_id>",
        "unban_fail": "Failed to lift the ban: {error}",
        "unban_ok": "✅ User {user_id} unbanned in chat {chat_id} and marked trusted.",
        "allow_usage": "Usage: /allow <@username|user_id>",
        "allow_added": "✅ Added to the whitelist: {target}",
        "allow_exists": "{target} is already in the whitelist.",
        "unallow_usage": "Usage: /unallow <@username|user_id>",
        "unallow_removed": "Removed entries: {count}",
        "unallow_none": "Not found in the whitelist.",
        "whitelist_empty": "The whitelist is empty.",
        "whitelist_header": "✅ Whitelist:",
        "whitelist_item": "• {ref}",
        "resetstats_ok": "✅ Statistics reset.",
        "report_action_ban": "banned",
        "report_action_mute": "muted",
        "report_action_report": "flagged",
        "report": (
            "🚫 Spammer {action}\n"
            "User: {uname} (id {user_id})\n"
            "Chat: {chat}\n"
            "Confidence: {conf:.2f} | model: {model}\n"
            "Reason: {reason}\n"
            "Message:\n{preview}"
        ),
        "budget_alert": (
            "⚠️ Daily LLM limit reached (llm_daily_limit={limit}). "
            "Moderation is paused until tomorrow (fail-safe). "
            "Increase it: /set llm_daily_limit <N>"
        ),
        "llm_alert": (
            "⚠️ LLM classifier is not responding: {label}.\n"
            "Consecutive errors: {streak}. Spam is NOT being filtered right now (fail-safe).\n"
            "Check your provider balance/key or switch the model: /set model <id>"
        ),
        "err_out_of_credits": "out of provider credits (402)",
        "err_rate_limited": "rate limit exceeded (429)",
        "err_unavailable": "provider unavailable",
        "err_bad_response": "model returns an invalid response",
        "cb_only_admin": "Only an administrator can do this.",
        "cb_bad_data": "Invalid data",
        "cb_done": "Done",
        "cb_note_unbanned": "♻️ Reverted, user restored ({who})",
        "cb_note_banned": "🔨 Banned manually ({who})",
        "cb_note_confirmed": "✅ Confirmed ({who})",
        "cb_error": "Error: {error}",
        "btn_unban": "♻️ Unban",
        "btn_unmute": "♻️ Unmute",
        "btn_ok": "✅ OK",
        "btn_ban": "🔨 Ban",
        "btn_ignore": "✅ Ignore",
    },
    "ru": {
        "admin_only": "⛔ Команда доступна только администратору бота.",
        "start_user": "Привет! Я антиспам-бот. Управление доступно администратору.",
        "help": (
            "🤖 Антиспам-бот — команды администратора:\n\n"
            "/stats — статистика модерации\n"
            "/recent [N] — последние N действий (по умолчанию 10)\n"
            "/test <текст> — прогнать классификатор без последствий\n"
            "/config — текущие параметры\n"
            "/set <ключ> <значение> — изменить параметр\n"
            "/unban <user_id> [chat_id] — снять бан\n"
            "/allow <@user|user_id> — добавить в белый список\n"
            "/unallow <@user|user_id> — убрать из белого списка\n"
            "/whitelist — показать белый список\n"
            "/resetstats — обнулить статистику\n"
            "/help — эта справка\n\n"
            "Пример: /set spam_confidence_threshold 0.9"
        ),
        "stats": (
            "📊 Статистика антиспам-бота\n\n"
            "Аптайм: {uptime:.1f} ч\n"
            "Проверено сообщений (LLM): {checked}\n"
            "Пропущено пре-фильтром: {skipped}\n"
            "Обнаружено спама: {spam}\n"
            "Применено санкций (бан/мьют): {banned}\n"
            "Действий за 24 ч: {actions_24h}\n"
            "Ошибок LLM (fail-safe): {llm_errors}\n"
            "Пропущено по дневному лимиту: {budget_skipped}\n"
            "Новых участников: {joined}\n\n"
            "Пользователи: доверенных {trusted} / на проверке {pending} / всего {total}"
        ),
        "recent_empty": "Журнал пуст.",
        "recent_header": "🗒 Последние действия ({count}):\n",
        "recent_item": (
            "{when} [{action}] {uname} (id {user_id}) conf={conf:.2f}\n"
            "   причина: {reason}\n   текст: {text}"
        ),
        "test_usage": "Использование: /test <текст сообщения>",
        "test_result": (
            "Решение: {decision}\nУверенность: {conf:.2f}\nПричина: {reason}\nМодель: {model}"
        ),
        "test_error_suffix": "\nОшибка: {error}",
        "config_header": "⚙️ Параметры (изменить: /set <ключ> <значение>):\n",
        "config_item": "• {key} = {value}\n   {desc}",
        "set_usage": "Использование: /set <ключ> <значение>\nСписок ключей: /config",
        "set_unknown": "Неизвестный ключ «{key}».\nДоступные: {known}",
        "set_invalid": "Неверное значение: {error}",
        "set_ok": "✅ {key} = {value}",
        "unban_usage": "Использование: /unban <user_id> [chat_id]",
        "unban_user_id_num": "user_id должен быть числом.",
        "unban_chat_id_num": "chat_id должен быть числом.",
        "unban_no_chat": "Не знаю, в каком чате снять бан. Укажите: /unban <user_id> <chat_id>",
        "unban_fail": "Не удалось снять бан: {error}",
        "unban_ok": "✅ Пользователь {user_id} разбанен в чате {chat_id} и помечен доверенным.",
        "allow_usage": "Использование: /allow <@username|user_id>",
        "allow_added": "✅ Добавлен в белый список: {target}",
        "allow_exists": "{target} уже в белом списке.",
        "unallow_usage": "Использование: /unallow <@username|user_id>",
        "unallow_removed": "Удалено записей: {count}",
        "unallow_none": "В белом списке не найдено.",
        "whitelist_empty": "Белый список пуст.",
        "whitelist_header": "✅ Белый список:",
        "whitelist_item": "• {ref}",
        "resetstats_ok": "✅ Статистика обнулена.",
        "report_action_ban": "забанен",
        "report_action_mute": "замьючен",
        "report_action_report": "помечен",
        "report": (
            "🚫 Спамер {action}\n"
            "Пользователь: {uname} (id {user_id})\n"
            "Чат: {chat}\n"
            "Уверенность: {conf:.2f} | модель: {model}\n"
            "Причина: {reason}\n"
            "Сообщение:\n{preview}"
        ),
        "budget_alert": (
            "⚠️ Достигнут дневной лимит обращений к LLM (llm_daily_limit={limit}). "
            "Модерация приостановлена до завтра (fail-safe). "
            "Увеличить: /set llm_daily_limit <N>"
        ),
        "llm_alert": (
            "⚠️ LLM-классификатор не отвечает: {label}.\n"
            "Подряд ошибок: {streak}. Спам сейчас НЕ фильтруется (fail-safe).\n"
            "Проверьте баланс/ключ провайдера или смените модель: /set model <id>"
        ),
        "err_out_of_credits": "закончились кредиты провайдера (402)",
        "err_rate_limited": "превышен лимит запросов (429)",
        "err_unavailable": "провайдер недоступен",
        "err_bad_response": "модель возвращает некорректный ответ",
        "cb_only_admin": "Только администратор может это сделать.",
        "cb_bad_data": "Некорректные данные",
        "cb_done": "Готово",
        "cb_note_unbanned": "♻️ Отменено, пользователь восстановлен ({who})",
        "cb_note_banned": "🔨 Забанен вручную ({who})",
        "cb_note_confirmed": "✅ Подтверждено ({who})",
        "cb_error": "Ошибка: {error}",
        "btn_unban": "♻️ Разбанить",
        "btn_unmute": "♻️ Размьютить",
        "btn_ok": "✅ Ок",
        "btn_ban": "🔨 Забанить",
        "btn_ignore": "✅ Игнорировать",
    },
}


def normalize_lang(lang: str | None) -> str:
    return lang if lang in _CATALOG else DEFAULT_LANG


def t(key: str, lang: str | None = DEFAULT_LANG, /, **kwargs: object) -> str:
    """Look up a localized string and format it with ``kwargs``."""
    lang = normalize_lang(lang)
    template = _CATALOG[lang].get(key) or _CATALOG[DEFAULT_LANG].get(key, key)
    return template.format(**kwargs) if kwargs else template
