"""Alembic environment.

The SQLite database path comes from ``config.attributes['db_path']`` when invoked
programmatically (see ``antispam_bot.db.run_migrations``) or from the ``DB_PATH``
environment variable when run via the Alembic CLI.
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _db_path() -> str:
    path = config.attributes.get("db_path") or os.environ.get("DB_PATH")
    if not path:
        raise RuntimeError("No database path: set DB_PATH or pass attributes['db_path'].")
    return str(path)


def _url() -> URL:
    return URL.create("sqlite", database=_db_path())


def run_migrations_offline() -> None:
    context.configure(url=_url().render_as_string(hide_password=False), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_url())
    with connectable.connect() as connection:
        # render_as_batch enables SQLite-friendly table rebuilds for ALTERs.
        context.configure(connection=connection, target_metadata=None, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
