"""Inline button callback handlers: complete, postpone, partial, energy, plan actions."""

import logging
from datetime import datetime

from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.context import load_history, save_history, load_yaml, save_yaml
from bot.render import render_task_done, render_task_skipped, render_task_partial

logger = logging.getLogger("cos.callbacks")

router = Router(name="callbacks")


class PartialTaskState(StatesGroup):
    """FSM state for waiting for partial completion text."""
    waiting_for_text = State()


def _find_task_in_history(history: dict, task_id: str) -> dict | None:
    """Find a task by ID in today's history."""
    for task in history.get("tasks", []):
        if task.get("id") == task_id:
            return task
    return None


def _get_task_title(history: dict, task_id: str) -> str:
    """Get task title from history by ID."""
    task = _find_task_in_history(history, task_id)
    return task.get("title", task_id) if task else task_id


async def _update_task_status(task_id: str, status: str, note: str = "") -> str:
    """Update task status in today's history YAML. Returns task title."""
    history = load_history()
    if not history:
        return task_id

    task_title = task_id
    for task in history.get("tasks", []):
        if task.get("id") == task_id:
            task["status"] = status
            task_title = task.get("title", task_id)
            if note:
                task["note"] = note
            break

    # Update skipped_tasks tracker
    if status == "skipped":
        skipped = history.get("skipped_tasks", [])
        existing = next((s for s in skipped if s.get("task_id") == task_id), None)
        if existing:
            existing["times_skipped"] = existing.get("times_skipped", 0) + 1
        else:
            skipped.append({"task_id": task_id, "times_skipped": 1})
        history["skipped_tasks"] = skipped

    await save_history(history)
    return task_title


async def _update_goal_progress(task_id: str) -> None:
    """Update goal progress in goals.yaml based on completed task.

    Reads the task's goal_id from history, increments the progress counter.
    """
    history = load_history()
    task = _find_task_in_history(history, task_id)
    if not task:
        return

    goal_id = task.get("goal_id")
    if not goal_id:
        return

    goals_data = load_yaml("goals.yaml")
    goals = goals_data.get("goals", [])
    for goal in goals:
        if goal.get("id") == goal_id:
            goal["updated_at"] = datetime.now().isoformat()
            goal["updated_by"] = "telegram"
            logger.info(f"Updated goal {goal_id} timestamp")
            break

    await save_yaml("goals.yaml", goals_data)


def _rebuild_keyboard(original_markup, completed_task_id: str, status_icon: str) -> InlineKeyboardMarkup | None:
    """Rebuild inline keyboard with the completed task's buttons replaced by status icon."""
    if not original_markup:
        return None

    new_rows = []
    for row in original_markup.inline_keyboard:
        # Check if this row contains buttons for the completed task
        is_task_row = any(
            btn.callback_data and completed_task_id in btn.callback_data
            for btn in row
        )
        if is_task_row:
            # Find task title from the row context
            task_title = ""
            for btn in row:
                if btn.callback_data and btn.callback_data.startswith("complete:"):
                    # Extract from the original message context
                    break
            # Replace with status indicator (single disabled-looking button)
            new_rows.append([
                InlineKeyboardButton(text=f"{status_icon} \u0413\u043e\u0442\u043e\u0432\u043e", callback_data=f"noop:{completed_task_id}")
            ])
        else:
            new_rows.append(row)

    return InlineKeyboardMarkup(inline_keyboard=new_rows) if new_rows else None


# --- Task completion ---

@router.callback_query(F.data.startswith("complete:"))
async def on_complete(callback: CallbackQuery):
    """Handle task completion button press. Instant -- no LLM."""
    task_id = callback.data.split(":", 1)[1]
    logger.info(f"Task completed: {task_id}")

    task_title = await _update_task_status(task_id, "done")
    await _update_goal_progress(task_id)

    await callback.answer(f"\u2705 {task_title}")

    # Update message keyboard to show completed status
    new_kb = _rebuild_keyboard(callback.message.reply_markup, task_id, "\u2705")
    try:
        await callback.message.edit_reply_markup(reply_markup=new_kb)
    except Exception as e:
        logger.warning(f"Could not edit keyboard: {e}")


# --- Task postpone ---

@router.callback_query(F.data.startswith("postpone:"))
async def on_postpone(callback: CallbackQuery):
    """Handle task postpone button press. Instant -- no LLM."""
    task_id = callback.data.split(":", 1)[1]
    logger.info(f"Task postponed: {task_id}")

    task_title = await _update_task_status(task_id, "skipped")

    # Count total skips for this task title
    history = load_history()
    skipped = history.get("skipped_tasks", [])
    skip_entry = next((s for s in skipped if s.get("task_id") == task_id), None)
    times = skip_entry.get("times_skipped", 1) if skip_entry else 1

    suffix = f" ({times}-\u0439 \u0440\u0430\u0437)" if times > 1 else ""
    await callback.answer(f"\u23ed {task_title}{suffix}")

    new_kb = _rebuild_keyboard(callback.message.reply_markup, task_id, "\u23ed")
    try:
        await callback.message.edit_reply_markup(reply_markup=new_kb)
    except Exception as e:
        logger.warning(f"Could not edit keyboard: {e}")


