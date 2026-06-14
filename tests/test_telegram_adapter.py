from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.constants import ChatMemberStatus as CMS

from antispam_bot import telegram_adapter as tg
from tests.conftest import (
    SPAM,
    fake,
    make_bot,
    make_chat,
    make_msg,
    make_update,
    make_user,
    tg_context,
)


# --------------------------------------------------------------------------- #
# update -> event conversion
# --------------------------------------------------------------------------- #
def test_to_message_group():
    ev = tg.to_message(
        make_update(message=make_msg("hi"), chat=make_chat(), user=make_user(50, "u"))
    )
    assert ev.chat_id == -100123 and ev.is_group and ev.text == "hi"
    assert ev.user.id == 50 and ev.user.username == "u"


def test_to_message_none():
    assert tg.to_message(make_update(message=None, chat=make_chat(), user=make_user(1))) is None


def test_to_message_flags():
    msg = make_msg(
        "see",
        entities=[SimpleNamespace(type="url")],
        sender_chat=SimpleNamespace(id=-1),
        forward_origin=object(),
    )
    ev = tg.to_message(make_update(message=msg, chat=make_chat(ctype="private"), user=make_user(1)))
    assert ev.has_links and ev.is_automatic and ev.is_forward and not ev.is_group


def test_to_message_caption_and_mention():
    msg = make_msg("", caption="hello @bob", caption_entities=[SimpleNamespace(type="mention")])
    ev = tg.to_message(make_update(message=msg, chat=make_chat(), user=make_user(1)))
    assert ev.text == "hello @bob" and ev.has_mentions


# --------------------------------------------------------------------------- #
# TelegramPlatform -> bot calls
# --------------------------------------------------------------------------- #
async def test_platform_actions():
    bot = make_bot()
    p = tg.TelegramPlatform(bot)
    await p.delete_message(-1, 5)
    bot.delete_message.assert_awaited_with(-1, 5)
    await p.ban_user(-1, 2)
    bot.ban_chat_member.assert_awaited_with(-1, 2)
    await p.mute_user(-1, 2)
    bot.restrict_chat_member.assert_awaited()
    await p.unban_user(-1, 2)
    bot.unban_chat_member.assert_awaited()
    await p.send_message(-1, "hi", buttons=[[("a", "b")]])
    assert bot.send_message.await_args.kwargs["reply_markup"] is not None
    await p.edit_message(-1, 5, "t")
    bot.edit_message_text.assert_awaited()
    await p.answer_callback("c", "t", alert=True)
    bot.answer_callback_query.assert_awaited()
    await p.leave_chat(-1)
    bot.leave_chat.assert_awaited_with(-1)


async def test_platform_admin_cache():
    bot = make_bot()
    bot.get_chat_administrators = AsyncMock(
        return_value=[SimpleNamespace(user=SimpleNamespace(id=7))]
    )
    p = tg.TelegramPlatform(bot)
    assert await p.chat_admin_ids(-1) == {7}
    assert await p.chat_admin_ids(-1) == {7}  # cached
    assert bot.get_chat_administrators.await_count == 1


async def test_platform_admin_error():
    bot = make_bot()
    bot.get_chat_administrators = AsyncMock(side_effect=Exception("x"))
    assert await tg.TelegramPlatform(bot).chat_admin_ids(-1) == set()


@pytest.mark.parametrize("buttons, is_none", [(None, True), ([[("a", "b")]], False)])
def test_to_markup(buttons, is_none):
    markup = tg._to_markup(buttons)
    assert (markup is None) is is_none
    if markup:
        assert markup.inline_keyboard[0][0].callback_data == "b"


# --------------------------------------------------------------------------- #
# PTB callbacks -> core dispatch
# --------------------------------------------------------------------------- #
async def test_dispatch_message(make_core, platform):
    core = make_core(fake(SPAM))
    upd = make_update(message=make_msg("buy"), chat=make_chat(), user=make_user(50))
    await tg.on_message(upd, tg_context(core))
    platform.mock.ban_user.assert_awaited()


async def test_dispatch_message_none(make_core, platform):
    core = make_core(fake(SPAM))
    await tg.on_message(
        make_update(message=None, chat=make_chat(), user=make_user(1)), tg_context(core)
    )
    platform.mock.ban_user.assert_not_awaited()


async def test_dispatch_chat_member(make_core, storage):
    cm = SimpleNamespace(
        old_chat_member=SimpleNamespace(status=CMS.LEFT),
        new_chat_member=SimpleNamespace(status=CMS.MEMBER, user=make_user(7, "n")),
    )
    await tg.on_chat_member(make_update(chat=make_chat(), chat_member=cm), tg_context(make_core()))
    assert (await storage.all_stats())["members_joined"] == 1


async def test_dispatch_chat_member_none(make_core):
    await tg.on_chat_member(
        make_update(chat=make_chat(), chat_member=None), tg_context(make_core())
    )


async def test_dispatch_my_chat_member(make_core, platform):
    core = make_core(allowed_chat_ids=[-100999])
    mcm = SimpleNamespace(new_chat_member=SimpleNamespace(status=CMS.MEMBER))
    await tg.on_my_chat_member(
        make_update(chat=make_chat(-100123), my_chat_member=mcm), tg_context(core)
    )
    platform.mock.leave_chat.assert_awaited()


async def test_dispatch_my_chat_member_none(make_core):
    await tg.on_my_chat_member(
        make_update(chat=make_chat(), my_chat_member=None), tg_context(make_core())
    )


async def test_dispatch_callback(make_core, platform):
    core = make_core(admin_user_ids=[1])
    q = SimpleNamespace(
        id="c1",
        data="ok:-1:2",
        from_user=make_user(1, "a"),
        message=SimpleNamespace(chat=SimpleNamespace(id=-100999), message_id=5, text="report"),
    )
    await tg.on_callback(make_update(callback_query=q), tg_context(core))
    platform.mock.answer_callback.assert_awaited()


async def test_dispatch_callback_none(make_core):
    await tg.on_callback(make_update(callback_query=None), tg_context(make_core()))


async def test_dispatch_command(make_core, platform):
    handler = tg.make_command("cmd_stats")
    upd = make_update(
        message=make_msg("x"), chat=make_chat(ctype="private"), user=make_user(1, "a")
    )
    await handler(upd, tg_context(make_core(admin_user_ids=[1])))
    platform.mock.send_message.assert_awaited()


async def test_dispatch_command_no_user(make_core):
    handler = tg.make_command("cmd_stats")
    await handler(make_update(chat=make_chat(), user=None), tg_context(make_core()))
