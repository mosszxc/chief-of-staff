"""Free text message handler: routes through router -> action."""

import logging

from aiogram import Router
from aiogram.types import Message

from bot.router import route_message, Intent

logger = logging.getLogger("cos.messages")

router = Router(name="messages")


@router.message()
async def on_message(message: Message):
    """Handle any text message not caught by commands/callbacks."""
    if not message.text:
        return

    intent = await route_message(message.text)
    logger.info(f"[message] '{message.text[:50]}' -> {intent.value}")

    # Phase 1: only basic responses
    # Phase 2: full recipe-based handling
    match intent:
        case Intent.COMPLETE:
            await message.answer("Got it, marking as done. (Phase 1 placeholder)")
        case Intent.POSTPONE:
            await message.answer("Postponed to tomorrow. (Phase 1 placeholder)")
        case Intent.STATUS:
            await message.answer("Status display coming in Phase 1.")
        case Intent.PLAN:
            await message.answer("Plan generation coming in Phase 1.")
        case _:
            await message.answer(
                "Free chat and advanced routing coming in Phase 2. "
                "Use /today for plan, /status for progress."
            )
