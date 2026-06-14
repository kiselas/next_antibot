from types import SimpleNamespace
from unittest.mock import AsyncMock

import antispam_bot.main as m
from antispam_bot.config import Config


def test_main_registers_handlers(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123:ABC")
    captured = {}

    def fake_poll(self, *a, **k):
        captured["handlers"] = sum(len(v) for v in self.handlers.values())

    monkeypatch.setattr(m.Application, "run_polling", fake_poll)
    m.main()
    assert captured["handlers"] >= 16


async def test_post_init_and_shutdown(tmp_path):
    cfg = Config(_env_file=None).model_copy(
        update={
            "db_path": str(tmp_path / "b.db"),
            "classifier_backend": "heuristic",
            "allowed_chat_ids": [],
        }
    )
    bot = AsyncMock()
    bot.get_me = AsyncMock(return_value=SimpleNamespace(username="x"))
    bot.set_my_commands = AsyncMock()
    jobs = []
    jq = SimpleNamespace(run_repeating=lambda *a, **k: jobs.append(a))
    app = SimpleNamespace(bot_data={"config": cfg}, job_queue=jq, bot=bot)

    await m._post_init(app)
    assert app.bot_data["classifier"].name == "heuristic"
    assert jobs  # heartbeat scheduled

    await m._heartbeat(SimpleNamespace(application=app))
    assert (tmp_path / "heartbeat").exists()

    await m._post_shutdown(app)


def test_heartbeat_path():
    cfg = Config(_env_file=None).model_copy(update={"db_path": "/data/bot.db"})
    assert m._heartbeat_path(cfg).replace("\\", "/") == "/data/heartbeat"


async def test_heartbeat_write_failure():
    cfg = Config(_env_file=None).model_copy(update={"db_path": "/no_such_dir_xyz/sub/bot.db"})
    app = SimpleNamespace(bot_data={"config": cfg})
    await m._heartbeat(SimpleNamespace(application=app))  # OSError swallowed


async def test_post_init_set_commands_failure(tmp_path):
    cfg = Config(_env_file=None).model_copy(
        update={"db_path": str(tmp_path / "b.db"), "classifier_backend": "heuristic"}
    )
    bot = AsyncMock()
    bot.get_me = AsyncMock(return_value=SimpleNamespace(username="x"))
    bot.set_my_commands = AsyncMock(side_effect=Exception("nope"))
    app = SimpleNamespace(
        bot_data={"config": cfg},
        job_queue=SimpleNamespace(run_repeating=lambda *a, **k: None),
        bot=bot,
    )
    await m._post_init(app)  # set_my_commands error swallowed
    await m._post_shutdown(app)
