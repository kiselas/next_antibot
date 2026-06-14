from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.constants import ChatMemberStatus as CMS

from antispam_bot import handlers
from tests.conftest import (
    CLEAN,
    ERROR,
    SPAM,
    admin_update,
    fake,
    group_update,
    make_callback,
    make_chat,
    make_msg,
    make_update,
    make_user,
)

LEFT, MEMBER = CMS.LEFT, CMS.MEMBER


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "ref, expected",
    [("@Vasya", (None, "Vasya")), ("12345", (12345, None)), ("plain", (None, "plain"))],
)
def test_parse_user_ref(ref, expected):
    assert handlers._parse_user_ref(ref) == expected


@pytest.mark.parametrize(
    "user, expected",
    [
        (make_user(1, "Foo"), True),  # by username, case-insensitive
        (make_user(777), True),  # by id
        (make_user(2, "bar"), False),
        (None, False),
    ],
)
def test_is_bot_admin(config, user, expected):
    cfg = config.model_copy(update={"admin_usernames": ["foo"], "admin_user_ids": [777]})
    assert handlers._is_bot_admin(user, cfg) is expected


@pytest.mark.parametrize(
    "allowed, cid, expected",
    [([], -1, True), ([-100, -200], -100, True), ([-100, -200], -999, False)],
)
def test_chat_allowed(config, allowed, cid, expected):
    assert (
        handlers._chat_allowed(cid, config.model_copy(update={"allowed_chat_ids": allowed}))
        is expected
    )


@pytest.mark.parametrize(
    "mode, primary",
    [("ban", "unban:-100123:42"), ("mute", "unban:-100123:42"), ("report", "ban:-100123:42")],
)
def test_spam_keyboard(mode, primary):
    kb = handlers._spam_keyboard(mode, -100123, 42, "en")
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert primary in datas
    assert "ok:-100123:42" in datas


def test_budget_consume(make_ctx, settings):
    ctx = make_ctx()
    assert handlers._llm_budget_consume(ctx) is True  # unlimited by default
    ctx.application.bot_data["llm_day"] = ("2000-01-01", 999)
    settings._cache["llm_daily_limit"] = 2
    assert handlers._llm_budget_consume(ctx) is True  # day rollover resets counter
    assert handlers._llm_budget_consume(ctx) is True
    assert handlers._llm_budget_consume(ctx) is False  # limit reached


# --------------------------------------------------------------------------- #
# handle_message — messages that must NOT reach the classifier
# --------------------------------------------------------------------------- #
async def _sc_trusted(storage, settings):
    await storage.ensure_user(-100123, 50, "u")
    await storage.set_status(-100123, 50, "trusted")
    return {}, group_update("buy crypto")


async def _sc_whitelist(storage, settings):
    await storage.add_whitelist(50, None)
    return {}, group_update("buy crypto")


async def _sc_admin(storage, settings):
    return {"admin_user_ids": [50]}, group_update("buy crypto")


async def _sc_disabled(storage, settings):
    await settings.set("enabled", "false")
    return {}, group_update("x")


async def _sc_not_allowed(storage, settings):
    return {"allowed_chat_ids": [-100999]}, group_update("x")


async def _sc_bot_user(storage, settings):
    return {}, group_update("x", uid=1, is_bot=True)


async def _sc_sender_chat(storage, settings):
    return {}, group_update("x", sender_chat=SimpleNamespace(id=-1))


async def _sc_empty(storage, settings):
    return {}, group_update("")


async def _sc_private(storage, settings):
    return {}, group_update("x", ctype="private")


async def _sc_no_message(storage, settings):
    return {}, make_update(message=None, chat=make_chat(), user=make_user(50))


SKIP_SCENARIOS = {
    "trusted": _sc_trusted,
    "whitelist": _sc_whitelist,
    "admin": _sc_admin,
    "disabled": _sc_disabled,
    "not_allowed_chat": _sc_not_allowed,
    "bot_user": _sc_bot_user,
    "sender_chat": _sc_sender_chat,
    "empty_text": _sc_empty,
    "private_chat": _sc_private,
    "no_message": _sc_no_message,
}


