"""Music source search — Avaland, Audius, SoundCloud, Piped, YouTube, Archive.org."""

import asyncio
import logging
import re
import time
from typing import Any

from .config import Settings
from .http import get_session
from .utils import fuzzy_ratio

log = logging.getLogger("music_bot.sources")

# Words that indicate a non-original version — penalize these in matching
_REMIX_WORDS = re.compile(
    r"\b(remix|edit|live|cover|acoustic|slowed|reverb|mashup|bootleg|vip|extended|radio\s*edit|dance\s*remix|club\s*mix|instrumental|karaoke|parody)\b",
    re.IGNORECASE,
)


def _is_remix(result_title: str) -> bool:
    """Check if a result title looks like a remix/non-original."""
    return bool(_REMIX_WORDS.search(result_title))


def _match_score(original_title: str, result_title: str) -> float:
    """Score a result — base fuzzy ratio penalized if it's a remix."""
    score = fuzzy_ratio(original_title, result_title)
    if _is_remix(result_title):
        score *= 0.5
        log.info(f"[MATCH] Remix penalty applied: {result_title[:40]} → {score:.2f}")
    return score

# ── Avaland (Persian music sources) ──
try:
    from avaland.manager import SourceManager
    from avaland.sources import Bia2, Navahang, WikiSeda, Nex1

    AVALAND_AVAILABLE = True
except Exception:
    AVALAND_AVAILABLE = False

_SPOTIFY_TRACK_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]{2}/)?track/([A-Za-z0-9]+)", flags=re.IGNORECASE
)

# ── Piped instances ──
# Sorted by observed reliability: the first few carry almost all traffic, and
# the search fan-out is capped at PIPED_MAX_ATTEMPTS so one dead host can't
# stretch a query past its budget.
PIPED_INSTANCES = [
    "pipedapi.kavin.rocks",
    "pipedapi.adminforge.de",
    "pipedapi.reallyaweso.me",
    "api.piped.private.coffee",
    "pipedapi.drgns.space",
]

PIPED_MAX_ATTEMPTS = 3

# Per-source wall-clock budget, enforced inside each source coroutine.
SEARCH_TIMEOUT_SEC = 12.0

# How long the engine will hold the line for a higher-priority source once a
# lower-priority answer is already in hand. Priority is a preference, not a
# guarantee: without this cap a single hung host would make every query as slow
# as the full SEARCH_TIMEOUT_SEC, even when a good answer was ready in 300ms.
TIER_WAIT_SEC = 4.0

# Minimum fuzzy score for a candidate to be considered a real match.
MATCH_THRESHOLD = 0.50

# Identical (title, artist) queries repeat constantly — the same link pasted by
# many users, retries, the "other quality" button. Cache resolved matches.
CACHE_TTL_SEC = 600
_MATCH_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def _cancel_all(tasks: list[asyncio.Task]) -> None:
    """Cancel every unfinished task in the list (idempotent)."""
    for t in tasks:
        if not t.done():
            t.cancel()


async def _run_with_timeout(coro, seconds: float, default: Any) -> Any:
    """Await a source coroutine, returning `default` if it exceeds its budget."""
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except (asyncio.TimeoutError, Exception) as e:
        log.info(f"[SEARCH] Source timed out or failed: {type(e).__name__}")
        return default


# ═══════════════════════════════════════════════════
# URL detection
# ═══════════════════════════════════════════════════


def is_instagram(url: str) -> bool:
    return bool(re.match(r"^https?://(?:www\.)?instagram\.com/", url, flags=re.IGNORECASE))


def is_tiktok(url: str) -> bool:
    return bool(re.match(r"^https?://(?:www\.|vm\.|vt\.)?tiktok\.com/", url, flags=re.IGNORECASE))


def is_spotify(url: str) -> bool:
    return bool(re.match(r"^https?://(?:open\.)?spotify\.com/", url, flags=re.IGNORECASE))


def is_soundcloud(url: str) -> bool:
    return bool(
        re.match(
            r"^https?://(?:www\.|m\.)?soundcloud\.com/[\w\-]+/[\w\-]+",
            url,
            flags=re.IGNORECASE,
        )
    )


def is_twitter(url: str) -> bool:
    return bool(
        re.match(
            r"^https?://(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/",
            url,
            flags=re.IGNORECASE,
        )
    )


def is_youtube(url: str) -> bool:
    return bool(
        re.match(
            r"^https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w\-]+",
            url,
            flags=re.IGNORECASE,
        )
    )


