"""Music source search — Avaland, Audius, SoundCloud, Piped, YouTube, Archive.org."""

import asyncio
import logging
import re
from typing import Any

import aiohttp

from .config import Settings
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
PIPED_INSTANCES = [
    "pipedapi.kavin.rocks",
    "pipedapi.adminforge.de",
    "pipedapi.reallyaweso.me",
    "api.piped.private.coffee",
    "pipedapi.drgns.space",
]


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
        async with aiohttp.ClientSession() as session:
            async with session.get(
                embed_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
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
        async with aiohttp.ClientSession() as session:
            async with session.get(
                embed_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
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
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.audius.co",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
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

    async def _work() -> list[dict[str, Any]]:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{node}/v1/tracks/search"
                params = {"query": query, "limit": str(limit)}
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    tracks = data.get("data", [])
                    results = []
                    for t in tracks:
                        track_id = t.get("id", "")
                        title = t.get("title", "")
                        user = t.get("user", {})
                        artist = user.get("name", "")
                        duration = t.get("duration", 0)
                        stream_url = f"{node}/v1/tracks/{track_id}/stream"
                        results.append(
                            {
                                "id": track_id,
                                "title": title,
                                "artist": artist,
                                "duration": duration,
                                "stream_url": stream_url,
                            }
                        )
                    return results
        except Exception as e:
            log.warning(f"[AUDIUS] Search error: {e}")
            return []

    return await _work()


async def search_audius_async(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search Audius for tracks (async wrapper)."""
    node = await _get_audius_node()
    if not node:
        return []
    try:
        async with aiohttp.ClientSession() as session:
            url = f"{node}/v1/tracks/search"
            params = {"query": query, "limit": str(limit)}
            async with session.get(
                url,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                tracks = data.get("data", [])
                results = []
                for t in tracks:
                    track_id = t.get("id", "")
                    title = t.get("title", "")
                    user = t.get("user", {})
                    artist = user.get("name", "")
                    duration = t.get("duration", 0)
                    stream_url = f"{node}/v1/tracks/{track_id}/stream"
                    results.append(
                        {
                            "id": track_id,
                            "title": title,
                            "artist": artist,
                            "duration": duration,
                            "stream_url": stream_url,
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


async def search_piped(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search YouTube via Piped API (bypasses datacenter IP blocks)."""

    async def _try_instance(instance: str) -> list[dict[str, Any]]:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://{instance}/search"
                params = {"q": query, "filter": "music_songs"}
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
                    items = data.get("items", []) or []
                    results = []
                    for item in items[:limit]:
                        if item.get("type") != "stream":
                            continue
                        vid = item.get("url", "").lstrip("/")
                        title = item.get("title", "")
                        uploader = item.get("uploaderName", "")
                        duration = item.get("duration", 0)
                        if not vid or not title:
                            continue
                        results.append(
                            {
                                "video_id": vid,
                                "title": title,
                                "channel": uploader,
                                "duration": int(duration) if duration else 0,
                            }
                        )
                    return results
        except Exception:
            return []

    # Try first 2 instances in parallel for speed
    batch1 = await asyncio.gather(
        _try_instance(PIPED_INSTANCES[0]),
        _try_instance(PIPED_INSTANCES[1]),
    )
    for results in batch1:
        if results:
            log.info(f"[PIPED] Got {len(results)} results")
            return results

    # Fallback: try remaining instances sequentially
    for instance in PIPED_INSTANCES[2:]:
        results = await _try_instance(instance)
        if results:
            log.info(f"[PIPED] Got {len(results)} results from {instance}")
            return results
    log.info("[PIPED] All instances failed")
    return []


async def piped_stream_url(video_id: str) -> str | None:
    """Get a direct audio stream URL from Piped for a YouTube video."""
    for instance in PIPED_INSTANCES:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"https://{instance}/streams/{video_id}"
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()
                    audio_streams = data.get("audioStreams") or []
                    # Pick the best audio stream by bitrate
                    best = None
                    best_br = 0
                    for s in audio_streams:
                        br = s.get("bitrate", 0) or 0
                        if br > best_br:
                            best_br = br
                            best = s
                    if best and best.get("url"):
                        log.info(f"[PIPED] Got stream from {instance} ({best_br}bps)")
                        return best["url"]
        except Exception:
            continue
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

    if best_score >= 0.50:
        log.info(f"[YT] Best fuzzy match: {best_match['title'][:50]} (score={best_score:.2f})")
        return best_match

    log.info(f"[YT] No strong match (best={best_score:.2f}), accepting first result")
    return results[0] if results else None


# ═══════════════════════════════════════════════════
# Archive.org
# ═══════════════════════════════════════════════════


async def search_archive(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search Archive.org for audio items."""
    try:
        async with aiohttp.ClientSession() as session:
            params = {
                "q": f"({query}) AND mediatype:(audio)",
                "output": "json",
                "rows": str(limit),
                "fl[]": "identifier,title,item_size",
            }
            async with session.get(
                "https://archive.org/advancedsearch.php",
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                docs = data.get("response", {}).get("docs", [])
                results = []
                for doc in docs:
                    identifier = doc.get("identifier", "")
                    title = doc.get("title", "")
                    if not identifier:
                        continue
                    direct_url = f"https://archive.org/download/{identifier}/{identifier}.mp3"
                    results.append(
                        {
                            "id": identifier,
                            "title": title,
                            "artist": "",
                            "duration": 0,
                            "stream_url": direct_url,
                        }
                    )
                return results
    except Exception as e:
        log.warning(f"[ARCHIVE] Search error: {e}")
        return []


# ═══════════════════════════════════════════════════
# Combined: identify track from metadata
# ═══════════════════════════════════════════════════


async def identify_track(url: str) -> dict[str, Any] | None:
    """Identify the music behind a link via metadata."""
    from .downloader import probe_url

    if is_spotify(url):
        return await fetch_spotify_meta(url)

    try:
        meta = await probe_url(url)
    except Exception:
        meta = None
    if not meta:
        return None

    title = str(meta.get("title") or "").strip()
    artist = str(meta.get("uploader") or "").strip()

    # Instagram title parsing
    if "•" in title:
        parts = title.split("•", 1)
        title = parts[1].strip() if len(parts) > 1 else parts[0].strip()

    if " - " in title and not artist:
        parts = title.split(" - ", 1)
        title, artist = parts[0].strip(), parts[1].strip()

    title = re.split(r"[|]|\bposted\b|\bon Instagram\b", title)[0].strip(" -·|")

    if not title:
        return None
    return {"title": title[:100], "artist": artist[:60]}


# ═══════════════════════════════════════════════════
# Combined: find best match across all sources
# ═══════════════════════════════════════════════════


async def find_best_match(
    title: str, artist: str, settings: Settings | None = None
) -> dict[str, Any] | None:
    """Search all sources in parallel and return the best match.

    Priority: Avaland > Audius > SoundCloud > Piped > YouTube > Archive.org
    Returns {"url", "source", "title", "artist", "duration", "download_url"} or None.
    """
    query = f"{artist} {title}".strip() if artist else title

    # ── Search all sources in parallel ──
    async def _search_avaland():
        if settings and not settings.enable_avaland:
            return None
        try:
            return await search_avaland(query)
        except Exception:
            return None

    async def _search_audius():
        if settings and not settings.enable_audius:
            return []
        try:
            return await search_audius_async(query, limit=5)
        except Exception:
            return []

    async def _search_soundcloud():
        try:
            return await search_soundcloud(query, limit=5)
        except Exception:
            return []

    async def _search_piped():
        if settings and not settings.enable_piped:
            return []
        try:
            return await search_piped(query, limit=5)
        except Exception:
            return []

    async def _search_youtube():
        try:
            return await find_youtube_match(title, artist, settings=settings)
        except Exception:
            return None

    async def _search_archive():
        if settings and not settings.enable_archive:
            return []
        try:
            return await search_archive(query, limit=3)
        except Exception:
            return []

    avaland_res, audius_res, sc_res, piped_res, yt_res, arch_res = await asyncio.gather(
        _search_avaland(),
        _search_audius(),
        _search_soundcloud(),
        _search_piped(),
        _search_youtube(),
        _search_archive(),
    )

    # ── 1) Avaland (highest priority) ──
    if avaland_res and avaland_res.get("url"):
        log.info(f"[MATCH] Avaland hit: {avaland_res.get('title', '')} from {avaland_res.get('source', '')}")
        return {
            "url": avaland_res["url"],
            "download_url": avaland_res["url"],
            "source": avaland_res.get("source", "Avaland"),
            "title": avaland_res.get("title") or title,
            "artist": avaland_res.get("artist") or artist,
            "duration": avaland_res.get("duration", 0),
        }

    # ── 2) Audius ──
    for r in (audius_res or []):
        score = _match_score(title, r.get("title", ""))
        if score >= 0.55:
            log.info(f"[MATCH] Audius hit: {r['title'][:50]} (score={score:.2f})")
            return {
                "url": r.get("stream_url", ""),
                "download_url": r.get("stream_url", ""),
                "source": "Audius",
                "title": r.get("title") or title,
                "artist": r.get("artist") or artist,
                "duration": r.get("duration", 0),
            }

    # ── 3) SoundCloud ──
    for r in (sc_res or []):
        if not r.get("webpage_url"):
            continue
        dur = int(r.get("duration") or 0)
        if 0 < dur < 40:
            log.info(f"[MATCH] SoundCloud skip preview ({dur}s): {r.get('title', '')[:30]}")
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

    # ── 4) Piped ──
    for r in (piped_res or []):
        score = _match_score(title, r.get("title", ""))
        if score >= 0.50 and r.get("video_id"):
            stream_url = await piped_stream_url(r["video_id"])
            if stream_url:
                log.info(f"[MATCH] Piped hit: {r['title'][:50]} (score={score:.2f})")
                return {
                    "url": f"https://www.youtube.com/watch?v={r['video_id']}",
                    "download_url": stream_url,
                    "source": "Piped",
                    "title": r.get("title") or title,
                    "artist": r.get("channel") or artist,
                    "duration": r.get("duration", 0),
                }

    # ── 5) YouTube ──
    if yt_res and yt_res.get("video_id"):
        log.info(f"[MATCH] YouTube hit: {yt_res.get('title', '')[:50]}")
        return {
            "url": f"https://www.youtube.com/watch?v={yt_res['video_id']}",
            "download_url": "",
            "source": "YouTube",
            "title": yt_res.get("title") or title,
            "artist": yt_res.get("channel") or artist,
            "duration": int(yt_res.get("duration") or 0),
        }

    # ── 6) Archive.org ──
    for r in (arch_res or []):
        score = _match_score(title, r.get("title", ""))
        if score >= 0.50:
            log.info(f"[MATCH] Archive hit: {r['title'][:50]} (score={score:.2f})")
            return {
                "url": r.get("stream_url", ""),
                "download_url": r.get("stream_url", ""),
                "source": "Archive.org",
                "title": r.get("title") or title,
                "artist": artist,
                "duration": 0,
            }

    log.info("[MATCH] No match found in any source")
    return None
