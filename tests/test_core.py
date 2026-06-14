import pytest

from antispam_bot.core import parse_user_ref
from antispam_bot.platform import CallbackAction, User
from tests.conftest import (
    CLEAN,
    ERROR,
    SPAM,
    callback_event,
    cmd_req,
    fake,
    member_event,
    membership_event,
    msg_event,
)


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "ref, expected",
    [("@Vasya", (None, "Vasya")), ("12345", (12345, None)), ("plain", (None, "plain"))],
)
def test_parse_user_ref(ref, expected):
    assert parse_user_ref(ref) == expected


@pytest.mark.parametrize(
    "user, expected",
    [(User(1, "Foo"), True), (User(777), True), (User(2, "bar"), False), (None, False)],
)
def test_is_bot_admin(make_core, user, expected):
    core = make_core(admin_usernames=["foo"], admin_user_ids=[777])
    assert core.is_bot_admin(user) is expected


@pytest.mark.parametrize(
    "allowed, cid, expected",
    [([], -1, True), ([-100, -200], -100, True), ([-100, -200], -999, False)],
)
def test_chat_allowed(make_core, allowed, cid, expected):
    assert make_core(allowed_chat_ids=allowed).chat_allowed(cid) is expected


@pytest.mark.parametrize(
    "mode, primary",
    [("ban", "unban:-100123:42"), ("mute", "unban:-100123:42"), ("report", "ban:-100123:42")],
)
def test_spam_buttons(make_core, mode, primary):
    rows = make_core()._spam_buttons(mode, -100123, 42)
    datas = [data for row in rows for _label, data in row]
    assert primary in datas and "ok:-100123:42" in datas


def test_budget_consume(make_core, settings):
    core = make_core()
    assert core._budget_consume(-1) is True  # unlimited by default
    settings._cache["llm_daily_limit"] = 2
    assert core._budget_consume(-1) is True
    assert core._budget_consume(-1) is True
    assert core._budget_consume(-1) is False  # per-chat limit hit
    assert core._budget_consume(-2) is True  # separate chat has its own counter
    core._budget[-1] = {"day": "2000-01-01", "count": 99, "alerted": True}
    assert core._budget_consume(-1) is True  # day rollover resets


# --------------------------------------------------------------------------- #
# on_message — skipped before the classifier
# --------------------------------------------------------------------------- #
async def _trust(storage, settings):
    await storage.ensure_user(-100123, 50, "u")
    await storage.set_status(-100123, 50, "trusted")


async def _whitelist(storage, settings):
    await storage.add_whitelist(50, None)


async def _disabled(storage, settings):
    await settings.set("enabled", "false")


SKIP = {
    "trusted": ({}, _trust, msg_event("buy")),
    "whitelist": ({}, _whitelist, msg_event("buy")),
    "admin": ({"admin_user_ids": [50]}, None, msg_event("buy")),
    "disabled": ({}, _disabled, msg_event("x")),
    "not_allowed": ({"allowed_chat_ids": [-100999]}, None, msg_event("x")),
    "bot_user": ({}, None, msg_event("x", is_bot=True)),
    "automatic": ({}, None, msg_event("x", is_automatic=True)),
    "empty_text": ({}, None, msg_event("")),
    "private": ({}, None, msg_event("x", is_group=False)),
}


@pytest.mark.parametrize("scenario", SKIP.values(), ids=SKIP.keys())
async def test_message_skipped(make_core, storage, settings, scenario):
    cfg_over, setup, event = scenario
    if setup:
        await setup(storage, settings)
    clf = fake(SPAM)
    await make_core(clf, **cfg_over).on_message(event)
    assert clf.calls == 0


async def test_group_admin_trusted(make_core, platform, storage):
    platform.admin_ids = {50}
    clf = fake(SPAM)
    await make_core(clf).on_message(msg_event("buy", uid=50))
    assert clf.calls == 0
    assert (await storage.get_user(-100123, 50))["status"] == "trusted"


