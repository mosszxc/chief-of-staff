"""Message router: message -> enum (PLAN/COMPLETE/CHAT/...).

Two-level routing:
  Level 1: Python (instant, no LLM) -- buttons, commands, keyword match.
  Level 2: LLM (Haiku) -- ambiguous free text.
"""

import enum
import logging

from bot.claude import call_claude_safe

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
    NEW_INTENT = "new_intent"


# Model routing: intent -> model (see MODEL_GUIDE.md)
INTENT_MODEL = {
    Intent.PLAN: "sonnet",         # MODEL_GUIDE: daily_plan — Sonnet
    Intent.COMPLETE: "haiku",      # MODEL_GUIDE: task completion parsing — Haiku
    Intent.POSTPONE: "haiku",      # MODEL_GUIDE: CRUD operation — Haiku
    Intent.ADD_TASK: "haiku",      # MODEL_GUIDE: CRUD operation — Haiku
    Intent.STATUS: "haiku",        # MODEL_GUIDE: data formatting — Haiku
    Intent.CHAT: "sonnet",         # MODEL_GUIDE: free_chat — Sonnet
    Intent.DOMAIN: "sonnet",       # MODEL_GUIDE: domain question — Sonnet
    Intent.INTERVIEW: "sonnet",    # MODEL_GUIDE: interview_prep — Sonnet
    Intent.RECRUITER: "sonnet",    # MODEL_GUIDE: recruiter_reply — Sonnet
    Intent.GOAL_CHANGE: "opus",    # MODEL_GUIDE: goal_change challenge — Opus
    Intent.NEW_INTENT: "opus",     # MODEL_GUIDE: pipeline entry — Opus (handled by pipeline)
}

# Recipe mapping: intent -> recipe file name
INTENT_RECIPE = {
    Intent.PLAN: "daily_plan",
    Intent.CHAT: "free_chat",
    Intent.DOMAIN: "domain_question",
    Intent.INTERVIEW: "interview_prep",
    Intent.RECRUITER: "recruiter_reply",
    Intent.GOAL_CHANGE: "goal_change",
    Intent.NEW_INTENT: "new_intent",
}

# Classification prompt for Haiku
_CLASSIFY_PROMPT = """Classify this Telegram message into ONE category. Reply with ONLY the category name, nothing else.

Categories:
- COMPLETE — user says they did/finished something ("сделал portfolio", "откликнулся на 5", "закончил", "записал видео")
- POSTPONE — user wants to skip/delay something ("не буду сегодня", "отложу", "завтра")
- PLAN — user asks for a plan ("что делать", "план на сегодня")
- STATUS — user asks about progress ("как дела с целями", "прогресс")
- ADD_TASK — user wants to add a new task ("добавь задачу", "ещё надо")
- DOMAIN — question about strategy/marketing/business domain ("что мы знаем про VSL", "как работают воронки")
- INTERVIEW — interview preparation ("подготовь к интервью", "как отвечать на вопрос")
- RECRUITER — recruiter message or reply ("рекрутер написал", "как ответить рекрутеру")
- NEW_INTENT — user wants to START something new, learn something, begin a new project ("хочу выучить корейский", "хочу начать бегать", "хочу научиться X", "want to learn", "начну X")
- GOAL_CHANGE — wants to CHANGE/modify/drop an EXISTING goal ("хочу поменять цель", "может лучше в DA", "дропаю", "поменяй приоритет")
- CHAT — everything else (general question, conversation)

Important: NEW_INTENT = starting something completely new. GOAL_CHANGE = modifying something that already exists.

Message: "{message}"

Category:"""


async def _llm_classify(text: str) -> Intent:
    """Level 2: Use Haiku to classify ambiguous text."""
    prompt = _CLASSIFY_PROMPT.format(message=text[:500])
    # MODEL_GUIDE: Haiku — router classification, binary choice
    result = await call_claude_safe(prompt, model="haiku", timeout=30, recipe="router")

    if result:
        category = result.strip().upper().replace(" ", "_")
        # Try to match to enum
        for intent in Intent:
            if intent.value.upper() == category or intent.name == category:
                logger.info(f"[router] LLM -> {intent.value}")
                return intent

    logger.info(f"[router] LLM unrecognized '{result}' -> CHAT")
    return Intent.CHAT


def keyword_match(text: str) -> Intent | None:
    """Level 1: Python keyword matching. Returns None if ambiguous."""
    lower = text.lower().strip()

    if lower in ("план", "plan", "что сегодня", "today"):
        return Intent.PLAN
    if lower in ("статус", "status", "прогресс"):
        return Intent.STATUS
    if any(w in lower for w in ("завтра", "отложить", "skip", "postpone")):
        return Intent.POSTPONE

    # Interview keywords
    if any(w in lower for w in ("интервью", "собеседовани", "interview")):
        return Intent.INTERVIEW
    # Recruiter keywords
    if any(w in lower for w in ("рекрутер", "recruiter", "hr написал")):
        return Intent.RECRUITER

    # New intent keywords (BEFORE goal_change — "хочу выучить" is new, not change)
    _new_intent_patterns = [
        "хочу выучить", "хочу научить", "хочу начать",
        "хочу освоить", "хочу попробовать", "хочу запустить",
        "хочу открыть", "хочу создать", "хочу построить",
        "want to learn", "want to start",
        "начну учить", "начну изучать",
        "научи меня", "помоги выучить", "помоги освоить",
    ]
    if any(p in lower for p in _new_intent_patterns):
        return Intent.NEW_INTENT

    # Goal change keywords
    if any(w in lower for w in ("поменять цель", "дропаю", "хочу поменять", "может лучше")):
        return Intent.GOAL_CHANGE

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

    # Level 2: LLM classification
    return await _llm_classify(text)
