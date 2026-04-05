"""Morning cron job: 08:00 Moscow -> generate daily plan -> send to Telegram."""

import json
import logging
from datetime import date

from aiogram import Bot
from aiogram.enums import ChatAction

from bot.claude import call_claude_safe
from bot.context import assemble_context, build_system_prompt, save_history, load_history
from bot.render import render_plan_message

logger = logging.getLogger("cos.scheduler.morning")


def _parse_plan_json(text: str) -> dict | None:
    """Extract JSON plan from Claude's response.

    Claude might wrap JSON in markdown code blocks, so we try multiple approaches.
    """
    # Try direct parse first
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try extracting from markdown code block
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            # Skip the language identifier line
            cleaned = part.strip()
            if cleaned.startswith("json"):
                cleaned = cleaned[4:].strip()
            try:
                return json.loads(cleaned)
            except (json.JSONDecodeError, TypeError):
                continue

    # Try finding JSON object in text
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, TypeError):
            pass

    return None


def _generate_task_ids(plan: dict) -> dict:
    """Ensure all tasks have stable IDs: YYYY-MM-DD_N format."""
    today = date.today().isoformat()
    tasks = plan.get("tasks", [])
    for i, task in enumerate(tasks, 1):
        if not task.get("id") or not task["id"].startswith("20"):
            task["id"] = f"{today}_{i}"
    return plan


async def generate_morning_plan(bot: Bot, chat_id: int) -> dict | None:
    """Generate and send the daily plan.

    1. Load context (strategy, intents, goals, yesterday)
    2. Call Claude (sonnet) with daily_plan recipe
    3. Parse structured JSON response
    4. Save plan to history
    5. Render as Telegram message with inline buttons
    6. Send to user

    Returns the plan dict, or None on failure.
    """
    logger.info("Morning plan generation started")

    # Show typing indicator
    await bot.send_chat_action(chat_id, ChatAction.TYPING)

    # Assemble context
    context = assemble_context("daily_plan")
    system_prompt = build_system_prompt(**context)

    # Full prompt = system prompt (already includes recipe instruction)
    prompt = system_prompt

    # Call Claude
    response = await call_claude_safe(prompt, model="sonnet", recipe="daily_plan")

    if response is None:
        # Fallback: send yesterday's plan with warning
        logger.warning("Claude failed for morning plan, using fallback")
        yesterday_hist = load_history()
        if yesterday_hist and yesterday_hist.get("tasks"):
            text = "\u26a0\ufe0f Claude \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u0435\u043d. \u0412\u0447\u0435\u0440\u0430\u0448\u043d\u0438\u0439 \u043f\u043b\u0430\u043d:\n\n"
            for t in yesterday_hist["tasks"]:
                text += f"- {t.get('title', '???')}\n"
            await bot.send_message(chat_id, text)
        else:
            await bot.send_message(chat_id, "\u26a0\ufe0f \u041d\u0435 \u0443\u0434\u0430\u043b\u043e\u0441\u044c \u0441\u0433\u0435\u043d\u0435\u0440\u0438\u0440\u043e\u0432\u0430\u0442\u044c \u043f\u043b\u0430\u043d. \u041f\u043e\u043f\u0440\u043e\u0431\u0443\u0439 /today \u0447\u0435\u0440\u0435\u0437 5 \u043c\u0438\u043d\u0443\u0442.")
        return None

    # Parse JSON
    plan = _parse_plan_json(response)
    if plan is None:
        logger.warning("Failed to parse plan JSON, sending raw text")
        # Send raw response as fallback
        await bot.send_message(chat_id, f"\U0001f4cb \u041f\u043b\u0430\u043d \u043d\u0430 \u0441\u0435\u0433\u043e\u0434\u043d\u044f:\n\n{response[:4000]}")
        return None

    # Generate stable task IDs
    plan = _generate_task_ids(plan)

    # Save to history
    today_str = date.today().isoformat()
    history = {
        "date": today_str,
        "plan_reasoning": plan.get("reasoning", ""),
        "tasks": [
            {
                "id": t["id"],
                "title": t.get("title", "???"),
                "intent": t.get("intent", ""),
                "goal_id": t.get("goal_id"),
                "progress_delta": t.get("progress_delta"),
                "status": "pending",
            }
            for t in plan.get("tasks", [])
        ],
        "skipped_tasks": [],
        "energy": None,
    }
    await save_history(history)

    # Render and send
    text, keyboard = render_plan_message(plan)
    await bot.send_message(chat_id, text, reply_markup=keyboard)

    logger.info(f"Morning plan sent: {len(plan.get('tasks', []))} tasks")
    return plan
