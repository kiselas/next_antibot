from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.constants import ChatMemberStatus

from antispam_bot import handlers
from antispam_bot.classifiers.base import ClassifyResult, Verdict
from tests.conftest import (
    FakeClassifier,
    make_bot,
    make_chat,
    make_context,
    make_msg,
    make_update,
    make_user,
)

SPAM = ClassifyResult(Verdict(is_spam=True, confidence=0.95, reason="ad"), "m", None)
CLEAN = ClassifyResult(Verdict(is_spam=False, confidence=0.1, reason="ok"), "m", None)
ERROR = ClassifyResult(None, None, "out_of_credits")


def fake(result):
    return FakeClassifier(result)


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_parse_user_ref():
    assert handlers._parse_user_ref("@Vasya") == (None, "Vasya")
    assert handlers._parse_user_ref("12345") == (12345, None)
    assert handlers._parse_user_ref("plain") == (None, "plain")


def test_is_bot_admin(config):
    cfg = config.model_copy(update={"admin_usernames": ["foo"], "admin_user_ids": [777]})
    assert handlers._is_bot_admin(make_user(1, "Foo"), cfg) is True
    assert handlers._is_bot_admin(make_user(777), cfg) is True
    assert handlers._is_bot_admin(make_user(2, "bar"), cfg) is False
    assert handlers._is_bot_admin(None, cfg) is False


def test_chat_allowed(config):
    assert handlers._chat_allowed(-1, config.model_copy(update={"allowed_chat_ids": []}))
    restricted = config.model_copy(update={"allowed_chat_ids": [-100, -200]})
    assert handlers._chat_allowed(-100, restricted) is True
    assert handlers._chat_allowed(-999, restricted) is False


def test_spam_keyboard():
    kb = handlers._spam_keyboard("ban", -100123, 42, "en")
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "unban:-100123:42" in datas and "ok:-100123:42" in datas
    report_kb = handlers._spam_keyboard("report", -100123, 42, "en")
    assert any(
        b.callback_data == "ban:-100123:42" for row in report_kb.inline_keyboard for b in row
    )


def test_budget_consume(config, storage, settings, monkeypatch):
    import antispam_bot.handlers as h

    ctx = make_context(config=config, storage=storage, settings=settings, classifier=fake(CLEAN))
    # unlimited by default
    assert h._llm_budget_consume(ctx) is True
    # day-rollover branch
    ctx.application.bot_data["llm_day"] = ("2000-01-01", 999)
    settings._cache["llm_daily_limit"] = 2
    assert h._llm_budget_consume(ctx) is True  # resets to today
    assert h._llm_budget_consume(ctx) is True
    assert h._llm_budget_consume(ctx) is False  # limit reached


# --------------------------------------------------------------------------- #
# handle_message
# --------------------------------------------------------------------------- #
async def test_message_spam_bans(config, storage, settings):
    clf = fake(SPAM)
    bot = make_bot()
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=clf, bot=bot)
    upd = make_update(
        message=make_msg("buy crypto"), chat=make_chat(), user=make_user(50, "spammer")
    )
    await handlers.handle_message(upd, ctx)
    bot.delete_message.assert_awaited()
    bot.ban_chat_member.assert_awaited()
    assert (await storage.all_stats())["users_banned"] == 1
    assert len(await storage.recent_bans()) == 1


async def test_message_spam_report_mode_no_ban(config, storage, settings):
    await settings.set("action_mode", "report")
    bot = make_bot()
    ctx = make_context(
        config=config, storage=storage, settings=settings, classifier=fake(SPAM), bot=bot
    )
    upd = make_update(message=make_msg("buy crypto"), chat=make_chat(), user=make_user(50))
    await handlers.handle_message(upd, ctx)
    bot.delete_message.assert_awaited()
    bot.ban_chat_member.assert_not_awaited()
    assert (await storage.all_stats()).get("users_banned", 0) == 0


