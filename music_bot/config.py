"""Configuration — reads environment variables."""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _bool_env(key: str, default: bool = True) -> bool:
    val = os.getenv(key, "").strip()
    if not val:
        return default
    return val not in ("0", "false", "False", "no")


def _int_env(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "").strip() or default)
    except (ValueError, TypeError):
        return default


def _parse_admin_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


@dataclass
class Settings:
    bot_token: str
    admin_ids: set[int] = field(default_factory=set)
    max_duration_min: int = 15
    max_file_mb: int = 35
    # Telegram caps bot uploads at 50 MB; a higher value here only delays the
    # failure to send time.
    max_video_mb: int = 50
    db_path: str = "./music_bot.db"
    bot_tz: str = "Asia/Tehran"
    ytdl_concurrency: int = 4
    cookies_file: str = ""
    yt_proxy: str = ""
    enable_avaland: bool = True
    enable_audius: bool = True
    enable_piped: bool = True
    enable_archive: bool = True


def load_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN is missing. Put it in .env or environment variables.")

    return Settings(
        bot_token=bot_token,
        admin_ids=_parse_admin_ids(os.getenv("ADMIN_IDS", "")),
        max_duration_min=_int_env("MAX_DURATION_MIN", 15),
        max_file_mb=_int_env("MAX_FILE_MB", 35),
        max_video_mb=_int_env("MAX_VIDEO_MB", 50),
        db_path=os.getenv("DB_PATH", "./music_bot.db").strip(),
        bot_tz=os.getenv("BOT_TZ", "Asia/Tehran").strip() or "Asia/Tehran",
        ytdl_concurrency=max(1, _int_env("YTDL_CONCURRENCY", 4)),
        cookies_file=os.getenv("COOKIES_FILE", "").strip(),
        yt_proxy=os.getenv("YT_PROXY", "").strip(),
        enable_avaland=_bool_env("ENABLE_AVALAND", True),
        enable_audius=_bool_env("ENABLE_AUDIUS", True),
        enable_piped=_bool_env("ENABLE_PIPED", True),
        enable_archive=_bool_env("ENABLE_ARCHIVE", True),
    )
