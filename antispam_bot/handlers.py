"""Telegram handlers: message moderation, membership tracking, admin commands."""

from __future__ import annotations

import contextlib
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

from .classifiers import Classifier
from .config import Config
from .i18n import t
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
_ADMIN_CACHE_TTL = 300  # seconds
_MUTE_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
)


# --------------------------------------------------------------------------- #
# Service accessors (bot_data)
# --------------------------------------------------------------------------- #
def _config(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.application.bot_data["config"]


def _storage(context: ContextTypes.DEFAULT_TYPE) -> Storage:
    return context.application.bot_data["storage"]


def _settings(context: ContextTypes.DEFAULT_TYPE) -> Settings:
    return context.application.bot_data["settings"]


def _classifier(context: ContextTypes.DEFAULT_TYPE) -> Classifier:
    return context.application.bot_data["classifier"]


def _lang(context: ContextTypes.DEFAULT_TYPE) -> str:
    return str(_settings(context).get("language"))


# --------------------------------------------------------------------------- #
# Helpers
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
    """Administrator IDs of a chat, cached for 5 minutes."""
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
        except Exception as exc:  # missing rights / network — reuse previous cache
            log.debug("Could not fetch admins of chat %s: %s", chat_id, exc)
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
# Message moderation
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
        return  # anonymous admins / channel posts

    config = _config(context)
    if not _chat_allowed(chat.id, config):
        return

    settings = _settings(context)
    if not bool(settings.get("enabled")):
        return

    storage = _storage(context)

    # Already trusted? Fast exit, no DB write.
    rec = await storage.get_user(chat.id, user.id)
    if rec is not None and rec["status"] == "trusted":
        return

    # Whitelisted — always skip.
    if await storage.is_whitelisted(user.id, user.username):
        return

    # Bot/group admins are trusted.
    if _is_bot_admin(user, config) or await _is_group_admin(update, context):
        await storage.ensure_user(chat.id, user.id, user.username)
        await storage.set_status(chat.id, user.id, "trusted")
        return

    rec = await storage.ensure_user(chat.id, user.id, user.username)

    # Auto-trust by time spent in the group.
    trust_hours = int(settings.get("trust_after_hours"))
    if trust_hours > 0 and (time.time() - float(rec["first_seen"])) >= trust_hours * 3600:
        await storage.set_status(chat.id, user.id, "trusted")
        return

    text = msg.text or msg.caption or ""
    if not text.strip():
        return  # nothing to classify (sticker/media without caption)

    # Abuse protection: daily classifier-call budget (anti-flood/anti-raid).
    if not _llm_budget_consume(context):
        await storage.incr_stat("llm_budget_skipped")
        await _on_budget_exceeded(context)
        return

    meta = MessageMeta(
        has_links=_has_entity(msg, {MessageEntity.URL, MessageEntity.TEXT_LINK}),
        has_mentions=_has_entity(msg, {MessageEntity.MENTION, MessageEntity.TEXT_MENTION}),
        is_forward=msg.forward_origin is not None,
    )

    result = await evaluate(text, meta, settings=settings, classifier=_classifier(context))

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
        log.warning("Could not delete message: %s", exc)

    sanctioned = False
    if mode == "ban":
        try:
            await context.bot.ban_chat_member(chat.id, user.id)
            sanctioned = True
        except Exception as exc:
            log.warning("Could not ban %s: %s", user.id, exc)
    elif mode == "mute":
        try:
            await context.bot.restrict_chat_member(chat.id, user.id, _MUTE_PERMS)
            sanctioned = True
        except Exception as exc:
            log.warning("Could not mute %s: %s", user.id, exc)
    # mode == "report": no sanction, only deletion + notification

    await storage.record_ban(
        chat.id,
        user.id,
        user.username,
        mode,
        result.reason,
        result.confidence,
        result.model,
        text,
    )
    await storage.incr_stat("spam_detected")
    if sanctioned:
        await storage.incr_stat("users_banned")

    log.info(
        "SPAM [%s]: user=%s (@%s) chat=%s conf=%.2f model=%s reason=%s",
        mode,
        user.id,
        user.username,
        chat.id,
        result.confidence,
        result.model,
        result.reason,
    )

    config = _config(context)
    if config.admin_chat_id:
        lang = _lang(context)
        uname = f"@{user.username}" if user.username else f"id{user.id}"
        preview = text[:300] + ("…" if len(text) > 300 else "")
        report = t(
            "report",
            lang,
            action=t(f"report_action_{mode}", lang),
            uname=uname,
            user_id=user.id,
            chat=chat.title or chat.id,
            conf=result.confidence,
            model=result.model,
            reason=result.reason,
            preview=preview,
        )
        try:
            await context.bot.send_message(
                config.admin_chat_id,
                report,
                reply_markup=_spam_keyboard(mode, chat.id, user.id, lang),
            )
        except Exception as exc:
            log.warning("Could not send report to admin chat: %s", exc)


# --------------------------------------------------------------------------- #
# Daily classifier budget (abuse protection)
# --------------------------------------------------------------------------- #
def _llm_budget_consume(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True and consume one unit of the daily budget; False when exhausted."""
    limit = int(_settings(context).get("llm_daily_limit"))
    if limit <= 0:
        return True  # 0 = unlimited
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
    log.warning("Daily classifier limit reached — moderation paused (fail-safe).")
    if config.admin_chat_id and not bd.get("budget_alerted"):
        bd["budget_alerted"] = True
        try:
            await context.bot.send_message(
                config.admin_chat_id,
                t("budget_alert", _lang(context), limit=_settings(context).get("llm_daily_limit")),
            )
        except Exception as exc:
            log.warning("Could not send budget alert: %s", exc)


# --------------------------------------------------------------------------- #
# Classifier failure alerting
# --------------------------------------------------------------------------- #
def _reset_llm_streak(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.application.bot_data["llm_error_streak"] = 0
    context.application.bot_data["llm_alerted"] = False


async def _on_llm_error(context: ContextTypes.DEFAULT_TYPE, error: str | None) -> None:
    bd = context.application.bot_data
    streak = bd.get("llm_error_streak", 0) + 1
    bd["llm_error_streak"] = streak
    config = _config(context)
    log.warning("Classifier unavailable (%s) — message left in place. Streak: %d", error, streak)
    if (
        config.admin_chat_id
        and streak >= config.llm_error_alert_threshold
        and not bd.get("llm_alerted")
    ):
        bd["llm_alerted"] = True
        lang = _lang(context)
        label = t(f"err_{error}", lang) if error else t("err_unavailable", lang)
        try:
            await context.bot.send_message(
                config.admin_chat_id, t("llm_alert", lang, label=label, streak=streak)
            )
        except Exception as exc:
            log.warning("Could not send LLM alert: %s", exc)


# --------------------------------------------------------------------------- #
# Inline buttons in admin reports
# --------------------------------------------------------------------------- #
def _spam_keyboard(mode: str, chat_id: int, user_id: int, lang: str) -> InlineKeyboardMarkup:
    def cd(action: str) -> str:
        return f"{action}:{chat_id}:{user_id}"

    if mode == "report":
        row = [
            InlineKeyboardButton(t("btn_ban", lang), callback_data=cd("ban")),
            InlineKeyboardButton(t("btn_ignore", lang), callback_data=cd("ok")),
        ]
    else:
        unban_label = t("btn_unmute", lang) if mode == "mute" else t("btn_unban", lang)
        row = [
            InlineKeyboardButton(unban_label, callback_data=cd("unban")),
            InlineKeyboardButton(t("btn_ok", lang), callback_data=cd("ok")),
        ]
    return InlineKeyboardMarkup([row])


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data:
        return
    lang = _lang(context)
    parts = query.data.split(":")
    if len(parts) != 3:
        await query.answer()
        return
    action, chat_s, user_s = parts
    try:
        chat_id, user_id = int(chat_s), int(user_s)
    except ValueError:
        await query.answer(t("cb_bad_data", lang), show_alert=True)
        return

    presser = query.from_user
    if not (
        _is_bot_admin(presser, _config(context))
        or presser.id in await _chat_admin_ids(context, chat_id)
    ):
        await query.answer(t("cb_only_admin", lang), show_alert=True)
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
            await query.answer(t("cb_error", lang, error=exc), show_alert=True)
            return
        await storage.set_status(chat_id, user_id, "trusted")
        note = t("cb_note_unbanned", lang, who=who)
    elif action == "ban":
        try:
            await context.bot.ban_chat_member(chat_id, user_id)
        except Exception as exc:
            await query.answer(t("cb_error", lang, error=exc), show_alert=True)
            return
        await storage.set_status(chat_id, user_id, "untrusted")
        await storage.incr_stat("users_banned")
        note = t("cb_note_banned", lang, who=who)
    elif action == "ok":
        note = t("cb_note_confirmed", lang, who=who)
    else:
        await query.answer()
        return

    await query.answer(t("cb_done", lang))
    try:
        base = getattr(query.message, "text", None) or ""
        await query.edit_message_text(base + "\n\n" + note)
    except Exception as exc:  # message too old / already edited
        log.debug("Could not update report: %s", exc)


# --------------------------------------------------------------------------- #
# Membership / bot presence
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
        log.info("New member: %s (@%s) in chat %s", member.id, member.username, chat.id)


async def handle_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bot was added to / removed from a chat. Leave non-allowed chats."""
    cmu = update.my_chat_member
    chat = update.effective_chat
    if cmu is None or chat is None:
        return
    new_status = cmu.new_chat_member.status
    config = _config(context)
    if new_status in _MEMBER_STATUSES and not _chat_allowed(chat.id, config):
        log.warning("Added to a non-allowed chat %s (%s) — leaving.", chat.id, chat.title)
        try:
            await context.bot.leave_chat(chat.id)
        except Exception as exc:
            log.warning("Could not leave chat %s: %s", chat.id, exc)
    elif new_status in _MEMBER_STATUSES:
        log.info("Bot added to chat %s (%s)", chat.id, chat.title)


# --------------------------------------------------------------------------- #
# Admin commands (DM only)
# --------------------------------------------------------------------------- #
async def _require_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not _is_bot_admin(update.effective_user, _config(context)):
        if update.effective_message:
            await update.effective_message.reply_text(t("admin_only", _lang(context)))
        return False
    return True


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg is None:
        return
    lang = _lang(context)
    if not _is_bot_admin(update.effective_user, _config(context)):
        await msg.reply_text(t("start_user", lang))
        return
    await msg.reply_text(t("help", lang))


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    await update.effective_message.reply_text(t("help", _lang(context)))


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    storage = _storage(context)
    stats = await storage.all_stats()
    users = await storage.user_counts()
    bans_24h = await storage.bans_since(time.time() - 86400)
    started_at = context.application.bot_data.get("started_at", time.time())

    text = t(
        "stats",
        _lang(context),
        uptime=(time.time() - started_at) / 3600,
        checked=stats.get("messages_checked", 0),
        skipped=stats.get("messages_skipped_prefilter", 0),
        spam=stats.get("spam_detected", 0),
        banned=stats.get("users_banned", 0),
        actions_24h=bans_24h,
        llm_errors=stats.get("llm_errors", 0),
        budget_skipped=stats.get("llm_budget_skipped", 0),
        joined=stats.get("members_joined", 0),
        trusted=users.get("trusted", 0),
        pending=users.get("untrusted", 0),
        total=users.get("total", 0),
    )
    await update.effective_message.reply_text(text)


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    lang = _lang(context)
    limit = 10
    if context.args:
        with contextlib.suppress(ValueError):
            limit = max(1, min(50, int(context.args[0])))
    rows = await _storage(context).recent_bans(limit)
    if not rows:
        await update.effective_message.reply_text(t("recent_empty", lang))
        return
    lines = [t("recent_header", lang, count=len(rows))]
    for r in rows:
        when = time.strftime("%d.%m %H:%M", time.localtime(r["ts"]))
        uname = f"@{r['username']}" if r["username"] else f"id{r['user_id']}"
        text = (r["message_text"] or "").replace("\n", " ")[:120]
        lines.append(
            t(
                "recent_item",
                lang,
                when=when,
                action=r["action"],
                uname=uname,
                user_id=r["user_id"],
                conf=r["confidence"],
                reason=r["reason"],
                text=text,
            )
        )
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    msg = update.effective_message
    lang = _lang(context)
    text = " ".join(context.args or []).strip()
    if not text:
        await msg.reply_text(t("test_usage", lang))
        return
    meta = MessageMeta(
        has_links=("http://" in text or "https://" in text),
        has_mentions=("@" in text),
        is_forward=False,
    )
    result = await evaluate(
        text, meta, settings=_settings(context), classifier=_classifier(context)
    )
    body = t(
        "test_result",
        lang,
        decision=result.decision.value,
        conf=result.confidence,
        reason=result.reason or "—",
        model=result.model or "—",
    )
    if result.error:
        body += t("test_error_suffix", lang, error=t(f"err_{result.error}", lang))
    await msg.reply_text(body)


async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    lang = _lang(context)
    settings = _settings(context)
    values = settings.all()
    lines = [t("config_header", lang)]
    for key, desc in settings.describe().items():
        shown = values[key] if values[key] != "" else "—"
        lines.append(t("config_item", lang, key=key, value=shown, desc=desc))
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    msg = update.effective_message
    lang = _lang(context)
    args = context.args or []
    if len(args) < 2:
        await msg.reply_text(t("set_usage", lang))
        return
    key = args[0].lower()
    raw = " ".join(args[1:])
    settings = _settings(context)
    try:
        value = await settings.set(key, raw)
    except KeyError:
        await msg.reply_text(t("set_unknown", lang, key=key, known=", ".join(settings.SPEC)))
        return
    except ValueError as exc:
        await msg.reply_text(t("set_invalid", lang, error=exc))
        return
    await msg.reply_text(t("set_ok", lang, key=key, value=value))


async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    msg = update.effective_message
    lang = _lang(context)
    args = context.args or []
    if not args:
        await msg.reply_text(t("unban_usage", lang))
        return
    try:
        user_id = int(args[0])
    except ValueError:
        await msg.reply_text(t("unban_user_id_num", lang))
        return

    storage = _storage(context)
    config = _config(context)
    chat_id: int | None = None
    if len(args) >= 2:
        try:
            chat_id = int(args[1])
        except ValueError:
            await msg.reply_text(t("unban_chat_id_num", lang))
            return
    else:
        chat_id = await storage.last_ban_chat(user_id)
        if chat_id is None and len(config.allowed_chat_ids) == 1:
            chat_id = config.allowed_chat_ids[0]
        if chat_id is None:
            await msg.reply_text(t("unban_no_chat", lang))
            return

    assert chat_id is not None
    try:
        await context.bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
        await context.bot.restrict_chat_member(chat_id, user_id, ChatPermissions.all_permissions())
    except Exception as exc:
        await msg.reply_text(t("unban_fail", lang, error=exc))
        return
    await storage.set_status(chat_id, user_id, "trusted")
    await msg.reply_text(t("unban_ok", lang, user_id=user_id, chat_id=chat_id))


async def cmd_allow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    msg = update.effective_message
    lang = _lang(context)
    args = context.args or []
    if not args:
        await msg.reply_text(t("allow_usage", lang))
        return
    target = args[0]
    user_id, username = _parse_user_ref(target)
    added = await _storage(context).add_whitelist(user_id, username)
    await msg.reply_text(
        t("allow_added", lang, target=target) if added else t("allow_exists", lang, target=target)
    )


async def cmd_unallow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    msg = update.effective_message
    lang = _lang(context)
    args = context.args or []
    if not args:
        await msg.reply_text(t("unallow_usage", lang))
        return
    user_id, username = _parse_user_ref(args[0])
    removed = await _storage(context).remove_whitelist(user_id, username)
    await msg.reply_text(
        t("unallow_removed", lang, count=removed) if removed else t("unallow_none", lang)
    )


async def cmd_whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    lang = _lang(context)
    rows = await _storage(context).list_whitelist()
    if not rows:
        await update.effective_message.reply_text(t("whitelist_empty", lang))
        return
    lines = [t("whitelist_header", lang)]
    for r in rows:
        ref = f"@{r['username']}" if r["username"] else f"id{r['user_id']}"
        lines.append(t("whitelist_item", lang, ref=ref))
    await update.effective_message.reply_text("\n".join(lines))


async def cmd_resetstats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_admin(update, context):
        return
    await _storage(context).clear_stats()
    context.application.bot_data["started_at"] = time.time()
    await update.effective_message.reply_text(t("resetstats_ok", _lang(context)))


def _parse_user_ref(ref: str) -> tuple[int | None, str | None]:
    ref = ref.strip()
    if ref.startswith("@"):
        return None, ref[1:]
    try:
        return int(ref), None
    except ValueError:
        return None, ref  # treat as a username without @
