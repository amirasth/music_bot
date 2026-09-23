"""Shared aiohttp session — one connection pool for the whole process.

Creating a ClientSession per request forces a fresh TCP+TLS handshake every
time; source search fans out to 6 remote hosts per query, so a shared pool
removes most of that latency.
"""

import logging

import aiohttp

log = logging.getLogger("music_bot.http")

_session: aiohttp.ClientSession | None = None


def get_session() -> aiohttp.ClientSession:
    """Return the process-wide session, creating it on first use."""
    global _session
    if _session is None or _session.closed:
        connector = aiohttp.TCPConnector(limit=32, ttl_dns_cache=300)
        _session = aiohttp.ClientSession(
            connector=connector,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=aiohttp.ClientTimeout(total=20, connect=8),
        )
    return _session


async def close_session() -> None:
    """Close the shared session on shutdown."""
    global _session
    if _session and not _session.closed:
        await _session.close()
    _session = None
