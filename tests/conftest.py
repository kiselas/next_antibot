"""Shared fixtures, fakes and data builders for tests."""

from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import pytest

os.environ.setdefault("BOT_TOKEN", "123:TEST")

from antispam_bot.classifiers import Classifier  # noqa: E402
from antispam_bot.classifiers.base import (  # noqa: E402
    ClassificationContext,
    ClassifyResult,
    Verdict,
)
from antispam_bot.config import Config  # noqa: E402
from antispam_bot.core import Core  # noqa: E402
from antispam_bot.db import run_migrations  # noqa: E402
from antispam_bot.platform import (  # noqa: E402
    BotMembership,
    BotPlatform,
    CallbackAction,
    CommandRequest,
    IncomingMessage,
    MemberUpdate,
    User,
)
from antispam_bot.runtime_settings import Settings  # noqa: E402
from antispam_bot.storage import Storage  # noqa: E402

# --- canned classifier results ---
SPAM = ClassifyResult(Verdict(is_spam=True, confidence=0.95, reason="ad"), "m", None)
CLEAN = ClassifyResult(Verdict(is_spam=False, confidence=0.1, reason="ok"), "m", None)
ERROR = ClassifyResult(None, None, "out_of_credits")


class FakeClassifier(Classifier):
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


class FakePlatform(BotPlatform):
    """Records outbound actions via ``self.mock``; returns ``admin_ids``."""

    def __init__(self) -> None:
        self.mock = AsyncMock()
        self.admin_ids: set[int] = set()

    async def delete_message(self, chat_id, message_id):
        await self.mock.delete_message(chat_id, message_id)

    async def ban_user(self, chat_id, user_id):
        await self.mock.ban_user(chat_id, user_id)

    async def mute_user(self, chat_id, user_id):
        await self.mock.mute_user(chat_id, user_id)

    async def unban_user(self, chat_id, user_id):
        await self.mock.unban_user(chat_id, user_id)

    async def send_message(self, chat_id, text, *, buttons=None):
        await self.mock.send_message(chat_id, text, buttons=buttons)

    async def edit_message(self, chat_id, message_id, text):
        await self.mock.edit_message(chat_id, message_id, text)

    async def answer_callback(self, callback_id, text=None, *, alert=False):
        await self.mock.answer_callback(callback_id, text, alert=alert)

    async def chat_admin_ids(self, chat_id):
        await self.mock.chat_admin_ids(chat_id)
        return set(self.admin_ids)

    async def leave_chat(self, chat_id):
        await self.mock.leave_chat(chat_id)


# --- core fixtures ---
@pytest.fixture
def config():
    return Config(_env_file=None)


@pytest.fixture(scope="session")
def _template_db(tmp_path_factory):
    """Migrate a template DB once per session; tests clone its schema into RAM."""
    path = tmp_path_factory.mktemp("tmpl") / "tmpl.db"
    run_migrations(str(path))
    return str(path)


@pytest.fixture
async def storage(_template_db):
    # Each test gets an isolated in-memory DB; the migrated schema is cloned in via
    # SQLite's backup API (no disk, no cross-test state, schema stays Alembic-owned).
    st = Storage(":memory:")
    await st.connect()
    src = await aiosqlite.connect(_template_db)
    await src.backup(st.conn)
    await src.close()
    yield st
    await st.close()


@pytest.fixture
async def settings(storage, config):
    s = Settings(storage, config)
    await s.load()
    return s


@pytest.fixture
def platform():
    return FakePlatform()


@pytest.fixture
def make_core(config, storage, settings, platform):
    """Factory: ``make_core(fake(SPAM), admin_user_ids=[1])``."""

    def _make(classifier=None, **cfg_over):
        cfg = config.model_copy(update=cfg_over) if cfg_over else config
        return Core(cfg, storage, settings, classifier or fake(), platform)

    return _make


# --- event builders (normalized platform events) ---
def msg_event(
    text="hi",
    *,
    uid=50,
    username=None,
    is_bot=False,
    chat_id=-100123,
    is_group=True,
    is_automatic=False,
    has_links=False,
    has_mentions=False,
    is_forward=False,
    message_id=10,
):
    return IncomingMessage(
        chat_id=chat_id,
        message_id=message_id,
        user=User(uid, username, is_bot),
        text=text,
        is_group=is_group,
        is_automatic=is_automatic,
        has_links=has_links,
        has_mentions=has_mentions,
        is_forward=is_forward,
        chat_title="Test",
    )


def member_event(*, uid=7, username=None, is_bot=False, joined=True, chat_id=-100123):
    return MemberUpdate(chat_id=chat_id, user=User(uid, username, is_bot), joined=joined)


def membership_event(*, present=True, chat_id=-100123):
    return BotMembership(chat_id=chat_id, present=present, chat_title="Test")


def callback_event(data, *, uid=1, username=None, chat_id=-100999, message_id=5, text="report"):
    return CallbackAction(
        callback_id="cb1",
        data=data,
        user=User(uid, username),
        chat_id=chat_id,
        message_id=message_id,
        message_text=text,
    )


def cmd_req(*, uid=1, username="admin", args=None, chat_id=777):
    return CommandRequest(chat_id=chat_id, user=User(uid, username), args=args or [])


# --- Telegram object doubles (for adapter tests only) ---
def make_bot() -> AsyncMock:
    b = AsyncMock()
    b.get_chat_administrators = AsyncMock(return_value=[])
    return b


def make_user(uid=1, username=None, is_bot=False):
    return SimpleNamespace(id=uid, username=username, is_bot=is_bot)


def make_chat(cid=-100123, ctype="supergroup", title="Test"):
    return SimpleNamespace(id=cid, type=ctype, title=title)


def make_msg(
    text="",
    *,
    message_id=10,
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


def tg_context(core, *, args=None):
    """A PTB-like context whose bot_data holds the core (used by adapter handlers)."""
    app = SimpleNamespace(bot_data={"core": core, "config": core.config})
    return SimpleNamespace(application=app, bot=None, args=args or [], _time=time)
