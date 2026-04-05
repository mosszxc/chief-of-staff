"""Entry point: aiogram polling + APScheduler."""

import asyncio
import logging
import os

from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from bot.handlers.commands import router as commands_router
from bot.handlers.callbacks import router as callbacks_router
from bot.handlers.messages import router as messages_router

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("cos")


def create_bot() -> Bot:
    """Create bot instance from env token."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in .env")
    return Bot(token=token)


def create_dispatcher() -> Dispatcher:
    """Create dispatcher and register routers."""
    dp = Dispatcher()
    dp.include_router(commands_router)
    dp.include_router(callbacks_router)
    dp.include_router(messages_router)
    return dp


async def main():
    """Start bot polling."""
    bot = create_bot()
    dp = create_dispatcher()

    logger.info("Chief of Staff starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
