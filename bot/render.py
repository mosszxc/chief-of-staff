"""Render JSON plan -> Telegram message with inline buttons."""

import logging
from datetime import date

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger("cos.render")

_DAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_MONTHS_RU = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"
]


def _format_date() -> str:
    today = date.today()
    day_name = _DAYS_RU[today.weekday()]
    return f"{day_name}, {today.day} {_MONTHS_RU[today.month]}"


def render_plan_message(plan: dict) -> tuple[str, InlineKeyboardMarkup | None]:
    """Compact plan: header + blocker + numbered tasks + labeled buttons."""
    tasks = plan.get("tasks", [])
    runway = plan.get("runway_weeks", "?")
    reasoning = plan.get("reasoning", "")

    # Header
    date_str = _format_date()
    lines = [f"📋 {date_str} — Runway {runway} нед"]

    # Blocker: first sentence of reasoning (one line max)
    if reasoning:
        first_line = reasoning.split(".")[0].strip() + "."
        if len(first_line) > 80:
            first_line = first_line[:77] + "..."
        lines.append(f"🚧 {first_line}")

    lines.append("")

    # Tasks: short, numbered, no context hints
    for i, task in enumerate(tasks, 1):
        title = task.get("title", "???")
        lines.append(f"{i}. {title}")

    # Tomorrow preview
    preview = plan.get("tomorrow_preview")
    if preview:
        lines.append(f"\n📅 Завтра: {preview}")

    # Drift warning
    drift = plan.get("drift_warning")
    if drift:
        lines.append(f"\n⚠️ {drift}")

    text = "\n".join(lines)

    # --- Buttons ---
    # Row 1: plan actions
    plan_row = [
        InlineKeyboardButton(text="👍 Принять", callback_data="plan:accept"),
        InlineKeyboardButton(text="💬 Изменить", callback_data="plan:change"),
        InlineKeyboardButton(text="💡 Почему?", callback_data="plan:why"),
    ]

    # Task buttons: one row with just numbers
    # Press number → bot asks what to do with that task
    task_buttons = []
    for i, task in enumerate(tasks, 1):
        task_id = task.get("id", f"task_{i}")
        task_buttons.append(
            InlineKeyboardButton(text=str(i), callback_data=f"task:{task_id}")
        )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[plan_row, task_buttons])

    # Store full reasoning for "Why?" button
    plan["_full_reasoning"] = reasoning

    return text, keyboard


def render_reasoning(plan: dict) -> str:
    """Full reasoning for 'Why?' button."""
    reasoning = plan.get("_full_reasoning", plan.get("reasoning", ""))
    tasks = plan.get("tasks", [])

    lines = ["💡 Почему такой план:\n"]
    if reasoning:
        lines.append(reasoning)

    # Context hints per task
    hints = []
    for i, task in enumerate(tasks, 1):
        hint = task.get("context_hint", "")
        if hint:
            hints.append(f"{i}. {hint}")
    if hints:
        lines.append("\nПодробности:")
        lines.extend(hints)

    return "\n".join(lines)


def render_task_done(task_title: str) -> str:
    return f"✅ {task_title}"


def render_task_skipped(task_title: str, times_skipped: int = 1) -> str:
    suffix = f" ({times_skipped}-й раз)" if times_skipped > 1 else ""
    return f"⏭ {task_title}{suffix}"


def render_task_partial(task_title: str, note: str) -> str:
    return f"📝 {task_title}: {note}"


def render_evening_summary(history: dict) -> tuple[str, InlineKeyboardMarkup]:
    tasks = history.get("tasks", [])

    lines = ["📊 Итог дня", ""]

    done_count = 0
    total = len(tasks)
    for t in tasks:
        status = t.get("status", "pending")
        title = t.get("title", "???")
        icon = {"done": "✅", "skipped": "⏭", "partial": "📝", "pending": "⬜"}.get(status, "❓")
        lines.append(f"{icon} {title}")
        if status == "done":
            done_count += 1

    if total > 0:
        pct = int(done_count / total * 100)
        lines.append(f"\nВыполнено: {done_count}/{total} ({pct}%)")

    lines.append("\n⚡ Энергия сегодня:")

    text = "\n".join(lines)

    energy_buttons = [
        InlineKeyboardButton(text=str(i), callback_data=f"energy:{i}")
        for i in range(1, 6)
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[energy_buttons])

    return text, keyboard
