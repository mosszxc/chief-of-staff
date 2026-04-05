"""Free text message handler: routes through router -> action.

Also handles FSM states for partial task completion text input,
onboarding file upload, and new intent review/redo workflow.
"""

import asyncio
import json
import logging
import os
from datetime import datetime

from aiogram import Router, F
from aiogram.enums import ChatAction
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

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


# --- FSM states for New Intent workflow ---

class NewIntentState(StatesGroup):
    """FSM states for the new intent review/redo cycle."""
    waiting_for_redo_feedback = State()


# --- Progress-updating Claude call helper ---

_PROGRESS_STATUSES = [
    "🔍 Исследую тему...",
    "📊 Проверяю что у тебя уже есть по теме...",
    "🌐 Ищу лучшие методы в интернете...",
    "🌐 Ищу реалистичные сроки...",
    "📚 Проверяю базу знаний...",
    "🧩 Собираю goals и метрики...",
    "✍️ Формирую методологию...",
    "⏳ Ещё работаю, это большой ресёрч...",
    "🧠 Синтезирую всё вместе...",
    "📝 Финализирую план...",
    "⏳ Почти готово, проверяю...",
]

# Timeout for new_intent recipes (research can take 5-10 minutes)
_NEW_INTENT_TIMEOUT = 600


async def call_claude_with_progress(bot, chat_id: int, prompt: str, model: str = "sonnet", recipe: str = "unknown"):
    """Call Claude while showing progress status updates to the user.

    Sends a status message and updates it every 20 sec while waiting.
    Covers up to 10 minutes of waiting with progressive statuses.
    Returns (result_text, status_message) so caller can edit/delete the status msg.
    """
    msg = await bot.send_message(chat_id, _PROGRESS_STATUSES[0])

    # Use extended timeout for new_intent recipes
    timeout = _NEW_INTENT_TIMEOUT if "new_intent" in recipe else None

    # Start Claude call as background task
    task = asyncio.create_task(call_claude_safe(prompt, model=model, recipe=recipe, timeout=timeout))

    # Update status every 20 sec while waiting
    for status_text in _PROGRESS_STATUSES[1:]:
        await asyncio.sleep(20)
        if task.done():
            break
        try:
            await msg.edit_text(status_text)
        except Exception:
            pass

    # After all statuses exhausted, loop "still working" every 30 sec
    while not task.done():
        await asyncio.sleep(30)
        if task.done():
            break
        try:
            await msg.edit_text("🔄 Всё ещё работаю...")
        except Exception:
            pass

    result = await task
    return result, msg


# --- JSON extraction helper ---

def _extract_json(text: str) -> dict | None:
    """Extract JSON object from Claude response (handles markdown fences, extra text)."""
    if not text:
        return None
    text = text.strip()

    # Try stripping markdown fences
    if "```" in text:
        parts = text.split("```")
        for part in parts:
            cleaned = part.strip().removeprefix("json").strip()
            try:
                return json.loads(cleaned)
            except (json.JSONDecodeError, TypeError):
                continue

    # Try finding raw JSON object
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except (json.JSONDecodeError, TypeError):
            pass

    return None


# --- Render intent plan for Telegram ---

def _render_intent_plan(data: dict) -> str:
    """Render a new intent plan as a readable Telegram message."""
    lines = []

    title = data.get("title", "Новая цель")
    priority = data.get("priority", "P3")
    deadline = data.get("deadline", "—")
    success = data.get("success", "")
    methodology = data.get("methodology", "")
    reasoning = data.get("reasoning", "")

    lines.append(f"🎯 {title}")
    lines.append(f"Приоритет: {priority} | Дедлайн: {deadline}")
    if success:
        lines.append(f"Критерий успеха: {success}")
    lines.append("")

    if methodology:
        lines.append("📋 Метод:")
        lines.append(methodology)
        lines.append("")

    goals = data.get("goals", [])
    if goals:
        lines.append("🏁 Goals:")
        for i, g in enumerate(goals, 1):
            lines.append(f"  {i}. {g.get('title', '?')} — {g.get('progress', '0')}")
        lines.append("")

    if reasoning:
        lines.append(f"💡 {reasoning}")

    return "\n".join(lines)


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


