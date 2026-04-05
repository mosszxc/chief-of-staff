"""Evening cron job: 22:00 Moscow -> evening summary -> send to Telegram."""

import logging

logger = logging.getLogger("cos.scheduler.evening")


async def generate_evening_summary(bot, chat_id: int):
    """Generate and send evening summary.

    1. Load today's plan + completion status
    2. Call Claude (haiku) for summary
    3. Ask for energy level (1-5 buttons)
    4. Generate tomorrow preview
    """
    logger.info("Evening summary triggered")
    # Phase 2: implement full flow
    await bot.send_message(chat_id, "Evening summary coming in Phase 2.")
