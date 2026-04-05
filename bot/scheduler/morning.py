"""Morning cron job: 08:00 Moscow -> generate daily plan -> send to Telegram."""

import logging

logger = logging.getLogger("cos.scheduler.morning")


async def generate_morning_plan(bot, chat_id: int):
    """Generate and send the daily plan.

    1. Load context (strategy, intents, goals, yesterday)
    2. Call Claude (sonnet) with daily_plan recipe
    3. Parse structured JSON response
    4. Render as Telegram message with inline buttons
    5. Send to user
    """
    logger.info("Morning plan triggered")
    # Phase 1: implement full flow
    await bot.send_message(chat_id, "Good morning! Plan generation coming in Phase 1.")
