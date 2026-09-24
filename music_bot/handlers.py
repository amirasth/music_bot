"""Bot message and callback handlers — search, identify, download, send."""

import asyncio
import logging
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from aiogram import Bot, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .config import Settings
from .db import DB
from .errors import code_line
from .downloader import (
    download_audio,
    download_audio_from_stream,
    download_direct_file,
    probe_url,
)
from .http import get_session
from .keyboards import (
    help_text,
    kb_after_send,
    kb_back,
    kb_clip_quality,
    kb_instagram_choice,
    kb_main,
    kb_quality,
)
from .shazam import identify_with_multi_segment
from .sources import (
    find_best_match,
    is_instagram,
    is_soundcloud,
    is_spotify,
    is_supported_url,
    is_tiktok,
    is_twitter,
    is_youtube,
    soundcloud_meta,
    spotify_preview_url,
)
from .state import (
    PendingStore,
    cleanup_prev_msg as _cleanup_prev_msg,
    reply as _reply,
)
from .utils import (
    format_duration,
    clean_title,
    estimate_size_mb,
    sanitize_filename,
    to_persian,
)
from .video import DAILY_VIDEO_LIMIT, handle_youtube_link

log = logging.getLogger("music_bot.handlers")

# Transient-message cleanup lives in state.py (shared with video.py):
# _cleanup_prev_msg / _reply are imported from there.


async def _progress_task(bot: Bot, chat_id: int, msg_id: int, prefix: str) -> None:
    """Background task: update message with progress percentage."""
    persian = "۰۱۲۳۴۵۶۷۸۹"
    for pct in [10, 25, 50, 75, 90]:
        await asyncio.sleep(1.5)
        fa_pct = "".join(persian[int(d)] for d in str(pct))
        try:
            await bot.edit_message_text(
                f"{prefix}... {fa_pct}٪",
                chat_id=chat_id,
                message_id=msg_id,
            )
        except Exception:
            break

# Pending Instagram URL mapping: short_id -> (url, chat_id, user_id)
instagram_pending = PendingStore()

# Instagram clips awaiting a quality choice: token -> url
ig_clip_pending = PendingStore()

# Track last quality used per job for retry toggle
last_quality_by_job: dict[int, int] = {}


