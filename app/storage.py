"""Хранилище состояния на SQLite (aiosqlite).

Таблицы:
  users     — статус доверия участников (untrusted/trusted), счётчик чистых сообщений;
  bans      — журнал действий модерации (для контроля и статистики);
  stats     — кумулятивные счётчики событий;
  settings  — динамические параметры, изменяемые на лету командой /set;
  whitelist — пользователи, которых модерация всегда пропускает.
"""
from __future__ import annotations

import time

import aiosqlite

SCHEMA = """
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


class Storage:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    @property
    def _db(self) -> aiosqlite.Connection:
        assert self.conn is not None, "Storage не подключён (вызовите connect())"
        return self.conn

    # ---- users ----
    async def get_user(self, chat_id: int, user_id: int) -> aiosqlite.Row | None:
        cur = await self._db.execute(
            "SELECT * FROM users WHERE chat_id=? AND user_id=?", (chat_id, user_id)
        )
        return await cur.fetchone()

    async def ensure_user(
        self, chat_id: int, user_id: int, username: str | None
    ) -> aiosqlite.Row:
        await self._db.execute(
            "INSERT INTO users(chat_id, user_id, username, status, clean_count, first_seen) "
            "VALUES(?, ?, ?, 'untrusted', 0, ?) "
            "ON CONFLICT(chat_id, user_id) DO UPDATE SET username=excluded.username",
            (chat_id, user_id, username, time.time()),
        )
        await self._db.commit()
        row = await self.get_user(chat_id, user_id)
        assert row is not None
        return row

    async def set_status(self, chat_id: int, user_id: int, status: str) -> None:
        await self._db.execute(
            "UPDATE users SET status=? WHERE chat_id=? AND user_id=?",
            (status, chat_id, user_id),
        )
        await self._db.commit()

    async def increment_clean(self, chat_id: int, user_id: int) -> int:
        await self._db.execute(
            "UPDATE users SET clean_count = clean_count + 1 WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        )
        await self._db.commit()
        cur = await self._db.execute(
            "SELECT clean_count FROM users WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        )
        row = await cur.fetchone()
        return int(row["clean_count"]) if row else 0

    async def user_counts(self) -> dict[str, int]:
        cur = await self._db.execute(
            "SELECT status, COUNT(*) AS c FROM users GROUP BY status"
        )
        rows = await cur.fetchall()
        counts = {r["status"]: int(r["c"]) for r in rows}
        counts["total"] = sum(counts.values())
        return counts

    # ---- bans / журнал действий ----
    async def record_ban(
        self,
        chat_id: int,
        user_id: int,
        username: str | None,
        action: str,
        reason: str,
        confidence: float,
        model: str | None,
        message_text: str,
    ) -> None:
        await self._db.execute(
            "INSERT INTO bans(chat_id, user_id, username, action, reason, confidence, model, message_text, ts) "
            "VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (chat_id, user_id, username, action, reason, confidence, model, message_text, time.time()),
        )
        await self._db.commit()

    async def bans_since(self, ts: float) -> int:
        cur = await self._db.execute("SELECT COUNT(*) AS c FROM bans WHERE ts>=?", (ts,))
        row = await cur.fetchone()
        return int(row["c"]) if row else 0

    async def recent_bans(self, limit: int = 10) -> list[aiosqlite.Row]:
        cur = await self._db.execute(
            "SELECT * FROM bans ORDER BY ts DESC LIMIT ?", (limit,)
        )
        return list(await cur.fetchall())

    async def last_ban_chat(self, user_id: int) -> int | None:
        cur = await self._db.execute(
            "SELECT chat_id FROM bans WHERE user_id=? ORDER BY ts DESC LIMIT 1", (user_id,)
        )
        row = await cur.fetchone()
        return int(row["chat_id"]) if row else None

    # ---- stats ----
    async def incr_stat(self, name: str, by: int = 1) -> None:
        await self._db.execute(
            "INSERT INTO stats(name, value) VALUES(?, ?) "
            "ON CONFLICT(name) DO UPDATE SET value = value + ?",
            (name, by, by),
        )
        await self._db.commit()

    async def all_stats(self) -> dict[str, int]:
        cur = await self._db.execute("SELECT name, value FROM stats")
        return {r["name"]: int(r["value"]) for r in await cur.fetchall()}

    async def clear_stats(self) -> None:
        await self._db.execute("DELETE FROM stats")
        await self._db.commit()

    # ---- settings ----
    async def get_setting(self, key: str) -> str | None:
        cur = await self._db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self._db.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        await self._db.commit()

    async def all_settings(self) -> dict[str, str]:
        cur = await self._db.execute("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in await cur.fetchall()}

    # ---- whitelist ----
    async def is_whitelisted(self, user_id: int, username: str | None) -> bool:
        uname = username.lower() if username else None
        cur = await self._db.execute(
            "SELECT 1 FROM whitelist WHERE user_id=? OR (username IS NOT NULL AND LOWER(username)=?) LIMIT 1",
            (user_id, uname),
        )
        return await cur.fetchone() is not None

    async def add_whitelist(self, user_id: int | None, username: str | None) -> bool:
        uname = username.lstrip("@") if username else None
        # Не дублируем существующую запись.
        if user_id is not None:
            cur = await self._db.execute(
                "SELECT 1 FROM whitelist WHERE user_id=? LIMIT 1", (user_id,)
            )
            if await cur.fetchone():
                return False
        elif uname is not None:
            cur = await self._db.execute(
                "SELECT 1 FROM whitelist WHERE LOWER(username)=? LIMIT 1", (uname.lower(),)
            )
            if await cur.fetchone():
                return False
        await self._db.execute(
            "INSERT INTO whitelist(user_id, username, added_ts) VALUES(?, ?, ?)",
            (user_id, uname, time.time()),
        )
        await self._db.commit()
        return True

    async def remove_whitelist(
        self, user_id: int | None, username: str | None
    ) -> int:
        if user_id is not None:
            cur = await self._db.execute(
                "DELETE FROM whitelist WHERE user_id=?", (user_id,)
            )
        elif username is not None:
            cur = await self._db.execute(
                "DELETE FROM whitelist WHERE LOWER(username)=?",
                (username.lstrip("@").lower(),),
            )
        else:
            return 0
        await self._db.commit()
        return cur.rowcount

    async def list_whitelist(self) -> list[aiosqlite.Row]:
        cur = await self._db.execute(
            "SELECT user_id, username, added_ts FROM whitelist ORDER BY added_ts DESC"
        )
        return list(await cur.fetchall())
