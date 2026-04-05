"""Free text message handler: routes through router -> action.

Also handles FSM states for partial task completion text input,
onboarding file upload, and new intent 3-step pipeline workflow.
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
    add_to_memory, grimoire_retrieve, check_knowledge_coverage,
    load_recipe,
)

logger = logging.getLogger("cos.messages")

router = Router(name="messages")


# --- FSM states for New Intent Pipeline ---

class IntentPipelineState(StatesGroup):
    """FSM states for the 4-step new intent pipeline (CLARIFY → ASSESS → RESEARCH → DECOMPOSE)."""
    clarify_waiting = State()     # STEP 0: waiting for user's answers to clarifying questions
    assess_review = State()       # STEP 1: waiting for user to confirm areas
    assess_add = State()          # STEP 1: waiting for user to type area to add
    research_review = State()     # STEP 2: waiting for user to confirm approach
    decompose_review = State()    # STEP 3: waiting for accept/redo
    decompose_redo = State()      # STEP 3: waiting for redo feedback


# --- Progress-updating Claude call helper ---

_RESEARCH_STATUSES = [
    "🔍 Исследую тему...",
    "🌐 Ищу лучшие методы...",
    "🌐 Ищу реалистичные сроки...",
    "📚 Анализирую подходы...",
    "🧩 Сравниваю варианты...",
    "✍️ Формирую рекомендации...",
    "⏳ Ещё работаю, это большой ресёрч...",
    "🧠 Синтезирую всё вместе...",
    "📝 Финализирую...",
    "⏳ Почти готово...",
]

_DECOMPOSE_STATUSES = [
    "🧩 Собираю план...",
    "📊 Определяю goals и метрики...",
    "✍️ Формирую методологию...",
    "📝 Финализирую план...",
]

# Timeout for research (can take 2-5 minutes with web search)
_RESEARCH_TIMEOUT = 300
# Timeout for decompose (30-60 sec)
_DECOMPOSE_TIMEOUT = 120


async def call_claude_with_progress(bot, chat_id: int, prompt: str, model: str = "sonnet",
                                     recipe: str = "unknown", statuses: list[str] | None = None,
                                     timeout: int | None = None):
    """Call Claude while showing progress status updates to the user.

    Sends a status message and updates it every 20 sec while waiting.
    Returns (result_text, status_message) so caller can edit/delete the status msg.
    """
    if statuses is None:
        statuses = _RESEARCH_STATUSES

    msg = await bot.send_message(chat_id, statuses[0])

    # Start Claude call as background task
    task = asyncio.create_task(call_claude_safe(prompt, model=model, recipe=recipe, timeout=timeout))

    # Update status every 20 sec while waiting
    for status_text in statuses[1:]:
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

def _extract_json(text: str) -> dict | list | None:
    """Extract JSON object or array from Claude response (handles markdown fences, extra text)."""
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

    # Try finding raw JSON array
    start = text.find("[")
    end = text.rfind("]")
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


def _render_coverage(areas_coverage: dict, goal: str) -> str:
    """Render knowledge coverage check as a user-facing message."""
    status_icons = {
        "ok": "✅",
        "expired": "⚠️",
        "missing": "❌",
    }
    status_labels = {
        "ok": "есть в базе",
        "expired": "устарело, обновлю",
        "missing": "нужен ресёрч",
    }

    lines = [f"Для «{goal}» нужны знания по:\n"]
    for area, info in areas_coverage.items():
        icon = status_icons.get(info["status"], "❓")
        label = status_labels.get(info["status"], "неизвестно")
        source_note = ""
        if info["source"]:
            source_note = f" ({info['source']})"
        lines.append(f"{icon} {area.capitalize()} — {label}{source_note}")

    lines.append("\nПропустил что-то?")
    return "\n".join(lines)


def _render_research_findings(findings: dict) -> str:
    """Render research findings as a user-facing message."""
    lines = ["Вот что нашёл:\n"]

    findings_data = findings.get("findings", {})
    for area, info in findings_data.items():
        summary = info.get("summary", "")
        recommended = info.get("recommended", "")
        lines.append(f"📌 {area.capitalize()}")
        if summary:
            lines.append(f"  {summary}")
        if recommended:
            lines.append(f"  Рекомендация: {recommended}")
        lines.append("")

    recommendation = findings.get("recommendation", "")
    if recommendation:
        lines.append(f"💡 {recommendation}")

    # Show approach options if available
    options = findings.get("approach_options", [])
    if options:
        lines.append("\nПодходы:")
        for opt in options:
            lines.append(f"  {opt.get('id', '?')}. {opt.get('label', '?')} — {opt.get('description', '')}")

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

    # MODEL_GUIDE: Haiku — task completion parsing, determine which task → CRUD
    result = await call_claude_safe(prompt, model="haiku", timeout=30, recipe="complete_classify")

    if not result:
        await message.answer("Не удалось обработать. Попробуй кнопки под задачей.")
        return

    data = _extract_json(result)

    if not data or not isinstance(data, dict):
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

    # MODEL_GUIDE: model from INTENT_MODEL (router.py) — see per-intent comments there
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


# =====================================================================
# NEW INTENT PIPELINE: 4 steps with checkpoints (CLARIFY → ASSESS → RESEARCH → DECOMPOSE)
# =====================================================================

async def _pipeline_step0_clarify(message: Message, state: FSMContext) -> None:
    """STEP 0: CLARIFY — generate smart clarifying questions for the goal.

    Claude (opus) generates 2-4 questions specific to this goal type.
    User answers free text -> saved in FSM -> passed to STEP 1 ASSESS.
    """
    user_text = message.text or ""
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    # Build user model summary for the prompt
    user_model = load_yaml("user_model.yaml").get("user_model", {})
    identity = user_model.get("identity", {})
    user_summary_parts = []
    if identity.get("role"):
        user_summary_parts.append(f"Роль: {identity['role']}")
    if user_model.get("knowledge_stack"):
        user_summary_parts.append(f"Стек: {user_model['knowledge_stack']}")
    if user_model.get("preferences"):
        user_summary_parts.append(f"Предпочтения: {', '.join(user_model['preferences'])}")
    user_model_summary = "; ".join(user_summary_parts) if user_summary_parts else "нет данных"

    # Load clarify recipe and fill placeholders
    recipe_text = load_recipe("clarify")
    clarify_prompt = recipe_text.replace("{goal}", user_text).replace("{user_model_summary}", user_model_summary)

    # MODEL_GUIDE: Opus — CLARIFY determines direction, wrong questions = wasted pipeline
    result = await call_claude_safe(clarify_prompt, model="opus", timeout=60, recipe="clarify")

    if not result:
        # Fallback: skip CLARIFY, go straight to ASSESS
        logger.warning("[clarify] Claude failed, skipping to ASSESS")
        await _pipeline_step1_assess(message, state)
        return

    # Send questions to user
    await message.answer(result)

    # Save goal in FSM, wait for user's answers
    await state.set_state(IntentPipelineState.clarify_waiting)
    await state.update_data(pipeline_goal=user_text)

    add_to_memory("user", user_text)
    add_to_memory("bot", f"[CLARIFY] {result[:200]}")


async def _pipeline_step1_assess(message: Message, state: FSMContext, clarification: str = "") -> None:
    """STEP 1: ASSESS — determine knowledge areas + check Grimoire coverage.

    Part A: Claude (opus) determines knowledge areas needed for the goal.
    Part B: check_knowledge_coverage() queries Grimoire for each area.
    Part C: Show coverage to user with confirm/add buttons.
    """
    # Get goal from FSM (set by CLARIFY) or from message text
    fsm_data = await state.get_data()
    user_text = fsm_data.get("pipeline_goal", "") or (message.text or "")

    # --- Part A: Determine knowledge areas via Opus ---
    await message.bot.send_chat_action(message.chat.id, ChatAction.TYPING)

    # Build assess prompt with user context
    user_model = load_yaml("user_model.yaml").get("user_model", {})
    recipe_text = load_recipe("assess")

    assess_prompt = (
        f"# User\n"
        f"Role: {user_model.get('identity', {}).get('role', 'Unknown')}\n"
        f"Stack: {user_model.get('knowledge_stack', 'Unknown')}\n"
    )
    if user_model.get("preferences"):
        assess_prompt += f"Preferences: {', '.join(user_model['preferences'])}\n"
    assess_prompt += f"\n# User's goal\n{user_text}\n\n"

    # Add clarification context from STEP 0 if available
    if clarification:
        assess_prompt += f"# User's clarification (answers to clarifying questions)\n{clarification}\n\n"

    assess_prompt += f"# Instructions\n{recipe_text}\n"

    status_msg = await message.answer("🔍 Определяю области знаний...")

    # MODEL_GUIDE: Opus — ASSESS is strategic (which areas to research), error = useless research
    result = await call_claude_safe(assess_prompt, model="opus", timeout=120, recipe="assess")

    if not result:
        try:
            await status_msg.edit_text("❌ Не удалось определить области знаний. Попробуй позже.")
        except Exception:
            await message.answer("❌ Не удалось определить области знаний. Попробуй позже.")
        return

    # Parse areas list from response
    areas = _extract_json(result)
    if not areas or not isinstance(areas, list):
        # Try to salvage: extract list items from text
        logger.warning(f"[assess] Failed to parse areas JSON: {result[:200]}")
        try:
            await status_msg.edit_text(
                f"❌ Не удалось разобрать области. Вот что Claude ответил:\n\n{result[:2000]}"
            )
        except Exception:
            pass
        return

    # Clean up: ensure all items are strings
    areas = [str(a).strip() for a in areas if a][:7]

    if not areas:
        try:
            await status_msg.edit_text("❌ Не удалось определить области знаний.")
        except Exception:
            pass
        return

    # --- Part B: Check Grimoire coverage ---
    try:
        await status_msg.edit_text("📊 Проверяю базу знаний...")
    except Exception:
        pass

    coverage = await check_knowledge_coverage(areas)

    # --- Part C: Show to user with buttons ---
    coverage_text = _render_coverage(coverage, user_text)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍 Ок", callback_data="pipeline:assess_ok"),
        InlineKeyboardButton(text="💬 Добавить/убрать", callback_data="pipeline:assess_edit"),
    ]])

    try:
        await status_msg.delete()
    except Exception:
        pass

    await message.answer(coverage_text, reply_markup=kb)

    # Save pipeline state
    await state.set_state(IntentPipelineState.assess_review)
    await state.update_data(
        pipeline_goal=user_text,
        pipeline_areas=areas,
        pipeline_coverage={area: {k: v for k, v in info.items() if k != "data"} for area, info in coverage.items()},
        # Store Grimoire data separately (can be large)
        pipeline_knowledge={area: info.get("data", "") for area, info in coverage.items()},
    )

    # Save to memory
    add_to_memory("user", user_text)
    add_to_memory("bot", f"[ASSESS] areas: {', '.join(areas)}")


async def _pipeline_step2_research(bot, chat_id: int, state: FSMContext) -> None:
    """STEP 2: RESEARCH — research only gaps (missing/expired areas).

    - Areas with "ok" status: pull from Grimoire, skip research.
    - Areas with "missing"/"expired": research via Claude sonnet.
    - Show findings to user with approach selection buttons.
    """
    fsm_data = await state.get_data()
    goal = fsm_data.get("pipeline_goal", "")
    areas = fsm_data.get("pipeline_areas", [])
    coverage = fsm_data.get("pipeline_coverage", {})
    existing_knowledge = fsm_data.get("pipeline_knowledge", {})

    # Separate areas by status
    gaps = [area for area in areas if coverage.get(area, {}).get("needs_research", True)]
    covered = [area for area in areas if not coverage.get(area, {}).get("needs_research", True)]

    if not gaps:
        # All areas covered — skip research, go straight to decompose
        await bot.send_message(chat_id, "✅ Все области покрыты в базе знаний. Перехожу к планированию...")
        await _pipeline_step3_decompose(bot, chat_id, state, research_findings=None)
        return

    # Build research prompt
    user_model = load_yaml("user_model.yaml").get("user_model", {})
    recipe_text = load_recipe("research")

    # Existing knowledge context (from covered areas)
    existing_context = ""
    for area in covered:
        data = existing_knowledge.get(area)
        if data:
            existing_context += f"\n--- {area} ---\n{str(data)[:500]}\n"

    gaps_str = ", ".join(gaps)
    covered_str = ", ".join(covered) if covered else "none"

    research_prompt = (
        f"# Goal\n{goal}\n\n"
        f"# Areas to research (gaps)\n{gaps_str}\n\n"
        f"# Areas already covered (do NOT research)\n{covered_str}\n\n"
    )
    if existing_context:
        research_prompt += f"# Existing knowledge from base\n{existing_context}\n\n"
    research_prompt += (
        f"# User context\n"
        f"Role: {user_model.get('identity', {}).get('role', 'Unknown')}\n"
        f"Stack: {user_model.get('knowledge_stack', 'Unknown')}\n"
    )
    if user_model.get("preferences"):
        research_prompt += f"Preferences: {', '.join(user_model['preferences'])}\n"
    research_prompt += f"\n# Instructions\n{recipe_text}\n"

    # Call Claude with progress statuses
    # MODEL_GUIDE: Sonnet — RESEARCH is synthesis + analysis, standard work
    n_gaps = len(gaps)
    result, status_msg = await call_claude_with_progress(
        bot, chat_id, research_prompt,
        model="sonnet", recipe="research",
        statuses=_RESEARCH_STATUSES, timeout=_RESEARCH_TIMEOUT,
    )

    if not result:
        try:
            await status_msg.edit_text("❌ Ресёрч не удался. Попробуй позже.")
        except Exception:
            await bot.send_message(chat_id, "❌ Ресёрч не удался. Попробуй позже.")
        await state.clear()
        return

    findings = _extract_json(result)

    if not findings or not isinstance(findings, dict):
        logger.warning(f"[research] Failed to parse findings JSON: {result[:200]}")
        try:
            await status_msg.edit_text(
                f"Вот что нашёл (не удалось разобрать структурно):\n\n{result[:3000]}"
            )
        except Exception:
            pass
        # Save raw result as findings and continue
        findings = {"findings": {}, "recommendation": result[:1000], "approach_options": []}

    # Render findings
    findings_text = _render_research_findings(findings)

    # Build approach buttons
    options = findings.get("approach_options", [])
    button_rows = []
    if options:
        approach_buttons = []
        for opt in options:
            opt_id = opt.get("id", "?")
            opt_label = opt.get("label", opt_id)
            approach_buttons.append(
                InlineKeyboardButton(
                    text=f"👍 {opt_label}",
                    callback_data=f"pipeline:approach_{opt_id}",
                )
            )
        button_rows.append(approach_buttons)
    # Always add a "different approach" button
    button_rows.append([
        InlineKeyboardButton(text="💬 Другой подход", callback_data="pipeline:approach_other"),
    ])
    kb = InlineKeyboardMarkup(inline_keyboard=button_rows)

    try:
        await status_msg.delete()
    except Exception:
        pass

    await bot.send_message(chat_id, findings_text, reply_markup=kb)

    # Save research findings in FSM state
    await state.set_state(IntentPipelineState.research_review)
    await state.update_data(
        pipeline_findings=findings,
    )


async def _pipeline_step3_decompose(bot, chat_id: int, state: FSMContext,
                                     research_findings: dict | None = None,
                                     chosen_approach: str = "",
                                     redo_feedback: str = "") -> None:
    """STEP 3: DECOMPOSE + METHOD — combine all knowledge into a plan.

    Combines existing Grimoire knowledge + new research findings into a plan.
    Shows plan with Accept/Redo buttons.
    """
    fsm_data = await state.get_data()
    goal = fsm_data.get("pipeline_goal", "")
    areas = fsm_data.get("pipeline_areas", [])
    existing_knowledge = fsm_data.get("pipeline_knowledge", {})

    # Use passed findings or get from FSM
    if research_findings is None:
        research_findings = fsm_data.get("pipeline_findings", {})

    user_model = load_yaml("user_model.yaml").get("user_model", {})
    recipe_text = load_recipe("decompose")

    # Assemble all knowledge
    all_knowledge = ""

    # Existing knowledge from Grimoire
    for area, data in existing_knowledge.items():
        if data:
            all_knowledge += f"\n--- {area} (from knowledge base) ---\n{str(data)[:500]}\n"

    # New research findings
    findings_data = research_findings.get("findings", {}) if research_findings else {}
    for area, info in findings_data.items():
        summary = info.get("summary", "")
        recommended = info.get("recommended", "")
        if summary or recommended:
            all_knowledge += f"\n--- {area} (new research) ---\n{summary}\nRecommended: {recommended}\n"

    # Build decompose prompt
    decompose_prompt = (
        f"# Goal\n{goal}\n\n"
    )
    if chosen_approach:
        decompose_prompt += f"# Chosen approach\n{chosen_approach}\n\n"

    recommendation = ""
    if research_findings:
        recommendation = research_findings.get("recommendation", "")
    if recommendation and not chosen_approach:
        decompose_prompt += f"# Research recommendation\n{recommendation}\n\n"

    decompose_prompt += f"# All knowledge\n{all_knowledge}\n\n"
    decompose_prompt += (
        f"# User context\n"
        f"Role: {user_model.get('identity', {}).get('role', 'Unknown')}\n"
        f"Stack: {user_model.get('knowledge_stack', 'Unknown')}\n"
    )
    if user_model.get("preferences"):
        decompose_prompt += f"Preferences: {', '.join(user_model['preferences'])}\n"

    if redo_feedback:
        decompose_prompt += f"\n# Feedback on previous plan\n{redo_feedback}\nTake this feedback into account and improve the plan.\n"

    # Add previous plan if redo
    previous_plan = fsm_data.get("pipeline_plan")
    if previous_plan and redo_feedback:
        decompose_prompt += (
            f"\n# Previous plan (rejected)\n"
            f"{json.dumps(previous_plan, ensure_ascii=False, indent=2)}\n"
        )

    decompose_prompt += f"\n# Instructions\n{recipe_text}\n"

    # MODEL_GUIDE: Sonnet — DECOMPOSE is structuring, not deep reasoning
    result, status_msg = await call_claude_with_progress(
        bot, chat_id, decompose_prompt,
        model="sonnet", recipe="decompose",
        statuses=_DECOMPOSE_STATUSES, timeout=_DECOMPOSE_TIMEOUT,
    )

    if not result:
        try:
            await status_msg.edit_text("❌ Не удалось составить план. Попробуй позже.")
        except Exception:
            await bot.send_message(chat_id, "❌ Не удалось составить план. Попробуй позже.")
        await state.clear()
        return

    data = _extract_json(result)

    if not data or not isinstance(data, dict) or "intent_id" not in data:
        logger.warning(f"[decompose] Failed to parse plan JSON: {result[:200]}")
        try:
            await status_msg.edit_text(
                f"Не удалось разобрать план:\n\n{result[:3000]}"
            )
        except Exception:
            pass
        await state.clear()
        return

    # Render the plan
    plan_text = _render_intent_plan(data)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍 Принять", callback_data="pipeline:accept"),
        InlineKeyboardButton(text="💬 Переделать", callback_data="pipeline:redo"),
    ]])

    try:
        await status_msg.delete()
    except Exception:
        pass

    await bot.send_message(chat_id, plan_text, reply_markup=kb)

    # Save plan in FSM state (keep research data for potential redo)
    await state.set_state(IntentPipelineState.decompose_review)
    await state.update_data(
        pipeline_plan=data,
        pipeline_chosen_approach=chosen_approach,
    )


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


# =====================================================================
# PIPELINE CALLBACKS: Assess / Research / Decompose
# =====================================================================

@router.callback_query(F.data == "pipeline:assess_ok")
async def on_assess_ok(callback: CallbackQuery, state: FSMContext):
    """User confirmed knowledge areas — proceed to RESEARCH."""
    current_state = await state.get_state()
    if current_state != IntentPipelineState.assess_review.state:
        await callback.answer("Этот шаг уже пройден")
        return

    await callback.answer("👍 Начинаю ресёрч...")

    # Remove buttons from coverage message
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Proceed to STEP 2: RESEARCH
    await _pipeline_step2_research(callback.bot, callback.message.chat.id, state)


@router.callback_query(F.data == "pipeline:assess_edit")
async def on_assess_edit(callback: CallbackQuery, state: FSMContext):
    """User wants to add/remove areas — ask for input."""
    current_state = await state.get_state()
    if current_state != IntentPipelineState.assess_review.state:
        await callback.answer("Этот шаг уже пройден")
        return

    await callback.answer()

    # Remove buttons from coverage message
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await callback.message.answer(
        "Напиши какие области добавить или убрать.\n"
        "Например: «добавь технологии» или «убери юридику»"
    )
    await state.set_state(IntentPipelineState.assess_add)


@router.callback_query(F.data.startswith("pipeline:approach_"))
async def on_approach_chosen(callback: CallbackQuery, state: FSMContext):
    """User chose a research approach — proceed to DECOMPOSE."""
    current_state = await state.get_state()
    if current_state != IntentPipelineState.research_review.state:
        await callback.answer("Этот шаг уже пройден")
        return

    approach_id = callback.data.split("pipeline:approach_", 1)[1]

    # Remove buttons
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    if approach_id == "other":
        await callback.answer()
        await callback.message.answer("💬 Какой подход предпочитаешь? Напиши своими словами.")
        await state.set_state(IntentPipelineState.research_review)
        await state.update_data(pipeline_waiting_custom_approach=True)
        return

    # Find approach label from findings
    fsm_data = await state.get_data()
    findings = fsm_data.get("pipeline_findings", {})
    options = findings.get("approach_options", [])
    chosen_label = approach_id
    for opt in options:
        if opt.get("id") == approach_id:
            chosen_label = f"{opt.get('label', approach_id)}: {opt.get('description', '')}"
            break

    await callback.answer(f"👍 Подход: {chosen_label[:30]}")

    # Proceed to STEP 3: DECOMPOSE
    await _pipeline_step3_decompose(
        callback.bot, callback.message.chat.id, state,
        chosen_approach=chosen_label,
    )


@router.callback_query(F.data == "pipeline:accept")
async def on_pipeline_accept(callback: CallbackQuery, state: FSMContext):
    """Accept the proposed intent plan — save to intents.yaml + goals.yaml."""
    current_state = await state.get_state()
    if current_state != IntentPipelineState.decompose_review.state:
        await callback.answer("Нет данных для сохранения")
        return

    fsm_data = await state.get_data()
    pending = fsm_data.get("pipeline_plan")

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

    logger.info(f"[pipeline] Saved intent '{intent_id}' with {len(goal_entries)} goals")

    # Remove buttons from the plan message
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    # Confirmation + regenerate plan immediately
    title = pending.get("title", "Новая цель")
    n_goals = len(goal_entries)
    await callback.message.answer(
        f"✅ Сохранено: {title}\n"
        f"Goals: {n_goals}\n"
        f"Приоритет: {pending.get('priority', 'P3')}\n\n"
        f"⏳ Обновляю план на сегодня..."
    )

    # Clear FSM
    await state.clear()

    # Regenerate today's plan with new intent included
    from bot.scheduler.morning import generate_morning_plan
    await generate_morning_plan(callback.bot, callback.message.chat.id)


@router.callback_query(F.data == "pipeline:redo")
async def on_pipeline_redo(callback: CallbackQuery, state: FSMContext):
    """User wants to redo the plan — ask what to change."""
    current_state = await state.get_state()
    if current_state != IntentPipelineState.decompose_review.state:
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
    await state.set_state(IntentPipelineState.decompose_redo)


# =====================================================================
# FSM TEXT HANDLERS (must be before catch-all)
# =====================================================================

# --- Pipeline: CLARIFY waiting for answers ---

@router.message(IntentPipelineState.clarify_waiting)
async def on_clarify_answer(message: Message, state: FSMContext):
    """Handle user's answers to STEP 0 clarifying questions -> proceed to STEP 1 ASSESS."""
    if not _check_auth(message):
        return

    clarification = message.text or ""

    # Save clarification in FSM
    await state.update_data(pipeline_clarification=clarification)

    add_to_memory("user", clarification[:300])

    # Proceed to STEP 1: ASSESS with clarification context
    await _pipeline_step1_assess(message, state, clarification=clarification)


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