async def _handle_new_intent(message: Message, state: FSMContext) -> None:
    """Handle new intent creation workflow.

    1. Show progress statuses while Claude works
    2. Parse JSON response
    3. Show plan with [Accept] [Redo] buttons
    4. Set FSM state for redo feedback if needed
    """
    user_text = message.text or ""

    # Assemble context for the new_intent recipe
    context = assemble_context("new_intent", user_message=user_text)
    system_prompt = build_system_prompt(**context)

    # Call Claude with progress updates
    result, status_msg = await call_claude_with_progress(
        message.bot, message.chat.id, system_prompt,
        model="sonnet", recipe="new_intent"
    )

    if not result:
        try:
            await status_msg.edit_text("❌ Claude не ответил. Попробуй позже.")
        except Exception:
            await message.answer("❌ Claude не ответил. Попробуй позже.")
        return

    # Parse JSON from response
    data = _extract_json(result)

    if not data or "intent_id" not in data:
        # Claude didn't return valid JSON — show raw response
        logger.warning(f"[new_intent] Failed to parse JSON from response: {result[:200]}")
        try:
            await status_msg.edit_text(
                f"Не удалось разобрать план. Вот что Claude ответил:\n\n{result[:3000]}"
            )
        except Exception:
            await message.answer(f"Вот что получилось:\n\n{result[:3000]}")
        return

    # Render the plan
    plan_text = _render_intent_plan(data)

    # Build accept/redo buttons
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍 Принять", callback_data="intent:accept"),
        InlineKeyboardButton(text="💬 Переделать", callback_data="intent:redo"),
    ]])

    # Delete status message, send plan as new message
    try:
        await status_msg.delete()
    except Exception:
        pass

    await message.answer(plan_text, reply_markup=kb)

    # Save plan data in FSM state for accept/redo handlers
    await state.update_data(
        pending_intent=data,
        original_request=user_text,
    )

    # Save to memory
    add_to_memory("user", user_text)
    add_to_memory("bot", f"[NEW INTENT] {data.get('title', '?')}")


async def _handle_intent_redo(message: Message, state: FSMContext) -> None:
    """Handle intent redo: user said what they didn't like, call Claude again."""
    feedback = message.text or ""
    fsm_data = await state.get_data()

    original_request = fsm_data.get("original_request", "")
    previous_plan = fsm_data.get("pending_intent", {})

    # Build redo prompt with context
    redo_extra = {
        "user_message": (
            f"Первоначальный запрос: {original_request}\n\n"
            f"Предыдущий план (юзеру не понравился):\n"
            f"{json.dumps(previous_plan, ensure_ascii=False, indent=2)}\n\n"
            f"Обратная связь: {feedback}\n\n"
            f"Переделай план с учётом обратной связи."
        ),
    }

    context = assemble_context("new_intent", **redo_extra)
    system_prompt = build_system_prompt(**context)

    # Call Claude with progress
    result, status_msg = await call_claude_with_progress(
        message.bot, message.chat.id, system_prompt,
        model="sonnet", recipe="new_intent_redo"
    )

    if not result:
        try:
            await status_msg.edit_text("❌ Claude не ответил. Попробуй ещё раз.")
        except Exception:
            await message.answer("❌ Claude не ответил. Попробуй ещё раз.")
        await state.clear()
        return

    data = _extract_json(result)

    if not data or "intent_id" not in data:
        logger.warning(f"[new_intent_redo] Failed to parse JSON: {result[:200]}")
        try:
            await status_msg.edit_text(
                f"Не удалось разобрать план:\n\n{result[:3000]}"
            )
        except Exception:
            await message.answer(f"Вот что получилось:\n\n{result[:3000]}")
        await state.clear()
        return

    plan_text = _render_intent_plan(data)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍 Принять", callback_data="intent:accept"),
        InlineKeyboardButton(text="💬 Переделать", callback_data="intent:redo"),
    ]])

    try:
        await status_msg.delete()
    except Exception:
        pass

    await message.answer(plan_text, reply_markup=kb)

    # Update FSM with new plan
    await state.update_data(
        pending_intent=data,
        original_request=original_request,
    )
    # Clear the redo-waiting state (back to idle, but with data)
    await state.set_state(None)


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


