"""Telegram command handlers: /start, /today, /debug, /status."""

import logging

from aiogram import Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

logger = logging.getLogger("cos.commands")

router = Router(name="commands")


@router.message(CommandStart())
async def cmd_start(message: Message):
    """Handle /start -- onboarding entry point."""
    await message.answer(
        "Chief of Staff starting...\n\n"
        "I'm your AI planner. I'll send you a plan every morning at 08:00 Moscow time.\n\n"
        "Commands:\n"
        "/today -- show today's plan\n"
        "/status -- show goals progress\n"
        "/debug -- last Claude call info"
    )


@router.message(Command("today"))
async def cmd_today(message: Message):
    """Handle /today -- trigger daily plan generation."""
    await message.answer("Plan generation will be available in Phase 1.")


@router.message(Command("status"))
async def cmd_status(message: Message):
    """Handle /status -- show goals progress from YAML."""
    await message.answer("Status display will be available in Phase 1.")


@router.message(Command("debug"))
async def cmd_debug(message: Message):
    """Handle /debug -- show last Claude call info."""
    await message.answer("Debug info will be available in Phase 1.")