# --- Pipeline: ASSESS add/remove areas ---

@router.message(IntentPipelineState.assess_add)
async def on_assess_add_text(message: Message, state: FSMContext):
    """Handle text for adding/removing areas in ASSESS step."""
    if not _check_auth(message):
        return

    user_text = message.text or ""
    fsm_data = await state.get_data()
    goal = fsm_data.get("pipeline_goal", "")
    areas = fsm_data.get("pipeline_areas", [])

    # Use simple keyword matching to add/remove areas
    lower = user_text.lower()

    # Detect removal
    remove_keywords = ["убери", "убрать", "удали", "удалить", "без"]
    is_remove = any(kw in lower for kw in remove_keywords)

    if is_remove:
        # Try to find which area to remove
        removed = []
        new_areas = []
        for area in areas:
            if area.lower() in lower or any(word in lower for word in area.lower().split("/")):
                removed.append(area)
            else:
                new_areas.append(area)
        if removed:
            areas = new_areas
            await message.answer(f"Убрал: {', '.join(removed)}")
        else:
            await message.answer("Не понял какую область убрать. Напиши точное название.")
            return
    else:
        # Add new area — extract it from text
        add_keywords = ["добавь", "добавить", "ещё", "плюс"]
        new_area = user_text
        for kw in add_keywords:
            new_area = new_area.replace(kw, "").strip()
        if new_area:
            areas.append(new_area)
        else:
            await message.answer("Не понял какую область добавить.")
            return

    # Re-check Grimoire coverage for updated areas
    status_msg = await message.answer("📊 Проверяю обновлённый список...")
    coverage = await check_knowledge_coverage(areas)

    coverage_text = _render_coverage(coverage, goal)

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="👍 Ок", callback_data="pipeline:assess_ok"),
        InlineKeyboardButton(text="💬 Добавить/убрать", callback_data="pipeline:assess_edit"),
    ]])

    try:
        await status_msg.delete()
    except Exception:
        pass

    await message.answer(coverage_text, reply_markup=kb)

    # Update FSM
    await state.set_state(IntentPipelineState.assess_review)
    await state.update_data(
        pipeline_areas=areas,
        pipeline_coverage={area: {k: v for k, v in info.items() if k != "data"} for area, info in coverage.items()},
        pipeline_knowledge={area: info.get("data", "") for area, info in coverage.items()},
    )


