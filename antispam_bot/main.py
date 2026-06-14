"""Entry point: wire the core to the Telegram adapter and run long polling."""

from __future__ import annotations

import logging
import os
import time

from telegram import BotCommand, Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import telegram_adapter as tg
from .classifiers import build_classifier
from .config import Config
from .core import Core
from .runtime_settings import Settings
from .storage import Storage

log = logging.getLogger(__name__)

# (command, Core method, menu description)
_COMMANDS = [
    ("start", "cmd_start", None),
    ("help", "cmd_help", "Help"),
    ("stats", "cmd_stats", "Moderation statistics"),
    ("recent", "cmd_recent", "Recent actions"),
    ("test", "cmd_test", "Test text with the classifier"),
    ("config", "cmd_config", "Current parameters"),
    ("set", "cmd_set", "Change a parameter"),
    ("unban", "cmd_unban", "Lift a ban by user_id"),
    ("allow", "cmd_allow", "Add to the whitelist"),
    ("unallow", "cmd_unallow", "Remove from the whitelist"),
    ("whitelist", "cmd_whitelist", "Show the whitelist"),
    ("resetstats", "cmd_resetstats", "Reset statistics"),
]


def _heartbeat_path(config: Config) -> str:
    return os.path.join(os.path.dirname(config.db_path) or ".", "heartbeat")


async def _heartbeat(context: ContextTypes.DEFAULT_TYPE) -> None:
    path = _heartbeat_path(context.application.bot_data["config"])
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
    except OSError as exc:
        log.debug("Could not update heartbeat: %s", exc)


async def _post_init(app: Application) -> None:
    config: Config = app.bot_data["config"]

    storage = Storage(config.db_path)
    await storage.connect()
    settings = Settings(storage, config)
    await settings.load()
    classifier = build_classifier(config, settings)

    core = Core(config, storage, settings, classifier, tg.TelegramPlatform(app.bot))
    app.bot_data["core"] = core

    if app.job_queue is not None:
        app.job_queue.run_repeating(_heartbeat, interval=60, first=5)

    try:
        await app.bot.set_my_commands(
            [BotCommand(name, desc) for name, _m, desc in _COMMANDS if desc]
        )
    except Exception as exc:  # non-critical
        log.warning("Could not set the command menu: %s", exc)

    me = await app.bot.get_me()
    if not config.allowed_chat_ids:
        log.warning(
            "ALLOWED_CHAT_IDS is empty — the bot will work in ANY chat. "
            "Set allowed chats to avoid burning your LLM quota."
        )
    log.info(
        "Bot @%s started. Backend: %s. Admins: %s",
        me.username,
        classifier.name,
        config.admin_usernames or "—",
    )


async def _post_shutdown(app: Application) -> None:
    core: Core | None = app.bot_data.get("core")
    if core is not None:
        await core.storage.close()
        await core.classifier.close()


def main() -> None:
    config = Config()  # type: ignore[call-arg]  # values come from env, not args
    logging.basicConfig(
        level=config.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    app = (
        Application.builder()
        .token(config.bot_token)
        .concurrent_updates(True)
        .rate_limiter(AIORateLimiter())
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )
    app.bot_data["config"] = config

    private = filters.ChatType.PRIVATE
    for name, method, _desc in _COMMANDS:
        app.add_handler(CommandHandler(name, tg.make_command(method), filters=private))

    app.add_handler(CallbackQueryHandler(tg.on_callback))
    app.add_handler(ChatMemberHandler(tg.on_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(ChatMemberHandler(tg.on_chat_member, ChatMemberHandler.CHAT_MEMBER))

    group_msgs = filters.ChatType.GROUPS & (filters.TEXT | filters.CAPTION) & ~filters.COMMAND
    app.add_handler(MessageHandler(group_msgs, tg.on_message))

    log.info("Starting long polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
