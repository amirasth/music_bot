"""User-facing error codes.

Every download failure the user can hit gets a stable 4-digit code. The code
is shown in the error message so an admin can quote it and ask the bot what it
means, or find the matching server log line without guessing which of several
similar messages fired.

Only codes a flow can actually raise are named here. Adding a path means
choosing a new number, never reinterpreting an existing one.
"""

# Probe / metadata — yt-dlp could not read metadata for the URL.
PROBE_FAILED = "1001"

# Audio download.
AUDIO_DOWNLOAD_FAILED = "2001"
AUDIO_NO_OUTPUT = "2002"
AUDIO_STREAM_FAILED = "2003"
AUDIO_TOO_LARGE = "2004"
AUDIO_TOO_LONG = "2005"
AUDIO_SEND_FAILED = "2006"
AUDIO_FFMPEG_FAILED = "2007"

# Video / clip download.
VIDEO_DOWNLOAD_FAILED = "3001"
VIDEO_NO_OUTPUT = "3002"
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
PENDING_EXPIRED = "5002"

# What each code means, in the words an admin needs to act on it. Kept beside
# the codes so a code and its explanation can never drift apart.
_EXPLANATIONS: dict[str, str] = {
    PROBE_FAILED: (
        "خواندن اطلاعات لینک ممکن نشد.\n"
        "لینک ممکنه اشتباه باشه، یا یوتیوب/اینستاگرام از سرور ربات درخواست رو رد کرده باشه."
    ),
    AUDIO_DOWNLOAD_FAILED: (
        "دانلود موزیک شکست خورد.\n"
        "معمولاً مشکل شبکه یا مسدود بودن یوتیوب از IP سرور (پیام Sign in to confirm you're not a bot)."
    ),
    AUDIO_NO_OUTPUT: (
        "دانلود انجام شد ولی فایل صوتی ساخته نشد.\n"
        "معمولاً مشکل ffmpeg یا ناقص بودن دانلوده."
    ),
    AUDIO_STREAM_FAILED: (
        "دانلود از منبع مستقیم شکست خورد (آدیوس، Piped، آوالند یا Archive.org).\n"
        "معمولاً لینک منبع منقضی شده یا سایت در دسترس نیست."
    ),
    AUDIO_TOO_LARGE: (
        "حجم فایل از سقف مجاز بیشتره.\n"
        "برای حل: مقدار MAX_FILE_MB رو کم کن یا کیفیت پایین‌تر (۱۲۸) رو انتخاب کن."
    ),
    AUDIO_TOO_LONG: (
        "مدت فایل از حد مجاز بیشتره.\n"
        "برای حل: مقدار MAX_DURATION_MIN رو کم کن."
    ),
    AUDIO_SEND_FAILED: (
        "تلگرام ارسال فایل صوتی رو رد کرد.\n"
        "معمولاً حجم فایل یا محدودیت موقت تلگرام."
    ),
    AUDIO_FFMPEG_FAILED: (
        "ffmpeg روی سرور نصب نیست یا خراب کار میکنه.\n"
        "این کد همه دانلودهای موزیک رو از کار انداخته — باید ffmpeg نصب بشه."
    ),
    VIDEO_DOWNLOAD_FAILED: (
        "دانلود ویدیو شکست خورد.\n"
        "معمولاً مشکل شبکه یا مسدود بودن یوتیوب از IP سرور."
    ),
    VIDEO_NO_OUTPUT: (
        "دانلود ویدیو انجام شد ولی فایل قابل پخش ساخته نشد.\n"
        "معمولاً مشکل ffmpeg یا ناقص بودن دانلوده."
    ),
    VIDEO_TOO_LARGE: (
        "حجم ویدیو از سقف مجاز بیشتره.\n"
        "برای حل: کیفیت پایین‌تر (۴۸۰ یا ۳۶۰) رو انتخاب کن، یا MAX_VIDEO_MB رو کم کن."
    ),
    VIDEO_SEND_FAILED: (
        "تلگرام ارسال ویدیو رو رد کرد.\n"
        "معمولاً حجم فایل یا محدودیت موقت تلگرام."
    ),
    DAILY_QUOTA_REACHED: (
        "کاربر به سقف ۳ کلیپ/ویدیوی روزانه رسیده.\n"
        "برای حل: در دیتابیس، رکورد daily_limits رو برای این کاربر و تاریخ امروز پاک کن."
    ),
    CLIP_TOO_LARGE: (
        "کلیپ حتی با پایین‌ترین کیفیت هم از سقف تلگرام بزرگ‌تره.\n"
        "برای حل: MAX_VIDEO_MB رو کم کن."
    ),
    CLIP_DOWNLOAD_FAILED: (
        "دانلود کلیپ شکست خورد.\n"
        "معمولاً مشکل شبکه یا مسدود بودن اینستاگرام از IP سرور (نیاز به کوکی)."
    ),
    VIDEO_TOO_LONG: (
        "مدت ویدیو از حد مجاز بیشتره.\n"
        "برای حل: مقدار MAX_DURATION_MIN رو کم کن."
    ),
    NO_SOURCE_MATCH: (
        "هیچ منبعی آهنگ رو پیدا نکرد (آوالند، آدیوس، ساندکلاد، Piped، یوتیوب، Archive).\n"
        "معمولاً همه منابع در دسترس نبودن یا آهنگ پوشش داده نمیشه."
    ),
    NO_SEARCH_RESULTS: (
        "جستجو هیچ نتیجه‌ای نداشت.\n"
        "معمولاً یوتیوب از IP سرور مسدوده و منابع جایگزین هم جواب ندادن."
    ),
    SHZAM_FAILED: (
        "Shazam نتونست موزیک رو تشخیص بده.\n"
        "معمولاً کیفیت صدا پایینه یا قطعه آهنگ تکراریه."
    ),
    METADATA_UNREADABLE: (
        "خواندن اطلاعات لینک ممکن نشد (اسپاتیفای یا ساندکلاد).\n"
        "معمولاً لینک نامعتبره یا اون سرویس از سرور ربات درخواست رو رد کرده."
    ),
    UNSUPPORTED_URL: (
        "لینک پشتیبانی نمیشه یا نامعتبره.\n"
        "سایت‌های پشتیبانی‌شده: یوتیوب، اینستاگرام، تیک‌تاک، اسپاتیفای، ساندکلاد، ایکس."
    ),
    PENDING_EXPIRED: (
        "دکمه منقضی شده (بیشتر از ۱۵ دقیقه گذشته).\n"
        "برای حل: کاربر باید لینک رو دوباره بفرسته."
    ),
}


def code_line(code: str) -> str:
    """The user-visible code footer, e.g. 'کد خطا: 2001'."""
    return f"\n🔢 کد خطا: <code>{code}</code>"


def explain(code: str) -> str | None:
    """The admin-facing explanation for a code, or None if the code is unknown."""
    text = _EXPLANATIONS.get(code)
    if not text:
        return None
    return f"🔎 <b>علت خطا {code}:</b>\n{text}"