# --- Pipeline: RESEARCH custom approach text ---

@router.message(IntentPipelineState.research_review)
async def on_research_custom_approach(message: Message, state: FSMContext):
    """Handle custom approach text from user during RESEARCH review."""
    if not _check_auth(message):
        return

    fsm_data = await state.get_data()
    waiting_custom = fsm_data.get("pipeline_waiting_custom_approach", False)

    if waiting_custom:
        # User typed a custom approach — proceed to decompose with it
        custom_approach = message.text or ""
        await state.update_data(pipeline_waiting_custom_approach=False)
        await _pipeline_step3_decompose(
            message.bot, message.chat.id, state,
            chosen_approach=custom_approach,
        )
    else:
        # Shouldn't reach here normally — treat as custom approach
        custom_approach = message.text or ""
        await _pipeline_step3_decompose(
            message.bot, message.chat.id, state,
            chosen_approach=custom_approach,
        )


# --- Pipeline: DECOMPOSE redo feedback ---

@router.message(IntentPipelineState.decompose_redo)
async def on_decompose_redo_feedback(message: Message, state: FSMContext):
    """Handle redo feedback text — re-run ONLY step 3 (decompose)."""
    if not _check_auth(message):
        return

    feedback = message.text or ""

    # Re-run ONLY step 3 with feedback (research data preserved in FSM)
    await _pipeline_step3_decompose(
        message.bot, message.chat.id, state,
        redo_feedback=feedback,
    )


# --- Catch-all: free text ---

async def process_text(text: str, message: Message, state: FSMContext):
    """Process text through router. Called by on_message AND voice handler."""
    intent = await route_message(text)
    logger.info(f"[message] '{text[:50]}' -> {intent.value}")

    match intent:
        case Intent.NEW_INTENT:
            await _pipeline_step0_clarify(message, state)
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


@router.message()
async def on_message(message: Message, state: FSMContext):
    """Catch-all: route typed text through process_text."""
    if not message.text:
        return
    if not _check_auth(message):
        return
    await process_text(message.text, message, state)
