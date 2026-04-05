"""Entry point: aiogram polling + APScheduler."""

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

from bot.handlers.commands import router as commands_router
from bot.handlers.callbacks import router as callbacks_router
from bot.handlers.voice import router as voice_router
from bot.handlers.messages import router as messages_router
from bot.scheduler.morning import generate_morning_plan
from bot.scheduler.evening import generate_evening_summary
from bot.scheduler.drift import check_drift

load_dotenv()

# Setup logging — structured, with rotation
LOG_DIR = Path(__file__).resolve().parent.parent / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

from logging.handlers import RotatingFileHandler

_log_format = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

# Main log: all messages, 5MB rotation, keep 7 files
_main_handler = RotatingFileHandler(
    LOG_DIR / "cos.log", maxBytes=5_000_000, backupCount=7, encoding="utf-8"
)
_main_handler.setFormatter(logging.Formatter(_log_format))

# Error log: only WARNING+, separate file for quick debugging
_error_handler = RotatingFileHandler(
    LOG_DIR / "errors.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
)
_error_handler.setLevel(logging.WARNING)
_error_handler.setFormatter(logging.Formatter(_log_format))

# Claude calls log: track every LLM call (recipe, model, time, tokens)
_claude_handler = RotatingFileHandler(
    LOG_DIR / "claude.log", maxBytes=5_000_000, backupCount=7, encoding="utf-8"
)
_claude_handler.setFormatter(logging.Formatter(_log_format))
logging.getLogger("cos.claude").addHandler(_claude_handler)

# Console: INFO
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(logging.Formatter(_log_format))

logging.basicConfig(
    level=logging.INFO,
    handlers=[_console_handler, _main_handler, _error_handler],
)

# Reduce noise from libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("aiogram.event").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

logger = logging.getLogger("cos")


def create_bot() -> Bot:
    """Create bot instance from env token."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN not set in .env")
    return Bot(token=token)


def create_dispatcher() -> Dispatcher:
    """Create dispatcher with FSM storage and register routers."""
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(commands_router)
    dp.include_router(callbacks_router)
    dp.include_router(voice_router)
    dp.include_router(messages_router)
    return dp


def create_scheduler(bot: Bot) -> AsyncIOScheduler:
    """Create APScheduler with morning and evening cron jobs."""
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not chat_id:
        logger.warning("TELEGRAM_CHAT_ID not set -- scheduler jobs won't have a target")

    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")

    if chat_id:
        cid = int(chat_id)

        # Morning plan: 08:00 Moscow
        scheduler.add_job(
            generate_morning_plan,
            trigger=CronTrigger(hour=8, minute=0, timezone="Europe/Moscow"),
            args=[bot, cid],
            id="morning_plan",
            name="Morning Plan",
            replace_existing=True,
        )

        # Evening summary: 22:00 Moscow
        scheduler.add_job(
            generate_evening_summary,
            trigger=CronTrigger(hour=22, minute=0, timezone="Europe/Moscow"),
            args=[bot, cid],
            id="evening_summary",
            name="Evening Summary",
            replace_existing=True,
        )

        # Drift check: 15:00 Moscow (weekdays only — checked inside the function)
        scheduler.add_job(
            check_drift,
            trigger=CronTrigger(hour=15, minute=0, timezone="Europe/Moscow"),
            args=[bot, cid],
            id="drift_check",
            name="Drift Check",
            replace_existing=True,
        )

        logger.info(f"Scheduler configured: morning=08:00, evening=22:00, drift=15:00 MSK, chat_id={cid}")

    return scheduler


async def main():
    """Start bot polling with scheduler."""
    bot = create_bot()
    dp = create_dispatcher()
    scheduler = create_scheduler(bot)

    scheduler.start()
    logger.info("Chief of Staff starting... (scheduler active)")

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown()
        logger.info("Scheduler shut down")


if __name__ == "__main__":
    asyncio.run(main())