@pytest.mark.parametrize("scenario", SKIP_SCENARIOS.values(), ids=SKIP_SCENARIOS.keys())
async def test_message_skipped(make_ctx, storage, settings, scenario):
    cfg_over, upd = await scenario(storage, settings)
    clf = fake(SPAM)
    await handlers.handle_message(upd, make_ctx(clf, **cfg_over))
    assert clf.calls == 0


# --------------------------------------------------------------------------- #
# handle_message — spam actions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "mode, method, sanctioned",
    [
        ("ban", "ban_chat_member", True),
        ("mute", "restrict_chat_member", True),
        ("report", None, False),
    ],
)
async def test_spam_action(make_ctx, bot, storage, settings, mode, method, sanctioned):
    if mode != "ban":
        await settings.set("action_mode", mode)
    await handlers.handle_message(group_update("buy crypto"), make_ctx(fake(SPAM)))
    bot.delete_message.assert_awaited()
    if method:
        getattr(bot, method).assert_awaited()
    stats = await storage.all_stats()
    assert stats["spam_detected"] == 1
    assert stats.get("users_banned", 0) == (1 if sanctioned else 0)


@pytest.mark.parametrize(
    "mode, method", [("ban", "ban_chat_member"), ("mute", "restrict_chat_member")]
)
async def test_spam_action_failure(make_ctx, bot, storage, settings, mode, method):
    if mode != "ban":
        await settings.set("action_mode", mode)
    bot.delete_message = AsyncMock(side_effect=Exception("x"))
    setattr(bot, method, AsyncMock(side_effect=Exception("y")))
    await handlers.handle_message(group_update("spam"), make_ctx(fake(SPAM)))
    assert len(await storage.recent_bans()) == 1  # logged despite failures
    assert (await storage.all_stats()).get("users_banned", 0) == 0


async def test_spam_report_with_buttons(make_ctx, bot, storage):
    await handlers.handle_message(group_update("buy"), make_ctx(fake(SPAM), admin_chat_id=-100999))
    bot.send_message.assert_awaited()
    assert bot.send_message.await_args.kwargs.get("reply_markup") is not None


async def test_spam_report_send_failure(make_ctx, bot, storage):
    bot.send_message = AsyncMock(side_effect=Exception("x"))
    await handlers.handle_message(group_update("buy"), make_ctx(fake(SPAM), admin_chat_id=-100999))
    assert (await storage.all_stats())["spam_detected"] == 1  # no crash


# --------------------------------------------------------------------------- #
# handle_message — clean / trust / errors / budget
# --------------------------------------------------------------------------- #
async def test_clean_becomes_trusted(make_ctx, storage):
    ctx = make_ctx(fake(CLEAN))
    for _ in range(3):  # trust_after_clean_msgs == 3
        await handlers.handle_message(group_update("hello"), ctx)
    assert (await storage.get_user(-100123, 50))["status"] == "trusted"
    assert (await storage.all_stats())["messages_checked"] == 3


async def test_auto_trust_by_hours(make_ctx, storage):
    await storage.ensure_user(-100123, 50, "u")
    await storage._db.execute("UPDATE users SET first_seen=0 WHERE chat_id=-100123 AND user_id=50")
    await storage._db.commit()
    clf = fake(SPAM)
    await handlers.handle_message(group_update("hi"), make_ctx(clf))
    assert clf.calls == 0
    assert (await storage.get_user(-100123, 50))["status"] == "trusted"


async def test_prefilter_skip_stat(make_ctx, storage):
    clf = fake(SPAM)
    await handlers.handle_message(group_update("+"), make_ctx(clf))
    assert clf.calls == 0
    assert (await storage.all_stats())["messages_skipped_prefilter"] == 1


async def test_group_admin_lookup_failure_proceeds(make_ctx, bot, storage):
    bot.get_chat_administrators = AsyncMock(side_effect=Exception("no rights"))
    clf = fake(CLEAN)
    await handlers.handle_message(group_update("hello"), make_ctx(clf))
    assert clf.calls == 1  # treated as non-admin, no crash