def is_supported_url(url: str) -> bool:
    return (
        is_youtube(url)
        or is_instagram(url)
        or is_tiktok(url)
        or is_spotify(url)
        or is_soundcloud(url)
        or is_twitter(url)
    )


# ═══════════════════════════════════════════════════
# Spotify metadata
# ═══════════════════════════════════════════════════


async def fetch_spotify_meta(url: str) -> dict[str, str] | None:
    """Get track title/artist from Spotify's public embed page (no auth needed)."""
    m = _SPOTIFY_TRACK_RE.search(url)
    if not m:
        return None
    embed_url = f"https://open.spotify.com/embed/track/{m.group(1)}"
    try:
        async with get_session().get(embed_url) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
    except Exception:
        return None

    title = re.search(r'"title":"([^"]+)"', html)
    artist = re.search(r'"artistName":"([^"]+)"', html)
    if not title:
        return None
    return {
        "title": title.group(1),
        "artist": artist.group(1) if artist else "",
    }


async def spotify_preview_url(url: str) -> str | None:
    """Extract the 30s preview MP3 URL from Spotify's embed page."""
    m = _SPOTIFY_TRACK_RE.search(url)
    if not m:
        return None
    embed_url = f"https://open.spotify.com/embed/track/{m.group(1)}"
    try:
        async with get_session().get(embed_url) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
    except Exception:
        return None
    match = re.search(r'"audioPreview":\s*\{"url":"([^"]+)"', html)
    if match:
        return match.group(1).replace("\\u002F", "/")
    return None


# ═══════════════════════════════════════════════════
# Avaland — Persian music
# ═══════════════════════════════════════════════════


async def search_avaland(query: str) -> dict[str, Any] | None:
    """Search Persian music via Avaland (Bia2, Navahang, WikiSeda, Nex1)."""
    if not AVALAND_AVAILABLE:
        log.info("[AVALAND] Not available, skipping")
        return None

    def _work() -> dict[str, Any] | None:
        try:
            manager = SourceManager()
            for src_cls in [Bia2, Navahang, WikiSeda, Nex1]:
                try:
                    manager.register(src_cls)
                except Exception:
                    pass
            try:
                log.info(f"[AVALAND] Searching: {query}")
                results = manager.search(query)
            except (TypeError, Exception) as e:
                log.warning(f"[AVALAND] Source error: {e}, trying without Bia2")
                manager2 = SourceManager()
                for src_cls2 in [Navahang, WikiSeda, Nex1]:
                    try:
                        manager2.register(src_cls2)
                    except Exception:
                        pass
                try:
                    results = manager2.search(query)
                except Exception:
                    return None

            for source_name, search_result in results.items():
                if not search_result or not hasattr(search_result, "musics"):
                    continue
                for mus in search_result.musics:
                    try:
                        dl_url = mus.get_link()
                    except Exception:
                        dl_url = getattr(mus, "url", None) or getattr(
                            mus, "download_url", None
                        )
                    if not dl_url:
                        continue
                    log.info(
                        f"[AVALAND] Found in {source_name}: {mus.full_title} -> {dl_url[:60]}"
                    )
                    return {
                        "url": dl_url,
                        "source": f"Avaland/{source_name}",
                        "title": getattr(mus, "title", "") or "",
                        "artist": getattr(mus, "artist", "") or "",
                        "duration": 0,
                    }
            log.info("[AVALAND] No results found")
            return None
        except Exception as e:
            log.warning(f"[AVALAND] Error: {e}")
            return None

    return await asyncio.to_thread(_work)


# ═══════════════════════════════════════════════════
# Audius — free streaming API
# ═══════════════════════════════════════════════════


async def _get_audius_node() -> str | None:
    """Get a working Audius discovery node."""
    try:
        async with get_session().get("https://api.audius.co") as resp:
            if resp.status == 200:
                data = await resp.json()
                nodes = data.get("data", [])
                if nodes:
                    return nodes[0]
    except Exception:
        pass
    return "https://discovery-provider.audius.co"


