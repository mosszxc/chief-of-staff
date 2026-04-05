"""Evening cron job: 22:00 Moscow -> evening summary -> send to Telegram."""

import logging

from aiogram import Bot

from bot.context import load_history
from bot.render import render_evening_summary

logger = logging.getLogger("cos.scheduler.evening")


async def generate_evening_summary(bot: Bot, chat_id: int):
    """Generate and send evening summary.

    1. Load today's plan + completion status
    2. Show planned vs completed
    3. Ask for energy level (1-5 buttons)
    """
    logger.info("Evening summary triggered")

    history = load_history()
    if not history or not history.get("tasks"):
        await bot.send_message(
            chat_id,
            "\U0001f4ca \u0418\u0442\u043e\u0433: \u0441\u0435\u0433\u043e\u0434\u043d\u044f \u043d\u0435 \u0431\u044b\u043b\u043e \u043f\u043b\u0430\u043d\u0430. \u0417\u0430\u0432\u0442\u0440\u0430 \u0432 08:00 \u043f\u0440\u0438\u0448\u043b\u044e \u043d\u043e\u0432\u044b\u0439."
        )
        return

    text, keyboard = render_evening_summary(history)
    await bot.send_message(chat_id, text, reply_markup=keyboard)
    logger.info("Evening summary sent")
