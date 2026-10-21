"""Inline keyboards — menus and buttons."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def kb_main(is_admin: bool = False) -> InlineKeyboardMarkup:
    """Main menu: search + help, plus a stats button for admins."""
    rows = [
        [
            InlineKeyboardButton(text="🔍 جستجوی موزیک", callback_data="search"),
            InlineKeyboardButton(text="ℹ️ راهنما", callback_data="help"),
        ],
    ]
    if is_admin:
        rows.append(
            [InlineKeyboardButton(text="📊 آمار ربات", callback_data="stats")]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_back() -> InlineKeyboardMarkup:
    """Back to main menu."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="home")]
        ]
    )


def kb_quality(job_id: int, size_128: str, size_320: str) -> InlineKeyboardMarkup:
    """Quality selection for audio."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🎵 128 kbps ~ {size_128}",
                    callback_data=f"dl:{job_id}:128",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"⚡ 320 kbps ~ {size_320}",
                    callback_data=f"dl:{job_id}:320",
                )
            ],
            [
                InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="home"),
            ],
        ]
    )


def kb_after_send(job_id: int) -> InlineKeyboardMarkup:
    """After successful send: retry quality or home."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 کیفیت دیگر", callback_data=f"retry:{job_id}"
                )
            ],
            [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="home")],
        ]
    )


def kb_video_quality(short_id: str) -> InlineKeyboardMarkup:
    """YouTube video quality selection."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎬 720p", callback_data=f"ytdl:{short_id}:720"
                ),
                InlineKeyboardButton(
                    text="🎬 480p", callback_data=f"ytdl:{short_id}:480"
                ),
                InlineKeyboardButton(
                    text="🎬 360p", callback_data=f"ytdl:{short_id}:360"
                ),
            ],
        ]
    )


def kb_youtube_choice(short_id: str) -> InlineKeyboardMarkup:
    """YouTube link: music or video?"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎵 موزیک", callback_data=f"yt_music:{short_id}"
                ),
                InlineKeyboardButton(
                    text="🎬 ویدیو", callback_data=f"yt_video:{short_id}"
                ),
            ],
        ]
    )


def kb_clip_quality(prefix: str, token: str) -> InlineKeyboardMarkup:
    """Clip quality for Instagram/X: small rendition or the original."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📉 کیفیت معمولی",
                    callback_data=f"{prefix}:{token}:480",
                ),
                InlineKeyboardButton(
                    text="🎬 کیفیت بالاتر",
                    callback_data=f"{prefix}:{token}:orig",
                ),
            ],
        ]
    )


def kb_instagram_choice(short_id: str) -> InlineKeyboardMarkup:
    """Instagram link: music or clip?"""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎵 موزیک", callback_data=f"ig_music:{short_id}"
                ),
                InlineKeyboardButton(
                    text="🎬 کلیپ", callback_data=f"ig_video:{short_id}"
                ),
            ],
        ]
    )


def kb_spotify_tracks(token: str, tracks: list[dict]) -> InlineKeyboardMarkup:
    """Track picker for a Spotify album/playlist/artist link."""
    rows = []
    for i, t in enumerate(tracks[:20]):
        label = t.get("title") or "بدون عنوان"
        if t.get("artist"):
            label += f" — {t['artist']}"
        rows.append(
            [InlineKeyboardButton(text=label[:64], callback_data=f"spt:{token}:{i}")]
        )
    rows.append([InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def help_text() -> str:
    """Help text in Persian."""
    return (
        "ℹ️ <b>راهنمای ربات</b>\n"
        "\n"
        "<b>۱) لینک بفرست</b>\n"
        "یوتیوب، اینستاگرام، تیک‌تاک، اسپاتیفای، ساندکلاد یا ایکس.\n"
        "لینک بدون https هم قبول است.\n"
        "\n"
        "<b>۲) مسیر هر سایت</b>\n"
        "🎬 یوتیوب: بین «موزیک» و «ویدیو» انتخاب می‌کنی. ویدیو در ۳۶۰، ۴۸۰ یا ۷۲۰p.\n"
        "🎵 اسپاتیفای: آهنگ، آلبوم، پلی‌لیست و آرتیست پشتیبانی می‌شود؛ آلبوم و\n"
        "پلی‌لیست به فهرست آهنگ تبدیل می‌شوند و خودت یکی را انتخاب می‌کنی.\n"
        "📸 اینستاگرام: «موزیک» یا «کلیپ». کلیپ در دو کیفیت معمولی/بالاتر.\n"
        "🐦 ایکس: متن و مدیا. برای کلیپ کیفیت انتخاب می‌کنی و اسم نویسنده لینک پست است.\n"
        "☁️ ساندکلاد: خود لینک دانلود می‌شود. اگر نسخه رسمی DRM داشته باشد،\n"
        "ربات همان آهنگ را از منابع دیگر پیدا می‌کند.\n"
        "\n"
        "<b>۳) کیفیت صدا</b>\n"
        "۱۲۸ یا ۳۲۰ kbps؛ فایل با تگ عنوان و خواننده ارسال می‌شود.\n"
        "\n"
        "<b>۴) محدودیت‌ها</b>\n"
        "🎬 کلیپ و ویدیو: ۳ عدد در روز (نیمه‌شب تهران ریست می‌شود).\n"
        "📦 سقف ارسال تلگرام ۵۰ مگابایت است.\n"
        "\n"
        "<b>۵) جستجو</b>\n"
        "با دکمه «🔍 جستجوی موزیک» اسم آهنگ را بفرست.\n"
        "\n"
        "<b>۶) کد خطا</b>\n"
        "هر خطا یک کد چهاررقمی دارد. کد را برای ادمین بفرست تا علتش را بگوید.\n"
        "\n"
        "🎧 @ASmusic_robot"
    )
