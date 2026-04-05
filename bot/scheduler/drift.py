"""Drift detection cron job: 15:00 Moscow -> check progress -> push if needed.

Only sends push if 3+ working days without P1 progress.
Does NOT fire on weekends.
"""

import logging
from datetime import date, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.context import load_history, load_yaml

logger = logging.getLogger("cos.scheduler.drift")


def _count_weekdays_without_p1_progress(days_back: int = 10) -> int:
    """Count consecutive weekdays (Mon-Fri) without any 'done' task on P1 intents.

    Scans backwards from today, only counting weekdays.
    Returns count of consecutive weekdays without P1 done tasks.
    """
    intents_data = load_yaml("intents.yaml")
    p1_intent_ids = {
        intent["id"]
        for intent in intents_data.get("intents", [])
        if intent.get("priority") == "P1"
    }

    if not p1_intent_ids:
        return 0

    consecutive_dry_weekdays = 0

    for i in range(1, days_back + 1):
        dt = date.today() - timedelta(days=i)

        # Skip weekends
        if dt.weekday() >= 5:  # Saturday=5, Sunday=6
            continue

        hist = load_history(dt)
        if not hist or not hist.get("tasks"):
            consecutive_dry_weekdays += 1
            continue

        # Check if any P1 task was done
        has_p1_done = any(
            task.get("status") == "done" and task.get("intent") in p1_intent_ids
            for task in hist.get("tasks", [])
        )

        if has_p1_done:
            break  # Found a day with P1 progress, stop counting
        else:
            consecutive_dry_weekdays += 1

    return consecutive_dry_weekdays


def _build_drift_facts() -> str:
    """Build a facts summary for the drift alert message."""
    intents_data = load_yaml("intents.yaml")
    strategy_data = load_yaml("strategy.yaml")
    strategy = strategy_data.get("strategy", {})

    lines = []

    # P1 goals progress
    for intent in intents_data.get("intents", []):
        if intent.get("priority") != "P1":
            continue
        for goal in intent.get("goals", []):
            progress = goal.get("progress", "?")
            title = goal.get("title", "???")
            lines.append(f"  {title}: {progress}")

    # Runway
    for c in strategy.get("hard_constraints", []):
        if c.get("id") == "runway":
            lines.append(f"  Runway: {c['fact']}")
        elif c.get("id") == "f4":
            lines.append(f"  F-4: {c['fact']}")

    return "\n".join(lines)


async def check_drift(bot: Bot, chat_id: int):
    """Check for drift and send alert if needed.

    1. Skip if today is weekend (Sat/Sun)
    2. Load recent history (10 days)
    3. Count consecutive weekdays without P1 progress
    4. If >= 3 weekdays: send drift alert with facts + reason buttons
    5. If < 3: do nothing (no push)
    """
    logger.info("Drift check triggered")

    # Skip weekends entirely
    today = date.today()
    if today.weekday() >= 5:  # Saturday=5, Sunday=6
        logger.info("Drift check skipped: weekend")
        return

    dry_days = _count_weekdays_without_p1_progress()
    logger.info(f"Drift check: {dry_days} weekdays without P1 progress")

    if dry_days < 3:
        return  # No drift, no push

    # Build drift alert
    facts = _build_drift_facts()
    text = (
        f"⚠️ {dry_days} рабочих дней без прогресса по P1.\n\n"
        f"Факты:\n{facts}\n\n"
        f"Что мешает?"
    )

    # Reason buttons
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Некогда", callback_data="drift:busy"),
        InlineKeyboardButton(text="Не знаю что делать", callback_data="drift:stuck"),
    ], [
        InlineKeyboardButton(text="Устал", callback_data="drift:tired"),
        InlineKeyboardButton(text="Переосмыслить", callback_data="drift:rethink"),
    ]])

    await bot.send_message(chat_id, text, reply_markup=keyboard)
    logger.info(f"Drift alert sent: {dry_days} days without P1 progress")