# --- New Intent: Accept / Redo callbacks ---

@router.callback_query(F.data == "intent:accept")
async def on_intent_accept(callback: CallbackQuery, state: FSMContext):
    """Accept the proposed intent plan — save to intents.yaml + goals.yaml."""
    fsm_data = await state.get_data()
    pending = fsm_data.get("pending_intent")

    if not pending:
        await callback.answer("Нет данных для сохранения")
        return

    await callback.answer("✅ Сохраняю...")

    intent_id = pending.get("intent_id", "new-intent")
    now_iso = datetime.now().isoformat()

    # Build intent entry for intents.yaml
    intent_entry = {
        "id": intent_id,
        "title": pending.get("title", "Новая цель"),
        "priority": pending.get("priority", "P3"),
        "deadline": pending.get("deadline"),
        "success": pending.get("success", ""),
        "methodology": pending.get("methodology", ""),
        "goals": [],
    }

    # Build goal entries
    goal_entries = []
    for g in pending.get("goals", []):
        goal_id = g.get("id", "goal")
        intent_entry["goals"].append({
            "id": goal_id,
            "title": g.get("title", "?"),
            "progress": g.get("progress", "0"),
            "updated_at": now_iso,
            "updated_by": "telegram",
        })
        goal_entries.append({
            "id": f"{intent_id}/{goal_id}",
            "intent": intent_id,
            "title": g.get("title", "?"),
            "progress": g.get("progress", "0"),
            "updated_at": now_iso,
            "updated_by": "telegram",
        })

    # Save to intents.yaml
    intents_data = load_yaml("intents.yaml")
    if "intents" not in intents_data:
        intents_data["intents"] = []
    intents_data["intents"].append(intent_entry)
    await save_yaml("intents.yaml", intents_data)

    # Save to goals.yaml
    goals_data = load_yaml("goals.yaml")
    if "goals" not in goals_data:
        goals_data["goals"] = []
    goals_data["goals"].extend(goal_entries)
    await save_yaml("goals.yaml", goals_data)

    logger.info(f"[new_intent] Saved intent '{intent_id}' with {len(goal_entries)} goals")

    # Remove buttons from the plan message
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Confirmation
    title = pending.get("title", "Новая цель")
    n_goals = len(goal_entries)
    await callback.message.answer(
        f"✅ Сохранено: {title}\n"
        f"Goals: {n_goals}\n"
        f"Приоритет: {pending.get('priority', 'P3')}\n\n"
        f"Завтра увидишь задачи в утреннем плане."
    )

    # Clear FSM
    await state.clear()


@router.callback_query(F.data == "intent:redo")
async def on_intent_redo(callback: CallbackQuery, state: FSMContext):
    """User wants to redo the intent plan — ask what to change."""
    fsm_data = await state.get_data()
    pending = fsm_data.get("pending_intent")

    if not pending:
        await callback.answer("Нет данных для переделки")
        return

    await callback.answer()

    # Remove buttons from the plan message
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await callback.message.answer("💬 Что не нравится? Напиши, что изменить.")

    # Set FSM state: waiting for redo feedback text
    await state.set_state(NewIntentState.waiting_for_redo_feedback)


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


# --- New Intent: waiting for redo feedback ---

@router.message(NewIntentState.waiting_for_redo_feedback)
async def on_intent_redo_feedback(message: Message, state: FSMContext):
    """Handle redo feedback text for new intent workflow."""
    if not _check_auth(message):
        return

    await _handle_intent_redo(message, state)


# --- Catch-all: free text ---

@router.message()
async def on_message(message: Message, state: FSMContext):
    """Handle any text message not caught by commands/callbacks/FSM."""
    if not message.text:
        return
    if not _check_auth(message):
        return

    intent = await route_message(message.text)
    logger.info(f"[message] '{message.text[:50]}' -> {intent.value}")

    match intent:
        case Intent.NEW_INTENT:
            await _handle_new_intent(message, state)
            return

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