# --------------------------------------------------------------------------- #
# on_message — spam actions
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "mode, method, sanctioned",
    [("ban", "ban_user", True), ("mute", "mute_user", True), ("report", None, False)],
)
async def test_spam_action(make_core, platform, storage, settings, mode, method, sanctioned):
    if mode != "ban":
        await settings.set("action_mode", mode)
    await make_core(fake(SPAM)).on_message(msg_event("buy crypto"))
    platform.mock.delete_message.assert_awaited()
    if method:
        getattr(platform.mock, method).assert_awaited()
    stats = await storage.all_stats()
    assert stats["spam_detected"] == 1
    assert stats.get("users_banned", 0) == (1 if sanctioned else 0)


@pytest.mark.parametrize("mode, method", [("ban", "ban_user"), ("mute", "mute_user")])
async def test_spam_action_failure(make_core, platform, storage, settings, mode, method):
    if mode != "ban":
        await settings.set("action_mode", mode)
    platform.mock.delete_message.side_effect = Exception("x")
    getattr(platform.mock, method).side_effect = Exception("y")
    await make_core(fake(SPAM)).on_message(msg_event("spam"))
    assert len(await storage.recent_bans()) == 1
    assert (await storage.all_stats()).get("users_banned", 0) == 0


async def test_spam_report_with_buttons(make_core, platform):
    await make_core(fake(SPAM), admin_chat_id=-100999).on_message(msg_event("buy"))
    platform.mock.send_message.assert_awaited()
    assert platform.mock.send_message.await_args.kwargs.get("buttons") is not None


async def test_spam_report_send_failure(make_core, platform, storage):
    platform.mock.send_message.side_effect = Exception("x")
    await make_core(fake(SPAM), admin_chat_id=-100999).on_message(msg_event("buy"))
    assert (await storage.all_stats())["spam_detected"] == 1


# --------------------------------------------------------------------------- #
# on_message — clean / trust / error / budget
# --------------------------------------------------------------------------- #
async def test_clean_becomes_trusted(make_core, storage):
    core = make_core(fake(CLEAN))
    for _ in range(3):
        await core.on_message(msg_event("hello"))
    assert (await storage.get_user(-100123, 50))["status"] == "trusted"
    assert (await storage.all_stats())["messages_checked"] == 3


async def test_auto_trust_by_hours(make_core, storage):
    await storage.ensure_user(-100123, 50, "u")
    await storage._db.execute("UPDATE users SET first_seen=0 WHERE chat_id=-100123 AND user_id=50")
    await storage._db.commit()
    clf = fake(SPAM)
    await make_core(clf).on_message(msg_event("hi"))
    assert clf.calls == 0
    assert (await storage.get_user(-100123, 50))["status"] == "trusted"


async def test_prefilter_skip(make_core, storage):
    clf = fake(SPAM)
    await make_core(clf).on_message(msg_event("+"))
    assert clf.calls == 0
    assert (await storage.all_stats())["messages_skipped_prefilter"] == 1


@pytest.mark.parametrize("send_fails", [False, True])
async def test_error_failsafe(make_core, platform, storage, send_fails):
    if send_fails:
        platform.mock.send_message.side_effect = Exception("x")
    core = make_core(fake(ERROR), admin_chat_id=-100999, llm_error_alert_threshold=1)
    await core.on_message(msg_event("longer text"))
    assert (await storage.all_stats())["llm_errors"] == 1
    platform.mock.ban_user.assert_not_awaited()
    if not send_fails:
        platform.mock.send_message.assert_awaited()


