"""Video handlers — YouTube video download and Twitter/X posts."""

import contextlib
import html as _html
import logging
import mimetypes
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
from .keyboards import kb_back, kb_clip_quality, kb_video_quality, kb_youtube_choice
from .state import PendingStore
from .utils import to_persian

log = logging.getLogger("music_bot.video")

# Pending YouTube selections: token -> url, owned by the user who sent the link.
yt_video_pending = PendingStore()

# Pending X/Twitter video posts awaiting a quality choice; the resolved tweet is
# held so the callback does not have to re-fetch it.
x_clip_pending = PendingStore()

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

# Telegram rejects photos over 10 MB and caps bot video uploads at 50 MB.
# Twitter serves several renditions, so a 1-byte Range probe picks one that fits.
_TG_PHOTO_LIMIT_MB = 10
_TWITTER_VIDEO_CAP_MB = 50

_TWEET_URL_RE = re.compile(
    r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]{1,20})/status/(\d+)",
    flags=re.IGNORECASE,
)

# Twitter rendition paths look like .../vid/avc1/1280x720/name.mp4
_RES_RE = re.compile(r"/(\d+)x(\d+)/")


def _https(u: str) -> str:
    u = (u or "").strip()
    return "https://" + u[len("http://"):] if u.startswith("http://") else u


def _photo_variants(u: str) -> list[str]:
    """Preferred-to-fallback photo URLs; `:orig` is full size and can exceed 10 MB."""
    u = _https(u)
    if not u:
        return []
    if "pbs.twimg.com" not in u:
        return [u]
    base = u.split("?")[0]
    return [f"{base}?name=orig", f"{base}?name=large", f"{base}?name=medium"]


def _video_variants(v: dict) -> list[tuple[str, int, int]]:
    """Direct mp4 renditions as (url, height, estimated_bytes), best-first.

    Drops the m3u8 HLS playlist: Telegram cannot play a manifest and FxTwitter
    reports no size for it. Height comes from the rendition path
    (`/1280x720/`), and the bitrate plus duration yields an estimated size so a
    4K rendition is not downloaded only to be rejected as too large.
    """
    duration = v.get("duration") or 0
    scored: list[tuple[int, float, str]] = []
    for f in v.get("formats") or []:
        if (f.get("container") or "").lower() != "mp4":
            continue
        u = _https(f.get("url") or "")
        if not u:
            continue
        m = _RES_RE.search(u)
        height = int(m.group(2)) if m else 0
        bitrate = f.get("bitrate") or 0
        est = int((bitrate / 8) * duration) if bitrate and duration else 0
        scored.append((height, est, u))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)

    out = [(u, h, e) for h, e, u in scored]
    main = _https(v.get("url") or "")
    if main and main not in {u for u, _, _ in out}:
        out.append((main, 0, 0))
    return out


async def _probe_size(url: str) -> int | None:
    """Size in bytes, read from Content-Range without downloading the body."""
    if not url:
        return None
    try:
        async with get_session().get(url, headers={"Range": "bytes=0-0"}) as resp:
            m = re.search(r"/(\d+)\s*$", resp.headers.get("Content-Range") or "")
            if m:
                return int(m.group(1))
            cl = resp.headers.get("Content-Length")
            if resp.status == 200 and cl and cl.isdigit():
                return int(cl)
    except Exception as e:
        log.info(f"[X] size probe failed: {e}")
    return None


async def _fetch_media(
    url: str, dest: str, base: str, max_bytes: int = 0
) -> tuple[str, str, bool]:
    """Download one remote media URL as (path, content_type, too_big).

    The CDN URL cannot simply be handed to Telegram: the API fetches it
    server-side and a rejection surfaces as a silent no-op. Pulling the bytes
    down here turns that into an ordinary, reportable error. `max_bytes` is
    enforced while streaming, so a rendition whose size could not be probed is
    still cut off rather than downloaded in full.
    """
    path: Path | None = None
    try:
        async with get_session().get(url) as resp:
            if resp.status != 200:
                log.warning(f"[X] media HTTP {resp.status} for {url[:100]}")
                return "", "", False
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            ext = (mimetypes.guess_extension(ctype) if ctype else None) or (
                Path(url.split("?")[0]).suffix or ".bin"
            )
            path = Path(dest) / f"{base}{ext}"
            total = 0
            with open(path, "wb") as fh:
                async for chunk in resp.content.iter_chunked(65536):
                    total += len(chunk)
                    if max_bytes and total > max_bytes:
                        log.info(f"[X] {base} exceeded {max_bytes} bytes, skipping")
                        return "", "", True
                    fh.write(chunk)
    except Exception as e:
        log.warning(f"[X] media download failed: {e}")
        return "", "", False
    if path and path.exists() and path.stat().st_size > 0:
        return str(path), ctype, False
    return "", "", False


