"""Shared fixtures, fakes and data builders for tests."""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("BOT_TOKEN", "123:TEST")

from antispam_bot.classifiers import Classifier  # noqa: E402
from antispam_bot.classifiers.base import (  # noqa: E402
    ClassificationContext,
    ClassifyResult,
    Verdict,
)
from antispam_bot.config import Config  # noqa: E402
from antispam_bot.runtime_settings import Settings  # noqa: E402
from antispam_bot.storage import Storage  # noqa: E402

# --- canned classifier results, reused everywhere ---
SPAM = ClassifyResult(Verdict(is_spam=True, confidence=0.95, reason="ad"), "m", None)
CLEAN = ClassifyResult(Verdict(is_spam=False, confidence=0.1, reason="ok"), "m", None)
ERROR = ClassifyResult(None, None, "out_of_credits")


class FakeClassifier(Classifier):
    """Returns a preset ClassifyResult and records calls."""

    name = "fake"

    def __init__(self, result: ClassifyResult = CLEAN) -> None:
        self.result = result
        self.calls = 0
        self.last_ctx: ClassificationContext | None = None

    async def classify(self, ctx: ClassificationContext) -> ClassifyResult:
        self.calls += 1
        self.last_ctx = ctx
        return self.result


def fake(result: ClassifyResult = CLEAN) -> FakeClassifier:
    return FakeClassifier(result)


# --- core fixtures ---
@pytest.fixture
def config():
    return Config(_env_file=None)


@pytest.fixture
async def storage(tmp_path):
    st = Storage(str(tmp_path / "t.db"))
    await st.connect()
    yield st
    await st.close()


@pytest.fixture
async def settings(storage, config):
    s = Settings(storage, config)
    await s.load()
    return s


@pytest.fixture
def bot():
    return make_bot()


@pytest.fixture
def make_ctx(config, storage, settings, bot):
    """Factory for a handler context with overridable config and classifier.

    Usage: ``make_ctx(fake(SPAM), args=["42"], admin_user_ids=[1])``.
    Config keyword overrides are applied via ``model_copy``; the shared ``bot``
    fixture is reused so tests can assert on it.
    """

    def _make(classifier=None, *, args=None, **cfg_over):
        cfg = config.model_copy(update=cfg_over) if cfg_over else config
        return make_context(
            config=cfg,
            storage=storage,
            settings=settings,
            classifier=classifier or fake(),
            args=args,
            bot=bot,
        )

    return _make


# --- Telegram object doubles ---
def make_bot() -> AsyncMock:
    b = AsyncMock()
    b.get_chat_administrators = AsyncMock(return_value=[])
    return b


def make_user(uid: int = 1, username: str | None = None, is_bot: bool = False):
    return SimpleNamespace(id=uid, username=username, is_bot=is_bot)


def make_chat(cid: int = -100123, ctype: str = "supergroup", title: str = "Test"):
    return SimpleNamespace(id=cid, type=ctype, title=title)


def make_msg(
    text: str = "",
    *,
    message_id: int = 10,
    caption=None,
    entities=None,
    caption_entities=None,
    forward_origin=None,
    sender_chat=None,
):
    return SimpleNamespace(
        text=text,
        caption=caption,
        message_id=message_id,
        entities=entities,
        caption_entities=caption_entities,
        forward_origin=forward_origin,
        sender_chat=sender_chat,
        reply_text=AsyncMock(),
    )


def make_update(
    *,
    message=None,
    chat=None,
    user=None,
    callback_query=None,
    chat_member=None,
    my_chat_member=None,
):
    return SimpleNamespace(
        effective_message=message,
        effective_chat=chat,
        effective_user=user,
        callback_query=callback_query,
        chat_member=chat_member,
        my_chat_member=my_chat_member,
    )


def make_context(*, config, storage, settings, classifier, args=None, bot=None):
    import time

    bot = bot or make_bot()
    app = SimpleNamespace(
        bot_data={
            "config": config,
            "storage": storage,
            "settings": settings,
            "classifier": classifier,
            "started_at": time.time(),
            "llm_error_streak": 0,
            "llm_alerted": False,
        }
    )
    return SimpleNamespace(application=app, bot=bot, args=args or [])


# --- high-level event builders (compose the doubles above) ---
def group_update(
    text: str = "hi",
    *,
    uid: int = 50,
    username=None,
    is_bot=False,
    chat_id: int = -100123,
    ctype: str = "supergroup",
    **msg_kw,
):
    return make_update(
        message=make_msg(text, **msg_kw),
        chat=make_chat(chat_id, ctype),
        user=make_user(uid, username, is_bot),
    )


def admin_update(text: str = "cmd", *, uid: int = 1, username: str = "admin"):
    msg = make_msg(text)
    upd = make_update(message=msg, chat=make_chat(ctype="private"), user=make_user(uid, username))
    return upd, msg


def make_callback(data: str, *, uid: int = 1, username=None, text: str = "report"):
    return SimpleNamespace(
        data=data,
        from_user=make_user(uid, username),
        message=SimpleNamespace(text=text),
        answer=AsyncMock(),
        edit_message_text=AsyncMock(),
    )
