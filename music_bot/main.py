"""Entry point — initialize bot, dispatcher, and start polling."""

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher, Router

from .config import load_settings
from .db import DB
from .downloader import configure_concurrency
from .handlers import setup_handlers
from .http import close_session
from .sources import AVALAND_AVAILABLE

log = logging.getLogger("music_bot")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = load_settings()
    log.info(f"[INIT] Avaland available: {AVALAND_AVAILABLE}")
    log.info(f"[INIT] Admin IDs: {settings.admin_ids}")
    log.info(f"[INIT] Timezone: {settings.bot_tz}")
    configure_concurrency(settings.ytdl_concurrency)

    db = DB(settings.db_path, tz=settings.bot_tz)
    await db.init()

    dp = Dispatcher()
    router = setup_handlers_router(db, settings)
    dp.include_router(router)

    bot = Bot(token=settings.bot_token)
    await bot.delete_webhook(drop_pending_updates=False)
    log.info("Bot starting polling...")
    try:
        await dp.start_polling(bot)
    finally:
        # Release the shared connector and the Telegram session so the process
        # can exit without dangling sockets.
        await close_session()
        with contextlib.suppress(Exception):
            await bot.session.close()
        log.info("Bot stopped.")


def setup_handlers_router(db: DB, settings) -> Router:
    """Create a router and register all handlers."""
    router = Router()
    setup_handlers(router, db, settings)
    return router


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
