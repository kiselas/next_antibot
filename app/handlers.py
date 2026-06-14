"""Хендлеры Telegram: модерация сообщений, учёт участников, админ-команды."""
from __future__ import annotations

import logging
import time

from telegram import (
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MessageEntity,
    Update,
)
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import ContextTypes

from .config import Config
from .llm import ERROR_LABELS, LLMClient
from .pipeline import Decision, MessageMeta, Result, evaluate
from .runtime_settings import Settings
from .storage import Storage

log = logging.getLogger(__name__)

_MEMBER_STATUSES = {
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
    ChatMemberStatus.RESTRICTED,
}
_ADMIN_CACHE_TTL = 300  # сек
_MUTE_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
)


# --------------------------------------------------------------------------- #
# Доступ к сервисам из bot_data
# --------------------------------------------------------------------------- #
def _config(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.application.bot_data["config"]


def _storage(context: ContextTypes.DEFAULT_TYPE) -> Storage:
    return context.application.bot_data["storage"]


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def _llm(context: ContextTypes.DEFAULT_TYPE) -> LLMClient:
    return context.application.bot_data["llm"]


# --------------------------------------------------------------------------- #
# Вспомогательное
# --------------------------------------------------------------------------- #
def _is_bot_admin(user, config: Config) -> bool:
    if user is None:
        return False
    if user.id in config.admin_user_ids:
        return True
    return bool(user.username and user.username.lower() in config.admin_usernames)


def _chat_allowed(chat_id: int, config: Config) -> bool:
    return not config.allowed_chat_ids or chat_id in config.allowed_chat_ids


async def _chat_admin_ids(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> set[int]:
    """ID администраторов чата с кешем на 5 минут."""
    cache: dict[int, tuple[float, set[int]]] = context.application.bot_data.setdefault(
        "admin_cache", {}
    )
    now = time.monotonic()
    entry = cache.get(chat_id)
    if entry is None or now - entry[0] > _ADMIN_CACHE_TTL:
        try:
            admins = await context.bot.get_chat_administrators(chat_id)
            ids = {a.user.id for a in admins}
            cache[chat_id] = (now, ids)
        except Exception as exc:  # нет прав/сеть — используем прошлый кеш, если есть
            log.debug("Не удалось получить админов чата %s: %s", chat_id, exc)
            ids = entry[1] if entry else set()
    else:
        ids = entry[1]
    return ids


async def _is_group_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None:
        return False
    return user.id in await _chat_admin_ids(context, chat.id)


def _has_entity(msg, types: set[str]) -> bool:
    entities = list(msg.entities or []) + list(msg.caption_entities or [])
    return any(e.type in types for e in entities)


# --------------------------------------------------------------------------- #
# Модерация сообщений
# --------------------------------------------------------------------------- #
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if msg is None or chat is None:
        return
    if chat.type not in (ChatType.GROUP, ChatType.SUPERGROUP):
        return
    if user is None or user.is_bot or msg.sender_chat is not None:
        return  # анонимные админы / посты от имени канала

    config = _config(context)
    if not _chat_allowed(chat.id, config):
        return

    settings = _settings(context)
    if not bool(settings.get("enabled")):
        return

    storage = _storage(context)

    # Уже доверенный? Быстрый выход без записи в БД.
    rec = await storage.get_user(chat.id, user.id)
    if rec is not None and rec["status"] == "trusted":
        return

    # Белый список — всегда пропускаем.
    if await storage.is_whitelisted(user.id, user.username):
        return

    # Админы бота и группы — доверенные.
    if _is_bot_admin(user, config) or await _is_group_admin(update, context):
        await storage.ensure_user(chat.id, user.id, user.username)
        await storage.set_status(chat.id, user.id, "trusted")
        return

    rec = await storage.ensure_user(chat.id, user.id, user.username)

    # Авто-доверие по времени пребывания в группе.
    trust_hours = int(settings.get("trust_after_hours"))
    if trust_hours > 0 and (time.time() - float(rec["first_seen"])) >= trust_hours * 3600:
        await storage.set_status(chat.id, user.id, "trusted")
        return

    text = msg.text or msg.caption or ""
    if not text.strip():
        return  # нечего классифицировать (стикер/медиа без подписи)

    # Защита от злоупотребления: дневной лимит обращений к LLM (анти-флуд/анти-рейд).
    if not _llm_budget_consume(context):
        await storage.incr_stat("llm_budget_skipped")
        await _on_budget_exceeded(context)
        return

    meta = MessageMeta(
        has_links=_has_entity(msg, {MessageEntity.URL, MessageEntity.TEXT_LINK}),
        has_mentions=_has_entity(msg, {MessageEntity.MENTION, MessageEntity.TEXT_MENTION}),
        is_forward=msg.forward_origin is not None,
    )

    result = await evaluate(text, meta, settings=settings, llm=_llm(context), config=config)

    if result.decision in (Decision.CLEAN, Decision.SPAM):
        await storage.incr_stat("messages_checked")
        _reset_llm_streak(context)

    if result.decision is Decision.SPAM:
        await _act_on_spam(update, context, result, text)
    elif result.decision is Decision.CLEAN:
        new_count = await storage.increment_clean(chat.id, user.id)
        threshold = int(settings.get("trust_after_clean_msgs"))
        if threshold > 0 and new_count >= threshold:
            await storage.set_status(chat.id, user.id, "trusted")
    elif result.decision is Decision.ERROR:
        await storage.incr_stat("llm_errors")
        await _on_llm_error(context, result.error)
    else:  # SKIP
        await storage.incr_stat("messages_skipped_prefilter")


async def _act_on_spam(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    result: Result,
    text: str,
) -> None:
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    assert msg and chat and user
    storage = _storage(context)
    mode = str(_settings(context).get("action_mode"))

    try:
        await context.bot.delete_message(chat.id, msg.message_id)
    except Exception as exc:
        log.warning("Не удалось удалить сообщение: %s", exc)

    sanctioned = False
    if mode == "ban":
        try:
            await context.bot.ban_chat_member(chat.id, user.id)
            sanctioned = True
        except Exception as exc:
            log.warning("Не удалось забанить %s: %s", user.id, exc)
    elif mode == "mute":
        try:
            await context.bot.restrict_chat_member(chat.id, user.id, _MUTE_PERMS)
            sanctioned = True
        except Exception as exc:
            log.warning("Не удалось замьютить %s: %s", user.id, exc)
    # mode == "report": санкций нет, только удаление + уведомление

    await storage.record_ban(
        chat.id, user.id, user.username, mode, result.reason, result.confidence,
        result.model, text,
    )
    await storage.incr_stat("spam_detected")
    if sanctioned:
        await storage.incr_stat("users_banned")

    log.info(
        "СПАМ [%s]: user=%s (@%s) chat=%s conf=%.2f model=%s reason=%s",
        mode, user.id, user.username, chat.id, result.confidence, result.model, result.reason,
    )

    config = _config(context)
    if config.admin_chat_id:
        action_ru = {"ban": "забанен", "mute": "замьючен", "report": "помечен"}[mode]
        uname = f"@{user.username}" if user.username else "(без ника)"
        preview = text[:300] + ("…" if len(text) > 300 else "")
        report = (
            f"🚫 Спамер {action_ru}\n"
            f"Пользователь: {uname} (id {user.id})\n"
            f"Чат: {chat.title or chat.id}\n"
            f"Уверенность: {result.confidence:.2f} | модель: {result.model}\n"
            f"Причина: {result.reason}\n"
            f"Сообщение:\n{preview}"
        )
        try:
            await context.bot.send_message(
                config.admin_chat_id,
                report,
                reply_markup=_spam_keyboard(mode, chat.id, user.id),
            )
        except Exception as exc:
            log.warning("Не удалось отправить отчёт в админ-чат: %s", exc)


# --------------------------------------------------------------------------- #
# Уведомления о сбоях LLM
# --------------------------------------------------------------------------- #
def _reset_llm_streak(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.application.bot_data["llm_error_streak"] = 0
    context.application.bot_data["llm_alerted"] = False


async def _on_llm_error(context: ContextTypes.DEFAULT_TYPE, error: str | None) -> None:
    bd = context.application.bot_data
    streak = bd.get("llm_error_streak", 0) + 1
    bd["llm_error_streak"] = streak
    config = _config(context)
    log.warning("LLM недоступна (%s) — сообщение оставлено (fail-safe). Серия: %d",
                error, streak)
    if (
        config.admin_chat_id
        and streak >= config.llm_error_alert_threshold
        and not bd.get("llm_alerted")
    ):
        bd["llm_alerted"] = True
        label = ERROR_LABELS.get(error or "", "ошибка LLM")
        try:
            await context.bot.send_message(
                config.admin_chat_id,
                f"⚠️ LLM-классификатор не отвечает: {label}.\n"
                f"Подряд ошибок: {streak}. Спам сейчас НЕ фильтруется (fail-safe).\n"
                f"Проверьте баланс/ключ OpenRouter или смените модель: /set model <id>",
            )
        except Exception as exc:
            log.warning("Не удалось отправить алерт в админ-чат: %s", exc)


# --------------------------------------------------------------------------- #
# Дневной лимит обращений к LLM (анти-злоупотребление)
# --------------------------------------------------------------------------- #
def _llm_budget_consume(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Возвращает True и расходует единицу дневного лимита; False — лимит исчерпан."""
    limit = int(_settings(context).get("llm_daily_limit"))
    if limit <= 0:
        return True  # 0 = без ограничения
    bd = context.application.bot_data
    today = time.strftime("%Y-%m-%d")
    day, count = bd.get("llm_day", (today, 0))
    if day != today:
        day, count = today, 0
        bd["budget_alerted"] = False
    if count >= limit:
        bd["llm_day"] = (day, count)
        return False
    bd["llm_day"] = (day, count + 1)
    return True


async def _on_budget_exceeded(context: ContextTypes.DEFAULT_TYPE) -> None:
    bd = context.application.bot_data
    config = _config(context)
    log.warning("Дневной лимит LLM исчерпан — модерация приостановлена (fail-safe).")
    if config.admin_chat_id and not bd.get("budget_alerted"):
        bd["budget_alerted"] = True
        try:
            await context.bot.send_message(
                config.admin_chat_id,
                "⚠️ Достигнут дневной лимит обращений к LLM "
                f"(llm_daily_limit={_settings(context).get('llm_daily_limit')}). "
                "Модерация приостановлена до завтра (fail-safe). "
                "Увеличить: /set llm_daily_limit <N>",
            )
        except Exception as exc:
            log.warning("Не удалось отправить алерт о лимите: %s", exc)


# --------------------------------------------------------------------------- #
# Inline-кнопки в отчёте админу
# --------------------------------------------------------------------------- #
def _spam_keyboard(mode: str, chat_id: int, user_id: int) -> InlineKeyboardMarkup:
    def cd(action: str) -> str:
        return f"{action}:{chat_id}:{user_id}"

    if mode == "report":
        row = [
            InlineKeyboardButton("🔨 Забанить", callback_data=cd("ban")),
            InlineKeyboardButton("✅ Игнорировать", callback_data=cd("ok")),
        ]
    else:
        unban_label = "♻️ Размьютить" if mode == "mute" else "♻️ Разбанить"
        row = [
            InlineKeyboardButton(unban_label, callback_data=cd("unban")),
            InlineKeyboardButton("✅ Ок", callback_data=cd("ok")),
        ]
    return InlineKeyboardMarkup([row])


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer()
        return
    action, chat_s, user_s = parts
    try:
        chat_id, user_id = int(chat_s), int(user_s)
    except ValueError:
        await query.answer("Некорректные данные", show_alert=True)
        return

    presser = query.from_user
    if not (
        _is_bot_admin(presser, _config(context))
        or presser.id in await _chat_admin_ids(context, chat_id)
    ):
        await query.answer("Только администратор может это сделать.", show_alert=True)
        return

    storage = _storage(context)
    who = f"@{presser.username}" if presser.username else f"id{presser.id}"

    if action == "unban":
        try:
            await context.bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
            await context.bot.restrict_chat_member(
                chat_id, user_id, ChatPermissions.all_permissions()
            )
        except Exception as exc:
            await query.answer(f"Ошибка: {exc}", show_alert=True)
            return
        await storage.set_status(chat_id, user_id, "trusted")
        note = f"♻️ Отменено, пользователь восстановлен ({who})"
    elif action == "ban":
        try:
            await context.bot.ban_chat_member(chat_id, user_id)
        except Exception as exc:
            await query.answer(f"Ошибка: {exc}", show_alert=True)
            return
        await storage.set_status(chat_id, user_id, "untrusted")
        await storage.incr_stat("users_banned")
        note = f"🔨 Забанен вручную ({who})"
    elif action == "ok":
        note = f"✅ Подтверждено ({who})"
    else:
        await query.answer()
        return

    await query.answer("Готово")
    try:
        await query.edit_message_text((query.message.text or "") + "\n\n" + note)
    except Exception as exc:  # сообщение слишком старое/уже изменено
        log.debug("Не удалось обновить отчёт: %s", exc)


# --------------------------------------------------------------------------- #
# Учёт участников / присутствие бота
# --------------------------------------------------------------------------- #
async def handle_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmu = update.chat_member
    chat = update.effective_chat
    if cmu is None or chat is None:
        return
    if not _chat_allowed(chat.id, _config(context)):
        return
    member = cmu.new_chat_member.user
    if member.is_bot:
        return
    was_in = cmu.old_chat_member.status in _MEMBER_STATUSES
    now_in = cmu.new_chat_member.status in _MEMBER_STATUSES
    if now_in and not was_in:
        await _storage(context).ensure_user(chat.id, member.id, member.username)
        await _storage(context).incr_stat("members_joined")
        log.info("Новый участник: %s (@%s) в чате %s", member.id, member.username, chat.id)


async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Бота добавили/удалили из чата. Покидаем неразрешённые чаты."""
    cmu = update.my_chat_member
    chat = update.effective_chat
    if cmu is None or chat is None:
        return
    new_status = cmu.new_chat_member.status
    config = _config(context)
    if new_status in _MEMBER_STATUSES and not _chat_allowed(chat.id, config):
        log.warning("Добавлен в неразрешённый чат %s (%s) — выхожу.", chat.id, chat.title)
        try:
            await context.bot.leave_chat(chat.id)
        except Exception as exc:
            log.warning("Не удалось покинуть чат %s: %s", chat.id, exc)
    elif new_status in _MEMBER_STATUSES:
        log.info("Бот добавлен в чат %s (%s)", chat.id, chat.title)


# --------------------------------------------------------------------------- #
# Админ-команды (только в личке бота)
# --------------------------------------------------------------------------- #
async def _require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not _is_bot_admin(update.effective_user, _config(context)):
        if update.effective_message:
            await update.effective_message.reply_text(
                "⛔ Команда доступна только администратору бота."
            )
        return False
    return True


def _help_text() -> str:
    return (
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
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg is None:
        return
    if not _is_bot_admin(update.effective_user, _config(context)):
        await msg.reply_text("Привет! Я антиспам-бот. Управление доступно администратору.")
        return
    await msg.reply_text(_help_text())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    await update.effective_message.reply_text(_help_text())


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    storage = _storage(context)
    stats = await storage.all_stats()
    users = await storage.user_counts()
    bans_24h = await storage.bans_since(time.time() - 86400)

    started_at = context.application.bot_data.get("started_at", time.time())
    uptime_h = (time.time() - started_at) / 3600

    text = (
        "📊 Статистика антиспам-бота\n\n"
        f"Аптайм: {uptime_h:.1f} ч\n"
        f"Проверено сообщений (LLM): {stats.get('messages_checked', 0)}\n"
        f"Пропущено пре-фильтром: {stats.get('messages_skipped_prefilter', 0)}\n"
        f"Обнаружено спама: {stats.get('spam_detected', 0)}\n"
        f"Применено санкций (бан/мьют): {stats.get('users_banned', 0)}\n"
        f"Действий за 24 ч: {bans_24h}\n"
        f"Ошибок LLM (fail-safe): {stats.get('llm_errors', 0)}\n"
        f"Пропущено по дневному лимиту: {stats.get('llm_budget_skipped', 0)}\n"
        f"Новых участников: {stats.get('members_joined', 0)}\n\n"
        f"Пользователи: доверенных {users.get('trusted', 0)} / "
        f"на проверке {users.get('untrusted', 0)} / всего {users.get('total', 0)}"
    )
    await update.effective_message.reply_text(text)


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    limit = 10
    if context.args:
        try:
            limit = max(1, min(50, int(context.args[0])))
        except ValueError:
            pass
    rows = await _storage(context).recent_bans(limit)
    if not rows:
        await update.effective_message.reply_text("Журнал пуст.")
        return
    lines = [f"🗒 Последние действия ({len(rows)}):\n"]
    for r in rows:
        when = time.strftime("%d.%m %H:%M", time.localtime(r["ts"]))
        uname = f"@{r['username']}" if r["username"] else f"id{r['user_id']}"
        text = (r["message_text"] or "").replace("\n", " ")[:120]
        lines.append(
            f"{when} [{r['action']}] {uname} (id {r['user_id']}) "
            f"conf={r['confidence']:.2f}\n   причина: {r['reason']}\n   текст: {text}"
        )
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    msg = update.effective_message
    text = " ".join(context.args or []).strip()
    if not text:
        await msg.reply_text("Использование: /test <текст сообщения>")
        return
    meta = MessageMeta(
        has_links=("http://" in text or "https://" in text),
        has_mentions=("@" in text),
        is_forward=False,
    )
    result = await evaluate(
        text, meta, settings=_settings(context), llm=_llm(context), config=_config(context)
    )
    body = (
        f"Решение: {result.decision.value}\n"
        f"Уверенность: {result.confidence:.2f}\n"
        f"Причина: {result.reason or '—'}\n"
        f"Модель: {result.model or '—'}"
    )
    if result.error:
        body += f"\nОшибка: {ERROR_LABELS.get(result.error, result.error)}"
    await msg.reply_text(body)


async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    settings = _settings(context)
    values = settings.all()
    descs = settings.describe()
    lines = ["⚙️ Параметры (изменить: /set <ключ> <значение>):\n"]
    for key, desc in descs.items():
        shown = values[key] if values[key] != "" else "—"
        lines.append(f"• {key} = {shown}\n   {desc}")
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    msg = update.effective_message
    args = context.args or []
    if len(args) < 2:
        await msg.reply_text("Использование: /set <ключ> <значение>\nСписок ключей: /config")
        return
    key = args[0].lower()
    raw = " ".join(args[1:])
    settings = _settings(context)
    try:
        value = await settings.set(key, raw)
    except KeyError:
        known = ", ".join(settings.SPEC.keys())
        await msg.reply_text(f"Неизвестный ключ «{key}».\nДоступные: {known}")
        return
    except ValueError as exc:
        await msg.reply_text(f"Неверное значение: {exc}")
        return
    await msg.reply_text(f"✅ {key} = {value}")


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    msg = update.effective_message
    args = context.args or []
    if not args:
        await msg.reply_text("Использование: /unban <user_id> [chat_id]")
        return
    try:
        user_id = int(args[0])
    except ValueError:
        await msg.reply_text("user_id должен быть числом.")
        return

    storage = _storage(context)
    config = _config(context)
    if len(args) >= 2:
        try:
            chat_id = int(args[1])
        except ValueError:
            await msg.reply_text("chat_id должен быть числом.")
            return
    else:
        chat_id = await storage.last_ban_chat(user_id)
        if chat_id is None and len(config.allowed_chat_ids) == 1:
            chat_id = config.allowed_chat_ids[0]
        if chat_id is None:
            await msg.reply_text(
                "Не знаю, в каком чате снять бан. Укажите: /unban <user_id> <chat_id>"
            )
            return

    try:
        await context.bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
        # снимаем возможный мьют
        await context.bot.restrict_chat_member(
            chat_id, user_id, ChatPermissions.all_permissions()
        )
    except Exception as exc:
        await msg.reply_text(f"Не удалось снять бан: {exc}")
        return
    await storage.set_status(chat_id, user_id, "trusted")
    await msg.reply_text(f"✅ Пользователь {user_id} разбанен в чате {chat_id} и помечен доверенным.")


async def cmd_allow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    msg = update.effective_message
    args = context.args or []
    if not args:
        await msg.reply_text("Использование: /allow <@username|user_id>")
        return
    target = args[0]
    user_id, username = _parse_user_ref(target)
    added = await _storage(context).add_whitelist(user_id, username)
    if added:
        await msg.reply_text(f"✅ Добавлен в белый список: {target}")
    else:
        await msg.reply_text(f"{target} уже в белом списке.")


async def cmd_unallow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    msg = update.effective_message
    args = context.args or []
    if not args:
        await msg.reply_text("Использование: /unallow <@username|user_id>")
        return
    user_id, username = _parse_user_ref(args[0])
    removed = await _storage(context).remove_whitelist(user_id, username)
    await msg.reply_text(
        f"Удалено записей: {removed}" if removed else "В белом списке не найдено."
    )


async def cmd_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    rows = await _storage(context).list_whitelist()
    if not rows:
        await update.effective_message.reply_text("Белый список пуст.")
        return
    lines = ["✅ Белый список:"]
    for r in rows:
        ref = f"@{r['username']}" if r["username"] else f"id{r['user_id']}"
        lines.append(f"• {ref}")
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_resetstats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    await _storage(context).clear_stats()
    context.application.bot_data["started_at"] = time.time()
    await update.effective_message.reply_text("✅ Статистика обнулена.")


def _parse_user_ref(ref: str) -> tuple[int | None, str | None]:
    ref = ref.strip()
    if ref.startswith("@"):
        return None, ref[1:]
    try:
        return int(ref), None
    except ValueError:
        return None, ref  # трактуем как username без @
