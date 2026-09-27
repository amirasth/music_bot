FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# nodejs is not an app dependency: yt-dlp uses it as the JavaScript runtime
# that solves YouTube's signature/n-parameter challenges. Without it yt-dlp
# extracts a single degraded format that 403s intermittently, which the bot
# reports as a download failure (codes 1001/2001).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

RUN mkdir -p /data

CMD ["python", "-m", "music_bot.main"]
