"""yt-dlp wrappers — probe, download audio, download video."""

import asyncio
import logging
from pathlib import Path
from typing import Any

import aiohttp

from .config import Settings

log = logging.getLogger("music_bot.downloader")

YT_VIDEO_MAX_MB = 60


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
        opts["extractor_args"] = {"youtube": {"player_client": ["android", "web"]}}
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
) -> str | None:
    """Download audio from a URL and convert to MP3."""

    def _work() -> str | None:
        YoutubeDL = _require_ytdlp()
        outtmpl = str(Path(dest) / "%(id)s.%(ext)s")
        opts = _base_opts(settings)
        opts["format"] = "bestaudio/best"
        opts["outtmpl"] = outtmpl
        opts["extractor_args"] = {"youtube": {"player_client": ["android", "web"]}}
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
            log.warning(f"[DL_AUDIO] Error: {e}")
            return None
        if not info:
            return None
        vid = info.get("id")
        if not vid:
            return None
        candidate = Path(dest) / f"{vid}.mp3"
        return str(candidate) if candidate.exists() else None

    return await asyncio.to_thread(_work)


async def download_audio_from_stream(stream_url: str, dest: str) -> str | None:
    """Download audio from a direct stream URL (Audius, Piped, Archive)."""
    dest_path = Path(dest) / "stream_track.mp3"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                stream_url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    return None
                with open(dest_path, "wb") as f:
                    while True:
                        chunk = await resp.content.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
        if dest_path.exists() and dest_path.stat().st_size > 10000:
            return str(dest_path)
        return None
    except Exception as e:
        log.warning(f"[DL_STREAM] Error: {e}")
        return None


async def download_direct_file(url: str, dest: str) -> str | None:
    """Download a direct file URL (e.g. from Avaland) without yt-dlp."""
    dest_path = Path(dest) / "avaland_track.mp3"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                if resp.status != 200:
                    return None
                with open(dest_path, "wb") as f:
                    while True:
                        chunk = await resp.content.read(8192)
                        if not chunk:
                            break
                        f.write(chunk)
        if dest_path.exists() and dest_path.stat().st_size > 10000:
            return str(dest_path)
        return None
    except Exception as e:
        log.warning(f"[DL_DIRECT] Error: {e}")
        return None


async def download_youtube_video(
    source_url: str, height: int, dest: str, settings: Settings | None = None
) -> str | None:
    """Download YouTube video as mp4 capped at given max height."""

    def _work() -> str | None:
        YoutubeDL = _require_ytdlp()
        outtmpl = str(Path(dest) / "%(id)s.%(ext)s")
        opts = _base_opts(settings)
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
            log.warning(f"[DL_VIDEO] Error: {e}")
            return None
        if not info:
            return None
        vid = info.get("id")
        if not vid:
            return None
        for f in sorted(Path(dest).glob(f"{vid}.*")):
            if f.suffix.lower() in {".mp4", ".mkv", ".webm"}:
                return str(f)
        return None

    return await asyncio.to_thread(_work)
