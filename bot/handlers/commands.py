"""Telegram command handlers: /start, /today, /debug, /status."""

import logging
import os

from aiogram import Router, Bot
from aiogram.enums import ChatAction
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from bot.context import data_files_exist, load_yaml
from bot.claude import last_call_info
from bot.scheduler.morning import generate_morning_plan

logger = logging.getLogger("cos.commands")

router = Router(name="commands")

CHAT_ID = None


def get_chat_id() -> int | None:
    """Get authorized chat ID from env."""
    global CHAT_ID
    if CHAT_ID is None:
        raw = os.getenv("TELEGRAM_CHAT_ID")
        if raw:
            CHAT_ID = int(raw)
    return CHAT_ID


def _check_auth(message: Message) -> bool:
    """Check if message is from authorized user. Single-user bot."""
    authorized = get_chat_id()
    if authorized is None:
        # No CHAT_ID configured, allow all (for setup)
        return True
    return message.chat.id == authorized


def _auto_save_chat_id(chat_id: int) -> None:
    """Append TELEGRAM_CHAT_ID to .env if not already present."""
    from bot.context import BASE_DIR
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        content = env_path.read_text()
        if "TELEGRAM_CHAT_ID" not in content:
            with open(env_path, "a") as f:
                f.write(f"\nTELEGRAM_CHAT_ID={chat_id}\n")
            os.environ["TELEGRAM_CHAT_ID"] = str(chat_id)


class OnboardingState(StatesGroup):
    """FSM for onboarding flow."""
    waiting_for_strategy = State()


