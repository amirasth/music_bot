"""yt-dlp wrappers — probe, download audio, download video."""

import asyncio
import logging
import mimetypes
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import (
    AUDIO_DOWNLOAD_FAILED,
    AUDIO_FFMPEG_FAILED,
    AUDIO_NO_OUTPUT,
    VIDEO_DOWNLOAD_FAILED,
    VIDEO_NO_OUTPUT,
)
from .http import get_session

log = logging.getLogger("music_bot.downloader")

# Container extensions yt-dlp may produce, mapped from what a stream hands us.
_AUDIO_EXT_BY_CONTENT_TYPE = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".m4a",
    "audio/ogg": ".ogg",
    "audio/opus": ".opus",
    "audio/webm": ".webm",
    "audio/flac": ".flac",
    "audio/wav": ".wav",
}

_AUDIO_EXTS = set(_AUDIO_EXT_BY_CONTENT_TYPE.values())

# yt-dlp downloads run in worker threads and each one can hold tens of MB.
# Unbounded, a burst of users would push a small container into swap; the
# semaphore bounds concurrent downloads without serializing them.
_ytdl_semaphore: asyncio.Semaphore | None = None


def configure_concurrency(limit: int) -> None:
    """Set how many yt-dlp downloads may run at once (call once at startup)."""
    global _ytdl_semaphore
    _ytdl_semaphore = asyncio.Semaphore(max(1, limit))
    log.info(f"[DL] yt-dlp concurrency limit: {max(1, limit)}")


def _semaphore() -> asyncio.Semaphore:
    if _ytdl_semaphore is None:
        configure_concurrency(4)
    assert _ytdl_semaphore is not None
    return _ytdl_semaphore


def _require_ytdlp():
    try:
        from yt_dlp import YoutubeDL  # type: ignore
    except Exception as e:
        raise RuntimeError("yt-dlp is not installed. Please install requirements.txt") from e
    return YoutubeDL


def _base_opts(settings: Settings | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    if settings:
        if settings.cookies_file:
            opts["cookiefile"] = settings.cookies_file
        if settings.yt_proxy:
            opts["proxy"] = settings.yt_proxy
    return opts


async def probe_url(url: str, settings: Settings | None = None) -> dict[str, Any] | None:
    """Get metadata for a URL without downloading."""

    def _work() -> dict[str, Any] | None:
        YoutubeDL = _require_ytdlp()
        opts = _base_opts(settings)
        opts["skip_download"] = True
        opts["extractor_args"] = {"youtube": {"player_client": ["android", "web", "android_vr", "tv_embedded"]}}
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            log.warning(f"[PROBE] Error: {e}")
            return None
        if not info:
            return None
        return {
            "title": info.get("title") or "music",
            "uploader": info.get("uploader") or "",
            "duration": int(info.get("duration") or 0),
            "webpage_url": info.get("webpage_url") or url,
        }

    return await asyncio.to_thread(_work)


async def download_audio(
    source_url: str, quality: int, dest: str, settings: Settings | None = None
) -> tuple[str | None, str | None]:
    """Download audio from a URL and convert to MP3.

    Returns (path, error_code). A postprocessing failure is reported apart
    from a download failure: ffmpeg missing from the image is a deployment
    problem, not a problem with the track.
    """

    def _work() -> tuple[str | None, str | None]:
        YoutubeDL = _require_ytdlp()
        outtmpl = str(Path(dest) / "%(id)s.%(ext)s")
        opts = _base_opts(settings)
        opts["format"] = "bestaudio/best"
        opts["outtmpl"] = outtmpl
        opts["extractor_args"] = {"youtube": {"player_client": ["android", "web", "android_vr", "tv_embedded"]}}
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": str(quality),
            }
        ]
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(source_url, download=True)
        except Exception as e:
            msg = str(e)
            # ffprobe/ffmpeg missing or a broken postprocessor is an image
            # problem that will fail every conversion, not this track.
            if "ffmpeg" in msg.lower() or "ffprobe" in msg.lower() or "Postprocessing" in msg:
                log.warning(f"[DL_AUDIO] AUDIO_FFMPEG_FAILED Error: {msg[:200]}")
                return None, AUDIO_FFMPEG_FAILED
            log.warning(f"[DL_AUDIO] AUDIO_DOWNLOAD_FAILED Error: {msg[:200]}")
            return None, AUDIO_DOWNLOAD_FAILED
        if not info:
            return None, AUDIO_DOWNLOAD_FAILED
        vid = info.get("id")
        if not vid:
            return None, AUDIO_NO_OUTPUT
        candidate = Path(dest) / f"{vid}.mp3"
        if not candidate.exists():
            return None, AUDIO_NO_OUTPUT
        return str(candidate), None

    async with _semaphore():
        return await asyncio.to_thread(_work)


