"""User-facing error codes.

Every download failure the user can hit gets a stable 4-digit code. The code
is shown in the error message so an admin can quote it and the matching server
log line can be found without guessing which of several similar messages fired.

Only codes a flow can actually raise are named here. Adding a path means
choosing a new number, never reinterpreting an existing one.
"""

# Probe / metadata — yt-dlp could not read metadata for the URL.
PROBE_FAILED = "1001"

# Audio download.
AUDIO_DOWNLOAD_FAILED = "2001"
AUDIO_TOO_LONG = "2005"

# Video / clip download.
VIDEO_DOWNLOAD_FAILED = "3001"
VIDEO_TOO_LARGE = "3003"
VIDEO_SEND_FAILED = "3004"
DAILY_QUOTA_REACHED = "3005"
CLIP_TOO_LARGE = "3006"
CLIP_DOWNLOAD_FAILED = "3007"
VIDEO_TOO_LONG = "3008"

# Search / identify.
NO_SOURCE_MATCH = "4001"
NO_SEARCH_RESULTS = "4002"
SHZAM_FAILED = "4003"
METADATA_UNREADABLE = "4004"

# Input / routing.
UNSUPPORTED_URL = "5001"


def code_line(code: str) -> str:
    """The user-visible code footer, e.g. 'کد خطا: 2001'."""
    return f"\n🔢 کد خطا: <code>{code}</code>"
