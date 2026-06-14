"""Telegram adapter: implements BotPlatform and converts PTB updates to events."""

from __future__ import annotations

import logging
import time

from telegram import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ChatType
from telegram.ext import ContextTypes

from .core import Core
from .platform import (
    BotMembership,
    BotPlatform,
    Button,
    CallbackAction,
    CommandRequest,
    IncomingMessage,
    MemberUpdate,
    User,
)

log = logging.getLogger(__name__)

_MEMBER_STATUSES = {
    ChatMemberStatus.MEMBER,
    ChatMemberStatus.ADMINISTRATOR,
    ChatMemberStatus.OWNER,
    ChatMemberStatus.RESTRICTED,
}
_GROUP_TYPES = (ChatType.GROUP, ChatType.SUPERGROUP)
_ADMIN_CACHE_TTL = 300  # seconds
_MUTE_PERMS = ChatPermissions(
    can_send_messages=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
)
_LINK_ENTITIES = {"url", "text_link"}
_MENTION_ENTITIES = {"mention", "text_mention"}


# --------------------------------------------------------------------------- #
# Platform implementation
# --------------------------------------------------------------------------- #
class TelegramPlatform(BotPlatform):
    def __init__(self, bot) -> None:
        self._bot = bot
        self._admin_cache: dict[int, tuple[float, set[int]]] = {}

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        await self._bot.delete_message(chat_id, message_id)

    async def ban_user(self, chat_id: int, user_id: int) -> None:
        await self._bot.ban_chat_member(chat_id, user_id)

    async def mute_user(self, chat_id: int, user_id: int) -> None:
        await self._bot.restrict_chat_member(chat_id, user_id, _MUTE_PERMS)

    async def unban_user(self, chat_id: int, user_id: int) -> None:
        await self._bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
        await self._bot.restrict_chat_member(chat_id, user_id, ChatPermissions.all_permissions())

    async def send_message(
        self, chat_id: int, text: str, *, buttons: list[list[Button]] | None = None
    ) -> None:
        await self._bot.send_message(chat_id, text, reply_markup=_to_markup(buttons))

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        await self._bot.edit_message_text(text, chat_id=chat_id, message_id=message_id)

    async def answer_callback(
        self, callback_id: str, text: str | None = None, *, alert: bool = False
    ) -> None:
        await self._bot.answer_callback_query(callback_id, text=text, show_alert=alert)

    async def chat_admin_ids(self, chat_id: int) -> set[int]:
        now = time.monotonic()
        entry = self._admin_cache.get(chat_id)
        if entry is None or now - entry[0] > _ADMIN_CACHE_TTL:
            try:
                admins = await self._bot.get_chat_administrators(chat_id)
                ids = {a.user.id for a in admins}
                self._admin_cache[chat_id] = (now, ids)
            except Exception as exc:  # missing rights / network — reuse previous cache
                log.debug("Could not fetch admins of chat %s: %s", chat_id, exc)
                ids = entry[1] if entry else set()
        else:
            ids = entry[1]
        return ids

    async def leave_chat(self, chat_id: int) -> None:
        await self._bot.leave_chat(chat_id)


def _to_markup(buttons: list[list[Button]] | None) -> InlineKeyboardMarkup | None:
    if not buttons:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text, callback_data=data) for text, data in row] for row in buttons]
    )


# --------------------------------------------------------------------------- #
# Update -> event conversion
# --------------------------------------------------------------------------- #
def _user(u) -> User:
    return User(id=u.id, username=u.username, is_bot=u.is_bot)


def _has_entity(msg, types: set[str]) -> bool:
    entities = list(msg.entities or []) + list(msg.caption_entities or [])
    return any(e.type in types for e in entities)


def to_message(update: Update) -> IncomingMessage | None:
    msg, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if msg is None or chat is None or user is None:
        return None
    return IncomingMessage(
        chat_id=chat.id,
        message_id=msg.message_id,
        user=_user(user),
        text=msg.text or msg.caption or "",
        is_group=chat.type in _GROUP_TYPES,
        is_automatic=msg.sender_chat is not None,
        has_links=_has_entity(msg, _LINK_ENTITIES),
        has_mentions=_has_entity(msg, _MENTION_ENTITIES),
        is_forward=msg.forward_origin is not None,
        chat_title=chat.title,
    )


# --------------------------------------------------------------------------- #
# PTB handler callbacks (thin: convert -> delegate to Core)
# --------------------------------------------------------------------------- #
def _core(context: ContextTypes.DEFAULT_TYPE) -> Core:
    return context.application.bot_data["core"]


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    event = to_message(update)
    if event is not None:
        await _core(context).on_message(event)


async def on_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmu, chat = update.chat_member, update.effective_chat
    if cmu is None or chat is None:
        return
    was_in = cmu.old_chat_member.status in _MEMBER_STATUSES
    now_in = cmu.new_chat_member.status in _MEMBER_STATUSES
    await _core(context).on_member(
        MemberUpdate(chat.id, _user(cmu.new_chat_member.user), joined=now_in and not was_in)
    )


async def on_my_chat_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cmu, chat = update.my_chat_member, update.effective_chat
    if cmu is None or chat is None:
        return
    present = cmu.new_chat_member.status in _MEMBER_STATUSES
    await _core(context).on_bot_membership(BotMembership(chat.id, present, chat.title))


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None or not q.data:
        return
    msg = q.message
    await _core(context).on_callback(
        CallbackAction(
            callback_id=q.id,
            data=q.data,
            user=_user(q.from_user),
            chat_id=msg.chat.id if msg else None,
            message_id=msg.message_id if msg else None,
            message_text=getattr(msg, "text", None),
        )
    )


def make_command(method_name: str):
    """Build a PTB command handler that delegates to ``Core.<method_name>``."""

    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat, user = update.effective_chat, update.effective_user
        if chat is None or user is None:
            return
        req = CommandRequest(chat_id=chat.id, user=_user(user), args=context.args or [])
        await getattr(_core(context), method_name)(req)

    return handler
