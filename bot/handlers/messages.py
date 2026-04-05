"""Free text message handler: routes through router -> action.

Also handles FSM states for partial task completion text input
and onboarding file upload.
"""

import logging
import os

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from bot.router import route_message, Intent
from bot.handlers.callbacks import PartialTaskState
from bot.context import load_history, save_history

logger = logging.getLogger("cos.messages")

router = Router(name="messages")


def _check_auth(message: Message) -> bool:
    """Check if message is from authorized user."""
    authorized = os.getenv("TELEGRAM_CHAT_ID")
    if not authorized:
        return True
    return message.chat.id == int(authorized)


# --- Onboarding callback handlers (must be before catch-all) ---

@router.callback_query(F.data == "onboard:file")
async def on_onboard_file(callback: CallbackQuery):
    """User chose to upload strategy file."""
    await callback.answer()
    await callback.message.answer(
        "\U0001f4ce \u041e\u0442\u043f\u0440\u0430\u0432\u044c .md \u0444\u0430\u0439\u043b \u0441\u0442\u0440\u0430\u0442\u0435\u0433\u0438\u0438.\n\n"
        "\u041f\u043e\u043a\u0430 \u0434\u0430\u043d\u043d\u044b\u0435 \u0443\u0436\u0435 \u0437\u0430\u043f\u043e\u043b\u043d\u0435\u043d\u044b \u0432 data/ \u2014 "
        "\u043c\u043e\u0436\u0435\u0448\u044c \u0441\u0440\u0430\u0437\u0443 \u043d\u0430\u0436\u0430\u0442\u044c /today \u0434\u043b\u044f \u043f\u043b\u0430\u043d\u0430."
    )


@router.callback_query(F.data == "onboard:questions")
async def on_onboard_questions(callback: CallbackQuery):
    """User chose to answer questions for onboarding."""
    await callback.answer()
    await callback.message.answer(
        "\U0001f4ac \u041e\u043d\u0431\u043e\u0440\u0434\u0438\u043d\u0433 \u0447\u0435\u0440\u0435\u0437 \u0432\u043e\u043f\u0440\u043e\u0441\u044b \u0431\u0443\u0434\u0435\u0442 \u0432 Phase 2.\n\n"
        "\u041f\u043e\u043a\u0430 \u0434\u0430\u043d\u043d\u044b\u0435 \u0443\u0436\u0435 \u0437\u0430\u043f\u043e\u043b\u043d\u0435\u043d\u044b \u0432 data/ \u2014 "
        "\u043c\u043e\u0436\u0435\u0448\u044c \u0441\u0440\u0430\u0437\u0443 \u043d\u0430\u0436\u0430\u0442\u044c /today \u0434\u043b\u044f \u043f\u043b\u0430\u043d\u0430."
    )


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
        await message.answer(f"\U0001f4dd {task_title}: {note}\n\u0417\u0430\u043f\u0438\u0441\u0430\u043d\u043e!")
    else:
        await message.answer("\u041d\u0435 \u043d\u0430\u0448\u0451\u043b \u0437\u0430\u0434\u0430\u0447\u0443.")

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

    # Phase 1: basic routing
    match intent:
        case Intent.PLAN:
            await message.answer("\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439 /today \u0434\u043b\u044f \u0433\u0435\u043d\u0435\u0440\u0430\u0446\u0438\u0438 \u043f\u043b\u0430\u043d\u0430.")
        case Intent.STATUS:
            await message.answer("\u0418\u0441\u043f\u043e\u043b\u044c\u0437\u0443\u0439 /status \u0434\u043b\u044f \u043f\u0440\u043e\u0433\u0440\u0435\u0441\u0441\u0430.")
        case _:
            await message.answer(
                "\u0421\u0432\u043e\u0431\u043e\u0434\u043d\u044b\u0439 \u0447\u0430\u0442 \u0431\u0443\u0434\u0435\u0442 \u0432 Phase 2.\n"
                "\u041a\u043e\u043c\u0430\u043d\u0434\u044b: /today, /status, /debug"
            )
