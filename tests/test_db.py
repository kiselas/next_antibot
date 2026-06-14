import sqlite3
from pathlib import Path

from alembic import command
from alembic.config import Config

from antispam_bot import db as dbmod
from antispam_bot.db import run_migrations


def test_run_migrations_creates_schema(tmp_path):
    db = tmp_path / "nested" / "sub" / "bot.db"  # parent dirs don't exist -> created
    run_migrations(str(db))
    assert db.exists()
    con = sqlite3.connect(str(db))
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(settings)")}
        assert {"chat_id", "key", "value"} <= cols  # per-chat schema applied
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"users", "bans", "stats", "settings", "whitelist", "alembic_version"} <= tables
    finally:
        con.close()


def test_run_migrations_idempotent(tmp_path):
    db = tmp_path / "bot.db"
    run_migrations(str(db))
    run_migrations(str(db))  # already at head -> no-op
    assert db.exists()


def _cfg(db_path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(dbmod.__file__).parent / "migrations"))
    cfg.attributes["db_path"] = str(db_path)
    return cfg


def test_migration_downgrade_roundtrip(tmp_path):
    db = tmp_path / "bot.db"
    run_migrations(str(db))
    cfg = _cfg(db)
    command.downgrade(cfg, "base")  # exercises 0002 + 0001 downgrades
    command.upgrade(cfg, "head")  # and back up again
    con = sqlite3.connect(str(db))
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(settings)")}
        assert "chat_id" in cols
    finally:
        con.close()