async def search_audius(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search Audius for tracks."""
    node = await _get_audius_node()
    if not node:
        return []
    try:
        url = f"{node}/v1/tracks/search"
        params = {"query": query, "limit": str(limit)}
        async with get_session().get(url, params=params) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            results = []
            for t in data.get("data", []):
                track_id = t.get("id", "")
                if not track_id:
                    continue
                results.append(
                    {
                        "id": track_id,
                        "title": t.get("title", ""),
                        "artist": (t.get("user") or {}).get("name", ""),
                        "duration": t.get("duration", 0),
                        "stream_url": f"{node}/v1/tracks/{track_id}/stream",
                    }
                )
            return results
    except Exception as e:
        log.warning(f"[AUDIUS] Search error: {e}")
        return []


# ═══════════════════════════════════════════════════
# SoundCloud (via yt-dlp)
# ═══════════════════════════════════════════════════


async def search_soundcloud(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search SoundCloud via yt-dlp scsearch."""

    def _work() -> list[dict[str, Any]]:
        try:
            from yt_dlp import YoutubeDL  # type: ignore
        except Exception:
            return []
        opts = {
            "quiet": True,
            "noprogress": True,
            "no_warnings": True,
            "skip_download": True,
            "socket_timeout": 8,
        }
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"scsearch{limit}:{query}", download=False)
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for e in info.get("entries") or []:
            if not e:
                continue
            out.append(
                {
                    "id": e.get("id"),
                    "title": e.get("title") or "بدون عنوان",
                    "uploader": e.get("uploader") or "",
                    "duration": int(e.get("duration") or 0),
                    "webpage_url": e.get("webpage_url") or "",
                }
            )
        return out

    return await asyncio.to_thread(_work)


# ═══════════════════════════════════════════════════
# Piped — YouTube proxy
# ═══════════════════════════════════════════════════


async def _piped_try_instance(instance: str, query: str, limit: int) -> list[dict[str, Any]]:
    try:
        url = f"https://{instance}/search"
        params = {"q": query, "filter": "music_songs"}
        async with get_session().get(url, params=params) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            results = []
            for item in (data.get("items") or [])[:limit]:
                if item.get("type") != "stream":
                    continue
                vid = (item.get("url") or "").lstrip("/")
                title = item.get("title") or ""
                if not vid or not title:
                    continue
                duration = item.get("duration", 0)
                results.append(
                    {
                        "video_id": vid,
                        "title": title,
                        "channel": item.get("uploaderName") or "",
                        "duration": int(duration) if duration else 0,
                    }
                )
            return results
    except Exception:
        return []