async def test_message_spam_mute_mode(config, storage, settings):
    await settings.set("action_mode", "mute")
    bot = make_bot()
    ctx = make_context(
        config=config, storage=storage, settings=settings, classifier=fake(SPAM), bot=bot
    )
    upd = make_update(message=make_msg("buy crypto"), chat=make_chat(), user=make_user(50))
    await handlers.handle_message(upd, ctx)
    bot.restrict_chat_member.assert_awaited()


async def test_message_spam_sends_report_with_buttons(config, storage, settings):
    cfg = config.model_copy(update={"admin_chat_id": -100999})
    bot = make_bot()
    ctx = make_context(
        config=cfg, storage=storage, settings=settings, classifier=fake(SPAM), bot=bot
    )
    upd = make_update(message=make_msg("buy crypto"), chat=make_chat(), user=make_user(50))
    await handlers.handle_message(upd, ctx)
    bot.send_message.assert_awaited()
    assert bot.send_message.await_args.kwargs.get("reply_markup") is not None


async def test_message_clean_becomes_trusted(config, storage, settings):
    clf = fake(CLEAN)
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=clf)
    chat, user = make_chat(), make_user(50)
    for _ in range(3):  # trust_after_clean_msgs == 3
        await handlers.handle_message(
            make_update(message=make_msg("hello"), chat=chat, user=user), ctx
        )
    assert (await storage.get_user(chat.id, user.id))["status"] == "trusted"
    assert (await storage.all_stats())["messages_checked"] == 3


async def test_message_trusted_skips(config, storage, settings):
    chat, user = make_chat(), make_user(50)
    await storage.ensure_user(chat.id, user.id, "u")
    await storage.set_status(chat.id, user.id, "trusted")
    clf = fake(SPAM)
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=clf)
    await handlers.handle_message(
        make_update(message=make_msg("buy crypto"), chat=chat, user=user), ctx
    )
    assert clf.calls == 0


async def test_message_whitelist_skips(config, storage, settings):
    chat, user = make_chat(), make_user(50)
    await storage.add_whitelist(user.id, None)
    clf = fake(SPAM)
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=clf)
    await handlers.handle_message(
        make_update(message=make_msg("buy crypto"), chat=chat, user=user), ctx
    )
    assert clf.calls == 0


async def test_message_admin_skips(config, storage, settings):
    cfg = config.model_copy(update={"admin_user_ids": [50]})
    clf = fake(SPAM)
    ctx = make_context(config=cfg, storage=storage, settings=settings, classifier=clf)
    chat, user = make_chat(), make_user(50, "admin")
    await handlers.handle_message(
        make_update(message=make_msg("buy crypto"), chat=chat, user=user), ctx
    )
    assert clf.calls == 0
    assert (await storage.get_user(chat.id, user.id))["status"] == "trusted"


async def test_message_disabled(config, storage, settings):
    await settings.set("enabled", "false")
    clf = fake(SPAM)
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=clf)
    await handlers.handle_message(
        make_update(message=make_msg("x"), chat=make_chat(), user=make_user(50)), ctx
    )
    assert clf.calls == 0


async def test_message_not_allowed_chat(config, storage, settings):
    cfg = config.model_copy(update={"allowed_chat_ids": [-100999]})
    clf = fake(SPAM)
    ctx = make_context(config=cfg, storage=storage, settings=settings, classifier=clf)
    await handlers.handle_message(
        make_update(message=make_msg("x"), chat=make_chat(-100123), user=make_user(50)), ctx
    )
    assert clf.calls == 0


@pytest.mark.parametrize(
    "upd",
    [
        make_update(message=make_msg("x"), chat=make_chat(), user=make_user(1, is_bot=True)),
        make_update(
            message=make_msg("x", sender_chat=SimpleNamespace(id=-1)),
            chat=make_chat(),
            user=make_user(1),
        ),
        make_update(message=make_msg(""), chat=make_chat(), user=make_user(50)),
        make_update(message=make_msg("x"), chat=make_chat(ctype="private"), user=make_user(50)),
    ],
)
async def test_message_ignored_cases(config, storage, settings, upd):
    clf = fake(SPAM)
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=clf)
    await handlers.handle_message(upd, ctx)
    assert clf.calls == 0


