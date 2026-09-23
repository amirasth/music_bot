"""Video handlers — YouTube video download and Twitter/X posts."""

import html as _html
import logging
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from aiogram import Bot
from aiogram.types import CallbackQuery, FSInputFile, Message

from .config import Settings
from .downloader import download_youtube_video, probe_url
from .http import get_session
from .keyboards import kb_back, kb_video_quality, kb_youtube_choice
from .state import PendingStore
from .utils import to_persian

log = logging.getLogger("music_bot.video")

# Pending YouTube selections: token -> url, owned by the user who sent the link.
yt_video_pending = PendingStore()

DAILY_VIDEO_LIMIT = 3


def _limit_message() -> str:
    """Shared text for the daily video quota notice."""
    return (
        f"🚫 به محدودیت استفاده روزانه رسیدید ({to_persian(DAILY_VIDEO_LIMIT)} کلیپ/ویدیو در روز).\n"
        "⏰ محدودیت نیمه‌شب به وقت تهران ریست می‌شه."
    )


async def handle_youtube_link(m: Message, url: str) -> None:
    """Ask user: music or video for a YouTube link."""
    short_id = uuid.uuid4().hex[:8]
    yt_video_pending.put(
        short_id,
        url,
        user_id=m.from_user.id if m.from_user else 0,
        chat_id=m.chat.id,
    )
    await m.answer(
        "این لینک یوتیوبه — موزیک می‌خوای یا خود ویدیو؟",
        reply_markup=kb_youtube_choice(short_id),
    )


async def cb_yt_music(c: CallbackQuery, bot: Bot, settings: Settings, db=None) -> None:
    """User chose music from a YouTube link."""
    short_id = c.data.split(":", 1)[1]
    entry = yt_video_pending.take(short_id, user_id=c.from_user.id)
    if not entry:
        await c.answer("منقضی شد.", show_alert=True)
        return
    await c.answer()
    # Import here to avoid circular imports
    from .handlers import process_url_direct

    await process_url_direct(c.message, bot, entry.value, settings, db)


async def cb_yt_video(c: CallbackQuery, bot: Bot, settings: Settings) -> None:
    """User chose video from a YouTube link — show quality options."""
    short_id = c.data.split(":", 1)[1]
    entry = yt_video_pending.peek(short_id, user_id=c.from_user.id)
    if not entry:
        await c.answer("منقضی شد.", show_alert=True)
        return
    url = entry.value
    await c.answer()
    meta = await probe_url(url, settings)
    duration = int((meta or {}).get("duration") or 0)
    if duration and duration > settings.max_duration_min * 60:
        yt_video_pending.take(short_id, user_id=c.from_user.id)
        await c.message.edit_text(
            f"⏱ ویدیو بیش از {to_persian(settings.max_duration_min)} دقیقه است.",
            reply_markup=kb_back(),
        )
        return
    title = (meta or {}).get("title") or "video"
    await c.message.edit_text(
        f"🎬 {title[:80]}\n"
        f"کیفیت ویدیو رو انتخاب کن (حداکثر {to_persian(settings.max_video_mb)} مگابایت):",
        reply_markup=kb_video_quality(short_id),
    )


async def cb_ytdl(c: CallbackQuery, bot: Bot, settings: Settings, db=None) -> None:
    """Download YouTube video at selected quality."""
    try:
        _, short_id, h_s = c.data.split(":")
        height = int(h_s)
    except Exception:
        await c.answer("درخواست نامعتبر.", show_alert=True)
        return
    entry = yt_video_pending.take(short_id, user_id=c.from_user.id)
    if not entry:
        await c.answer("منقضی شد.", show_alert=True)
        return
    url, chat_id = entry.value, entry.chat_id
    await c.answer()
    # Check daily video limit for non-admins
    uid = c.from_user.id if c.from_user else 0
    if db and uid not in settings.admin_ids:
        count = await db.get_video_count_today(uid)
        if count >= DAILY_VIDEO_LIMIT:
            await c.message.edit_text(_limit_message(), reply_markup=kb_back())
            return
    status = await c.message.edit_text(
        f"⬇️ در حال دانلود ویدیو ({to_persian(height)}p)...",
        reply_markup=kb_back(),
    )
    root = Path(tempfile.gettempdir()) / "yt_video" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)
    try:
        path = await download_youtube_video(url, height, str(root), settings)
        if not path:
            await status.edit_text("❌ دانلود ناموفق بود.", reply_markup=kb_back())
            return
        src_f = Path(path)
        size_mb = src_f.stat().st_size / (1024 * 1024)
        if size_mb > settings.max_video_mb:
            # Re-arm the keyboard so the user can pick a lower quality. The
            # original token was consumed, so a fresh one is issued here.
            retry_id = uuid.uuid4().hex[:8]
            yt_video_pending.put(retry_id, url, user_id=uid, chat_id=chat_id)
            await status.edit_text(
                f"📦 حجم ویدیو {to_persian(round(size_mb))} مگابایته — "
                f"بیشتر از محدودیت {to_persian(settings.max_video_mb)} مگ.\n"
                "کیفیت پایین‌تر رو انتخاب کن.",
                reply_markup=kb_video_quality(retry_id),
            )
            return
        await status.edit_text(
            f"⬆️ در حال ارسال ({to_persian(round(size_mb, 1))} مگ)..."
        )
        try:
            await bot.send_video(
                chat_id=chat_id,
                video=FSInputFile(str(src_f)),
                caption=(
                    f"🎬 {to_persian(height)}p • "
                    f"📦 {to_persian(round(size_mb, 1))} MB\n🎧 @ASmusic_robot"
                ),
                supports_streaming=True,
            )
            if db and uid not in settings.admin_ids:
                await db.increment_video_count(uid)
            await status.delete()
        except Exception as e:
            log.warning(f"[YT_VIDEO] send_video failed: {e}")
            await status.edit_text(f"❌ ارسال ناموفق: {e}", reply_markup=kb_back())
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── Twitter / X ──


