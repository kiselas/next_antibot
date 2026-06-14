"""Точка входа: сборка приложения PTB и запуск long polling."""
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

from . import handlers
from .config import Config
from .llm import LLMClient
from .runtime_settings import Settings
from .storage import Storage

log = logging.getLogger(__name__)

_COMMANDS = [
    BotCommand("stats", "Статистика модерации"),
    BotCommand("recent", "Последние действия"),
    BotCommand("test", "Проверить текст классификатором"),
    BotCommand("config", "Текущие параметры"),
    BotCommand("set", "Изменить параметр"),
    BotCommand("unban", "Снять бан с user_id"),
    BotCommand("allow", "Добавить в белый список"),
    BotCommand("unallow", "Убрать из белого списка"),
    BotCommand("whitelist", "Показать белый список"),
    BotCommand("resetstats", "Обнулить статистику"),
    BotCommand("help", "Справка"),
]


def _heartbeat_path(config: Config) -> str:
    return os.path.join(os.path.dirname(config.db_path) or ".", "heartbeat")


async def _heartbeat(context: ContextTypes.DEFAULT_TYPE) -> None:
    path = _heartbeat_path(context.application.bot_data["config"])
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(time.time()))
    except OSError as exc:
        log.debug("Не удалось обновить heartbeat: %s", exc)


async def _post_init(app: Application) -> None:
    config: Config = app.bot_data["config"]

    storage = Storage(config.db_path)
    await storage.connect()

    settings = Settings(storage, config)
    await settings.load()

    llm = LLMClient(config)

    app.bot_data.update(storage=storage, settings=settings, llm=llm)
    app.bot_data["started_at"] = time.time()
    app.bot_data["llm_error_streak"] = 0
    app.bot_data["llm_alerted"] = False

    if app.job_queue is not None:
        app.job_queue.run_repeating(_heartbeat, interval=60, first=5)

    try:
        await app.bot.set_my_commands(_COMMANDS)
    except Exception as exc:  # не критично
        log.warning("Не удалось установить меню команд: %s", exc)

    me = await app.bot.get_me()
    if not config.allowed_chat_ids:
        log.warning(
            "ALLOWED_CHAT_IDS пуст — бот будет работать в ЛЮБОМ чате. "
            "Укажите разрешённые чаты, чтобы не расходовать квоту OpenRouter."
        )
    log.info("Бот @%s запущен. Админы: %s", me.username, config.admin_usernames or "—")


async def _post_shutdown(app: Application) -> None:
    storage: Storage | None = app.bot_data.get("storage")
    llm: LLMClient | None = app.bot_data.get("llm")
    if storage is not None:
        await storage.close()
    if llm is not None:
        await llm.close()


def main() -> None:
    config = Config()  # бросит ошибку, если нет обязательных переменных
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

    # Админ-команды — только в личке.
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", handlers.cmd_start, filters=private))
    app.add_handler(CommandHandler("help", handlers.cmd_help, filters=private))
    app.add_handler(CommandHandler("stats", handlers.cmd_stats, filters=private))
    app.add_handler(CommandHandler("recent", handlers.cmd_recent, filters=private))
    app.add_handler(CommandHandler("test", handlers.cmd_test, filters=private))
    app.add_handler(CommandHandler("config", handlers.cmd_config, filters=private))
    app.add_handler(CommandHandler("set", handlers.cmd_set, filters=private))
    app.add_handler(CommandHandler("unban", handlers.cmd_unban, filters=private))
    app.add_handler(CommandHandler("allow", handlers.cmd_allow, filters=private))
    app.add_handler(CommandHandler("unallow", handlers.cmd_unallow, filters=private))
    app.add_handler(CommandHandler("whitelist", handlers.cmd_whitelist, filters=private))
    app.add_handler(CommandHandler("resetstats", handlers.cmd_resetstats, filters=private))

    # Кнопки в отчётах админу (Разбанить/Подтвердить/Забанить).
    app.add_handler(CallbackQueryHandler(handlers.handle_callback))

    # Присутствие бота в чатах и учёт участников.
    app.add_handler(
        ChatMemberHandler(handlers.handle_my_chat_member, ChatMemberHandler.MY_CHAT_MEMBER)
    )
    app.add_handler(
        ChatMemberHandler(handlers.handle_chat_member, ChatMemberHandler.CHAT_MEMBER)
    )

    # Модерация текстов и подписей в группах (команды исключаем).
    group_msgs = (
        filters.ChatType.GROUPS
        & (filters.TEXT | filters.CAPTION)
        & ~filters.COMMAND
    )
    app.add_handler(MessageHandler(group_msgs, handlers.handle_message))

    log.info("Запуск long polling…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