@pytest.mark.parametrize("send_fails", [False, True])
async def test_budget_exceeded(make_core, platform, storage, settings, send_fails):
    await settings.set("llm_daily_limit", "1")
    if send_fails:
        platform.mock.send_message.side_effect = Exception("x")
    core = make_core(fake(CLEAN), admin_chat_id=-100999)
    await core.on_message(msg_event("hi a", uid=50))
    await core.on_message(msg_event("hi b", uid=51))
    assert (await storage.all_stats())["llm_budget_skipped"] == 1
    if not send_fails:
        platform.mock.send_message.assert_awaited()


# --------------------------------------------------------------------------- #
# membership / presence
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "cfg_over, ev, joined",
    [
        ({}, member_event(uid=7, username="new"), True),
        ({"allowed_chat_ids": [-100999]}, member_event(uid=7), False),
        ({}, member_event(uid=7, is_bot=True), False),
        ({}, member_event(uid=7, joined=False), False),
    ],
    ids=["join", "foreign", "bot", "not_joined"],
)
async def test_on_member(make_core, storage, cfg_over, ev, joined):
    await make_core(**cfg_over).on_member(ev)
    assert (await storage.all_stats()).get("members_joined", 0) == (1 if joined else 0)


@pytest.mark.parametrize(
    "cfg_over, present, leave, raises",
    [
        ({"allowed_chat_ids": [-100999]}, True, True, False),
        ({"allowed_chat_ids": [-100999]}, True, True, True),
        ({}, True, False, False),
        ({}, False, False, False),
    ],
    ids=["leave", "leave_error", "allowed", "absent"],
)
async def test_on_bot_membership(make_core, platform, cfg_over, present, leave, raises):
    if raises:
        platform.mock.leave_chat.side_effect = Exception("x")
    await make_core(**cfg_over).on_bot_membership(
        membership_event(present=present, chat_id=-100123)
    )
    (
        platform.mock.leave_chat.assert_awaited
        if leave
        else platform.mock.leave_chat.assert_not_awaited
    )()


# --------------------------------------------------------------------------- #
# callbacks
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "action, method", [("unban", "unban_user"), ("ban", "ban_user"), ("ok", None)]
)
async def test_callback_action(make_core, platform, storage, action, method):
    await make_core(admin_user_ids=[1]).on_callback(
        callback_event(f"{action}:-100123:42", uid=1, username="admin")
    )
    platform.mock.edit_message.assert_awaited()
    if method:
        getattr(platform.mock, method).assert_awaited()
    if action == "ban":
        assert (await storage.all_stats())["users_banned"] == 1


@pytest.mark.parametrize(
    "data, uid, admin_ids",
    [
        ("unban:-100123:42", 2, []),
        ("ban:x:5", 1, [1]),
        ("oneword", 1, [1]),
        ("weird:-100123:42", 1, [1]),
    ],
    ids=["not_admin", "bad_ints", "wrong_shape", "unknown"],
)
async def test_callback_noop(make_core, platform, data, uid, admin_ids):
    await make_core(admin_user_ids=admin_ids).on_callback(callback_event(data, uid=uid))
    platform.mock.answer_callback.assert_awaited()
    platform.mock.unban_user.assert_not_awaited()
    platform.mock.ban_user.assert_not_awaited()


@pytest.mark.parametrize("action, method", [("unban", "unban_user"), ("ban", "ban_user")])
async def test_callback_action_error(make_core, platform, action, method):
    getattr(platform.mock, method).side_effect = Exception("boom")
    await make_core(admin_user_ids=[1]).on_callback(
        callback_event(f"{action}:-100123:42", uid=1, username="admin")
    )
    platform.mock.answer_callback.assert_awaited()
    platform.mock.edit_message.assert_not_awaited()


async def test_callback_edit_failure(make_core, platform):
    platform.mock.edit_message.side_effect = Exception("old")
    await make_core(admin_user_ids=[1]).on_callback(callback_event("ok:-100123:42", uid=1))
    platform.mock.answer_callback.assert_awaited()


