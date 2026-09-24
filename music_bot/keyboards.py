"""Inline keyboards — menus and buttons."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def kb_main() -> InlineKeyboardMarkup:
    """Main menu: search + help."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🔍 جستجوی موزیک", callback_data="search"),
                InlineKeyboardButton(text="ℹ️ راهنما", callback_data="help"),
            ],
        ]
    )


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


def help_text() -> str:
    """Help text in Persian."""
    return (
        "ℹ️ راهنمای سریع:\n"
        "1️⃣ لینک بفرست: یوتیوب، اسپاتیفای، اینستاگرام، تیک‌تاک یا ساندکلاد\n"
        "2️⃣ ربات موزیک را شناسایی می‌کند و نسخه اصلی را پیدا می‌کند\n"
        "3️⃣ کیفیت 128 یا 320 kbps را انتخاب کن\n"
        "4️⃣ فایل صوتی برایت ارسال می‌شود\n"
        "🔍 با دکمه جستجو می‌توانی با نام آهنگ پیدا کنی\n"
        "🇮🇷 آهنگ‌های فارسی از سایت‌های ایرانی دانلود می‌شوند"
    )