# --- Partial completion ---

@router.callback_query(F.data.startswith("partial:"))
async def on_partial(callback: CallbackQuery, state: FSMContext):
    """Handle partial completion button press. Asks for text."""
    task_id = callback.data.split(":", 1)[1]
    logger.info(f"Task partial: {task_id}")

    history = load_history()
    task_title = _get_task_title(history, task_id)

    await callback.answer()
    await callback.message.answer(
        f"\U0001f4dd {task_title}\n\u0427\u0442\u043e \u0443\u0441\u043f\u0435\u043b? \u041d\u0430\u043f\u0438\u0448\u0438 \u0442\u0435\u043a\u0441\u0442\u043e\u043c:"
    )
    await state.set_state(PartialTaskState.waiting_for_text)
    await state.update_data(partial_task_id=task_id)


# --- Energy (evening summary) ---

@router.callback_query(F.data.startswith("energy:"))
async def on_energy(callback: CallbackQuery):
    """Handle energy level button press from evening summary."""
    energy = int(callback.data.split(":", 1)[1])
    logger.info(f"Energy level: {energy}")

    history = load_history()
    if history:
        history["energy"] = energy
        await save_history(history)

    energy_text = {
        1: "\U0001f614 \u041f\u043e\u043d\u044f\u043b, \u0442\u044f\u0436\u0451\u043b\u044b\u0439 \u0434\u0435\u043d\u044c. \u0417\u0430\u0432\u0442\u0440\u0430 \u043b\u0435\u0433\u0447\u0435.",
        2: "\U0001f610 \u041e\u043a, \u0437\u0430\u043f\u043e\u043c\u043d\u044e.",
        3: "\U0001f44d \u0421\u0440\u0435\u0434\u043d\u0438\u0439 \u0434\u0435\u043d\u044c, \u043d\u043e\u0440\u043c\u0430.",
        4: "\U0001f4aa \u0425\u043e\u0440\u043e\u0448\u043e!",
        5: "\U0001f525 \u041e\u0442\u043b\u0438\u0447\u043d\u043e!",
    }
    await callback.answer(energy_text.get(energy, "OK"))
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"\u26a1 \u042d\u043d\u0435\u0440\u0433\u0438\u044f: {energy}/5 \u2014 \u0437\u0430\u043f\u0438\u0441\u0430\u043d\u043e. \u0421\u043f\u043e\u043a\u043e\u0439\u043d\u043e\u0439 \u043d\u043e\u0447\u0438!"
        )
    except Exception as e:
        logger.warning(f"Could not update evening message: {e}")


# --- Plan-level buttons ---

@router.callback_query(F.data == "plan:accept")
async def on_plan_accept(callback: CallbackQuery):
    """Accept the plan as-is."""
    await callback.answer("\U0001f44d \u041f\u043b\u0430\u043d \u043f\u0440\u0438\u043d\u044f\u0442!")


@router.callback_query(F.data == "plan:change")
async def on_plan_change(callback: CallbackQuery):
    """Request plan change (Phase 2: will trigger re-generation)."""
    await callback.answer()
    await callback.message.answer(
        "\U0001f4ac \u041d\u0430\u043f\u0438\u0448\u0438, \u0447\u0442\u043e \u0438\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u0432 \u043f\u043b\u0430\u043d\u0435 (\u0431\u0443\u0434\u0435\u0442 \u0432 Phase 2)"
    )


@router.callback_query(F.data == "plan:why")
async def on_plan_why(callback: CallbackQuery):
    """Show full reasoning behind the plan."""
    history = load_history()
    reasoning = history.get("plan_reasoning", "\u041d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445 \u043e \u0440\u0435\u0437\u043e\u043d\u0438\u043d\u0433\u0435.")
    await callback.answer()
    await callback.message.answer(f"\U0001f4a1 \u041f\u043e\u0447\u0435\u043c\u0443 \u044d\u0442\u043e\u0442 \u043f\u043b\u0430\u043d:\n\n{reasoning}")


# --- No-op for completed task buttons ---

@router.callback_query(F.data.startswith("noop:"))
async def on_noop(callback: CallbackQuery):
    """No-op handler for already-completed tasks."""
    await callback.answer("\u0423\u0436\u0435 \u043e\u0442\u043c\u0435\u0447\u0435\u043d\u043e")