async def test_callback_no_message(make_core, platform):
    ev = CallbackAction(
        callback_id="cb", data="ok:-1:2", user=User(1, "a"), chat_id=None, message_id=None
    )
    await make_core(admin_user_ids=[1]).on_callback(ev)
    platform.mock.edit_message.assert_not_awaited()


# --------------------------------------------------------------------------- #
# admin commands
# --------------------------------------------------------------------------- #
ALL_COMMANDS = [
    "cmd_help",
    "cmd_stats",
    "cmd_recent",
    "cmd_test",
    "cmd_config",
    "cmd_set",
    "cmd_setchat",
    "cmd_unban",
    "cmd_allow",
    "cmd_unallow",
    "cmd_whitelist",
    "cmd_resetstats",
]


@pytest.mark.parametrize("name", ALL_COMMANDS)
async def test_command_denies_non_admin(make_core, platform, name):
    await getattr(make_core(), name)(cmd_req(uid=99, username="nobody"))
    platform.mock.send_message.assert_awaited()  # admin_only


@pytest.mark.parametrize(
    "name", ["cmd_help", "cmd_stats", "cmd_config", "cmd_whitelist", "cmd_recent"]
)
async def test_simple_admin_replies(make_core, platform, name):
    await getattr(make_core(admin_user_ids=[1]), name)(cmd_req())
    platform.mock.send_message.assert_awaited()


@pytest.mark.parametrize("uid", [1, 99], ids=["admin", "user"])
async def test_cmd_start(make_core, platform, uid):
    await make_core(admin_user_ids=[1]).cmd_start(cmd_req(uid=uid))
    platform.mock.send_message.assert_awaited()


async def test_cmd_set_applies(make_core, settings):
    await make_core(admin_user_ids=[1]).cmd_set(cmd_req(args=["language", "ru"]))
    assert settings.get("language") == "ru"


@pytest.mark.parametrize(
    "args",
    [["language", "ru"], ["nope", "1"], ["spam_confidence_threshold", "9"], []],
    ids=["ok", "unknown", "bad", "usage"],
)
async def test_cmd_set_replies(make_core, platform, args):
    await make_core(admin_user_ids=[1]).cmd_set(cmd_req(args=args))
    platform.mock.send_message.assert_awaited()


@pytest.mark.parametrize(
    "args, result",
    [([], CLEAN), (["hi", "world"], CLEAN), (["text"], ERROR)],
    ids=["usage", "result", "error"],
)
async def test_cmd_test(make_core, platform, args, result):
    await make_core(fake(result), admin_user_ids=[1]).cmd_test(cmd_req(args=args))
    platform.mock.send_message.assert_awaited()


async def test_cmd_recent_with_data(make_core, platform, storage):
    await storage.record_ban(-1, 7, "sp", "ban", "scam", 0.9, "m", "buy")
    await make_core(admin_user_ids=[1]).cmd_recent(cmd_req(args=["5"]))
    platform.mock.send_message.assert_awaited()


async def _noop(storage):
    pass


async def _last_ban(storage):
    await storage.record_ban(-55, 42, None, "ban", "r", 0.9, "m", "t")


@pytest.mark.parametrize(
    "args, cfg_over, setup, expect_unban",
    [
        ([], {}, _noop, False),
        (["abc"], {}, _noop, False),
        (["42", "notint"], {}, _noop, False),
        (["42", "-100777"], {}, _noop, True),
        (["42"], {"allowed_chat_ids": [-100123]}, _noop, True),
        (["42"], {}, _noop, False),
        (["42"], {}, _last_ban, True),
    ],
    ids=["usage", "bad_user", "bad_chat", "explicit", "single_allowed", "no_chat", "last_ban"],
)
async def test_cmd_unban(make_core, platform, storage, args, cfg_over, setup, expect_unban):
    await setup(storage)
    await make_core(admin_user_ids=[1], **cfg_over).cmd_unban(cmd_req(args=args))
    platform.mock.send_message.assert_awaited()
    (
        platform.mock.unban_user.assert_awaited
        if expect_unban
        else platform.mock.unban_user.assert_not_awaited
    )()


