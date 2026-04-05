"""Free text message handler: routes through router -> action.

Also handles FSM states for partial task completion text input
and onboarding file upload.
"""

import json
import logging
import os
from datetime import datetime

from aiogram import Router, F
from aiogram.enums import ChatAction
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.router import route_message, Intent, INTENT_MODEL, INTENT_RECIPE
from bot.handlers.callbacks import PartialTaskState
from bot.claude import call_claude_safe
from bot.context import (
    load_history, save_history, load_yaml, save_yaml,
    assemble_context, build_system_prompt,
    add_to_memory, grimoire_retrieve,
)

logger = logging.getLogger("cos.messages")

router = Router(name="messages")


def _check_auth(message: Message) -> bool:
    """Check if message is from authorized user."""
    authorized = os.getenv("TELEGRAM_CHAT_ID")
    if not authorized:
        return True
    return message.chat.id == int(authorized)


# --- Task completion via free text ---

_COMPLETE_CLASSIFY_PROMPT = """The user says they completed something. Determine which task/goal this refers to.

Today's tasks:
{tasks}

All goals:
{goals}

User message: "{message}"

Reply with ONLY a JSON object (no markdown, no explanation):
{{"task_id": "matching task ID or null", "goal_id": "matching goal ID or null", "task_title": "human-readable task name", "progress_note": "what was done"}}"""


async def _handle_completion(message: Message) -> None:
    """Handle free text task completion ("сделал portfolio", "откликнулся на 5")."""
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    # Build context for classification
    history = load_history()
    tasks_str = ""
    if history and history.get("tasks"):
        for t in history["tasks"]:
            status = t.get("status", "pending")
            tasks_str += f"- id:{t['id']} title:{t.get('title','?')} status:{status}\n"
    else:
        tasks_str = "No tasks today"

    goals_data = load_yaml("goals.yaml")
    goals_str = ""
    for g in goals_data.get("goals", []):
        goals_str += f"- id:{g['id']} title:{g.get('title','?')} progress:{g.get('progress','?')}\n"

    prompt = _COMPLETE_CLASSIFY_PROMPT.format(
        tasks=tasks_str, goals=goals_str, message=message.text
    )

    result = await call_claude_safe(prompt, model="haiku", timeout=30, recipe="complete_classify")

    if not result:
        await message.answer("Не удалось обработать. Попробуй кнопки под задачей.")
        return

    # Parse the JSON response
    try:
        # Try to extract JSON from response
        text = result.strip()
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                cleaned = part.strip().removeprefix("json").strip()
                try:
                    data = json.loads(cleaned)
                    break
                except (json.JSONDecodeError, TypeError):
                    continue
            else:
                data = None
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end > start:
                data = json.loads(text[start:end + 1])
            else:
                data = None
    except (json.JSONDecodeError, TypeError):
        data = None

    if not data:
        await message.answer("Не понял, какую задачу ты завершил. Попробуй нажать кнопку под задачей.")
        return

    task_title = data.get("task_title", "задача")
    task_id = data.get("task_id")
    goal_id = data.get("goal_id")
    progress_note = data.get("progress_note", "")

    # Update task in history
    updated_task = False
    if task_id and history and history.get("tasks"):
        for task in history["tasks"]:
            if task.get("id") == task_id:
                task["status"] = "done"
                if progress_note:
                    task["note"] = progress_note
                updated_task = True
                break
        if updated_task:
            await save_history(history)

    # Update goal progress timestamp
    if goal_id:
        goals_data = load_yaml("goals.yaml")
        for goal in goals_data.get("goals", []):
            if goal.get("id") == goal_id:
                goal["updated_at"] = datetime.now().isoformat()
                goal["updated_by"] = "telegram"
                logger.info(f"Updated goal {goal_id} via free text")
                break
        await save_yaml("goals.yaml", goals_data)

    # Route indicator + confirmation
    indicator = f"**Отметка задачи: {task_title}.**"
    if updated_task:
        response = f"{indicator}\n\n✅ Записано! {progress_note}" if progress_note else f"{indicator}\n\n✅ Записано!"
    else:
        response = f"{indicator}\n\n✅ Понял, {progress_note}" if progress_note else f"{indicator}\n\n✅ Понял!"

    await message.answer(response, parse_mode="Markdown")


# --- Recipe execution ---