async def process_tweet(m: Message, bot: Bot, url: str, db=None, settings=None) -> None:
    """X/Twitter post: fetch via FxTwitter API, reply with text + media."""
    from .keyboards import kb_back

    # Check daily video limit for non-admins
    uid = m.from_user.id if m.from_user else 0
    if db and settings and uid not in settings.admin_ids:
        count = await db.get_video_count_today(uid)
        if count >= DAILY_VIDEO_LIMIT:
            await m.answer(_limit_message(), reply_markup=kb_back())
            return

    status = await m.answer("🐦 در حال خواندن پست از ایکس...", reply_markup=kb_back())

    tweet = None
    for u in (url, re.sub(r"(?:www\.)?twitter\.com", "x.com", url)):
        m2 = re.match(
            r"^https?://(?:www\.|mobile\.)?x\.com/([A-Za-z0-9_]{1,20})/status/(\d+)",
            u,
            flags=re.IGNORECASE,
        )
        if not m2:
            continue
        status_id = m2.group(2)
        api = f"https://api.fxtwitter.com/status/{status_id}"
        try:
            async with get_session().get(api) as resp:
                if resp.status != 200:
                    continue
                data = await resp.json()
                tweet = (data or {}).get("tweet") or {}
            if tweet:
                break
        except Exception:
            continue

    if not tweet:
        await status.edit_text(
            "❌ نتونستم این پست ایکس رو بخونم.\nلینک رو چک کن یا دوباره امتحان کن.",
            reply_markup=kb_back(),
        )
        return

    author = ((tweet.get("author") or {}).get("name") or "").strip()
    text = (tweet.get("text") or "").strip()
    text = _html.escape(text)
    author = _html.escape(author)

    cap_lines = []
    if author:
        cap_lines.append(f'👤 <a href="{url}">{author}</a>' if author else "")
    cap_lines.append("━━━━━━━━━━━━━━")
    if text:
        cap_lines.append(text)
    caption = "\n".join(cap_lines)

    media = tweet.get("media") or {}
    vids = media.get("videos") or []
    photos = media.get("photos") or []

    try:
        if vids:
            v0 = vids[0]
            vurl = v0.get("url") or ""
            if vurl:
                await status.delete()
                await m.answer_video(
                    video=vurl,
                    caption=caption[:1024],
                    parse_mode="HTML",
                )
                if db and settings and uid not in settings.admin_ids:
                    await db.increment_video_count(uid)
                if len(vids) > 1:
                    for v in vids[1:4]:
                        vu = v.get("url") or ""
                        if vu:
                            await m.answer_video(vu)
                for ph in photos[:6]:
                    pu = ph.get("url") or ""
                    if pu:
                        await m.answer_photo(pu)
                return
        if photos:
            await status.delete()
            first = photos[0].get("url") or ""
            if first:
                await m.answer_photo(first, caption=caption[:1024], parse_mode="HTML")
                for ph in photos[1:6]:
                    pu = ph.get("url") or ""
                    if pu:
                        await m.answer_photo(pu)
                return
        # text-only
        await status.edit_text(caption[:4096], parse_mode="HTML", reply_markup=kb_back())
    except Exception as e:
        try:
            await status.edit_text(f"❌ خطا در ارسال: {e}", reply_markup=kb_back())
        except Exception:
            pass