async def test_cmd_unban_failure(make_core, platform, storage):
    platform.mock.unban_user.side_effect = Exception("x")
    await make_core(admin_user_ids=[1], allowed_chat_ids=[-100123]).cmd_unban(cmd_req(args=["42"]))
    platform.mock.send_message.assert_awaited()


@pytest.mark.parametrize(
    "name, args",
    [("cmd_allow", ["@bob"]), ("cmd_allow", []), ("cmd_unallow", ["@nobody"]), ("cmd_unallow", [])],
    ids=["allow", "allow_usage", "unallow_missing", "unallow_usage"],
)
async def test_whitelist_command_replies(make_core, platform, name, args):
    await getattr(make_core(admin_user_ids=[1]), name)(cmd_req(args=args))
    platform.mock.send_message.assert_awaited()


async def test_whitelist_flow(make_core, storage):
    core = make_core(admin_user_ids=[1])
    await core.cmd_allow(cmd_req(args=["@bob"]))
    assert await storage.is_whitelisted(999, "bob")
    await core.cmd_allow(cmd_req(args=["@bob"]))  # already-exists branch
    await core.cmd_whitelist(cmd_req())
    await core.cmd_unallow(cmd_req(args=["@bob"]))
    assert not await storage.is_whitelisted(999, "bob")


async def test_cmd_resetstats(make_core, storage):
    await storage.incr_stat("spam_detected")
    await make_core(admin_user_ids=[1]).cmd_resetstats(cmd_req())
    assert await storage.all_stats() == {}


# --------------------------------------------------------------------------- #
# per-chat settings
# --------------------------------------------------------------------------- #
async def test_per_chat_enabled_disables_only_that_chat(make_core, platform, settings):
    await settings.set("enabled", "false", chat_id=-100123)  # off in this chat only
    clf = fake(SPAM)
    await make_core(clf).on_message(msg_event("buy", chat_id=-100123))
    assert clf.calls == 0  # disabled here
    clf2 = fake(SPAM)
    await make_core(clf2).on_message(msg_event("buy", chat_id=-100999))
    assert clf2.calls == 1  # still on elsewhere


async def test_per_chat_threshold(make_core, settings):
    # global threshold lets 0.95 through as spam; chat override raises the bar
    await settings.set("spam_confidence_threshold", "0.99", chat_id=-100123)
    clf = fake(SPAM)  # confidence 0.95
    await make_core(clf).on_message(msg_event("buy", chat_id=-100123))
    assert clf.calls == 1  # classified, but below the chat's bar -> treated as clean


async def test_cmd_setchat_applies(make_core, settings):
    await make_core(admin_user_ids=[1]).cmd_setchat(cmd_req(args=["-100", "action_mode", "mute"]))
    assert settings.get("action_mode", -100) == "mute"
    assert settings.get("action_mode") == "ban"  # global unchanged


@pytest.mark.parametrize(
    "args",
    [
        ["-100", "action_mode", "mute"],
        ["x", "action_mode", "mute"],
        ["-100", "nope", "1"],
        ["-100", "action_mode", "bad"],
        ["-100"],
    ],
    ids=["ok", "bad_chat", "unknown_key", "bad_value", "usage"],
)
async def test_cmd_setchat_replies(make_core, platform, args):
    await make_core(admin_user_ids=[1]).cmd_setchat(cmd_req(args=args))
    platform.mock.send_message.assert_awaited()


@pytest.mark.parametrize("args", [[], ["-100"], ["notint"]], ids=["global", "chat", "bad_chat"])
async def test_cmd_config(make_core, platform, args):
    await make_core(admin_user_ids=[1]).cmd_config(cmd_req(args=args))
    platform.mock.send_message.assert_awaited()