async def _execute_recipe(message: Message, intent: Intent) -> None:
    """Execute a recipe: assemble context -> call Claude -> reply."""
    recipe_name = INTENT_RECIPE.get(intent)
    if not recipe_name:
        await message.answer("Этот тип сообщений пока не поддерживается.")
        return

    model = INTENT_MODEL.get(intent, "sonnet")
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    # Recipe-specific context enrichment
    extra = {"user_message": message.text}

    # Grimoire RAG for domain/interview recipes
    if intent in (Intent.DOMAIN, Intent.INTERVIEW):
        grimoire_data = await grimoire_retrieve(message.text)
        if grimoire_data:
            extra["grimoire_results"] = grimoire_data

    if intent == Intent.RECRUITER:
        extra["recruiter_message"] = message.text

    # Assemble context and build prompt
    context = assemble_context(recipe_name, **extra)
    system_prompt = build_system_prompt(**context)

    # Call Claude
    response = await call_claude_safe(system_prompt, model=model, recipe=recipe_name)

    if not response:
        await message.answer("Claude не ответил. Попробуй позже или переформулируй.")
        return

    # Route indicator: add to response if not CHAT
    indicator_map = {
        Intent.DOMAIN: "Доменный вопрос",
        Intent.INTERVIEW: "Подготовка к интервью",
        Intent.RECRUITER: "Ответ рекрутеру",
        Intent.GOAL_CHANGE: "Изменение цели",
        Intent.CHAT: None,  # No indicator for chat
    }
    indicator = indicator_map.get(intent)
    if indicator:
        response = f"**{indicator}.**\n\n{response}"

    # Save to conversation memory
    add_to_memory("user", message.text)
    add_to_memory("bot", response[:500])

    # Send response (split if too long for Telegram)
    if len(response) <= 4096:
        await message.answer(response, parse_mode="Markdown")
    else:
        # Split into chunks
        for i in range(0, len(response), 4096):
            chunk = response[i:i + 4096]
            await message.answer(chunk, parse_mode="Markdown")


# --- Goal change handling ---

# Keywords that indicate a strategic (big) change vs. a minor tweak
_STRATEGIC_KEYWORDS = [
    "дропаю", "бросаю", "отказываюсь", "другое направление",
    "полностью поменять", "не хочу больше", "может лучше",
    "пересмотреть всё", "всё поменялось", "получил оффер",
    "переехал", "уволился", "новая стратегия",
]

_MINOR_KEYWORDS = [
    "приоритет", "дедлайн", "срок", "добавь goal",
    "поменяй приоритет", "p1", "p2", "p3",
]


def _is_strategic_change(text: str) -> bool:
    """Determine if the goal change is strategic (needs Claude Code)."""
    lower = text.lower()
    # If any strategic keyword matches, it's strategic
    if any(kw in lower for kw in _STRATEGIC_KEYWORDS):
        return True
    # If any minor keyword matches, it's minor
    if any(kw in lower for kw in _MINOR_KEYWORDS):
        return False
    # Default: treat as strategic to be safe
    return True


async def _handle_goal_change(message: Message, intent: Intent) -> None:
    """Handle goal change requests.

    Minor changes (priority, deadline) -> handle in Telegram via recipe.
    Strategic changes (drop goal, pivot) -> challenge with data, redirect to Claude Code.
    """
    text = message.text or ""

    if _is_strategic_change(text):
        # Challenge with strategy data before redirecting
        strategy = load_yaml("strategy.yaml").get("strategy", {})
        constraints = strategy.get("hard_constraints", [])
        blocking = strategy.get("blocking_chain", "")

        challenge_parts = [
            "**Изменение цели.**\n",
            "Прежде чем менять, вот факты:",
        ]

        if constraints:
            for c in constraints:
                challenge_parts.append(f"  - {c.get('id', '?')}: {c.get('fact', '')}")

        if blocking:
            chain = str(blocking).strip()
            if len(chain) > 100:
                chain = chain[:100] + "..."
            challenge_parts.append(f"\nBlocking chain: {chain}")

        challenge_parts.append(
            "\nЭто стратегическое решение. Для полного анализа "
            "открой Claude Code -- там ресёрч, данные, декомпозиция."
        )

        await message.answer("\n".join(challenge_parts), parse_mode="Markdown")
    else:
        # Minor change -- execute recipe normally
        await _execute_recipe(message, intent)


# --- Onboarding callback handlers (must be before catch-all) ---

@router.callback_query(F.data == "onboard:file")
async def on_onboard_file(callback: CallbackQuery):
    """User chose to upload strategy file."""
    await callback.answer()
    await callback.message.answer(
        "📎 Отправь .md файл стратегии.\n\n"
        "Пока данные уже заполнены в data/ — "
        "можешь сразу нажать /today для плана."
    )


