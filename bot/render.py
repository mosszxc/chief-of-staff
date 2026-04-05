"""Render JSON plan -> Telegram message with inline buttons."""

import logging
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger("cos.render")


def render_plan_message(plan: dict) -> tuple[str, InlineKeyboardMarkup | None]:
    """Convert a structured plan dict to Telegram text + inline keyboard.

    Args:
        plan: Dict with keys: reasoning, runway_weeks, tasks, drift_warning, tomorrow_preview.

    Returns:
        (text, keyboard) tuple.
    """
    tasks = plan.get("tasks", [])
    runway = plan.get("runway_weeks", "?")
    reasoning = plan.get("reasoning", "")

    # Header
    lines = [f"\\U0001f4cb Plan -- Runway {runway} weeks", ""]

    if reasoning:
        lines.append(f"Blocker: {reasoning[:200]}")
        lines.append("")

    # Task list
    for i, task in enumerate(tasks, 1):
        title = task.get("title", "???")
        lines.append(f"{i}. {title}")

    # Drift warning
    drift = plan.get("drift_warning")
    if drift:
        lines.append("")
        lines.append(f"\\u26a0\\ufe0f {drift}")

    # Tomorrow preview
    preview = plan.get("tomorrow_preview")
    if preview:
        lines.append("")
        lines.append(f"Tomorrow: {preview}")

    text = "\n".join(lines)

    # Inline keyboard: one row per task with Done/Skip buttons
    keyboard_rows = []
    for task in tasks:
        task_id = task.get("id", "unknown")
        keyboard_rows.append([
            InlineKeyboardButton(text="\\u2705 Done", callback_data=f"complete:{task_id}"),
            InlineKeyboardButton(text="\\u23ed Skip", callback_data=f"postpone:{task_id}"),
        ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows) if keyboard_rows else None

    return text, keyboard
