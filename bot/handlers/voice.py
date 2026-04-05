"""Voice message handler: transcribe with faster-whisper, route as text.

Pattern from grimoire/src/ingestion/whisper.py:
- Singleton model cache (lazy load)
- Graceful import (WHISPER_AVAILABLE flag)
- GPU with float16, fallback to CPU int8
"""

import asyncio
import logging
import os
from pathlib import Path

from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

logger = logging.getLogger("cos.voice")

router = Router(name="voice")

# Graceful import — faster-whisper is optional
try:
    from faster_whisper import WhisperModel

    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

# Singleton model cache — avoid reloading 3GB model per voice message
_model_cache: dict[str, "WhisperModel"] = {}


def _get_model(
    model_size: str = "large-v3",
    device: str = "cuda",
    compute_type: str = "float16",
) -> "WhisperModel":
    """Get or create cached WhisperModel instance.

    Falls back to base model on CPU if CUDA is unavailable.
    """
    cache_key = f"{model_size}:{device}:{compute_type}"
    if cache_key not in _model_cache:
        logger.info("Loading whisper model: %s (device=%s, compute=%s)", model_size, device, compute_type)
        try:
            _model_cache[cache_key] = WhisperModel(model_size, device=device, compute_type=compute_type)
        except Exception as e:
            if device == "cuda":
                # GPU failed — fallback to CPU with smaller model
                logger.warning("CUDA failed (%s), falling back to base/cpu/int8", e)
                fallback_key = "base:cpu:int8"
                if fallback_key not in _model_cache:
                    _model_cache[fallback_key] = WhisperModel("base", device="cpu", compute_type="int8")
                return _model_cache[fallback_key]
            raise
        logger.info("Whisper model loaded: %s", model_size)
    return _model_cache[cache_key]


def _transcribe_sync(ogg_path: str) -> str | None:
    """Synchronous transcription — runs in thread pool.

    Returns transcribed text or None if empty.
    """
    model = _get_model()
    segments, info = model.transcribe(ogg_path, beam_size=5)
    text = " ".join(s.text.strip() for s in segments)
    if text.strip():
        logger.info(
            "Transcription complete (lang=%s, prob=%.2f, duration=%.0fs)",
            info.language, info.language_probability, info.duration,
        )
        return text.strip()
    return None


def _check_auth(message: Message) -> bool:
    """Check if message is from authorized user."""
    authorized = os.getenv("TELEGRAM_CHAT_ID")
    if not authorized:
        return True
    return message.chat.id == int(authorized)


@router.message(F.voice)
async def on_voice(message: Message, state: FSMContext):
    """Handle voice message: download -> transcribe -> route as text."""
    if not _check_auth(message):
        return

    if not WHISPER_AVAILABLE:
        await message.reply("Голосовые сообщения недоступны (faster-whisper не установлен)")
        return

    ogg_path = f"/tmp/voice_{message.from_user.id}_{message.message_id}.ogg"

    try:
        # 1. Download .ogg from Telegram
        file = await message.bot.get_file(message.voice.file_id)
        await message.bot.download_file(file.file_path, ogg_path)

        # 2. Transcribe in thread pool (faster-whisper is sync)
        text = await asyncio.to_thread(_transcribe_sync, ogg_path)

        if not text:
            await message.reply("Не удалось распознать речь")
            return

        # 3. Show transcript
        # Escape markdown special chars in transcript to avoid parse errors
        safe_text = text.replace("_", "\\_").replace("*", "\\*").replace("`", "\\`")
        await message.reply(f"🎤 _{safe_text}_", parse_mode="Markdown")

        # 4. Route through the same pipeline as text messages
        from bot.handlers.messages import process_text
        logger.info(f"Routing voice text to process_text: '{text[:50]}'")
        await process_text(text, message, state)
        logger.info("Voice text routed successfully")

    except Exception as e:
        logger.error("Voice processing failed: %s", e, exc_info=True)
        # Don't hide errors behind "не удалось распознать" if transcription succeeded
        if text:
            await message.reply(f"Распознал: {text}\nНо ошибка при обработке: {e}")
        else:
            await message.reply("Не удалось распознать речь")

    finally:
        # Clean up .ogg file
        try:
            Path(ogg_path).unlink(missing_ok=True)
        except Exception:
            pass