@router.callback_query(F.data == "onboard:questions")
async def on_onboard_questions(callback: CallbackQuery):
    """User chose to answer questions for onboarding."""
    await callback.answer()
    await callback.message.answer(
        "💬 Онбординг через вопросы будет в Phase 3.\n\n"
        "Пока данные уже заполнены в data/ — "
        "можешь сразу нажать /today для плана."
    )


# --- Drift reason callbacks ---

@router.callback_query(F.data.startswith("drift:"))
async def on_drift_reason(callback: CallbackQuery):
    """Handle drift alert reason button press."""
    reason = callback.data.split(":", 1)[1]
    reason_text = {
        "busy": "Некогда",
        "stuck": "Не знаю что делать",
        "tired": "Устал",
        "rethink": "Переосмыслить",
    }.get(reason, reason)

    logger.info(f"Drift reason: {reason}")
    await callback.answer()

    responses = {
        "busy": "Понял. Попробуй выделить 10 минут на самую маленькую P1 задачу. Даже 10 минут — это прогресс.",
        "stuck": "Давай разберём что конкретно блокирует. Напиши: что не можешь сделать и почему.",
        "tired": "Ок. Сегодня отдых. Но завтра — хотя бы одна P1 задача, даже маленькая.",
        "rethink": "Хорошо. Напиши что хочешь пересмотреть — разберём.",
    }
    await callback.message.answer(responses.get(reason, "Понял, запомню."))
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


# --- Partial task: waiting for text ---

@router.message(PartialTaskState.waiting_for_text)
async def on_partial_text(message: Message, state: FSMContext):
    """Handle text response for partial task completion."""
    if not _check_auth(message):
        return

    data = await state.get_data()
    task_id = data.get("partial_task_id")
    note = message.text or ""

    if task_id:
        history = load_history()
        for task in history.get("tasks", []):
            if task.get("id") == task_id:
                task["status"] = "partial"
                task["note"] = note
                task_title = task.get("title", task_id)
                break
        else:
            task_title = task_id
        await save_history(history)
        await message.answer(f"📝 {task_title}: {note}\nЗаписано!")
    else:
        await message.answer("Не нашёл задачу.")

    await state.clear()


# --- Catch-all: free text ---

@router.message()
async def on_message(message: Message):
    """Handle any text message not caught by commands/callbacks/FSM."""
    if not message.text:
        return
    if not _check_auth(message):
        return

    intent = await route_message(message.text)
    logger.info(f"[message] '{message.text[:50]}' -> {intent.value}")

    match intent:
        case Intent.PLAN:
            # Redirect to /today command logic
            from bot.scheduler.morning import generate_morning_plan
            from bot.context import load_history as lh
            from bot.render import render_plan_message
            from datetime import date

            history = lh(date.today())
            if history and history.get("tasks"):
                plan = {
                    "reasoning": history.get("plan_reasoning", ""),
                    "runway_weeks": "?",
                    "tasks": history["tasks"],
                    "drift_warning": None,
                    "tomorrow_preview": None,
                }
                text, keyboard = render_plan_message(plan)
                await message.answer(text, reply_markup=keyboard)
            else:
                await message.answer("⏳ Генерирую план...")
                await generate_morning_plan(message.bot, message.chat.id)

        case Intent.STATUS:
            # Inline status display
            intents_data = load_yaml("intents.yaml")
            intents = intents_data.get("intents", [])
            if not intents:
                await message.answer("Нет целей. Нажми /start для настройки.")
                return
            lines = ["🎯 Прогресс:", ""]
            for intent_item in intents:
                priority = intent_item.get("priority", "?")
                title = intent_item.get("title", "???")
                lines.append(f"[{priority}] {title}")
                for goal in intent_item.get("goals", []):
                    progress = goal.get("progress", "?")
                    goal_title = goal.get("title", "???")
                    lines.append(f"  - {goal_title}: {progress}")
                lines.append("")
            await message.answer("\n".join(lines))

        case Intent.COMPLETE:
            await _handle_completion(message)

        case Intent.POSTPONE:
            # For free text postpone, acknowledge and suggest using buttons
            await message.answer("Понял. Используй кнопку ⏭ под задачей чтобы отложить конкретную задачу.")

        case Intent.ADD_TASK:
            await message.answer("Добавление задач будет в Phase 3 (MCP tools).\nПока задачи генерируются автоматически в утреннем плане.")

        case Intent.GOAL_CHANGE:
            await _handle_goal_change(message, intent)

        case _:
            # All other intents: execute recipe
            await _execute_recipe(message, intent)
