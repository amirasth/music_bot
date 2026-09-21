"""Utility functions — Persian numbers, sanitization, fuzzy matching."""

import re
from difflib import SequenceMatcher
from typing import Any


def to_persian(value: Any) -> str:
    """Convert digits in a value to Persian/Arabic numerals."""
    s = str(value)
    en = "0123456789"
    fa = "۰۱۲۳۴۵۶۷۸۹"
    return s.translate(str.maketrans(en, fa))


def sanitize_filename(title: str | None, limit: int = 60) -> str:
    """Create a filesystem-safe filename from a title."""
    base = (title or "music").strip()
    base = re.sub(r"[^\w\s\-&()]+", "", base)
    base = re.sub(r"\s+", " ", base).strip()
    return (base[:limit] or "music").strip()


def clean_title(title: str | None) -> str:
    """Remove common suffixes like (Official Video), [Audio], etc."""
    if not title:
        return "music"
    t = title
    t = re.sub(
        r"\s*[\(\[\{][^\)\]\}]*\b(official|video|audio|lyric|lyrics|hd|4k)\b[^\)\]\}]*[\)\]\}]",
        " ",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\s+", " ", t).strip()
    return t[:60] or "music"


def fuzzy_ratio(a: str, b: str) -> float:
    """Calculate similarity ratio between two strings (0.0 to 1.0)."""
    a_low = a.lower().strip()
    b_low = b.lower().strip()
    return SequenceMatcher(None, a_low, b_low).ratio()


def estimate_size_mb(duration_sec: int, kbps: int) -> str:
    """Estimate file size in MB for a given duration and bitrate."""
    if duration_sec <= 0:
        return "؟"
    mb = (kbps * duration_sec) / 8 / 1024
    return f"{mb:.1f} MB"


def extract_twitter_status_id(url: str) -> str | None:
    """Extract tweet status ID from a Twitter/X URL."""
    m = re.match(
        r"^https?://(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/\w+/status/(\d+)",
        url,
        flags=re.IGNORECASE,
    )
    return m.group(1) if m else None