async def test_message_error_failsafe_and_alert(config, storage, settings):
    cfg = config.model_copy(update={"admin_chat_id": -100999, "llm_error_alert_threshold": 1})
    bot = make_bot()
    ctx = make_context(
        config=cfg, storage=storage, settings=settings, classifier=fake(ERROR), bot=bot
    )
    await handlers.handle_message(
        make_update(message=make_msg("longer text"), chat=make_chat(), user=make_user(50)), ctx
    )
    assert (await storage.all_stats())["llm_errors"] == 1
    bot.ban_chat_member.assert_not_awaited()
    bot.send_message.assert_awaited()  # alert


async def test_message_budget_exceeded(config, storage, settings):
    await settings.set("llm_daily_limit", "1")
    cfg = config.model_copy(update={"admin_chat_id": -100999})
    bot = make_bot()
    ctx = make_context(
        config=cfg, storage=storage, settings=settings, classifier=fake(CLEAN), bot=bot
    )
    chat = make_chat()
    await handlers.handle_message(
        make_update(message=make_msg("hi there"), chat=chat, user=make_user(50)), ctx
    )
    await handlers.handle_message(
        make_update(message=make_msg("hi again"), chat=chat, user=make_user(51)), ctx
    )
    assert (await storage.all_stats())["llm_budget_skipped"] == 1
    bot.send_message.assert_awaited()  # budget alert


async def test_message_prefilter_skip_stat(config, storage, settings):
    clf = fake(SPAM)
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=clf)
    await handlers.handle_message(
        make_update(message=make_msg("+"), chat=make_chat(), user=make_user(50)), ctx
    )
    assert clf.calls == 0
    assert (await storage.all_stats())["messages_skipped_prefilter"] == 1


# --------------------------------------------------------------------------- #
# membership / presence
# --------------------------------------------------------------------------- #
async def test_chat_member_join(config, storage, settings):
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=fake(CLEAN))
    cm = SimpleNamespace(
        old_chat_member=SimpleNamespace(status=ChatMemberStatus.LEFT),
        new_chat_member=SimpleNamespace(status=ChatMemberStatus.MEMBER, user=make_user(7, "new")),
    )
    await handlers.handle_chat_member(make_update(chat=make_chat(), chat_member=cm), ctx)
    assert await storage.get_user(-100123, 7) is not None
    assert (await storage.all_stats())["members_joined"] == 1


async def test_my_chat_member_leaves_disallowed(config, storage, settings):
    cfg = config.model_copy(update={"allowed_chat_ids": [-100999]})
    bot = make_bot()
    ctx = make_context(
        config=cfg, storage=storage, settings=settings, classifier=fake(CLEAN), bot=bot
    )
    mcm = SimpleNamespace(new_chat_member=SimpleNamespace(status=ChatMemberStatus.MEMBER))
    await handlers.handle_my_chat_member(
        make_update(chat=make_chat(-100123), my_chat_member=mcm), ctx
    )
    bot.leave_chat.assert_awaited()


# --------------------------------------------------------------------------- #
# callbacks
# --------------------------------------------------------------------------- #
def _callback(data, user):
    return SimpleNamespace(
        data=data,
        from_user=user,
        message=SimpleNamespace(text="report"),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )


async def test_callback_unban(config, storage, settings):
    cfg = config.model_copy(update={"admin_user_ids": [1]})
    bot = make_bot()
    cq = _callback("unban:-100123:42", make_user(1, "admin"))
    ctx = make_context(
        config=cfg, storage=storage, settings=settings, classifier=fake(CLEAN), bot=bot
    )
    await handlers.handle_callback(make_update(callback_query=cq, user=make_user(1)), ctx)
    bot.unban_chat_member.assert_awaited()
    cq.edit_message_text.assert_awaited()


async def test_callback_ban(config, storage, settings):
    cfg = config.model_copy(update={"admin_user_ids": [1]})
    bot = make_bot()
    cq = _callback("ban:-100123:42", make_user(1, "admin"))
    ctx = make_context(
        config=cfg, storage=storage, settings=settings, classifier=fake(CLEAN), bot=bot
    )
    await handlers.handle_callback(make_update(callback_query=cq), ctx)
    bot.ban_chat_member.assert_awaited()
    assert (await storage.all_stats())["users_banned"] == 1


