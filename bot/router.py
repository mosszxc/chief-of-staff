"""Message router: message -> enum (PLAN/COMPLETE/CHAT/...).

Two-level routing:
  Level 1: Python (instant, no LLM) -- buttons, commands, keyword match.
  Level 2: LLM (Haiku) -- ambiguous free text.
"""

import enum
import logging

logger = logging.getLogger("cos.router")


class Intent(enum.Enum):
    """Router output enum. Maps to recipe + model."""
    PLAN = "plan"
    COMPLETE = "complete"
    POSTPONE = "postpone"
    ADD_TASK = "add_task"
    STATUS = "status"
    CHAT = "chat"
    DOMAIN = "domain"
    INTERVIEW = "interview"
    RECRUITER = "recruiter"
    GOAL_CHANGE = "goal_change"


# Model routing: intent -> model
INTENT_MODEL = {
    Intent.PLAN: "sonnet",
    Intent.COMPLETE: "haiku",
    Intent.POSTPONE: "haiku",
    Intent.ADD_TASK: "haiku",
    Intent.STATUS: "haiku",
    Intent.CHAT: "sonnet",
    Intent.DOMAIN: "sonnet",
    Intent.INTERVIEW: "sonnet",
    Intent.RECRUITER: "sonnet",
    Intent.GOAL_CHANGE: "sonnet",
}


def keyword_match(text: str) -> Intent | None:
    """Level 1: Python keyword matching. Returns None if ambiguous."""
    lower = text.lower().strip()

    if lower in ("план", "plan", "что сегодня", "today"):
        return Intent.PLAN
    if lower in ("статус", "status", "прогресс"):
        return Intent.STATUS
    if any(w in lower for w in ("сделал", "готово", "done", "выполнил")):
        return Intent.COMPLETE
    if any(w in lower for w in ("завтра", "отложить", "skip", "postpone")):
        return Intent.POSTPONE

    return None


async def route_message(text: str) -> Intent:
    """Route a user message to an intent.

    Tries keyword match first (free), falls back to LLM (Haiku) for ambiguous text.
    """
    # Level 1: keyword match
    intent = keyword_match(text)
    if intent:
        logger.info(f"[router] keyword -> {intent.value}")
        return intent

    # Level 2: LLM classification (placeholder -- Phase 2)
    logger.info(f"[router] fallback -> CHAT")
    return Intent.CHAT
