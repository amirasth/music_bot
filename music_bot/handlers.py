"""Bot message and callback handlers — search, identify, download, send."""

import asyncio
import logging
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

import aiohttp
from aiogram import Bot, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .config import Settings
from .db import DB
from .downloader import (
    download_audio,
    download_audio_from_stream,
    download_direct_file,
    probe_url,
)
from .keyboards import (
    help_text,
    kb_after_send,
    kb_back,
    kb_instagram_choice,
    kb_main,
    kb_quality,
)
from .shazam import identify_with_multi_segment
from .sources import (
    find_best_match,
    identify_track,
    is_instagram,
    is_soundcloud,
    is_spotify,
    is_supported_url,
    is_tiktok,
    is_twitter,
    is_youtube,
    spotify_preview_url,
)
from .utils import clean_title, estimate_size_mb, sanitize_filename, to_persian
from .video import handle_youtube_link, yt_video_pending

log = logging.getLogger("music_bot.handlers")

# Pending Instagram URL mapping: short_id -> (url, chat_id, user_id)
instagram_pending: dict[str, tuple[str, int, int]] = {}

# Track last quality used per job for retry toggle
last_quality_by_job: dict[int, int] = {}


def setup_handlers(router: Router, db: DB, settings: Settings) -> None:
    """Register all handlers on the given router."""

    # ── /start ──

    @router.message(CommandStart())
    async def cmd_start(m: Message):
        name = m.from_user.first_name if m.from_user else "دوست گرامی"
        txt = (
            f"🎶 سلام <b>{name}</b>!\n"
            "آهنگ مورد علاقت رو پیدا کن!\n"
            "\n"
            "🔗 لینک بفرست یا 🔍 جستجو کن\n"
            "⚡ کیفیت ۱۲۸ یا ۳۲۰ kbps\n"
            "\n"
            "🎧 <b>@ASmusic_robot</b>"
        )
        await m.answer(
            txt,
            reply_markup=kb_main(m.from_user.id if m.from_user else 0),
            parse_mode="HTML",
        )

    # ── /help ──

    @router.message(Command("help"))
    async def cmd_help(m: Message):
        await m.answer(
            help_text(),
            reply_markup=kb_main(m.from_user.id if m.from_user else 0),
        )

    # ── /jobs (admin only) ──

    @router.message(Command("jobs"))
    async def cmd_jobs(m: Message):
        if not m.from_user or m.from_user.id not in settings.admin_ids:
            return
        rows = await db.list_recent(limit=10)
        if not rows:
            await m.answer("هنوز هیچ کار ثبت نشده.")
            return
        lines = ["<b>آخرین ۱۰ درخواست:</b>", ""]
        for r in rows:
            lines.append(
                f"{to_persian(r['id'])} | {r['status']} | {(r['title'] or '')[:30]}"
            )
        await m.answer("\n".join(lines))

    # ── Callback: help ──

    @router.callback_query(lambda c: c.data == "help")
    async def cb_help(c: CallbackQuery):
        await c.answer()
        await c.message.answer(help_text())

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
    async def cb_search(c: CallbackQuery):
        pending_search.add(c.from_user.id)
        await c.answer()
        await c.message.answer("🔎 نام آهنگ رو بفرست تا جستجو کنم:")

    # ── Instagram music ──

    @router.callback_query(lambda c: c.data.startswith("ig_music:"))
    async def cb_ig_music(c: CallbackQuery, bot: Bot):
        url_part = c.data.split(":", 1)[1]
        entry = instagram_pending.pop(url_part, None)
        if entry is None:
            await c.answer("منقضی شد یا لینک نامعتبر است.", show_alert=True)
            return
        original_url, chat_id, user_id = entry
        await c.answer()
        await c.message.edit_text("🎧 در حال استخراج موزیک از اینستاگرام...")
        track = await identify_music_with_shazam(original_url)
        if not track:
            await c.message.edit_text(
                "😢 نتونستم موزیک رو تشخیص بدم.", reply_markup=kb_back()
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
            await c.message.edit_text(
                "😢 این موزیک در هیچ منبعی پیدا نشد.", reply_markup=kb_back()
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
            f"🎵 {title}\n👤 {artist if artist else 'نامشخص'}\n🔗 {source_label}\n⏱ {to_persian(duration // 60)}:{to_persian(duration % 60).zfill(2)}",
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
        entry = instagram_pending.pop(url_part, None)
        if entry is None:
            await c.answer("منقضی شد یا لینک نامعتبر است.", show_alert=True)
            return
        original_url, chat_id, user_id = entry
        await c.answer()
        await c.message.edit_text("🎬 در حال دانلود کلیپ اینستاگرام...")
        root = Path(tempfile.gettempdir()) / "ig_video" / str(abs(hash(original_url)))
        root.mkdir(parents=True, exist_ok=True)

        def _work():
            try:
                from yt_dlp import YoutubeDL  # type: ignore
            except Exception:
                return None
            outtmpl = str(root / "video.%(ext)s")
            opts = {
                "format": "bestvideo+bestaudio/best",
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

        import asyncio

        path = await asyncio.to_thread(_work)
        if not path:
            await c.message.edit_text(
                "❌ دانلود کلیپ ناموفق بود.", reply_markup=kb_back()
            )
            shutil.rmtree(root, ignore_errors=True)
            return
        src = Path(path)
        size_mb = src.stat().st_size / (1024 * 1024)
        await c.message.edit_text("⬆️ در حال ارسال...")
        try:
            await bot.send_video(
                chat_id=c.message.chat.id,
                video=FSInputFile(str(src)),
                caption=f"🎬 کلیپ اینستاگرام\n📦 {round(size_mb, 1)} MB\n🎧 @ASmusic_robot",
                supports_streaming=True,
            )
        except Exception as e:
            await c.message.edit_text(
                f"❌ ارسال ناموفق: {e}", reply_markup=kb_back()
            )
        finally:
            shutil.rmtree(root, ignore_errors=True)

    # ── YouTube music ──

    @router.callback_query(lambda c: c.data.startswith("yt_music:"))
    async def cb_yt_music(c: CallbackQuery, bot: Bot):
        from .video import cb_yt_music as _cb

        await _cb(c, bot, settings)

    # ── YouTube video ──

    @router.callback_query(lambda c: c.data.startswith("yt_video:"))
    async def cb_yt_video(c: CallbackQuery, bot: Bot):
        from .video import cb_yt_video as _cb

        await _cb(c, bot, settings)

    # ── YouTube video download ──

    @router.callback_query(lambda c: c.data.startswith("ytdl:"))
    async def cb_ytdl(c: CallbackQuery, bot: Bot):
        from .video import cb_ytdl as _cb

        await _cb(c, bot, settings)

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
            await status.edit_text(
                "😢 اطلاعات ویدیو یافت نشد.", reply_markup=kb_back()
            )
            return
        duration = int(meta.get("duration") or 0)
        if duration > settings.max_duration_min * 60:
            await status.edit_text(
                f"⏱ فایل بیش از {to_persian(settings.max_duration_min)} دقیقه است.",
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
            f"⏱ {to_persian(duration // 60)}:{to_persian(duration % 60).zfill(2)}"
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
        try:
            _, job_id_s, q_s = c.data.split(":")
            job_id, quality = int(job_id_s), int(q_s)
        except Exception:
            await c.answer("درخواست نامعتبر.", show_alert=True)
            return
        job = await db.get_job(job_id)
        if not job:
            await c.answer("درخواست منقضی شد.", show_alert=True)
            return
        await c.answer()
        await c.message.edit_text("⬇️ در حال دانلود...", reply_markup=kb_back())
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
        )
        if path:
            await c.message.edit_text(
                "✅ ارسال کامل", reply_markup=kb_after_send(job_id)
            )
        else:
            await c.message.edit_text(
                "❌ خطا در دانلود/ارسال.", reply_markup=kb_back()
            )

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
        await c.message.edit_text(
            "⬇️ در حال دانلود کیفیت دیگر...", reply_markup=kb_back()
        )
        path = await _download_and_send(
            bot=bot,
            chat_id=c.message.chat.id,
            job_id=job_id,
            quality=new_q,
            title=job.get("title") or "music",
            artist=job.get("artist") or "",
            source_url=job.get("source_url") or "",
            download_url=job.get("download_url") or "",
            source_name=job.get("source_name") or "",
            settings=settings,
        )
        if path:
            await c.message.edit_text(
                "✅ ارسال کامل", reply_markup=kb_after_send(job_id)
            )
        else:
            await c.message.edit_text(
                "❌ خطا در دانلود/ارسال.", reply_markup=kb_back()
            )

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
        uid = m.from_user.id
        txt = m.text.strip()

        # Check if user is in search mode
        if uid in pending_search:
            pending_search.discard(uid)
            await process_search(m, txt, settings)
            return

        # Must be a URL
        if not re.match(r"^https?://", txt):
            await m.answer(
                "❌ لینک معتبر بفرست یا از دکمه «🔍 جستجوی موزیک» استفاده کن.",
                reply_markup=kb_main(uid),
            )
            return

        url = txt.split()[0]
        if not is_supported_url(url):
            await m.answer(
                "❌ لینک پشتیبانی نمی‌شود.\n📎 یوتیوب، اینستاگرام، تیک‌تاک، اسپاتیفای، ساندکلاد یا ایکس.",
                reply_markup=kb_main(uid),
            )
            return

        # Instagram: ask music or video
        if is_instagram(url):
            short_id = uuid.uuid4().hex[:8]
            instagram_pending[short_id] = (
                url,
                m.chat.id,
                m.from_user.id if m.from_user else 0,
            )
            await m.answer(
                "آیا موزیک رو استخراج کنم یا کلیپ کامل بفرستم؟",
                reply_markup=kb_instagram_choice(short_id),
            )
            return

        # TikTok, Spotify, SoundCloud: identify music
        if is_tiktok(url) or is_spotify(url) or is_soundcloud(url):
            await process_identify(m, bot, url, settings)
            return

        # Twitter/X
        if is_twitter(url):
            from .video import process_tweet

            await process_tweet(m, bot, url)
            return

        # YouTube: ask music or video
        if is_youtube(url):
            await handle_youtube_link(m, url)
            return

        # Direct URL (unknown but supported)
        await process_url_direct(m, bot, url, settings)


# ═══════════════════════════════════════════════════
# Internal processing functions
# ═══════════════════════════════════════════════════


async def identify_music_with_shazam(url: str) -> dict[str, Any] | None:
    """Full Shazam identification pipeline.

    1. Spotify: download 30s preview MP3 and Shazam it.
    2. Others: download audio stream, cut segments, Shazam.
    Returns {"title", "artist", "method"} or None.
    """
    probe_root = Path(tempfile.gettempdir()) / "music_probe" / str(abs(hash(url)))
    probe_root.mkdir(parents=True, exist_ok=True)

    # Spotify: try preview MP3 first
    if is_spotify(url):
        preview = await spotify_preview_url(url)
        if preview:
            preview_path = probe_root / "preview.mp3"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        preview, timeout=aiohttp.ClientTimeout(total=20)
                    ) as r:
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
    from .downloader import download_audio

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


async def process_identify(m: Message, bot: Bot, url: str, settings: Settings) -> None:
    """Shazam the music from a link, then find & download it."""
    from .keyboards import kb_back

    status = await m.answer(
        "🎧 در حال شناسایی موزیک با Shazam...", reply_markup=kb_back()
    )
    track = await identify_music_with_shazam(url)
    if not track:
        await status.edit_text(
            "😢 نتونستم موزیک رو تشخیص بدم.\n"
            "لطفاً لینک مستقیم یوتیوب رو بفرست یا اسم آهنگ رو جستجو کن.",
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
        await status.edit_text(
            "😢 این موزیک در هیچ منبعی پیدا نشد.", reply_markup=kb_back()
        )
        return

    duration = int(match.get("duration") or 0)
    if not duration and not match.get("source", "").startswith("Avaland"):
        meta = await probe_url(match["url"], settings)
        duration = int((meta or {}).get("duration") or 0)

    if duration > settings.max_duration_min * 60:
        await status.edit_text(
            f"⏱ فایل بیش از {to_persian(settings.max_duration_min)} دقیقه است.",
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
        f"{'🎧 شناسایی با Shazam' if method == 'shazam' else 'ℹ️ شناسایی از اطلاعات لینک'}"
    )
    await status.edit_text(
        card,
        reply_markup=kb_quality(
            job_id,
            estimate_size_mb(duration, 128),
            estimate_size_mb(duration, 320),
        ),
    )


async def process_search(m: Message, query: str, settings: Settings) -> None:
    """Search YouTube and show results."""
    from .sources import search_youtube

    status = await m.answer(
        f"🔍 در حال جستجوی «{query}»\n⏳ لطفاً صبر کنید...",
        reply_markup=kb_back(),
    )

    seen_ids: set[str] = set()
    all_results: list[dict[str, Any]] = []

    # Search 1: original query
    try:
        res1 = await search_youtube(query, limit=6, settings=settings)
    except Exception:
        res1 = []
    for r in res1 or []:
        vid = r.get("video_id", "")
        if vid and vid not in seen_ids:
            seen_ids.add(vid)
            all_results.append(r)

    # Search 2: popular songs (if query looks like artist name)
    song_indicators = [
        "official", "video", "audio", "lyric", "remix", "live", "cover", "amv", "mv",
    ]
    is_likely_artist = not any(ind in query.lower() for ind in song_indicators) and len(query.split()) <= 4
    if is_likely_artist:
        try:
            res2 = await search_youtube(f"{query} popular songs", limit=6, settings=settings)
        except Exception:
            res2 = []
        for r in res2 or []:
            vid = r.get("video_id", "")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                all_results.append(r)

    all_results = all_results[:6]

    if not all_results:
        await status.edit_text(
            "😢 نتیجه‌ای یافت نشد.", reply_markup=kb_main(m.from_user.id)
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


async def process_url_direct(m: Message, bot: Bot, url: str, settings: Settings) -> None:
    """Handle a direct URL — probe, create job, show quality options."""
    from .keyboards import kb_back

    uid = m.from_user.id
    status = await m.answer("🔍 در حال بررسی...", reply_markup=kb_back())
    meta = await probe_url(url, settings)
    if not meta:
        await status.edit_text(
            "😢 اطلاعات ویدیو یافت نشد.", reply_markup=kb_back()
        )
        return
    duration = int(meta.get("duration") or 0)
    if duration > settings.max_duration_min * 60:
        await status.edit_text(
            f"⏱ ویدیو بیش از {to_persian(settings.max_duration_min)} دقیقه است.",
            reply_markup=kb_back(),
        )
        return

    job_id = await db.create_job(user_id=uid, chat_id=m.chat.id, source_url=url)
    await db.update_job(
        job_id,
        title=meta.get("title"),
        artist=meta.get("uploader"),
        duration_sec=duration,
        status="ready",
    )

    title = meta.get("title") or "music"
    artist = meta.get("uploader") or ""
    card = (
        f"🎵 {title}\n"
        f"👤 {artist if artist else 'نامشخص'}\n"
        f"⏱ {to_persian(duration // 60)}:{to_persian(duration % 60).zfill(2)}"
    )
    await status.edit_text(
        card,
        reply_markup=kb_quality(
            job_id,
            estimate_size_mb(duration, 128),
            estimate_size_mb(duration, 320),
        ),
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
) -> str | None:
    """Download audio and send it to the chat."""
    from .keyboards import kb_back

    root = Path(tempfile.gettempdir()) / "music_dl" / str(job_id)
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
        await bot.send_message(chat_id, "❌ دانلود ناموفق بود.", reply_markup=kb_back())
        return None

    src = Path(path)
    ext = src.suffix or ".mp3"

    # Clean title for filename
    clean_name = title or "music"
    if " - " in clean_name and artist:
        parts = clean_name.split(" - ", 1)
        if artist.lower() in parts[0].lower() or parts[1].strip():
            clean_name = parts[1].strip() if len(parts) > 1 else parts[0].strip()
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
        await bot.send_message(
            chat_id, f"❌ ارسال ناموفق: {e}", reply_markup=kb_back()
        )
        return None
    finally:
        shutil.rmtree(root, ignore_errors=True)

    await db.update_job(job_id, status="sent")
    last_quality_by_job[job_id] = quality
    return str(src)
