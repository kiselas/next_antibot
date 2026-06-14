"""Database migration runner (Alembic, applied programmatically at startup)."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def run_migrations(db_path: str) -> None:
    """Upgrade the SQLite database at ``db_path`` to the latest schema."""
    parent = Path(db_path).parent
    if parent and not parent.exists():
        parent.mkdir(parents=True, exist_ok=True)
    cfg = Config()
    cfg.set_main_option("script_location", str(_MIGRATIONS_DIR))
    cfg.attributes["db_path"] = db_path
    command.upgrade(cfg, "head")
