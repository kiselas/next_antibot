from types import SimpleNamespace

from app import handlers


def _user(uid, username=None):
    return SimpleNamespace(id=uid, username=username)


def test_parse_user_ref():
    assert handlers._parse_user_ref("@Vasya") == (None, "Vasya")
    assert handlers._parse_user_ref("12345") == (12345, None)
    assert handlers._parse_user_ref("plainname") == (None, "plainname")


def test_is_bot_admin(config):
    cfg = config.model_copy(update={"admin_usernames": ["foo"], "admin_user_ids": [777]})
    assert handlers._is_bot_admin(_user(1, "Foo"), cfg) is True   # по нику, регистр не важен
    assert handlers._is_bot_admin(_user(777, None), cfg) is True  # по id
    assert handlers._is_bot_admin(_user(2, "bar"), cfg) is False
    assert handlers._is_bot_admin(None, cfg) is False


def test_chat_allowed(config):
    open_cfg = config.model_copy(update={"allowed_chat_ids": []})
    assert handlers._chat_allowed(-100, open_cfg) is True  # пустой список = везде

    restricted = config.model_copy(update={"allowed_chat_ids": [-100, -200]})
    assert handlers._chat_allowed(-100, restricted) is True
    assert handlers._chat_allowed(-999, restricted) is False


def test_spam_keyboard_callback_data():
    kb = handlers._spam_keyboard("ban", -100123, 42)
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert "unban:-100123:42" in datas
    assert "ok:-100123:42" in datas

    report_kb = handlers._spam_keyboard("report", -100123, 42)
    rdatas = [b.callback_data for row in report_kb.inline_keyboard for b in row]
    assert "ban:-100123:42" in rdatas
