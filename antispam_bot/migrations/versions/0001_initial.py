"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-06-14

Uses ``CREATE TABLE IF NOT EXISTS`` so it is also safe to run against databases
created by pre-Alembic releases (they get adopted in place, then later migrations
apply normally).
"""

from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

_TABLES = """
CREATE TABLE IF NOT EXISTS users (
    chat_id     INTEGER NOT NULL,
    user_id     INTEGER NOT NULL,
    username    TEXT,
    status      TEXT    NOT NULL DEFAULT 'untrusted',
    clean_count INTEGER NOT NULL DEFAULT 0,
    first_seen  REAL    NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
CREATE TABLE IF NOT EXISTS bans (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id      INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    username     TEXT,
    action       TEXT,
    reason       TEXT,
    confidence   REAL,
    model        TEXT,
    message_text TEXT,
    ts           REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bans_ts ON bans(ts);
CREATE TABLE IF NOT EXISTS stats (
    name  TEXT PRIMARY KEY,
    value INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS whitelist (
    user_id  INTEGER,
    username TEXT,
    added_ts REAL NOT NULL
);
"""


def upgrade() -> None:
    for statement in filter(None, (s.strip() for s in _TABLES.split(";"))):
        op.execute(statement)


def downgrade() -> None:
    for table in ("whitelist", "settings", "stats", "bans", "users"):
        op.execute(f"DROP TABLE IF EXISTS {table}")
