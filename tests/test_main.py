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


def _fake_app(cfg, *, set_commands=None):
    bot = AsyncMock()
    bot.get_me = AsyncMock(return_value=SimpleNamespace(username="x"))
    bot.set_my_commands = set_commands or AsyncMock()
    return SimpleNamespace(
        bot_data={"config": cfg},
        job_queue=SimpleNamespace(run_repeating=lambda *a, **k: None),
        bot=bot,
    )


def _heuristic_cfg(tmp_path):
    return Config(_env_file=None).model_copy(
        update={"db_path": str(tmp_path / "b.db"), "classifier_backend": "heuristic"}
    )


async def test_post_init_and_shutdown(tmp_path):
    app = _fake_app(_heuristic_cfg(tmp_path))
    await m._post_init(app)
    assert app.bot_data["core"].classifier.name == "heuristic"
    await m._heartbeat(SimpleNamespace(application=app))
    assert (tmp_path / "heartbeat").exists()
    await m._post_shutdown(app)


async def test_post_init_set_commands_failure(tmp_path):
    app = _fake_app(_heuristic_cfg(tmp_path), set_commands=AsyncMock(side_effect=Exception("nope")))
    await m._post_init(app)  # error swallowed
    assert "core" in app.bot_data
    await m._post_shutdown(app)


def test_heartbeat_path():
    cfg = Config(_env_file=None).model_copy(update={"db_path": "/data/bot.db"})
    assert m._heartbeat_path(cfg).replace("\\", "/") == "/data/heartbeat"


async def test_heartbeat_write_failure():
    cfg = Config(_env_file=None).model_copy(update={"db_path": "/no_such_dir_xyz/sub/bot.db"})
    app = SimpleNamespace(bot_data={"config": cfg})
    await m._heartbeat(SimpleNamespace(application=app))  # OSError swallowed