async def search_piped(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search YouTube via Piped API (bypasses datacenter IP blocks).

    Instances are probed in parallel and the first non-empty result wins, so
    a slow or dead host costs latency only up to PIPED_MAX_ATTEMPTS.
    """
    attempts = [
        asyncio.create_task(_piped_try_instance(inst, query, limit))
        for inst in PIPED_INSTANCES[:PIPED_MAX_ATTEMPTS]
    ]
    try:
        for coro in asyncio.as_completed(attempts):
            results = await coro
            if results:
                log.info(f"[PIPED] Got {len(results)} results")
                return results
    finally:
        _cancel_all(attempts)
        await asyncio.gather(*attempts, return_exceptions=True)
    log.info("[PIPED] All instances failed")
    return []


async def _piped_stream_try(instance: str, video_id: str) -> str | None:
    try:
        url = f"https://{instance}/streams/{video_id}"
        async with get_session().get(url) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            best, best_br = None, 0
            for s in data.get("audioStreams") or []:
                br = s.get("bitrate", 0) or 0
                if br > best_br:
                    best_br, best = br, s
            if best and best.get("url"):
                log.info(f"[PIPED] Got stream from {instance} ({best_br}bps)")
                return best["url"]
    except Exception:
        return None
    return None


async def piped_stream_url(video_id: str) -> str | None:
    """Get a direct audio stream URL from Piped for a YouTube video.

    Races the probe across instances — the resolve step is on the critical
    path of every download, so serial retries here are pure added latency.
    """
    attempts = [
        asyncio.create_task(_piped_stream_try(inst, video_id))
        for inst in PIPED_INSTANCES[:PIPED_MAX_ATTEMPTS]
    ]
    try:
        for coro in asyncio.as_completed(attempts):
            url = await coro
            if url:
                return url
    finally:
        _cancel_all(attempts)
        await asyncio.gather(*attempts, return_exceptions=True)
    return None


# ═══════════════════════════════════════════════════
# YouTube (yt-dlp) — last resort
# ═══════════════════════════════════════════════════


async def search_youtube(query: str, limit: int = 5, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Search YouTube via yt-dlp."""

    def _work() -> list[dict[str, Any]]:
        try:
            from yt_dlp import YoutubeDL  # type: ignore
        except Exception:
            return []
        opts: dict[str, Any] = {
            "quiet": True,
            "noprogress": True,
            "no_warnings": True,
            "skip_download": True,
        }
        opts["extractor_args"] = {"youtube": {"player_client": ["android", "web"]}}
        if settings and settings.cookies_file:
            opts["cookiefile"] = settings.cookies_file
        if settings and settings.yt_proxy:
            opts["proxy"] = settings.yt_proxy
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
        except Exception as e:
            log.warning(f"[YT] Search error: {e}")
            return []
        out: list[dict[str, Any]] = []
        for e in info.get("entries") or []:
            if not e:
                continue
            out.append(
                {
                    "video_id": e.get("id"),
                    "title": e.get("title") or "بدون عنوان",
                    "channel": e.get("uploader") or "",
                    "duration": int(e.get("duration") or 0),
                }
            )
        return out

    return await asyncio.to_thread(_work)


async def find_youtube_match(
    title: str, artist: str, settings: Settings | None = None
) -> dict[str, Any] | None:
    """Search YouTube and return the best fuzzy match."""
    query = f"{artist} {title}".strip() if artist else title
    results = await search_youtube(query, limit=5, settings=settings)
    if not results:
        return None

    best_match = None
    best_score = 0.0
    for r in results:
        r_title = str(r.get("title", ""))
        score = _match_score(title, r_title)
        log.info(f"[YT] Candidate: {r_title[:50]} | fuzzy={score:.2f}")
        if score > best_score:
            best_score = score
            best_match = r

    if best_score >= MATCH_THRESHOLD:
        log.info(f"[YT] Best fuzzy match: {best_match['title'][:50]} (score={best_score:.2f})")
        return best_match

    # No candidate clears the bar: return nothing rather than the first result,
    # which would silently download an unrelated song under the wrong title.
    log.info(f"[YT] No strong match (best={best_score:.2f})")
    return None


# ═══════════════════════════════════════════════════
# Archive.org
# ═══════════════════════════════════════════════════


async def search_archive(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search Archive.org for audio items."""
    try:
        params = {
            "q": f"({query}) AND mediatype:(audio)",
            "output": "json",
            "rows": str(limit),
            "fl[]": "identifier,title",
        }
        async with get_session().get(
            "https://archive.org/advancedsearch.php", params=params
        ) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
            results = []
            for doc in data.get("response", {}).get("docs", []):
                identifier = doc.get("identifier", "")
                if not identifier:
                    continue
                results.append(
                    {
                        "id": identifier,
                        "title": doc.get("title", ""),
                        "artist": "",
                        "duration": 0,
                        "stream_url": f"https://archive.org/download/{identifier}/{identifier}.mp3",
                    }
                )
            return results
    except Exception as e:
        log.warning(f"[ARCHIVE] Search error: {e}")
        return []


# ═══════════════════════════════════════════════════
# Combined: identify track from metadata
# ═══════════════════════════════════════════════════


# ═══════════════════════════════════════════════════
# Combined: find best match across all sources
# ═══════════════════════════════════════════════════


async def find_best_match(
    title: str, artist: str, settings: Settings | None = None
) -> dict[str, Any] | None:
    """Search all sources and return the best match, in priority order.

    Priority: Avaland > Audius > SoundCloud > Piped > YouTube > Archive.org
    Returns {"url", "source", "title", "artist", "duration", "download_url"} or None.
    """
    query = f"{artist} {title}".strip() if artist else title

    # ── Cache: resolved-match lookups are the cheapest possible speedup ──
    cache_key = (title.strip().lower(), artist.strip().lower())
    now = time.monotonic()
    cached = _MATCH_CACHE.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL_SEC:
        log.info(f"[MATCH] Cache hit for {title[:40]!r}")
        return cached[1]
    if len(_MATCH_CACHE) > 500:
        _MATCH_CACHE.clear()

    # ── Search all sources in parallel ──
    async def _avaland() -> dict[str, Any] | None:
        if settings and not settings.enable_avaland:
            return None
        res = await _run_with_timeout(search_avaland(query), SEARCH_TIMEOUT_SEC, None)
        if res and res.get("url"):
            log.info(f"[MATCH] Avaland hit: {res.get('title', '')}")
            return {
                "url": res["url"],
                "download_url": res["url"],
                "source": res.get("source", "Avaland"),
                "title": res.get("title") or title,
                "artist": res.get("artist") or artist,
                "duration": res.get("duration", 0),
            }
        return None

    async def _audius() -> dict[str, Any] | None:
        if settings and not settings.enable_audius:
            return None
        res = await _run_with_timeout(search_audius(query, limit=5), SEARCH_TIMEOUT_SEC, [])
        for r in res or []:
            score = _match_score(title, r.get("title", ""))
            if score >= 0.55 and r.get("stream_url"):
                log.info(f"[MATCH] Audius hit: {r['title'][:50]} (score={score:.2f})")
                return {
                    "url": r["stream_url"],
                    "download_url": r["stream_url"],
                    "source": "Audius",
                    "title": r.get("title") or title,
                    "artist": r.get("artist") or artist,
                    "duration": r.get("duration", 0),
                }
        return None

    async def _soundcloud() -> dict[str, Any] | None:
        res = await _run_with_timeout(search_soundcloud(query, limit=5), SEARCH_TIMEOUT_SEC, [])
        for r in res or []:
            if not r.get("webpage_url"):
                continue
            dur = int(r.get("duration") or 0)
            if 0 < dur < 40:
                log.info(f"[MATCH] SoundCloud skip preview ({dur}s)")
                continue
            score = _match_score(title, r.get("title", ""))
            if score >= 0.50:
                log.info(f"[MATCH] SoundCloud hit: {r.get('title', '')[:50]} (score={score:.2f})")
                return {
                    "url": r["webpage_url"],
                    "download_url": "",
                    "source": "SoundCloud",
                    "title": r.get("title") or title,
                    "artist": r.get("uploader") or artist,
                    "duration": dur,
                }
        return None

    async def _piped() -> dict[str, Any] | None:
        if settings and not settings.enable_piped:
            return None
        res = await _run_with_timeout(search_piped(query, limit=5), SEARCH_TIMEOUT_SEC, [])
        for r in res or []:
            vid = r.get("video_id")
            if not vid:
                continue
            score = _match_score(title, r.get("title", ""))
            if score < 0.50:
                continue
            # Resolve inside the task so the candidate is download-ready the
            # moment this source is considered.
            stream_url = await _run_with_timeout(
                piped_stream_url(vid), SEARCH_TIMEOUT_SEC, None
            )
            if stream_url:
                log.info(f"[MATCH] Piped hit: {r['title'][:50]} (score={score:.2f})")
                return {
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "download_url": stream_url,
                    "source": "Piped",
                    "title": r.get("title") or title,
                    "artist": r.get("channel") or artist,
                    "duration": r.get("duration", 0),
                }
        return None

    async def _youtube() -> dict[str, Any] | None:
        res = await _run_with_timeout(
            find_youtube_match(title, artist, settings=settings), SEARCH_TIMEOUT_SEC, None
        )
        if res and res.get("video_id"):
            log.info(f"[MATCH] YouTube hit: {res.get('title', '')[:50]}")
            return {
                "url": f"https://www.youtube.com/watch?v={res['video_id']}",
                "download_url": "",
                "source": "YouTube",
                "title": res.get("title") or title,
                "artist": res.get("channel") or artist,
                "duration": int(res.get("duration") or 0),
            }
        return None

    async def _archive() -> dict[str, Any] | None:
        if settings and not settings.enable_archive:
            return None
        res = await _run_with_timeout(search_archive(query, limit=3), SEARCH_TIMEOUT_SEC, [])
        for r in res or []:
            score = _match_score(title, r.get("title", ""))
            if score >= 0.50 and r.get("stream_url"):
                log.info(f"[MATCH] Archive hit: {r['title'][:50]} (score={score:.2f})")
                return {
                    "url": r["stream_url"],
                    "download_url": r["stream_url"],
                    "source": "Archive.org",
                    "title": r.get("title") or title,
                    "artist": artist,
                    "duration": 0,
                }
        return None

    # Priority tiers. Sources are launched together (so they all make progress
    # concurrently) but *resolved* tier by tier: the best available answer
    # returns the instant its tier is ready, without waiting on lower tiers.
    tiers: list[list[Any]] = [
        [_avaland],
        [_audius],
        [_soundcloud, _piped],
        [_youtube],
        [_archive],
    ]

    tasks: list[asyncio.Task] = []
    tier_slices: list[list[asyncio.Task]] = []
    for factories in tiers:
        group = [asyncio.create_task(fn()) for fn in factories]
        tier_slices.append(group)
        tasks.extend(group)

    try:
        for idx, group in enumerate(tier_slices):
            done, _pending = await asyncio.wait(group, timeout=TIER_WAIT_SEC)
            for t in done:
                if t.cancelled() or t.exception() is not None:
                    continue
                candidate = t.result()
                if candidate:
                    _MATCH_CACHE[cache_key] = (now, candidate)
                    return candidate
            log.info(f"[MATCH] Tier {idx} produced no match within {TIER_WAIT_SEC}s")
    finally:
        _cancel_all(tasks)
        # Await the cancellations so no "Task exception was never retrieved"
        # noise is emitted for the losing sources.
        await asyncio.gather(*tasks, return_exceptions=True)

    log.info("[MATCH] No match found in any source")
    return None