def _pick_audio_ext(url: str, content_type: str, base: str) -> str:
    """Choose a real container extension for a streamed download.

    Hardcoding .mp3 mislabels opus/webm/m4a streams: the file is then sent to
    Telegram as an MP3 that it is not, and ID3 tagging writes into a container
    that does not support it. Prefer the response's Content-Type, then the
    URL's own path, then fall back to mp3.
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in _AUDIO_EXT_BY_CONTENT_TYPE:
        return _AUDIO_EXT_BY_CONTENT_TYPE[ct]

    suffix = Path(url.split("?")[0]).suffix.lower()
    if suffix in _AUDIO_EXTS:
        return suffix

    guessed, _ = mimetypes.guess_type(url.split("?")[0])
    if guessed in _AUDIO_EXT_BY_CONTENT_TYPE:
        return _AUDIO_EXT_BY_CONTENT_TYPE[guessed]

    if ct and not ct.startswith("audio") and ct != "application/octet-stream":
        log.info(f"[DL] Unexpected content-type {ct!r} for {base}")
    return ".mp3"


async def _stream_to_file(url: str, dest: str, base: str, log_tag: str) -> str | None:
    """Fetch a direct media URL into `dest`, naming the file by its real type."""
    try:
        async with get_session().get(url) as resp:
            if resp.status != 200:
                log.warning(f"[{log_tag}] HTTP {resp.status} for {url[:80]}")
                return None
            ext = _pick_audio_ext(url, resp.headers.get("Content-Type", ""), base)
            dest_path = Path(dest) / f"{base}{ext}"
            with open(dest_path, "wb") as f:
                while True:
                    chunk = await resp.content.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
    except Exception as e:
        log.warning(f"[{log_tag}] Error: {e}")
        return None

    if dest_path.exists() and dest_path.stat().st_size > 10000:
        return str(dest_path)
    return None


async def download_audio_from_stream(stream_url: str, dest: str) -> str | None:
    """Download audio from a direct stream URL (Audius, Piped, Archive)."""
    return await _stream_to_file(stream_url, dest, "stream_track", "DL_STREAM")


async def download_direct_file(url: str, dest: str) -> str | None:
    """Download a direct file URL (e.g. from Avaland) without yt-dlp."""
    return await _stream_to_file(url, dest, "direct_track", "DL_DIRECT")


async def download_youtube_video(
    source_url: str, height: int, dest: str, settings: Settings | None = None
) -> tuple[str | None, str | None]:
    """Download YouTube video as mp4 capped at given max height.

    Returns (path, error_code): the file on success, or (None, code) naming
    which failure occurred, so the caller can tell a refused download from a
    run that succeeded but left no playable file.
    """

    def _work() -> tuple[str | None, str | None]:
        YoutubeDL = _require_ytdlp()
        outtmpl = str(Path(dest) / "%(id)s.%(ext)s")
        opts = _base_opts(settings)
        opts["extractor_args"] = {
            "youtube": {"player_client": ["android", "web", "android_vr", "tv_embedded"]}
        }
        opts["format"] = (
            f"bestvideo[height={height}][ext=mp4]+bestaudio[ext=m4a]/"
            f"bestvideo[height={height}]+bestaudio/"
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]"
        )
        opts["outtmpl"] = outtmpl
        opts["merge_output_format"] = "mp4"
        try:
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(source_url, download=True)
        except Exception as e:
            log.warning(f"[DL_VIDEO] VIDEO_DOWNLOAD_FAILED Error: {e}")
            return None, VIDEO_DOWNLOAD_FAILED
        if not info:
            return None, VIDEO_DOWNLOAD_FAILED
        vid = info.get("id")
        if not vid:
            return None, VIDEO_NO_OUTPUT
        for f in sorted(Path(dest).glob(f"{vid}.*")):
            if f.suffix.lower() in {".mp4", ".mkv", ".webm"}:
                return str(f), None
        # yt-dlp reported success but left no playable container behind — a
        # distinct cause from an outright failure, so it gets its own code.
        return None, VIDEO_NO_OUTPUT

    async with _semaphore():
        return await asyncio.to_thread(_work)