# --- /start ---

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Handle /start -- onboarding entry point."""
    global CHAT_ID
    # Always log chat_id for setup purposes
    logger.info(f"[start] chat_id={message.chat.id}, user={message.from_user.username if message.from_user else 'unknown'}")

    # Auto-save chat_id on first /start if not configured
    if not os.getenv("TELEGRAM_CHAT_ID"):
        _auto_save_chat_id(message.chat.id)
        CHAT_ID = message.chat.id
        logger.info(f"Auto-saved TELEGRAM_CHAT_ID={message.chat.id}")

    if not _check_auth(message):
        await message.answer(f"\u26d4 \u041d\u0435 \u0430\u0432\u0442\u043e\u0440\u0438\u0437\u043e\u0432\u0430\u043d. \u0422\u0432\u043e\u0439 chat_id: {message.chat.id}")
        return

    # Check if data files already exist
    if data_files_exist():
        await message.answer(
            "\U0001f44b \u041f\u0440\u0438\u0432\u0435\u0442! Chief of Staff \u0443\u0436\u0435 \u043d\u0430\u0441\u0442\u0440\u043e\u0435\u043d.\n\n"
            "\u041a\u043e\u043c\u0430\u043d\u0434\u044b:\n"
            "/today \u2014 \u043f\u043b\u0430\u043d \u043d\u0430 \u0441\u0435\u0433\u043e\u0434\u043d\u044f\n"
            "/status \u2014 \u043f\u0440\u043e\u0433\u0440\u0435\u0441\u0441 \u043f\u043e \u0446\u0435\u043b\u044f\u043c\n"
            "/debug \u2014 \u0438\u043d\u0444\u043e \u043e \u043f\u043e\u0441\u043b\u0435\u0434\u043d\u0435\u043c \u0432\u044b\u0437\u043e\u0432\u0435 Claude\n\n"
            "\u0423\u0442\u0440\u0435\u043d\u043d\u0438\u0439 \u043f\u043b\u0430\u043d \u043f\u0440\u0438\u0445\u043e\u0434\u0438\u0442 \u0432 08:00 \u041c\u0421\u041a."
        )
        return

    # Onboarding needed
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="\U0001f4ce \u0417\u0430\u0433\u0440\u0443\u0437\u0438\u0442\u044c \u0444\u0430\u0439\u043b",
            callback_data="onboard:file"
        ),
        InlineKeyboardButton(
            text="\U0001f4ac \u041e\u0442\u0432\u0435\u0442\u0438\u0442\u044c \u043d\u0430 \u0432\u043e\u043f\u0440\u043e\u0441\u044b",
            callback_data="onboard:questions"
        ),
    ]])

    await message.answer(
        "\U0001f44b \u041f\u0440\u0438\u0432\u0435\u0442! \u042f Chief of Staff \u2014 \u0431\u0443\u0434\u0443 \u043a\u0430\u0436\u0434\u044b\u0439 \u0434\u0435\u043d\u044c \u0433\u043e\u0432\u043e\u0440\u0438\u0442\u044c \u0447\u0442\u043e \u0434\u0435\u043b\u0430\u0442\u044c "
        "\u0438 \u0442\u0440\u0435\u043a\u0430\u0442\u044c \u043f\u0440\u043e\u0433\u0440\u0435\u0441\u0441.\n\n"
        "\u0414\u043b\u044f \u043d\u0430\u0447\u0430\u043b\u0430 \u043c\u043d\u0435 \u043d\u0443\u0436\u043d\u0430 \u0442\u0432\u043e\u044f \u0441\u0442\u0440\u0430\u0442\u0435\u0433\u0438\u044f:",
        reply_markup=kb,
    )


# --- /today ---

@router.message(Command("today"))
async def cmd_today(message: Message):
    """Handle /today -- trigger daily plan generation now."""
    if not _check_auth(message):
        return

    if not data_files_exist():
        await message.answer("\u26a0\ufe0f \u0421\u043d\u0430\u0447\u0430\u043b\u0430 \u043d\u0443\u0436\u043d\u043e \u043d\u0430\u0441\u0442\u0440\u043e\u0438\u0442\u044c \u043f\u0440\u043e\u0444\u0438\u043b\u044c. \u041d\u0430\u0436\u043c\u0438 /start")
        return

    # Check if plan already exists for today → serve from cache
    from bot.context import load_history
    from bot.render import render_plan_message
    from datetime import date

    history = load_history(date.today())
    if history and history.get("tasks"):
        # Rebuild plan dict from history for render
        plan = {
            "reasoning": history.get("plan_reasoning", ""),
            "runway_weeks": "?",
            "tasks": history["tasks"],
            "drift_warning": None,
            "tomorrow_preview": None,
        }
        text, keyboard = render_plan_message(plan)
        await message.answer(text, reply_markup=keyboard)
        return

    await message.answer("⏳ Генерирую план...")
    await generate_morning_plan(message.bot, message.chat.id)


# --- /status ---

@router.message(Command("status"))
async def cmd_status(message: Message):
    """Handle /status -- show goals progress from YAML."""
    if not _check_auth(message):
        return

    intents_data = load_yaml("intents.yaml")
    intents = intents_data.get("intents", [])

    if not intents:
        await message.answer("\u041d\u0435\u0442 \u0446\u0435\u043b\u0435\u0439. \u041d\u0430\u0436\u043c\u0438 /start \u0434\u043b\u044f \u043d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438.")
        return

    lines = ["\U0001f3af \u041f\u0440\u043e\u0433\u0440\u0435\u0441\u0441:", ""]
    for intent in intents:
        priority = intent.get("priority", "?")
        title = intent.get("title", "???")
        deadline = intent.get("deadline", "")
        deadline_str = f" (\u0434\u043e {deadline})" if deadline else ""
        lines.append(f"[{priority}] {title}{deadline_str}")

        for goal in intent.get("goals", []):
            progress = goal.get("progress", "?")
            goal_title = goal.get("title", "???")
            lines.append(f"  \u2022 {goal_title}: {progress}")
        lines.append("")

    await message.answer("\n".join(lines))


# --- /debug ---

@router.message(Command("debug"))
async def cmd_debug(message: Message):
    """Handle /debug -- show last Claude call info."""
    if not _check_auth(message):
        return

    if not last_call_info.timestamp:
        await message.answer("\u041d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445 \u043e \u0432\u044b\u0437\u043e\u0432\u0430\u0445 Claude.")
        return

    await message.answer(f"\U0001f50d \u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0439 \u0432\u044b\u0437\u043e\u0432 Claude:\n\n{last_call_info.summary()}")