async def test_callback_ok(config, storage, settings):
    cfg = config.model_copy(update={"admin_user_ids": [1]})
    cq = _callback("ok:-100123:42", make_user(1, "admin"))
    ctx = make_context(config=cfg, storage=storage, settings=settings, classifier=fake(CLEAN))
    await handlers.handle_callback(make_update(callback_query=cq), ctx)
    cq.edit_message_text.assert_awaited()


async def test_callback_not_admin(config, storage, settings):
    bot = make_bot()
    cq = _callback("unban:-100123:42", make_user(2))
    ctx = make_context(
        config=config, storage=storage, settings=settings, classifier=fake(CLEAN), bot=bot
    )
    await handlers.handle_callback(make_update(callback_query=cq), ctx)
    cq.answer.assert_awaited()
    bot.unban_chat_member.assert_not_awaited()


async def test_callback_bad_data(config, storage, settings):
    cfg = config.model_copy(update={"admin_user_ids": [1]})
    cq = _callback("ban:x:5", make_user(1))
    ctx = make_context(config=cfg, storage=storage, settings=settings, classifier=fake(CLEAN))
    await handlers.handle_callback(make_update(callback_query=cq), ctx)
    cq.answer.assert_awaited()


async def test_callback_short_data(config, storage, settings):
    cq = _callback("oneword", make_user(1))
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=fake(CLEAN))
    await handlers.handle_callback(make_update(callback_query=cq), ctx)
    cq.answer.assert_awaited()


# --------------------------------------------------------------------------- #
# admin commands
# --------------------------------------------------------------------------- #
def _admin_ctx(config, storage, settings, *, args=None, bot=None, **cfg_over):
    cfg = config.model_copy(update={"admin_user_ids": [1], **cfg_over})
    return make_context(
        config=cfg,
        storage=storage,
        settings=settings,
        classifier=fake(CLEAN),
        args=args,
        bot=bot,
    )


def _admin_update():
    msg = make_msg("cmd")
    return make_update(
        message=msg, chat=make_chat(ctype="private"), user=make_user(1, "admin")
    ), msg


async def test_require_admin_denies(config, storage, settings):
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=fake(CLEAN))
    upd, msg = _admin_update()
    upd = make_update(message=msg, user=make_user(99))  # not admin
    assert await handlers._require_admin(upd, ctx) is False
    msg.reply_text.assert_awaited()


async def test_cmd_start_admin_and_user(config, storage, settings):
    ctx = _admin_ctx(config, storage, settings)
    upd, msg = _admin_update()
    await handlers.cmd_start(upd, ctx)
    msg.reply_text.assert_awaited()
    # non-admin greeting
    ctx2 = make_context(config=config, storage=storage, settings=settings, classifier=fake(CLEAN))
    msg2 = make_msg("x")
    await handlers.cmd_start(make_update(message=msg2, user=make_user(99)), ctx2)
    msg2.reply_text.assert_awaited()


async def test_cmd_help_stats_config(config, storage, settings):
    ctx = _admin_ctx(config, storage, settings)
    for cmd in (handlers.cmd_help, handlers.cmd_stats, handlers.cmd_config):
        upd, msg = _admin_update()
        await cmd(upd, ctx)
        msg.reply_text.assert_awaited()


async def test_cmd_set_variants(config, storage, settings):
    upd, msg = _admin_update()
    await handlers.cmd_set(upd, _admin_ctx(config, storage, settings, args=["language", "ru"]))
    assert settings.get("language") == "ru"

    upd, msg = _admin_update()
    await handlers.cmd_set(upd, _admin_ctx(config, storage, settings, args=["nope", "1"]))
    msg.reply_text.assert_awaited()

    upd, msg = _admin_update()
    await handlers.cmd_set(
        upd, _admin_ctx(config, storage, settings, args=["spam_confidence_threshold", "9"])
    )
    msg.reply_text.assert_awaited()

    upd, msg = _admin_update()
    await handlers.cmd_set(upd, _admin_ctx(config, storage, settings, args=[]))
    msg.reply_text.assert_awaited()


