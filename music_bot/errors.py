"""User-facing error codes.

Every download failure the user can hit gets a stable 4-digit code. The code
is shown in the error message so an admin can quote it and the matching server
log line can be found without guessing which of several similar messages fired.

The mapping lives here, apart from the flows that raise it, so a code means
exactly one cause: a new failure path is added by choosing a new number, never
by reinterpreting an existing one.
"""

# Code -> what the server saw when this was reported.
ERROR_CODES: dict[str, str] = {
    # Probe / metadata
    "1001": "yt-dlp could not read metadata for the URL (probe failed)",
    "1002": "metadata read, but duration/title was missing",
    # Audio download
    "2001": "yt-dlp audio download raised (network, extractor, or format error)",
    "2002": "yt-dlp returned no track id, or the expected .mp3 was not produced",
    "2003": "direct stream download failed (Audius/Piped/Avaland/Archive)",
    "2004": "downloaded audio exceeded the size limit",
    "2005": "audio exceeded the max duration",
    "2006": "Telegram rejected the audio upload",
    "2007": "ffmpeg/ffprobe missing or postprocessing failed",
    # Video / clip download
    "3001": "yt-dlp video download raised",
    "3002": "video file missing after a successful yt-dlp run",
    "3003": "video exceeded the size limit",
    "3004": "Telegram rejected the video upload",
    "3005": "daily clip/video quota reached (non-admin)",
    "3006": "clip too large for Telegram even at the lowest quality",
    "3007": "clip download raised inside the handler",
    "3008": "clip exceeded the max duration",
    # Search / identify
    "4001": "no source returned a match above the threshold",
    "4002": "YouTube search returned nothing (and the Piped fallback did too)",
    "4003": "Shazam could not identify the audio",
    "4004": "source metadata could not be read (Spotify embed, SoundCloud oEmbed)",
    # Input / routing
    "5001": "URL is malformed or not a supported host",
    "5002": "the pending token expired or belongs to another user",
}


def code_line(code: str) -> str:
    """The user-visible code footer, e.g. 'کد خطا: 2001'."""
    return f"\n🔢 کد خطا: <code>{code}</code>"


def describe(code: str) -> str:
    """Server-side description for `code`; used in logs next to the user id."""
    return ERROR_CODES.get(code, "unclassified failure")
