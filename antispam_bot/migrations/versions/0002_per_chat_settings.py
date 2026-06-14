"""per-chat settings

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-14

Adds a ``chat_id`` dimension to the ``settings`` table so each chat can override
the global defaults. Existing rows become the global defaults (chat_id = 0).
"""

from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# Global defaults live under this sentinel chat id.
GLOBAL = 0


def upgrade() -> None:
    op.execute(
        "CREATE TABLE settings_new ("
        "chat_id INTEGER NOT NULL DEFAULT 0, key TEXT NOT NULL, value TEXT NOT NULL, "
        "PRIMARY KEY (chat_id, key))"
    )
    op.execute(
        f"INSERT INTO settings_new (chat_id, key, value) SELECT {GLOBAL}, key, value FROM settings"
    )
    op.execute("DROP TABLE settings")
    op.execute("ALTER TABLE settings_new RENAME TO settings")


def downgrade() -> None:
    op.execute("CREATE TABLE settings_old (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    op.execute(
        f"INSERT OR REPLACE INTO settings_old (key, value) "
        f"SELECT key, value FROM settings WHERE chat_id={GLOBAL}"
    )
    op.execute("DROP TABLE settings")
    op.execute("ALTER TABLE settings_old RENAME TO settings")
