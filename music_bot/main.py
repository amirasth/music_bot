"""Entry point — initialize bot, dispatcher, and start polling."""

import asyncio
import logging

from aiogram import Bot, Dispatcher

from .config import load_settings
from .db import DB
from .handlers import setup_handlers
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

    db = DB(settings.db_path)
    await db.init()

    dp = Dispatcher()
    router = setup_handlers_router(db, settings)
    dp.include_router(router)

    bot = Bot(token=settings.bot_token)
    await bot.delete_webhook(drop_pending_updates=False)
    log.info("Bot starting polling...")
    await dp.start_polling(bot)


def setup_handlers_router(db: DB, settings):
    """Create a router and register all handlers."""
    from aiogram import Router

    router = Router()
    setup_handlers(router, db, settings)
    return router


if __name__ == "__main__":
    asyncio.run(main())