async def test_cmd_recent(config, storage, settings):
    upd, msg = _admin_update()
    await handlers.cmd_recent(upd, _admin_ctx(config, storage, settings))
    msg.reply_text.assert_awaited()  # empty
    await storage.record_ban(-1, 7, "sp", "ban", "scam", 0.9, "m", "buy")
    upd, msg = _admin_update()
    await handlers.cmd_recent(upd, _admin_ctx(config, storage, settings, args=["5"]))
    msg.reply_text.assert_awaited()


async def test_cmd_test(config, storage, settings):
    upd, msg = _admin_update()
    await handlers.cmd_test(upd, _admin_ctx(config, storage, settings, args=[]))
    msg.reply_text.assert_awaited()  # usage
    upd, msg = _admin_update()
    ctx = _admin_ctx(config, storage, settings, args=["hello", "world"])
    await handlers.cmd_test(upd, ctx)
    msg.reply_text.assert_awaited()


async def test_cmd_test_shows_error(config, storage, settings):
    upd, msg = _admin_update()
    cfg = config.model_copy(update={"admin_user_ids": [1]})
    ctx = make_context(
        config=cfg, storage=storage, settings=settings, classifier=fake(ERROR), args=["some text"]
    )
    await handlers.cmd_test(upd, ctx)
    msg.reply_text.assert_awaited()


async def test_cmd_unban(config, storage, settings):
    bot = make_bot()
    # usage
    upd, msg = _admin_update()
    await handlers.cmd_unban(upd, _admin_ctx(config, storage, settings, args=[], bot=bot))
    # non-int
    upd, msg = _admin_update()
    await handlers.cmd_unban(upd, _admin_ctx(config, storage, settings, args=["abc"], bot=bot))
    # ok with single allowed chat
    upd, msg = _admin_update()
    ctx = _admin_ctx(config, storage, settings, args=["42"], bot=bot, allowed_chat_ids=[-100123])
    await handlers.cmd_unban(upd, ctx)
    bot.unban_chat_member.assert_awaited()


async def test_cmd_unban_no_chat(config, storage, settings):
    upd, msg = _admin_update()
    ctx = _admin_ctx(config, storage, settings, args=["42"])  # no last ban, multiple/none allowed
    await handlers.cmd_unban(upd, ctx)
    msg.reply_text.assert_awaited()


async def test_cmd_whitelist_flow(config, storage, settings):
    upd, msg = _admin_update()
    await handlers.cmd_allow(upd, _admin_ctx(config, storage, settings, args=["@bob"]))
    upd, msg = _admin_update()
    await handlers.cmd_allow(upd, _admin_ctx(config, storage, settings, args=["@bob"]))  # exists
    upd, msg = _admin_update()
    await handlers.cmd_allow(upd, _admin_ctx(config, storage, settings, args=[]))  # usage
    upd, msg = _admin_update()
    await handlers.cmd_whitelist(upd, _admin_ctx(config, storage, settings))
    msg.reply_text.assert_awaited()
    upd, msg = _admin_update()
    await handlers.cmd_unallow(upd, _admin_ctx(config, storage, settings, args=["@bob"]))
    upd, msg = _admin_update()
    await handlers.cmd_unallow(upd, _admin_ctx(config, storage, settings, args=["@nobody"]))  # none
    upd, msg = _admin_update()
    await handlers.cmd_unallow(upd, _admin_ctx(config, storage, settings, args=[]))  # usage


async def test_cmd_whitelist_empty_and_resetstats(config, storage, settings):
    upd, msg = _admin_update()
    await handlers.cmd_whitelist(upd, _admin_ctx(config, storage, settings))
    msg.reply_text.assert_awaited()  # empty
    await storage.incr_stat("spam_detected")
    upd, msg = _admin_update()
    await handlers.cmd_resetstats(upd, _admin_ctx(config, storage, settings))
    assert await storage.all_stats() == {}


