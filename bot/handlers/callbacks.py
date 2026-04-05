"""Inline button callback handlers: complete, postpone, partial."""

import logging

from aiogram import Router, F
from aiogram.types import CallbackQuery

logger = logging.getLogger("cos.callbacks")

router = Router(name="callbacks")


@router.callback_query(F.data.startswith("complete:"))
async def on_complete(callback: CallbackQuery):
    """Handle task completion button press."""
    task_id = callback.data.split(":", 1)[1]
    logger.info(f"Task completed: {task_id}")
    # Phase 1: update YAML, update progress
    await callback.answer(f"Marked as done: {task_id}")
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("postpone:"))
async def on_postpone(callback: CallbackQuery):
    """Handle task postpone button press."""
    task_id = callback.data.split(":", 1)[1]
    logger.info(f"Task postponed: {task_id}")
    # Phase 1: update YAML, increment skip counter
    await callback.answer(f"Postponed: {task_id}")
    await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data.startswith("partial:"))
async def on_partial(callback: CallbackQuery):
    """Handle partial completion button press."""
    task_id = callback.data.split(":", 1)[1]
    logger.info(f"Task partial: {task_id}")
    await callback.answer(f"What did you finish? Reply with text.")
