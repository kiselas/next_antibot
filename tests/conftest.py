"""Shared fixtures and lightweight fakes for tests."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("BOT_TOKEN", "123:TEST")

from antispam_bot.classifiers import Classifier  # noqa: E402
from antispam_bot.classifiers.base import ClassificationContext, ClassifyResult  # noqa: E402
from antispam_bot.config import Config  # noqa: E402
from antispam_bot.runtime_settings import Settings  # noqa: E402
from antispam_bot.storage import Storage  # noqa: E402


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


class FakeClassifier(Classifier):
    """Returns a preset ClassifyResult and records calls."""

    name = "fake"

    def __init__(self, result: ClassifyResult) -> None:
        self.result = result
        self.calls = 0
        self.last_ctx: ClassificationContext | None = None

    async def classify(self, ctx: ClassificationContext) -> ClassifyResult:
        self.calls += 1
        self.last_ctx = ctx
        return self.result


def make_bot() -> AsyncMock:
    bot = AsyncMock()
    bot.get_chat_administrators = AsyncMock(return_value=[])
    return bot


def make_user(uid: int = 1, username: str | None = None, is_bot: bool = False):
    return SimpleNamespace(id=uid, username=username, is_bot=is_bot)


def make_chat(cid: int = -100123, ctype: str = "supergroup", title: str = "Test"):
    return SimpleNamespace(id=cid, type=ctype, title=title)


def make_msg(
    text: str = "",
    *,
    message_id: int = 10,
    caption: str | None = None,
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