# --------------------------------------------------------------------------- #
# extra branches
# --------------------------------------------------------------------------- #
async def test_auto_trust_by_hours(config, storage, settings):
    chat, user = make_chat(), make_user(50)
    await storage.ensure_user(chat.id, user.id, "u")
    await storage._db.execute(
        "UPDATE users SET first_seen=0 WHERE chat_id=? AND user_id=?", (chat.id, user.id)
    )
    await storage._db.commit()
    clf = fake(SPAM)
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=clf)
    await handlers.handle_message(make_update(message=make_msg("hi"), chat=chat, user=user), ctx)
    assert clf.calls == 0
    assert (await storage.get_user(chat.id, user.id))["status"] == "trusted"


async def test_group_admin_lookup_failure_proceeds(config, storage, settings):
    bot = make_bot()
    bot.get_chat_administrators = AsyncMock(side_effect=Exception("no rights"))
    clf = fake(CLEAN)
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=clf, bot=bot)
    await handlers.handle_message(
        make_update(message=make_msg("hello"), chat=make_chat(), user=make_user(50)), ctx
    )
    assert clf.calls == 1  # not crashed, treated as non-admin


async def test_act_on_spam_api_failures(config, storage, settings):
    bot = make_bot()
    bot.delete_message = AsyncMock(side_effect=Exception("x"))
    bot.ban_chat_member = AsyncMock(side_effect=Exception("y"))
    ctx = make_context(
        config=config, storage=storage, settings=settings, classifier=fake(SPAM), bot=bot
    )
    await handlers.handle_message(
        make_update(message=make_msg("spam"), chat=make_chat(), user=make_user(50)), ctx
    )
    assert len(await storage.recent_bans()) == 1  # logged despite failures
    assert (await storage.all_stats()).get("users_banned", 0) == 0  # sanction failed


async def test_mute_failure(config, storage, settings):
    await settings.set("action_mode", "mute")
    bot = make_bot()
    bot.restrict_chat_member = AsyncMock(side_effect=Exception("x"))
    ctx = make_context(
        config=config, storage=storage, settings=settings, classifier=fake(SPAM), bot=bot
    )
    await handlers.handle_message(
        make_update(message=make_msg("spam"), chat=make_chat(), user=make_user(50)), ctx
    )
    assert (await storage.all_stats()).get("users_banned", 0) == 0


async def test_report_send_failure(config, storage, settings):
    cfg = config.model_copy(update={"admin_chat_id": -100999})
    bot = make_bot()
    bot.send_message = AsyncMock(side_effect=Exception("x"))
    ctx = make_context(
        config=cfg, storage=storage, settings=settings, classifier=fake(SPAM), bot=bot
    )
    await handlers.handle_message(
        make_update(message=make_msg("spam"), chat=make_chat(), user=make_user(50)), ctx
    )
    assert (await storage.all_stats())["spam_detected"] == 1  # no crash


async def test_error_alert_send_failure(config, storage, settings):
    cfg = config.model_copy(update={"admin_chat_id": -100999, "llm_error_alert_threshold": 1})
    bot = make_bot()
    bot.send_message = AsyncMock(side_effect=Exception("x"))
    ctx = make_context(
        config=cfg, storage=storage, settings=settings, classifier=fake(ERROR), bot=bot
    )
    await handlers.handle_message(
        make_update(message=make_msg("longer text"), chat=make_chat(), user=make_user(50)), ctx
    )
    assert (await storage.all_stats())["llm_errors"] == 1  # no crash


async def test_budget_alert_send_failure(config, storage, settings):
    await settings.set("llm_daily_limit", "1")
    cfg = config.model_copy(update={"admin_chat_id": -100999})
    bot = make_bot()
    bot.send_message = AsyncMock(side_effect=Exception("x"))
    ctx = make_context(
        config=cfg, storage=storage, settings=settings, classifier=fake(CLEAN), bot=bot
    )
    chat = make_chat()
    await handlers.handle_message(
        make_update(message=make_msg("hi a"), chat=chat, user=make_user(50)), ctx
    )
    await handlers.handle_message(
        make_update(message=make_msg("hi b"), chat=chat, user=make_user(51)), ctx
    )
    assert (await storage.all_stats())["llm_budget_skipped"] == 1  # no crash


