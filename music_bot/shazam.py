"""Shazam music recognition — fingerprint audio segments."""

import asyncio
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger("music_bot.shazam")


async def recognize_with_shazam(audio_path: str) -> dict[str, str] | None:
    """Fingerprint an audio file with Shazam. Returns {"title", "artist"} or None."""
    try:
        from shazamio import Shazam  # type: ignore
    except Exception:
        log.warning("[SHAZAM] shazamio not installed")
        return None
    try:
        shazam = Shazam()
        out = await shazam.recognize(audio_path)
    except Exception as e:
        log.warning(f"[SHAZAM] Recognition error: {e}")
        return None
    track = out.get("track") if isinstance(out, dict) else None
    if not track:
        return None
    title = str(track.get("title") or "").strip()
    artist = str(track.get("subtitle") or "").split("·")[0].strip()
    if not title:
        return None

    # Validate: reject invalid titles
    title_lower = title.lower()
    invalid_patterns = [
        "original audio",
        "original sound",
        "original music",
        "tiktok",
        "instagram",
        "reels",
        "viral",
    ]
    if any(p in title_lower for p in invalid_patterns):
        log.info(f"[SHAZAM] Rejected invalid title: {title}")
        return None
    if "@" in title or (len(title) < 5 and " " not in title):
        log.info(f"[SHAZAM] Rejected username-like title: {title}")
        return None
    return {"title": title[:100], "artist": artist[:60]}


async def prepare_segment(src: str, dst: str, start_sec: int, dur: int = 15) -> bool:
    """Cut a segment from audio for Shazam matching."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)

    def _work() -> bool:
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            str(start_sec),
            "-i",
            src,
            "-t",
            str(dur),
            "-vn",
            dst,
        ]
        try:
            subprocess.run(cmd, check=True, timeout=60)
        except Exception:
            return False
        return os.path.exists(dst)

    return await asyncio.to_thread(_work)


async def identify_with_multi_segment(probe: str, probe_root: Path) -> dict[str, str] | None:
    """Try Shazam on two segments (5s and 15s) for better fingerprinting."""
    # Segment 1: at 5s
    seg1 = probe_root / "seg_5.mp3"
    if await prepare_segment(probe, str(seg1), start_sec=5, dur=20):
        res = await recognize_with_shazam(str(seg1))
        try:
            seg1.unlink(missing_ok=True)
        except Exception:
            pass
        if res:
            log.info(f'[SHAZAM] Match at 5s: {res["title"]} — {res.get("artist", "")}')
            return res
        log.info("[SHAZAM] No match at 5s, trying 15s...")
    else:
        try:
            seg1.unlink(missing_ok=True)
        except Exception:
            pass

    # Segment 2: at 15s
    seg2 = probe_root / "seg_15.mp3"
    if await prepare_segment(probe, str(seg2), start_sec=15, dur=20):
        res = await recognize_with_shazam(str(seg2))
        try:
            seg2.unlink(missing_ok=True)
        except Exception:
            pass
        if res:
            log.info(f'[SHAZAM] Match at 15s: {res["title"]} — {res.get("artist", "")}')
            return res
        log.info("[SHAZAM] No match at 15s")
    else:
        try:
            seg2.unlink(missing_ok=True)
        except Exception:
            pass

    return None