@pytest.mark.parametrize("send_fails", [False, True])
async def test_error_failsafe_and_alert(make_ctx, bot, storage, send_fails):
    if send_fails:
        bot.send_message = AsyncMock(side_effect=Exception("x"))
    ctx = make_ctx(fake(ERROR), admin_chat_id=-100999, llm_error_alert_threshold=1)
    await handlers.handle_message(group_update("longer text"), ctx)
    assert (await storage.all_stats())["llm_errors"] == 1
    bot.ban_chat_member.assert_not_awaited()
    if not send_fails:
        bot.send_message.assert_awaited()


@pytest.mark.parametrize("send_fails", [False, True])
async def test_budget_exceeded(make_ctx, bot, storage, settings, send_fails):
    await settings.set("llm_daily_limit", "1")
    if send_fails:
        bot.send_message = AsyncMock(side_effect=Exception("x"))
    ctx = make_ctx(fake(CLEAN), admin_chat_id=-100999)
    await handlers.handle_message(group_update("hi a", uid=50), ctx)
    await handlers.handle_message(group_update("hi b", uid=51), ctx)
    assert (await storage.all_stats())["llm_budget_skipped"] == 1
    if not send_fails:
        bot.send_message.assert_awaited()


# --------------------------------------------------------------------------- #
# membership / presence
# --------------------------------------------------------------------------- #
def _cm(uid, username=None, is_bot=False, old=LEFT, new=MEMBER):
    return SimpleNamespace(
        old_chat_member=SimpleNamespace(status=old),
        new_chat_member=SimpleNamespace(status=new, user=make_user(uid, username, is_bot)),
    )


@pytest.mark.parametrize(
    "cfg_over, cm, joined",
    [
        ({}, _cm(7, "new"), True),
        ({"allowed_chat_ids": [-100999]}, _cm(7), False),  # foreign chat
        ({}, _cm(7, is_bot=True), False),  # bots ignored
        ({}, None, False),  # no chat_member payload
    ],
    ids=["join", "foreign_chat", "bot", "none"],
)
async def test_chat_member(make_ctx, storage, cfg_over, cm, joined):
    await handlers.handle_chat_member(
        make_update(chat=make_chat(), chat_member=cm), make_ctx(**cfg_over)
    )
    assert (await storage.all_stats()).get("members_joined", 0) == (1 if joined else 0)


@pytest.mark.parametrize(
    "cfg_over, status, leave_called, leave_raises",
    [
        ({"allowed_chat_ids": [-100999]}, MEMBER, True, False),  # foreign -> leave
        ({"allowed_chat_ids": [-100999]}, MEMBER, True, True),  # leave error swallowed
        ({}, MEMBER, False, False),  # allowed -> stay
        ({}, None, False, False),  # no payload
    ],
    ids=["leave", "leave_error", "allowed", "none"],
)
async def test_my_chat_member(make_ctx, bot, cfg_over, status, leave_called, leave_raises):
    if leave_raises:
        bot.leave_chat = AsyncMock(side_effect=Exception("x"))
    mcm = (
        None if status is None else SimpleNamespace(new_chat_member=SimpleNamespace(status=status))
    )
    await handlers.handle_my_chat_member(
        make_update(chat=make_chat(-100123), my_chat_member=mcm), make_ctx(**cfg_over)
    )
    (bot.leave_chat.assert_awaited if leave_called else bot.leave_chat.assert_not_awaited)()


# --------------------------------------------------------------------------- #
# callbacks
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "action, method", [("unban", "unban_chat_member"), ("ban", "ban_chat_member"), ("ok", None)]
)
async def test_callback_action(make_ctx, bot, storage, action, method):
    cq = make_callback(f"{action}:-100123:42", uid=1, username="admin")
    await handlers.handle_callback(make_update(callback_query=cq), make_ctx(admin_user_ids=[1]))
    cq.edit_message_text.assert_awaited()
    if method:
        getattr(bot, method).assert_awaited()
    if action == "ban":
        assert (await storage.all_stats())["users_banned"] == 1


