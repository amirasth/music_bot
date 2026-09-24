# Music Bot — Telegram Music Bot

ربات تلگرام دانلود موزیک با قابلیت شناسایی خودکار آهنگ از طریق Shazam.

## قابلیت‌ها

- **شناسایی موزیک**: از لینک‌های Spotify، Instagram، TikTok، SoundCloud
- **دانلود صوت**: کیفیت 128 یا 320 kbps با ID3 tag
- **دانلود ویدیو**: YouTube با کیفیت 360p/480p/720p (حداکثر ۵۰MB)
- **کلیپ اینستاگرام و ایکس**: انتخاب بین کم‌حجم (۴۸۰p) و کیفیت اصلی
- **جستجو**: نام آهنگ با نمایش 6 نتیجه
- **پشتیبانی از Twitter/X**: نمایش متن و مدیا پست
- **منابع متعدد**: Avaland (فارسی)، Audius، SoundCloud، Piped، YouTube، Archive.org
- **ادمین**: دکمه «📊 آمار ربات» در منوی اصلی، به‌همراه `/info` و `/jobs`

## راهنمای دیپلوی روی Railway

### قدم ۱: آماده‌سازی GitHub

1. یک ریپوزیتوری جدید در GitHub بسازید
2. فایل‌های پروژه را push کنید

### قدم ۲: ساخت پروژه در Railway

1. به [railway.app](https://railway.app) بروید
2. روی **New Project** کلیک کنید
3. **Deploy from GitHub** را انتخاب کنید
4. ریپوزیتوری خود را انتخاب کنید

### قدم ۳: تنظیم متغیرهای محیطی

در بخش **Variables** پروژه Railway:

| Variable | Value |
|---|---|
| `BOT_TOKEN` | توکن ربات تلگرام خود |
| `ADMIN_IDS` | آیدی عددی تلگرام خود |
| `DB_PATH` | `/data/music_bot.db` |

متغیرهای اختیاری:

| Variable | Default | توضیح |
|---|---|---|
| `MAX_DURATION_MIN` | `15` | حداکثر طول فایل (دقیقه) |
| `MAX_FILE_MB` | `35` | سقف حجم فایل صوتی |
| `MAX_VIDEO_MB` | `50` | سقف حجم ویدیو (سقف تلگرام ۵۰ است) |
| `BOT_TZ` | `Asia/Tehran` | مرز ریست محدودیت روزانه |
| `YTDL_CONCURRENCY` | `4` | دانلود همزمان yt-dlp |
| `COOKIES_FILE` | — | مسیر فایل کوکی برای یوتیوب |
| `YT_PROXY` | — | پراکسی برای یوتیوب |

### قدم ۴: ساخت Volume

1. در بخش **Volumes** پروژه Railway
2. **New Volume** را کلیک کنید
3. **Mount Path**: `/data` تنظیم کنید

### قدم ۵: مشاهده لاگ‌ها

در بخش **Deployments** می‌توانید لاگ‌ها را مشاهده کنید.

## راهنمای توسعه محلی

### پیش‌نیازها

- Python 3.11+
- ffmpeg

### نصب

```bash
git clone <repo-url>
cd music_bot
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
```

### تنظیم

فایل `.env` بسازید:

```env
BOT_TOKEN=YOUR_TOKEN
ADMIN_IDS=YOUR_ID
DB_PATH=./music_bot.db
```

### اجرا

```bash
python -m music_bot.main
```

## ساختار پروژه

```
music_bot/
├── music_bot/
│   ├── __init__.py
│   ├── config.py          # تنظیمات
│   ├── utils.py           # توابع کمکی
│   ├── http.py            # سشن مشترک aiohttp
│   ├── state.py           # دکمه‌های در انتظار (با TTL و مالکیت کاربر)
│   ├── db.py              # دیتابیس SQLite
│   ├── sources.py         # منابع جستجو
│   ├── shazam.py          # شناسایی موزیک
│   ├── downloader.py      # دانلودر yt-dlp
│   ├── keyboards.py       # منوها و دکمه‌ها
│   ├── handlers.py        # هندلرهای اصلی
│   ├── video.py           # هندلرهای ویدیو/توییتر
│   └── main.py            # نقطه ورود
├── requirements.txt
├── Dockerfile
├── Procfile
├── nixpacks.toml
├── .env.example
├── .gitignore
└── README.md
```

## عیب‌یابی

### YouTube blocked from datacenter IPs

اگر YouTube از IP سرور بلاک شده:

1. فایل کوکی مرورگر خود را دانلود کنید (فرمت Netscape)
2. فایل را به نام `cookies.txt` در روت پروژه قرار دهید
3. متغیر `COOKIES_FILE=/app/cookies.txt` را تنظیم کنید

یا از پراکسی استفاده کنید:

```env
YT_PROXY=socks5://user:pass@host:1080
```

### File too big

اگر فایل بیش از حد بزرگ است:

- متغیر `MAX_FILE_MB` را کاهش دهید
- متغیر `MAX_DURATION_MIN` را کاهش دهید

### Shazam fails

اگر Shazam نتوانست آهنگ را تشخیص دهد:

- از کیفیت صوتی خوب لینک مطمئن شوید
- لینک مستقیم یوتیوب بفرستید
- از دستور جستجو استفاده کنید

## لایسنس

MIT