def setup_handlers(router: Router, db: DB, settings: Settings) -> None:
    """Register all handlers on the given router."""

    # ── /start ──

    @router.message(CommandStart())
    async def cmd_start(m: Message, bot: Bot):
        await _cleanup_prev_msg(bot, m.chat.id)
        name = m.from_user.first_name if m.from_user else "دوست گرامی"
        txt = (
            f'🎶 <b>سلام {name}!</b>\n'
            "\n"
            "🔗 لینک موزیک یا ویدیوت رو بفرست 🎵\n"
            "\n"
            "🎧 شناسایی آهنگ\n"
            "🎵 دانلود موزیک\n"
            "🎬 دانلود کلیپ\n"
            "\n"
            "📥 YouTube • Instagram • TikTok • Spotify • SoundCloud • X\n"
            "\n"
            "🎧 @ASmusic_robot"
        )
        await _reply(
            m,
            txt,
            reply_markup=kb_main(m.from_user.id if m.from_user else 0),
            parse_mode="HTML",
        )

    # ── /help ──

    @router.message(Command("help"))
    async def cmd_help(m: Message, bot: Bot):
        await _cleanup_prev_msg(bot, m.chat.id)
        await _reply(
            m,
            help_text(),
            reply_markup=kb_main(m.from_user.id if m.from_user else 0),
        )

    # ── /jobs (admin only) ──

    @router.message(Command("jobs"))
    async def cmd_jobs(m: Message, bot: Bot):
        if not m.from_user or m.from_user.id not in settings.admin_ids:
            return
        await _cleanup_prev_msg(bot, m.chat.id)
        rows = await db.list_recent(limit=10)
        if not rows:
            await _reply(m, "هنوز هیچ کار ثبت نشده.")
            return
        lines = ["<b>آخرین ۱۰ درخواست:</b>", ""]
        for r in rows:
            lines.append(
                f"{to_persian(r['id'])} | {r['status']} | {(r['title'] or '')[:30]}"
            )
        await _reply(m, "\n".join(lines))

    # ── /info (admin only) ──

    @router.message(Command("info"))
    async def cmd_info(m: Message, bot: Bot):
        if not m.from_user or m.from_user.id not in settings.admin_ids:
            return
        await _cleanup_prev_msg(bot, m.chat.id)
        stats = await db.get_stats()
        lines = [
            "📊 <b>وضعیت ربات:</b>",
            "",
            f"👥 کاربران فعال امروز: <b>{to_persian(stats['today_users'])}</b>",
            f"👥 کل کاربران: <b>{to_persian(stats['total_users'])}</b>",
            "",
            f"📥 درخواست‌های امروز: <b>{to_persian(stats['today_jobs'])}</b>",
            f"🎬 کلیپ/ویدیو امروز: <b>{to_persian(stats['today_videos'])}</b>",
            f"📥 کل درخواست‌ها: <b>{to_persian(stats['total_jobs'])}</b>",
        ]
        await _reply(m, "\n".join(lines), parse_mode="HTML")

    # ── /stats (admin only) ──

    @router.message(Command("stats"))
    async def cmd_stats(m: Message, bot: Bot):
        if not m.from_user or m.from_user.id not in settings.admin_ids:
            return
        await _cleanup_prev_msg(bot, m.chat.id)
        stats = await db.get_stats()
        lines = [
            "📊 <b>آمار ربات:</b>",
            "",
            f"👥 کل کاربران: <b>{to_persian(stats['total_users'])}</b>",
            f"📅 کاربران امروز: <b>{to_persian(stats['today_users'])}</b>",
            f"📥 کل درخواست‌ها: <b>{to_persian(stats['total_jobs'])}</b>",
            f"📥 درخواست‌های امروز: <b>{to_persian(stats['today_jobs'])}</b>",
            "",
        ]
        # Video downloads today
        if stats["video_users"]:
            lines.append("🎬 <b>دانلود ویدیو امروز:</b>")
            for uid, cnt in stats["video_users"]:
                lines.append(f"  • <code>{to_persian(uid)}</code> — {to_persian(cnt)} ویدیو")
            lines.append("")
        # Top users
        if stats["user_jobs"]:
            lines.append("🏆 <b>فعال‌ترین کاربران:</b>")
            for uid, cnt in stats["user_jobs"][:10]:
                lines.append(f"  • <code>{to_persian(uid)}</code> — {to_persian(cnt)} درخواست")
        await _reply(m, "\n".join(lines), parse_mode="HTML")

    # ── Callback: help ──

    @router.callback_query(lambda c: c.data == "help")
    async def cb_help(c: CallbackQuery):
        await c.answer()
        await _reply(c.message, help_text(), reply_markup=kb_main(c.from_user.id))

    # ── Callback: home ──

    @router.callback_query(lambda c: c.data == "home")
    async def cb_home(c: CallbackQuery):
        await c.answer()
        await c.message.edit_text(
            "👇 لینک یا نام آهنگ رو بفرست:",
            reply_markup=kb_main(c.from_user.id),
        )

    # ── Callback: search ──

    pending_search: set[int] = set()

    @router.callback_query(lambda c: c.data == "search")
    async def cb_search(c: CallbackQuery, bot: Bot):
        pending_search.add(c.from_user.id)
        await c.answer()
        await _cleanup_prev_msg(bot, c.message.chat.id)
        await _reply(
            c.message,
            "🔎 نام آهنگ رو بفرست تا جستجو کنم:",
            reply_markup=kb_back(),
        )

    # ── Instagram music ──

    @router.callback_query(lambda c: c.data.startswith("ig_music:"))
    async def cb_ig_music(c: CallbackQuery, bot: Bot):
        url_part = c.data.split(":", 1)[1]
        entry = instagram_pending.take(url_part, user_id=c.from_user.id)
        if entry is None:
            await c.answer("منقضی شد یا لینک نامعتبر است.", show_alert=True)
            return
        original_url, chat_id = entry.value, entry.chat_id
        user_id = entry.user_id
        await c.answer()
        await c.message.edit_text("🎧 در حال استخراج موزیک از اینستاگرام...")
        track = await identify_music_with_shazam(original_url)
        if not track:
            log.info(f"[IG_MUSIC] 4003 shazam failed for {original_url[:80]}")
            await c.message.edit_text(
                "😢 نتونستم موزیک رو تشخیص بدم." + code_line("4003"),
                reply_markup=kb_back(),
            )
            return
        title = track["title"]
        artist = track.get("artist", "")
        method = track.get("method", "meta")
        badge = "🎧 شناسایی‌شده با Shazam" if method == "shazam" else "🎵 شناسایی‌شده"
        await c.message.edit_text(
            f"{badge}: {title}"
            + (f" — {artist}" if artist else "")
            + "\n🔎 در حال جستجو..."
        )
        match = await find_best_match(title, artist, settings)
        if not match:
            log.info(f"[IG_MUSIC] 4001 no match for {title[:40]!r}")
            await c.message.edit_text(
                "😢 این موزیک در هیچ منبعی پیدا نشد." + code_line("4001"),
                reply_markup=kb_back(),
            )
            return
        duration = int(match.get("duration") or 0)
        job_id = await db.create_job(
            user_id=user_id, chat_id=chat_id, source_url=match["url"]
        )
        await db.update_job(
            job_id,
            download_url=match.get("download_url", ""),
            source_name=match.get("source", ""),
            title=title,
            artist=artist,
            duration_sec=duration,
            status="ready",
        )
        source_label = match.get("source", "نامشخص")
        await c.message.edit_text(
            f"🎵 {title}\n👤 {artist if artist else 'نامشخص'}\n🔗 {source_label}\n⏱ {format_duration(duration)}",
            reply_markup=kb_quality(
                job_id,
                estimate_size_mb(duration, 128),
                estimate_size_mb(duration, 320),
            ),
        )

    # ── Instagram video ──

    @router.callback_query(lambda c: c.data.startswith("ig_video:"))
    async def cb_ig_video(c: CallbackQuery, bot: Bot):
        url_part = c.data.split(":", 1)[1]
        entry = instagram_pending.take(url_part, user_id=c.from_user.id)
        if entry is None:
            await c.answer("منقضی شد یا لینک نامعتبر است.", show_alert=True)
            return
        original_url, chat_id = entry.value, entry.chat_id
        user_id = entry.user_id
        await c.answer()
        # The download only starts after the quality pick, so the URL is moved
        # into a fresh token rather than being downloaded now.
        ig_clip_pending.put(
            url_part, original_url, user_id=user_id, chat_id=chat_id
        )
        await c.message.edit_text(
            "🎬 کیفیت کلیپ رو انتخاب کن:",
            reply_markup=kb_clip_quality("igclip", url_part),
        )

    # ── Instagram clip quality ──

    @router.callback_query(lambda c: c.data.startswith("igclip:"))
    async def cb_ig_clip(c: CallbackQuery, bot: Bot):
        try:
            _, token, quality = c.data.split(":")
        except ValueError:
            await c.answer("درخواست نامعتبر.", show_alert=True)
            return
        entry = ig_clip_pending.take(token, user_id=c.from_user.id)
        if entry is None:
            await c.answer("منقضی شد یا لینک نامعتبر است.", show_alert=True)
            return
        original_url, chat_id = entry.value, entry.chat_id
        user_id = entry.user_id
        await c.answer()
        # Check daily video limit for non-admins
        if user_id not in settings.admin_ids:
            count = await db.get_video_count_today(user_id)
            if count >= DAILY_VIDEO_LIMIT:
                await c.message.edit_text(
                    "🚫 به محدودیت استفاده روزانه رسیدید (۳ کلیپ/ویدیو در روز).\n"
                    "⏰ محدودیت ساعت ۱۲ شب ریست میشه." + code_line("3005"),
                    reply_markup=kb_back(),
                )
                return
        await c.message.edit_text("🎬 در حال دانلود کلیپ اینستاگرام...")
        # Unique per attempt: hash() is not stable across processes and collides
        # for the same URL, which would let one user's cleanup delete another's
        # in-progress download.
        root = Path(tempfile.gettempdir()) / "ig_video" / uuid.uuid4().hex
        root.mkdir(parents=True, exist_ok=True)

        # "orig" is the best single-file stream yt-dlp can hand over; the
        # low-bandwidth option caps the height, since Instagram does not offer
        # a labelled 480p rendition the way YouTube does.
        fmt = (
            "best[height<=480]/bestvideo[height<=480]+bestaudio/best"
            if quality == "480"
            else "bestvideo+bestaudio/best"
        )

        def _work():
            try:
                from yt_dlp import YoutubeDL  # type: ignore
            except Exception:
                return None
            outtmpl = str(root / "video.%(ext)s")
            opts = {
                "format": fmt,
                "outtmpl": outtmpl,
                "merge_output_format": "mp4",
                "quiet": True,
                "noprogress": True,
                "no_warnings": True,
            }
            try:
                with YoutubeDL(opts) as ydl:
                    ydl.download([original_url])
            except Exception:
                return None
            files = list(root.glob("video.*"))
            return str(files[0]) if files else None

        try:
            path = await asyncio.to_thread(_work)
            if not path:
                log.warning(f"[IG_VIDEO] 3007 download failed uid={user_id}")
                await c.message.edit_text(
                    "❌ دانلود کلیپ ناموفق بود." + code_line("3007"),
                    reply_markup=kb_back(),
                )
                return
            src = Path(path)
            size_mb = src.stat().st_size / (1024 * 1024)
            if size_mb > settings.max_video_mb:
                await c.message.edit_text(
                    f"📦 حجم کلیپ {to_persian(round(size_mb))} مگابایت است — "
                    f"بیشتر از محدودیت {to_persian(settings.max_video_mb)} مگابایتی.\n"
                    "گزینه کیفیت معمولی رو امتحان کن." + code_line("3003"),
                    reply_markup=kb_back(),
                )
                return
            await c.message.edit_text(
                f"⬆️ در حال ارسال ({to_persian(round(size_mb, 1))} مگ)..."
            )
            try:
                await bot.send_video(
                    chat_id=chat_id,
                    video=FSInputFile(str(src)),
                    caption=f"🎬 کلیپ اینستاگرام\n📦 {round(size_mb, 1)} MB\n🎧 @ASmusic_robot",
                    supports_streaming=True,
                )
                if user_id not in settings.admin_ids:
                    await db.increment_video_count(user_id)
            except Exception as e:
                log.warning(f"[IG_VIDEO] 3004 send failed uid={user_id}: {e}")
                await c.message.edit_text(
                    f"❌ ارسال ناموفق: {e}" + code_line("3004"),
                    reply_markup=kb_back(),
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # ── YouTube music ──

    @router.callback_query(lambda c: c.data.startswith("yt_music:"))
    async def cb_yt_music(c: CallbackQuery, bot: Bot):
        from .video import cb_yt_music as _cb

        await _cb(c, bot, settings, db)

    # ── YouTube video ──

    @router.callback_query(lambda c: c.data.startswith("yt_video:"))
    async def cb_yt_video(c: CallbackQuery, bot: Bot):
        from .video import cb_yt_video as _cb

        await _cb(c, bot, settings)

    # ── YouTube video download ──

    @router.callback_query(lambda c: c.data.startswith("ytdl:"))
    async def cb_ytdl(c: CallbackQuery, bot: Bot):
        from .video import cb_ytdl as _cb

        await _cb(c, bot, settings, db)

    # ── X/Twitter clip quality ──

    @router.callback_query(lambda c: c.data.startswith("xclip:"))
    async def cb_x_clip(c: CallbackQuery, bot: Bot):
        from .video import cb_x_clip as _cb

        await _cb(c, bot, settings, db)

    # ── Search result download ──

    @router.callback_query(lambda c: c.data.startswith("searchdl:"))
    async def cb_search_dl(c: CallbackQuery, bot: Bot):
        if not c.from_user:
            await c.answer("خطای کاربر.", show_alert=True)
            return
        vid = c.data.split(":", 1)[1]
        url = f"https://www.youtube.com/watch?v={vid}"
        await c.answer()
        status = await c.message.edit_text("🔍 در حال بررسی...", reply_markup=kb_back())
        meta = await probe_url(url, settings)
        if not meta:
            log.warning(f"[SEARCHDL] 1001 probe failed vid={vid}")
            await status.edit_text(
                "😢 اطلاعات ویدیو یافت نشد." + code_line("1001"),
                reply_markup=kb_back(),
            )
            return
        duration = int(meta.get("duration") or 0)
        if duration > settings.max_duration_min * 60:
            await status.edit_text(
                f"⏱ فایل بیش از {to_persian(settings.max_duration_min)} دقیقه است."
                + code_line("2005"),
                reply_markup=kb_back(),
            )
            return
        job_id = await db.create_job(c.from_user.id, c.message.chat.id, url)
        await db.update_job(
            job_id,
            title=meta.get("title"),
            artist=meta.get("uploader"),
            duration_sec=duration,
            status="ready",
        )
        card = (
            f"🎵 {meta.get('title') or 'music'}\n"
            f"👤 {meta.get('uploader') or 'نامشخص'}\n"
            f"⏱ {format_duration(duration)}"
        )
        await status.edit_text(
            card,
            reply_markup=kb_quality(
                job_id,
                estimate_size_mb(duration, 128),
                estimate_size_mb(duration, 320),
            ),
        )

    # ── Download audio callback ──

    @router.callback_query(lambda c: c.data.startswith("dl:"))
    async def cb_download(c: CallbackQuery, bot: Bot):
        log.info(f"[DL] Callback data: {c.data}")
        try:
            _, job_id_s, q_s = c.data.split(":")
            job_id, quality = int(job_id_s), int(q_s)
        except Exception:
            await c.answer("درخواست نامعتبر.", show_alert=True)
            return
        log.info(f"[DL] Looking up job_id={job_id}, quality={quality}")
        job = await db.get_job(job_id)
        if not job:
            log.warning(f"[DL] Job {job_id} not found in database!")
            await c.answer("درخواست منقضی شد.", show_alert=True)
            return
        await c.answer()
        await _run_audio_download(c, bot, db, job, job_id, quality, settings)

    # ── Retry with different quality ──

    @router.callback_query(lambda c: c.data.startswith("retry:"))
    async def cb_retry(c: CallbackQuery, bot: Bot):
        try:
            _, job_id_s = c.data.split(":")
            job_id = int(job_id_s)
        except Exception:
            await c.answer("درخواست نامعتبر.", show_alert=True)
            return
        job = await db.get_job(job_id)
        if not job:
            await c.answer("منقضی شد.", show_alert=True)
            return
        last_q = last_quality_by_job.get(job_id, 320)
        new_q = 128 if last_q == 320 else 320
        await c.answer()
        await _run_audio_download(c, bot, db, job, job_id, new_q, settings, retry=True)

    # ── Error handler ──

    @router.errors()
    async def on_error(event):
        log.error(f"Handler error: {event.exception}", exc_info=True)
        return True

    # ── Text message handler ──

    @router.message(lambda m: m.text is not None)
    async def handle_text(m: Message, bot: Bot):
        if not m.from_user or not m.text:
            return
        # Cleanup previous bot message
        await _cleanup_prev_msg(bot, m.chat.id)
        uid = m.from_user.id
        txt = m.text.strip()

        # Accept scheme-less links to supported hosts ("soundcloud.com/...").
        is_link = bool(re.match(r"^https?://", txt))
        if not is_link:
            candidate = "https://" + txt.split()[0]
            if is_supported_url(candidate) or re.match(
                r"^https?://(?:www\.|m\.)?on\.soundcloud\.com/",
                candidate,
                flags=re.IGNORECASE,
            ):
                txt = candidate
                is_link = True

        # A pasted link is always a link — even if the user was mid-search.
        # Otherwise the search prompt swallows the URL and searches for it.
        if uid in pending_search:
            pending_search.discard(uid)
            if not is_link:
                await process_search(m, txt, settings, bot)
                return

        # Must be a URL
        if not is_link:
            await _reply(
                m,
                "❌ لینک معتبر بفرست یا از دکمه «🔍 جستجوی موزیک» استفاده کن."
                + code_line("5001"),
                reply_markup=kb_main(uid),
            )
            return

        url = txt.split()[0]

        # SoundCloud share links (on.soundcloud.com/...) redirect to the real
        # track URL, which is the form is_soundcloud accepts.
        if re.match(
            r"^https?://(?:www\.|m\.)?on\.soundcloud\.com/", url, flags=re.IGNORECASE
        ):
            resolved = await _resolve_redirect(url)
            if resolved:
                url = resolved

        if not is_supported_url(url):
            await _reply(
                m,
                "❌ لینک پشتیبانی نمی‌شود.\n📎 یوتیوب، اینستاگرام، تیک‌تاک، اسپاتیفای، ساندکلاد یا ایکس."
                + code_line("5001"),
                reply_markup=kb_main(uid),
            )
            return

        # Instagram: ask music or video
        if is_instagram(url):
            short_id = uuid.uuid4().hex[:8]
            instagram_pending.put(
                short_id,
                url,
                user_id=uid,
                chat_id=m.chat.id,
            )
            await _reply(
                m,
                "آیا موزیک رو استخراج کنم یا کلیپ کامل بفرستم؟",
                reply_markup=kb_instagram_choice(short_id),
            )
            return

        # SoundCloud: download the link itself, or find it elsewhere if DRM
        if is_soundcloud(url):
            await process_soundcloud(m, bot, url, settings, db)
            return

        # TikTok, Spotify: identify music (Shazam)
        if is_tiktok(url) or is_spotify(url):
            await process_identify(m, bot, url, settings, db)
            return

        # Twitter/X
        if is_twitter(url):
            from .video import process_tweet

            await process_tweet(m, bot, url, db, settings)
            return

        # YouTube: ask music or video
        if is_youtube(url):
            await handle_youtube_link(m, url)
            return

        # Direct URL (unknown but supported)
        await process_url_direct(m, bot, url, settings, db)


# ═══════════════════════════════════════════════════
# Internal processing functions
# ═══════════════════════════════════════════════════


async def identify_music_with_shazam(url: str) -> dict[str, Any] | None:
    """Full Shazam identification pipeline.

    1. Spotify: download 30s preview MP3 and Shazam it.
    2. Others: download audio stream, cut segments, Shazam.
    Returns {"title", "artist", "method"} or None.
    """
    probe_root = Path(tempfile.gettempdir()) / "music_probe" / uuid.uuid4().hex
    probe_root.mkdir(parents=True, exist_ok=True)

    # Spotify: try preview MP3 first
    if is_spotify(url):
        preview = await spotify_preview_url(url)
        if preview:
            preview_path = probe_root / "preview.mp3"
            try:
                async with get_session().get(preview) as r:
                    if r.status == 200:
                        preview_path.write_bytes(await r.read())
                if preview_path.exists() and preview_path.stat().st_size > 10000:
                    from .shazam import recognize_with_shazam

                    res = await recognize_with_shazam(str(preview_path))
                    if res:
                        shutil.rmtree(probe_root, ignore_errors=True)
                        return {**res, "method": "shazam"}
            except Exception:
                pass
        from .sources import fetch_spotify_meta

        meta = await fetch_spotify_meta(url)
        shutil.rmtree(probe_root, ignore_errors=True)
        if meta:
            return {**meta, "method": "meta"}
        return None

    # Others: download probe audio and Shazam segments
    probe = await _download_probe_audio(url, str(probe_root))
    if probe:
        res = await identify_with_multi_segment(probe, probe_root)
        if res:
            shutil.rmtree(probe_root, ignore_errors=True)
            return {**res, "method": "shazam"}

    shutil.rmtree(probe_root, ignore_errors=True)
    log.info("[IDENTIFY] Shazam failed")
    return None


async def _download_probe_audio(source_url: str, dest: str) -> str | None:
    """Download just the audio stream for fingerprinting."""

    def _work() -> str | None:
        try:
            from yt_dlp import YoutubeDL  # type: ignore
        except Exception:
            return None
        outtmpl = str(Path(dest) / "probe.%(ext)s")
        opts = {
            "quiet": True,
            "noprogress": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
        }
        try:
            with YoutubeDL(opts) as ydl:
                ydl.extract_info(source_url, download=True)
        except Exception:
            return None
        files = list(Path(dest).glob("probe.*"))
        return str(files[0]) if files else None

    return await asyncio.to_thread(_work)


async def _resolve_redirect(url: str) -> str | None:
    """Follow short-link redirects to the final URL (SoundCloud share links)."""
    try:
        async with get_session().get(url, allow_redirects=True) as resp:
            if resp.status == 200:
                return str(resp.url)
    except Exception as e:
        log.info(f"[URL] redirect resolve failed: {e}")
    return None


async def _offer_direct(
    m: Message, status: Message, meta: dict[str, Any], url: str, settings: Settings, db: DB
) -> None:
    """Create a job from probed metadata and show the quality picker."""
    duration = int(meta.get("duration") or 0)
    if duration > settings.max_duration_min * 60:
        await status.edit_text(
            f"⏱ ویدیو بیش از {to_persian(settings.max_duration_min)} دقیقه است."
            + code_line("3008"),
            reply_markup=kb_back(),
        )
        return
    job_id = await db.create_job(
        user_id=m.from_user.id, chat_id=m.chat.id, source_url=url
    )
    await db.update_job(
        job_id,
        title=meta.get("title"),
        artist=meta.get("uploader"),
        duration_sec=duration,
        status="ready",
    )
    card = (
        f"🎵 {meta.get('title') or 'music'}\n"
        f"👤 {meta.get('uploader') or 'نامشخص'}\n"
        f"⏱ {format_duration(duration)}"
    )
    await status.edit_text(
        card,
        reply_markup=kb_quality(
            job_id,
            estimate_size_mb(duration, 128),
            estimate_size_mb(duration, 320),
        ),
    )


async def _offer_match(
    m: Message,
    status: Message,
    match: dict[str, Any],
    *,
    title: str,
    artist: str,
    method_note: str,
    settings: Settings,
    db: DB,
) -> None:
    """Duration check → job → quality card, for a match found via search."""
    duration = int(match.get("duration") or 0)
    if not duration and not match.get("source", "").startswith("Avaland"):
        meta = await probe_url(match["url"], settings)
        duration = int((meta or {}).get("duration") or 0)
    if duration > settings.max_duration_min * 60:
        await status.edit_text(
            f"⏱ فایل بیش از {to_persian(settings.max_duration_min)} دقیقه است."
            + code_line("2005"),
            reply_markup=kb_back(),
        )
        return
    job_id = await db.create_job(
        user_id=m.from_user.id, chat_id=m.chat.id, source_url=match["url"]
    )
    await db.update_job(
        job_id,
        download_url=match.get("download_url", ""),
        source_name=match.get("source", ""),
        title=title,
        artist=artist,
        duration_sec=duration,
        status="ready",
    )
    source_label = match.get("source", "نامشخص")
    card = (
        f"🎵 {title}\n"
        f"👤 {artist if artist else 'نامشخص'}\n"
        f"🔗 منبع دانلود: {source_label}\n"
        f"{method_note}"
    )
    await status.edit_text(
        card,
        reply_markup=kb_quality(
            job_id,
            estimate_size_mb(duration, 128),
            estimate_size_mb(duration, 320),
        ),
    )


async def process_soundcloud(m: Message, bot: Bot, url: str, settings: Settings, db: DB) -> None:
    """SoundCloud track: download it directly, or find it elsewhere if DRM blocks yt-dlp."""
    status = await _reply(m, "🔍 در حال بررسی لینک ساندکلاد...", reply_markup=kb_back())
    meta = await probe_url(url, settings)
    if meta:
        await _offer_direct(m, status, meta, url, settings, db)
        return
    # Official releases are DRM'd: yt-dlp refuses them, but oEmbed still has
    # the title — use it to find a downloadable copy on another source.
    await status.edit_text(
        "🔎 لینک مستقیم در دسترس نیست — دارم تو منابع دیگه میگردم...",
        reply_markup=kb_back(),
    )
    sc = await soundcloud_meta(url)
    if not sc:
        log.info(f"[SC] 4004 oEmbed failed for {url[:80]}")
        await status.edit_text(
            "😢 نتونستم این لینک ساندکلاد رو باز کنم.\nلینک رو دوباره کپی کن."
            + code_line("4004"),
            reply_markup=kb_back(),
        )
        return
    match = await find_best_match(sc["title"], sc.get("artist", ""), settings)
    if not match:
        log.info(f"[SC] 4001 no match for {sc['title'][:40]!r}")
        await status.edit_text(
            "😢 این موزیک در هیچ منبعی پیدا نشد." + code_line("4001"),
            reply_markup=kb_back(),
        )
        return
    await _offer_match(
        m,
        status,
        match,
        title=sc["title"],
        artist=sc.get("artist", ""),
        method_note="🔗 پیدا شده از منابع دیگر (لینک DRM)",
        settings=settings,
        db=db,
    )


async def process_identify(m: Message, bot: Bot, url: str, settings: Settings, db: DB) -> None:
    """Shazam the music from a link, then find & download it."""
    from .keyboards import kb_back

    status = await _reply(m, "🎧 در حال شناسایی موزیک با Shazam...", reply_markup=kb_back())
    track = await identify_music_with_shazam(url)
    if not track:
        log.info(f"[IDENTIFY] 4003 shazam failed for {url[:80]}")
        await status.edit_text(
            "😢 نتونستم موزیک رو تشخیص بدم.\n"
            "لطفاً لینک مستقیم یوتیوب رو بفرست یا اسم آهنگ رو جستجو کن."
            + code_line("4003"),
            reply_markup=kb_back(),
        )
        return

    title = track["title"]
    artist = track.get("artist", "")
    method = track.get("method", "meta")
    badge = "🎧 شناسایی‌شده با Shazam" if method == "shazam" else "🎵 شناسایی‌شده"
    await status.edit_text(
        f"{badge}: {title}" + (f" — {artist}" if artist else "")
        + "\n🔎 در حال جستجو در منابع فارسی، یوتیوب و ساندکلاد...",
        reply_markup=kb_back(),
    )

    match = await find_best_match(title, artist, settings)
    if not match:
        log.info(f"[IDENTIFY] 4001 no match for {title[:40]!r}")
        await status.edit_text(
            "😢 این موزیک در هیچ منبعی پیدا نشد." + code_line("4001"),
            reply_markup=kb_back(),
        )
        return

    await _offer_match(
        m,
        status,
        match,
        title=title,
        artist=artist,
        method_note=(
            "🎧 شناسایی با Shazam" if method == "shazam"
            else "ℹ️ شناسایی از اطلاعات لینک"
        ),
        settings=settings,
        db=db,
    )


async def process_search(m: Message, query: str, settings: Settings, bot: Bot) -> None:
    """Search YouTube and show results."""
    from .sources import SEARCH_TIMEOUT_SEC, _run_with_timeout, search_piped, search_youtube

    status = await _reply(
        m,
        f"🔍 در حال جستجوی «{query}»... ۱۰٪",
        reply_markup=kb_back(),
    )
    progress = asyncio.create_task(_progress_task(bot, m.chat.id, status.message_id, f"🔍 در حال جستجوی «{query}»"))

    seen_ids: set[str] = set()
    all_results: list[dict[str, Any]] = []

    # Determine if we should also search "popular songs"
    song_indicators = [
        "official", "video", "audio", "lyric", "remix", "live", "cover", "amv", "mv",
    ]
    is_likely_artist = not any(ind in query.lower() for ind in song_indicators) and len(query.split()) <= 4

    # Run both searches in parallel
    searches = [search_youtube(query, limit=6, settings=settings)]
    if is_likely_artist:
        searches.append(search_youtube(f"{query} popular songs", limit=6, settings=settings))
    # Bound the YouTube fan-out: a degraded network can otherwise hold the
    # progress message for minutes before the Piped fallback is even tried.
    results_list = await asyncio.gather(
        *(_run_with_timeout(s_, SEARCH_TIMEOUT_SEC, []) for s_ in searches),
        return_exceptions=True,
    )

    for res in results_list:
        if isinstance(res, Exception):
            continue
        for r in res or []:
            vid = r.get("video_id", "")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                all_results.append(r)

    # YouTube bot-checks datacenter IPs outright; Piped's live instance
    # still answers, in the same result shape the searchdl buttons expect.
    if not all_results:
        piped = await search_piped(query, limit=6)
        for r in piped or []:
            vid = r.get("video_id", "")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                all_results.append(r)

    all_results = all_results[:6]

    # Cancel progress updates
    progress.cancel()
    try:
        await progress
    except asyncio.CancelledError:
        pass

    if not all_results:
        log.info(f"[SEARCH] 4002 no results for {query[:40]!r}")
        await status.edit_text(
            "😢 نتیجه‌ای یافت نشد." + code_line("4002"),
            reply_markup=kb_main(m.from_user.id),
        )
        return

    rows = []
    for r in all_results:
        dur = int(r.get("duration") or 0)
        mm, ss = dur // 60, dur % 60
        label = f"{r.get('title', 'بدون عنوان')} — {r.get('channel', '')} ({mm}:{ss:02d})"
        rows.append(
            [
                InlineKeyboardButton(
                    text=label[:64], callback_data=f"searchdl:{r['video_id']}"
                )
            ]
        )
    rows.append(
        [InlineKeyboardButton(text="🏠 منوی اصلی", callback_data="home")]
    )

    await status.edit_text(
        f"🔎 نتایج جستجو برای: {query}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )


async def process_url_direct(m: Message, bot: Bot, url: str, settings: Settings, db: DB) -> None:
    """Handle a direct URL — probe, create job, show quality options."""
    status = await _reply(m, "🔍 در حال بررسی...", reply_markup=kb_back())
    meta = await probe_url(url, settings)
    if not meta:
        log.warning(f"[URL_DIRECT] 1001 probe failed for {url[:80]}")
        await status.edit_text(
            "😢 اطلاعات ویدیو یافت نشد." + code_line("1001"),
            reply_markup=kb_back(),
        )
        return
    await _offer_direct(m, status, meta, url, settings, db)


async def _run_audio_download(
    c: CallbackQuery,
    bot: Bot,
    db: DB,
    job: dict[str, Any],
    job_id: int,
    quality: int,
    settings: Settings,
    retry: bool = False,
) -> None:
    """Drive one audio download end to end and report the outcome once."""
    label = "⬇️ در حال دانلود کیفیت دیگر" if retry else "⬇️ در حال دانلود"
    await c.message.edit_text(f"{label}... ۱۰٪", reply_markup=kb_back())
    progress = asyncio.create_task(
        _progress_task(bot, c.message.chat.id, c.message.message_id, label)
    )
    try:
        path = await _download_and_send(
            bot=bot,
            chat_id=c.message.chat.id,
            job_id=job_id,
            quality=quality,
            title=job.get("title") or "music",
            artist=job.get("artist") or "",
            source_url=job.get("source_url") or "",
            download_url=job.get("download_url") or "",
            source_name=job.get("source_name") or "",
            settings=settings,
            db=db,
        )
    finally:
        progress.cancel()
        try:
            await progress
        except asyncio.CancelledError:
            pass

    if path:
        await c.message.edit_text("✅ ارسال کامل", reply_markup=kb_after_send(job_id))
    else:
        log.warning(
            f"[DL] 2001 audio download failed job={job_id} q={quality} "
            f"src={job.get('source_name') or '?'} "
            f"uid={c.from_user.id if c.from_user else 0}"
        )
        await c.message.edit_text(
            "❌ خطا در دانلود/ارسال." + code_line("2001"), reply_markup=kb_back()
        )


async def _download_and_send(
    bot: Bot,
    chat_id: int,
    job_id: int,
    quality: int,
    title: str,
    artist: str,
    source_url: str,
    download_url: str = "",
    source_name: str = "",
    settings: Settings | None = None,
    db: DB | None = None,
) -> str | None:
    """Download audio and send it to the chat.

    Returns the sent file path on success, None on failure. On failure the
    caller reports it — this function must not send its own error message,
    otherwise the user sees a duplicate error and a stale progress message.
    """
    root = Path(tempfile.gettempdir()) / "music_dl" / uuid.uuid4().hex
    root.mkdir(parents=True, exist_ok=True)

    path = None

    # Determine download method based on source
    if download_url:
        # Direct stream URL (Audius, Piped, Avaland, Archive)
        log.info(f"[DL] Direct stream: {download_url[:80]}")
        path = await download_audio_from_stream(download_url, str(root))
        if not path:
            # Fallback: try as direct file
            path = await download_direct_file(download_url, str(root))
    elif source_url.startswith("http") and not any(
        h in source_url
        for h in ["youtube.com", "youtu.be", "soundcloud.com", "spotify.com"]
    ):
        # Direct download URL (Avaland)
        log.info(f"[DL] Direct download: {source_url[:80]}")
        path = await download_direct_file(source_url, str(root))
    else:
        # yt-dlp download
        path = await download_audio(source_url, quality, str(root), settings)

    if not path:
        # Caller owns the failure message: sending one here would leave the
        # user with both this text and the caller's "download failed" edit.
        shutil.rmtree(root, ignore_errors=True)
        return None

    src = Path(path)
    ext = src.suffix or ".mp3"

    # Clean title for filename
    clean_name = title or "music"
    if " - " in clean_name and artist:
        # "Artist - Song" is the common shape for upload titles; when the known
        # artist leads the string, the song is the trailing half.
        head, _, tail = clean_name.partition(" - ")
        if artist.lower() in head.lower() and tail.strip():
            clean_name = tail.strip()
    clean_name = re.sub(
        r"\s*[\(\[\{][^\)\]\}]*(?:official|video|audio|lyric|lyrics|hd|4k|remix|edit|clip)[^\)\]\}]*[\)\]\}]",
        "",
        clean_name,
        flags=re.IGNORECASE,
    )
    clean_name = re.sub(r"\s+", " ", clean_name).strip() or "music"
    new_name = f"{sanitize_filename(clean_name)}{ext}"
    dst = src.with_name(new_name)
    if dst != src and not dst.exists():
        src.rename(dst)
        src = dst

    # Write ID3 tags
    try:
        from mutagen.id3 import ID3, TIT2, TPE1  # type: ignore

        tags = ID3(str(src))
        tags.setall("TIT2", [TIT2(encoding=3, text=clean_title(title)[:60])])
        if artist:
            tags.setall("TPE1", [TPE1(encoding=3, text=artist[:60])])
        else:
            tags.delall("TPE1")
        tags.save()
    except Exception:
        pass

    size_mb = src.stat().st_size / (1024 * 1024)
    if settings and size_mb > settings.max_file_mb:
        log.info(f"[DL] File too large: {size_mb:.1f}MB > {settings.max_file_mb}MB")
        shutil.rmtree(root, ignore_errors=True)
        return None

    caption = f"🎵 {title}"
    if artist:
        caption += f"\n🎤 {artist}"
    caption += "\n━━━━━━━━━━━━━━"
    caption += f"\n🎚 {quality} kbps  •  📦 {round(size_mb, 1)} MB"
    caption += "\n🎧 @ASmusic_robot"

    try:
        await bot.send_audio(
            chat_id,
            audio=FSInputFile(str(src)),
            caption=caption,
            title=(title or "music")[:60],
            performer=(artist[:60] if artist else None),
        )
    except Exception as e:
        log.warning(f"[DL] send_audio failed: {e}")
        shutil.rmtree(root, ignore_errors=True)
        return None
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if db:
        await db.update_job(job_id, status="sent")
    last_quality_by_job[job_id] = quality
    return str(src)