@pytest.mark.parametrize(
    "data, uid, admin_ids",
    [
        ("unban:-100123:42", 2, []),  # not an admin
        ("ban:x:5", 1, [1]),  # non-integer ids
        ("oneword", 1, [1]),  # wrong shape
        ("weird:-100123:42", 1, [1]),  # unknown action
    ],
    ids=["not_admin", "bad_ints", "wrong_shape", "unknown_action"],
)
async def test_callback_noop(make_ctx, bot, data, uid, admin_ids):
    cq = make_callback(data, uid=uid)
    await handlers.handle_callback(
        make_update(callback_query=cq), make_ctx(admin_user_ids=admin_ids)
    )
    cq.answer.assert_awaited()
    bot.unban_chat_member.assert_not_awaited()
    bot.ban_chat_member.assert_not_awaited()


@pytest.mark.parametrize(
    "action, method", [("unban", "unban_chat_member"), ("ban", "ban_chat_member")]
)
async def test_callback_action_error(make_ctx, bot, action, method):
    setattr(bot, method, AsyncMock(side_effect=Exception("boom")))
    cq = make_callback(f"{action}:-100123:42", uid=1, username="admin")
    await handlers.handle_callback(make_update(callback_query=cq), make_ctx(admin_user_ids=[1]))
    cq.answer.assert_awaited()
    cq.edit_message_text.assert_not_awaited()


async def test_callback_edit_failure(make_ctx):
    cq = make_callback("ok:-100123:42", uid=1)
    cq.edit_message_text = AsyncMock(side_effect=Exception("old"))
    await handlers.handle_callback(make_update(callback_query=cq), make_ctx(admin_user_ids=[1]))
    cq.answer.assert_awaited()  # error swallowed


async def test_callback_none(make_ctx):
    await handlers.handle_callback(make_update(callback_query=None), make_ctx())  # no-op


# --------------------------------------------------------------------------- #
# admin commands
# --------------------------------------------------------------------------- #
ALL_COMMANDS = [
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
]


@pytest.mark.parametrize("cmd", ALL_COMMANDS, ids=lambda c: c.__name__)
async def test_commands_deny_non_admin(make_ctx, cmd):
    upd, msg = admin_update(uid=99, username="nobody")
    await cmd(upd, make_ctx())  # default config has no admins
    msg.reply_text.assert_awaited()  # admin_only reply


@pytest.mark.parametrize(
    "cmd",
    [
        handlers.cmd_help,
        handlers.cmd_stats,
        handlers.cmd_config,
        handlers.cmd_whitelist,
        handlers.cmd_recent,
    ],
    ids=lambda c: c.__name__,
)
async def test_simple_admin_replies(make_ctx, cmd):
    upd, msg = admin_update()
    await cmd(upd, make_ctx(admin_user_ids=[1]))
    msg.reply_text.assert_awaited()


@pytest.mark.parametrize(
    "uid, has_msg",
    [(1, True), (99, True), (1, False)],
    ids=["admin", "user", "no_message"],
)
async def test_cmd_start(make_ctx, uid, has_msg):
    msg = make_msg("x") if has_msg else None
    await handlers.cmd_start(
        make_update(message=msg, user=make_user(uid)), make_ctx(admin_user_ids=[1])
    )
    if has_msg:
        msg.reply_text.assert_awaited()


async def test_cmd_set_applies(make_ctx, settings):
    upd, _ = admin_update()
    await handlers.cmd_set(upd, make_ctx(args=["language", "ru"], admin_user_ids=[1]))
    assert settings.get("language") == "ru"


@pytest.mark.parametrize(
    "args",
    [["language", "ru"], ["nope", "1"], ["spam_confidence_threshold", "9"], []],
    ids=["ok", "unknown_key", "bad_value", "usage"],
)
async def test_cmd_set_replies(make_ctx, args):
    upd, msg = admin_update()
    await handlers.cmd_set(upd, make_ctx(args=args, admin_user_ids=[1]))
    msg.reply_text.assert_awaited()