async def test_callback_unban_error(config, storage, settings):
    cfg = config.model_copy(update={"admin_user_ids": [1]})
    bot = make_bot()
    bot.unban_chat_member = AsyncMock(side_effect=Exception("boom"))
    cq = _callback("unban:-100123:42", make_user(1, "admin"))
    ctx = make_context(
        config=cfg, storage=storage, settings=settings, classifier=fake(CLEAN), bot=bot
    )
    await handlers.handle_callback(make_update(callback_query=cq), ctx)
    cq.answer.assert_awaited()
    cq.edit_message_text.assert_not_awaited()


async def test_callback_ban_error(config, storage, settings):
    cfg = config.model_copy(update={"admin_user_ids": [1]})
    bot = make_bot()
    bot.ban_chat_member = AsyncMock(side_effect=Exception("boom"))
    cq = _callback("ban:-100123:42", make_user(1, "admin"))
    ctx = make_context(
        config=cfg, storage=storage, settings=settings, classifier=fake(CLEAN), bot=bot
    )
    await handlers.handle_callback(make_update(callback_query=cq), ctx)
    cq.answer.assert_awaited()


async def test_callback_unknown_action(config, storage, settings):
    cfg = config.model_copy(update={"admin_user_ids": [1]})
    cq = _callback("weird:-100123:42", make_user(1))
    ctx = make_context(config=cfg, storage=storage, settings=settings, classifier=fake(CLEAN))
    await handlers.handle_callback(make_update(callback_query=cq), ctx)
    cq.answer.assert_awaited()


async def test_callback_none(config, storage, settings):
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=fake(CLEAN))
    await handlers.handle_callback(make_update(callback_query=None), ctx)  # no-op


async def test_my_chat_member_allowed(config, storage, settings):
    bot = make_bot()
    ctx = make_context(
        config=config, storage=storage, settings=settings, classifier=fake(CLEAN), bot=bot
    )
    mcm = SimpleNamespace(new_chat_member=SimpleNamespace(status=ChatMemberStatus.MEMBER))
    await handlers.handle_my_chat_member(make_update(chat=make_chat(), my_chat_member=mcm), ctx)
    bot.leave_chat.assert_not_awaited()


async def test_chat_member_ignored(config, storage, settings):
    cfg = config.model_copy(update={"allowed_chat_ids": [-100999]})
    ctx = make_context(config=cfg, storage=storage, settings=settings, classifier=fake(CLEAN))
    cm = SimpleNamespace(
        old_chat_member=SimpleNamespace(status=ChatMemberStatus.LEFT),
        new_chat_member=SimpleNamespace(status=ChatMemberStatus.MEMBER, user=make_user(7)),
    )
    await handlers.handle_chat_member(make_update(chat=make_chat(-100123), chat_member=cm), ctx)
    assert await storage.get_user(-100123, 7) is None  # not allowed chat -> ignored


async def test_chat_member_bot_ignored(config, storage, settings):
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=fake(CLEAN))
    cm = SimpleNamespace(
        old_chat_member=SimpleNamespace(status=ChatMemberStatus.LEFT),
        new_chat_member=SimpleNamespace(
            status=ChatMemberStatus.MEMBER, user=make_user(7, is_bot=True)
        ),
    )
    await handlers.handle_chat_member(make_update(chat=make_chat(), chat_member=cm), ctx)
    assert (await storage.all_stats()).get("members_joined", 0) == 0


async def test_cmd_unban_explicit_chat(config, storage, settings):
    bot = make_bot()
    upd, msg = _admin_update()
    ctx = _admin_ctx(config, storage, settings, args=["42", "-100777"], bot=bot)
    await handlers.cmd_unban(upd, ctx)
    bot.unban_chat_member.assert_awaited()


async def test_cmd_unban_bad_chat_id(config, storage, settings):
    upd, msg = _admin_update()
    ctx = _admin_ctx(config, storage, settings, args=["42", "notint"])
    await handlers.cmd_unban(upd, ctx)
    msg.reply_text.assert_awaited()


