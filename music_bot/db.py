"""SQLite database — job tracking."""

import logging
from typing import Any

import aiosqlite

log = logging.getLogger("music_bot.db")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    download_url TEXT DEFAULT '',
    source_name TEXT DEFAULT '',
    title TEXT DEFAULT '',
    artist TEXT DEFAULT '',
    duration_sec INTEGER DEFAULT 0,
    status TEXT DEFAULT 'new',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


class DB:
    def __init__(self, path: str):
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(CREATE_TABLE)
            await conn.commit()
        log.info(f"[DB] Initialized at {self.path}")

    async def create_job(self, user_id: int, chat_id: int, source_url: str) -> int:
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute(
                "INSERT INTO jobs (user_id, chat_id, source_url) VALUES (?, ?, ?)",
                (user_id, chat_id, source_url),
            )
            await conn.commit()
            return int(cur.lastrowid)

    async def update_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        parts: list[str] = []
        values: list[Any] = []
        for k, v in fields.items():
            parts.append(f"{k} = ?")
            values.append(v)
        parts.append("updated_at = CURRENT_TIMESTAMP")
        values.append(job_id)
        sql = f"UPDATE jobs SET {', '.join(parts)} WHERE id = ?"
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(sql, values)
            await conn.commit()

    async def get_job(self, job_id: int) -> dict[str, Any] | None:
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_recent(self, limit: int = 10) -> list[dict[str, Any]]:
        async with aiosqlite.connect(self.path) as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                "SELECT id, user_id, status, title FROM jobs ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]