async def _pick_video(v: dict, tmp: str, quality: str = "orig") -> tuple[str | None, bool]:
    """Download the requested rendition. Returns (path, oversized).

    `quality` is "orig" for the best available or "480" for a small rendition.
    Candidates go best-first (or nearest-to-480 first), each falling through to
    the next when it is too big or fails to download.
    """
    cap = _TWITTER_VIDEO_CAP_MB * 1024 * 1024
    variants = _video_variants(v)
    if quality == "480":
        # Closest at-or-below 480p first; if every rendition is larger, take the
        # smallest available rather than failing outright.
        small = [t for t in variants if 0 < t[1] <= 480]
        small.sort(key=lambda t: (t[1], t[2]), reverse=True)
        rest = [t for t in variants if not (0 < t[1] <= 480)]
        rest.sort(key=lambda t: t[1] or 10**6)
        variants = small + rest

    oversized = False
    for i, (u, _, _) in enumerate(variants):
        size = await _probe_size(u)
        if size and size > cap:
            oversized = True
            continue
        path, _, too_big = await _fetch_media(u, tmp, f"tweet_video_{i}", max_bytes=cap)
        if path:
            return path, False
        if too_big:
            oversized = True
    return None, oversized


async def _pick_photo(p: dict, tmp: str, idx: int) -> str | None:
    """Download a photo at the largest variant that stays under Telegram's cap."""
    limit = _TG_PHOTO_LIMIT_MB * 1024 * 1024
    for u in _photo_variants(p.get("url") or ""):
        size = await _probe_size(u)
        if size and size > limit:
            continue
        path, _, _ = await _fetch_media(u, tmp, f"tweet_photo_{idx}", max_bytes=limit)
        if path:
            return path
    return None


async def _report(m: Message, status: Message, text: str) -> None:
    """Show an error even when the status message has already been removed."""
    try:
        await status.edit_text(text, reply_markup=kb_back())
        return
    except Exception:
        pass
    with contextlib.suppress(Exception):
        await m.answer(text, reply_markup=kb_back())


async def _fetch_tweet(url: str) -> dict:
    """Resolve an X/Twitter status URL via FxTwitter, newest API shape first."""
    m2 = _TWEET_URL_RE.match(url.strip())
    if not m2:
        return {}
    status_id = m2.group(2)
    for api in (
        f"https://api.fxtwitter.com/2/status/{status_id}",
        f"https://api.fxtwitter.com/status/{status_id}",
    ):
        try:
            async with get_session().get(api) as resp:
                if resp.status != 200:
                    log.info(f"[X] {api} -> HTTP {resp.status}")
                    continue
                data = await resp.json()
        except Exception as e:
            log.warning(f"[X] fetch failed: {e}")
            continue
        # The envelope carries its own code; the API answers HTTP 200 for some
        # errors, so a 200 alone does not mean the post was found.
        code = (data or {}).get("code")
        if code and code != 200:
            log.info(f"[X] API code {code} for {status_id}: {data.get('message')}")
            continue
        tweet = data.get("status") or data.get("tweet") or {}
        if tweet:
            return tweet
    return {}