@pytest.mark.parametrize(
    "args, clf_result",
    [([], CLEAN), (["hello", "world"], CLEAN), (["text"], ERROR)],
    ids=["usage", "result", "error"],
)
async def test_cmd_test(make_ctx, args, clf_result):
    upd, msg = admin_update()
    await handlers.cmd_test(upd, make_ctx(fake(clf_result), args=args, admin_user_ids=[1]))
    msg.reply_text.assert_awaited()


async def test_cmd_recent_with_data(make_ctx, storage):
    await storage.record_ban(-1, 7, "sp", "ban", "scam", 0.9, "m", "buy")
    upd, msg = admin_update()
    await handlers.cmd_recent(upd, make_ctx(args=["5"], admin_user_ids=[1]))
    msg.reply_text.assert_awaited()


async def _noop(storage):
    pass


async def _last_ban(storage):
    await storage.record_ban(-55, 42, None, "ban", "r", 0.9, "m", "t")


@pytest.mark.parametrize(
    "args, cfg_over, setup, expect_unban",
    [
        ([], {}, _noop, False),  # usage
        (["abc"], {}, _noop, False),  # bad user_id
        (["42", "notint"], {}, _noop, False),  # bad chat_id
        (["42", "-100777"], {}, _noop, True),  # explicit chat
        (["42"], {"allowed_chat_ids": [-100123]}, _noop, True),  # single allowed chat
        (["42"], {}, _noop, False),  # no chat resolvable
        (["42"], {}, _last_ban, True),  # resolved from last ban
    ],
    ids=["usage", "bad_user", "bad_chat", "explicit", "single_allowed", "no_chat", "last_ban"],
)
async def test_cmd_unban(make_ctx, bot, storage, args, cfg_over, setup, expect_unban):
    await setup(storage)
    upd, msg = admin_update()
    await handlers.cmd_unban(upd, make_ctx(args=args, admin_user_ids=[1], **cfg_over))
    msg.reply_text.assert_awaited()
    (
        bot.unban_chat_member.assert_awaited
        if expect_unban
        else bot.unban_chat_member.assert_not_awaited
    )()


async def test_cmd_unban_failure(make_ctx, bot, storage):
    bot.unban_chat_member = AsyncMock(side_effect=Exception("x"))
    upd, msg = admin_update()
    await handlers.cmd_unban(
        upd, make_ctx(args=["42"], admin_user_ids=[1], allowed_chat_ids=[-100123])
    )
    msg.reply_text.assert_awaited()


@pytest.mark.parametrize(
    "cmd, args",
    [
        (handlers.cmd_allow, ["@bob"]),
        (handlers.cmd_allow, []),
        (handlers.cmd_unallow, ["@nobody"]),
        (handlers.cmd_unallow, []),
    ],
    ids=["allow", "allow_usage", "unallow_missing", "unallow_usage"],
)
async def test_whitelist_command_replies(make_ctx, cmd, args):
    upd, msg = admin_update()
    await cmd(upd, make_ctx(args=args, admin_user_ids=[1]))
    msg.reply_text.assert_awaited()


async def test_whitelist_flow(make_ctx, storage):
    async def run(cmd, args):
        upd, _ = admin_update()
        await cmd(upd, make_ctx(args=args, admin_user_ids=[1]))

    await run(handlers.cmd_allow, ["@bob"])
    assert await storage.is_whitelisted(999, "bob")
    await run(handlers.cmd_allow, ["@bob"])  # already-exists branch
    await run(handlers.cmd_whitelist, [])
    await run(handlers.cmd_unallow, ["@bob"])
    assert not await storage.is_whitelisted(999, "bob")


async def test_cmd_resetstats(make_ctx, storage):
    await storage.incr_stat("spam_detected")
    upd, _ = admin_update()
    await handlers.cmd_resetstats(upd, make_ctx(admin_user_ids=[1]))
    assert await storage.all_stats() == {}


async def test_is_group_admin_no_chat(make_ctx):
    assert await handlers._is_group_admin(make_update(), make_ctx()) is False
