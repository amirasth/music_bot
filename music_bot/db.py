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

CREATE_DAILY_LIMITS = """
CREATE TABLE IF NOT EXISTS daily_limits (
    user_id INTEGER NOT NULL,
    download_date TEXT NOT NULL,
    video_count INTEGER DEFAULT 0,
    PRIMARY KEY (user_id, download_date)
)
"""


class DB:
    def __init__(self, path: str):
        self.path = path

    async def init(self) -> None:
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(CREATE_TABLE)
            await conn.execute(CREATE_DAILY_LIMITS)
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

    async def get_video_count_today(self, user_id: int) -> int:
        """Get how many videos/clips a user downloaded today."""
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute(
                "SELECT video_count FROM daily_limits WHERE user_id = ? AND download_date = ?",
                (user_id, today),
            )
            row = await cur.fetchone()
            return int(row[0]) if row else 0

    async def increment_video_count(self, user_id: int) -> int:
        """Increment and return today's video count for a user."""
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                """INSERT INTO daily_limits (user_id, download_date, video_count)
                   VALUES (?, ?, 1)
                   ON CONFLICT(user_id, download_date)
                   DO UPDATE SET video_count = video_count + 1""",
                (user_id, today),
            )
            await conn.commit()
        return await self.get_video_count_today(user_id)

    async def get_stats(self) -> dict[str, Any]:
        """Get bot usage statistics."""
        from datetime import datetime

        today = datetime.now().strftime("%Y-%m-%d")
        async with aiosqlite.connect(self.path) as conn:
            # Total jobs
            cur = await conn.execute("SELECT COUNT(*) FROM jobs")
            total_jobs = (await cur.fetchone())[0]

            # Today's jobs
            cur = await conn.execute(
                "SELECT COUNT(*) FROM jobs WHERE created_at >= ?", (today,)
            )
            today_jobs = (await cur.fetchone())[0]

            # Unique users total
            cur = await conn.execute("SELECT COUNT(DISTINCT user_id) FROM jobs")
            total_users = (await cur.fetchone())[0]

            # Today's unique users
            cur = await conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM jobs WHERE created_at >= ?", (today,)
            )
            today_users = (await cur.fetchone())[0]

            # Video downloads today per user
            cur = await conn.execute(
                "SELECT user_id, video_count FROM daily_limits WHERE download_date = ?",
                (today,),
            )
            video_users = await cur.fetchall()

            # Jobs per user (all time)
            cur = await conn.execute(
                """SELECT user_id, COUNT(*) as cnt, status
                   FROM jobs GROUP BY user_id ORDER BY cnt DESC LIMIT 20"""
            )
            user_jobs = await cur.fetchall()

            return {
                "total_jobs": total_jobs,
                "today_jobs": today_jobs,
                "total_users": total_users,
                "today_users": today_users,
                "video_users": [(r[0], r[1]) for r in video_users],
                "user_jobs": [(r[0], r[1], r[2]) for r in user_jobs],
            }