async def process_tweet(m: Message, bot: Bot, url: str, db=None, settings=None) -> None:
    """X/Twitter post: fetch via FxTwitter API, reply with text + media."""
    uid = m.from_user.id if m.from_user else 0
    # The daily quota is spent on a clip download, which happens in cb_x_clip
    # after the quality pick — reading a post's text or photos is free.

    status = await m.answer("🐦 در حال خواندن پست از ایکس...", reply_markup=kb_back())

    tweet = await _fetch_tweet(url)
    if not tweet:
        await _report(
            m,
            status,
            "❌ نتونستم این پست ایکس رو بخونم.\nلینک رو چک کن یا دوباره امتحان کن.",
        )
        return

    author = ((tweet.get("author") or {}).get("name") or "").strip()
    text = (tweet.get("text") or "").strip()
    link = _html.escape(tweet.get("url") or url, quote=True)
    cap_lines = []
    if author:
        cap_lines.append(f'👤 <a href="{link}">{_html.escape(author)}</a>')
    cap_lines.append("━━━━━━━━━━━━━━")
    if text:
        cap_lines.append(_html.escape(text))
    caption = "\n".join(cap_lines)

    media = tweet.get("media") or {}
    vids = media.get("videos") or []
    photos = media.get("photos") or []

    # A video post is delivered via an explicit quality choice, so the download
    # only starts once the user has picked. The tweet itself is kept in the
    # pending store: re-fetching it on the callback would duplicate the API
    # call and could resolve to a different payload.
    if vids:
        token = uuid.uuid4().hex[:8]
        x_clip_pending.put(
            token,
            {"vids": vids, "photos": photos, "caption": caption},
            user_id=uid,
            chat_id=m.chat.id,
        )
        await status.edit_text(
            "🎬 کیفیت کلیپ رو انتخاب کن:", reply_markup=kb_clip_quality("xclip", token)
        )
        return

    tmp = Path(tempfile.gettempdir()) / "tweet" / uuid.uuid4().hex
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        if photos:
            sent = await _send_photos(m, photos, str(tmp), caption=caption)
            if sent:
                with contextlib.suppress(Exception):
                    await status.delete()
                return

        # Nothing sendable — post the text on its own.
        await status.edit_text(caption[:4096], parse_mode="HTML", reply_markup=kb_back())
    except Exception as e:
        log.warning(f"[X] processing failed: {e}")
        await _report(m, status, f"❌ خطا در ارسال: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def cb_x_clip(c: CallbackQuery, bot: Bot, settings: Settings, db=None) -> None:
    """Deliver an X/Twitter video post at the quality the user picked."""
    try:
        _, token, quality = c.data.split(":")
    except ValueError:
        await c.answer("درخواست نامعتبر.", show_alert=True)
        return
    entry = x_clip_pending.take(token, user_id=c.from_user.id)
    if entry is None:
        await c.answer("منقضی شد یا لینک نامعتبر است.", show_alert=True)
        return
    payload = entry.value
    chat_id = entry.chat_id
    uid = entry.user_id
    await c.answer()

    if db and uid not in settings.admin_ids:
        count = await db.get_video_count_today(uid)
        if count >= DAILY_VIDEO_LIMIT:
            await c.message.edit_text(_limit_message(), reply_markup=kb_back())
            return

    vids = payload.get("vids") or []
    photos = payload.get("photos") or []
    caption = payload.get("caption") or ""

    await c.message.edit_text("⬇️ در حال دانلود کلیپ...")
    tmp = Path(tempfile.gettempdir()) / "tweet" / uuid.uuid4().hex
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        path, oversized = await _pick_video(vids[0], str(tmp), quality)
        if not path:
            await c.message.edit_text(
                "📦 حجم کلیپ از محدودیت ارسال تلگرام بیشتره." if oversized
                else "❌ دانلود کلیپ ناموفق بود.",
                reply_markup=kb_back(),
            )
            return
        size_mb = Path(path).stat().st_size / (1024 * 1024)
        await c.message.edit_text(f"⬆️ در حال ارسال ({to_persian(round(size_mb, 1))} مگ)...")
        try:
            await bot.send_video(
                chat_id=chat_id,
                video=FSInputFile(path),
                caption=caption[:1024],
                parse_mode="HTML",
                supports_streaming=True,
            )
        except Exception as e:
            log.warning(f"[X] send video failed: {e}")
            await c.message.edit_text("❌ ارسال کلیپ ناموفق بود.", reply_markup=kb_back())
            return
        if db and uid not in settings.admin_ids:
            await db.increment_video_count(uid)
        for i, v in enumerate(vids[1:4], start=1):
            extra, _ = await _pick_video(v, str(tmp), quality)
            if extra:
                with contextlib.suppress(Exception):
                    await bot.send_video(chat_id=chat_id, video=FSInputFile(extra))
        await _send_photos_msg(bot, chat_id, photos, str(tmp), caption=None)
        with contextlib.suppress(Exception):
            await c.message.delete()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def _send_photos(m: Message, photos: list, tmp: str, caption: str | None) -> bool:
    """Send up to six photos; the caption rides the first one that uploads."""
    async def _send(path: str, cap: str | None) -> None:
        if cap:
            await m.answer_photo(
                FSInputFile(path), caption=cap[:1024], parse_mode="HTML"
            )
        else:
            await m.answer_photo(FSInputFile(path))

    return await _send_photo_list(photos, tmp, caption, _send)


async def _send_photos_msg(
    bot: Bot, chat_id: int, photos: list, tmp: str, caption: str | None
) -> bool:
    """Photo sender for the callback path, which has no Message to reply to."""
    async def _send(path: str, cap: str | None) -> None:
        await bot.send_photo(
            chat_id=chat_id,
            photo=FSInputFile(path),
            caption=(cap[:1024] if cap else None),
            parse_mode="HTML" if cap else None,
        )

    return await _send_photo_list(photos, tmp, caption, _send)


async def _send_photo_list(photos: list, tmp: str, caption: str | None, send) -> bool:
    """Download and send photos, sharing the caption and surviving per-photo failure."""
    sent = False
    for i, p in enumerate(photos[:6]):
        path = await _pick_photo(p, tmp, i)
        if not path:
            continue
        try:
            await send(path, caption if not sent else None)
        except Exception as e:
            log.warning(f"[X] send photo failed: {e}")
            continue
        sent = True
    return sent