async def test_cmd_unban_from_last_ban(config, storage, settings):
    await storage.record_ban(-55, 42, None, "ban", "r", 0.9, "m", "txt")
    bot = make_bot()
    upd, msg = _admin_update()
    ctx = _admin_ctx(config, storage, settings, args=["42"], bot=bot)
    await handlers.cmd_unban(upd, ctx)
    bot.unban_chat_member.assert_awaited()


async def test_cmd_unban_failure(config, storage, settings):
    bot = make_bot()
    bot.unban_chat_member = AsyncMock(side_effect=Exception("x"))
    upd, msg = _admin_update()
    ctx = _admin_ctx(config, storage, settings, args=["42"], bot=bot, allowed_chat_ids=[-100123])
    await handlers.cmd_unban(upd, ctx)
    msg.reply_text.assert_awaited()


async def test_command_denied_for_non_admin(config, storage, settings):
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=fake(CLEAN))
    msg = make_msg("x")
    await handlers.cmd_stats(make_update(message=msg, user=make_user(99)), ctx)
    msg.reply_text.assert_awaited()  # admin_only reply


async def test_message_no_message(config, storage, settings):
    clf = fake(SPAM)
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=clf)
    await handlers.handle_message(
        make_update(message=None, chat=make_chat(), user=make_user(50)), ctx
    )
    assert clf.calls == 0


async def test_is_group_admin_no_chat(config, storage, settings):
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=fake(CLEAN))
    assert await handlers._is_group_admin(make_update(), ctx) is False


async def test_callback_edit_failure(config, storage, settings):
    cfg = config.model_copy(update={"admin_user_ids": [1]})
    cq = _callback("ok:-100123:42", make_user(1))
    cq.edit_message_text = AsyncMock(side_effect=Exception("old"))
    ctx = make_context(config=cfg, storage=storage, settings=settings, classifier=fake(CLEAN))
    await handlers.handle_callback(make_update(callback_query=cq), ctx)
    cq.answer.assert_awaited()  # error swallowed


async def test_chat_member_none(config, storage, settings):
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=fake(CLEAN))
    await handlers.handle_chat_member(make_update(chat=make_chat(), chat_member=None), ctx)


async def test_my_chat_member_none(config, storage, settings):
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=fake(CLEAN))
    await handlers.handle_my_chat_member(make_update(chat=make_chat(), my_chat_member=None), ctx)


async def test_cmd_start_no_message(config, storage, settings):
    cfg = config.model_copy(update={"admin_user_ids": [1]})
    ctx = make_context(config=cfg, storage=storage, settings=settings, classifier=fake(CLEAN))
    await handlers.cmd_start(make_update(message=None, user=make_user(1)), ctx)  # no-op


async def test_my_chat_member_leave_failure(config, storage, settings):
    cfg = config.model_copy(update={"allowed_chat_ids": [-100999]})
    bot = make_bot()
    bot.leave_chat = AsyncMock(side_effect=Exception("x"))
    ctx = make_context(
        config=cfg, storage=storage, settings=settings, classifier=fake(CLEAN), bot=bot
    )
    mcm = SimpleNamespace(new_chat_member=SimpleNamespace(status=ChatMemberStatus.MEMBER))
    await handlers.handle_my_chat_member(
        make_update(chat=make_chat(-100123), my_chat_member=mcm), ctx
    )
    bot.leave_chat.assert_awaited()  # attempted, error swallowed


@pytest.mark.parametrize(
    "cmd",
    [
        handlers.cmd_help,
        handlers.cmd_stats,
        handlers.cmd_recent,
        handlers.cmd_test,
        handlers.cmd_config,
        handlers.cmd_set,
        handlers.cmd_unban,
        handlers.cmd_allow,
        handlers.cmd_unallow,
        handlers.cmd_whitelist,
        handlers.cmd_resetstats,
    ],
)
async def test_all_commands_deny_non_admin(config, storage, settings, cmd):
    ctx = make_context(config=config, storage=storage, settings=settings, classifier=fake(CLEAN))
    msg = make_msg("x")
    await cmd(make_update(message=msg, user=make_user(99)), ctx)
    msg.reply_text.assert_awaited()  # admin_only
