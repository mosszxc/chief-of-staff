"""Context engine: recipe -> assembled prompt.

Reads YAML state + strategy + optional Grimoire RAG,
assembles a system prompt via Jinja2 template.

All YAML writes go through save_yaml() which uses an asyncio.Lock per file.
"""

import asyncio
import logging
import os
from collections import deque
from datetime import date, timedelta
from pathlib import Path

import httpx
import yaml
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger("cos.context")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
HISTORY_DIR = DATA_DIR / "history"
RECIPES_DIR = BASE_DIR / "recipes"
TEMPLATES_DIR = BASE_DIR / "templates"

# Per-file asyncio locks for safe concurrent YAML writes
_yaml_locks: dict[str, asyncio.Lock] = {}

# In-memory conversation buffer (last 10 messages) — OK for MVP
_conversation_memory: deque[dict] = deque(maxlen=10)

# Grimoire API config
GRIMOIRE_API_URL = os.getenv("GRIMOIRE_API_URL", "http://localhost:8879")
GRIMOIRE_PROJECT = "cos"
GRIMOIRE_TTL_DAYS = 30  # warn if data older than this


def _get_lock(filename: str) -> asyncio.Lock:
    """Get or create an asyncio.Lock for a specific file."""
    if filename not in _yaml_locks:
        _yaml_locks[filename] = asyncio.Lock()
    return _yaml_locks[filename]


def load_yaml(filename: str) -> dict:
    """Load a YAML file from data/ directory."""
    path = DATA_DIR / filename
    if not path.exists():
        logger.warning(f"YAML not found: {path}")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


async def save_yaml(filename: str, data: dict) -> None:
    """Save a YAML file to data/ directory with asyncio.Lock."""
    lock = _get_lock(filename)
    async with lock:
        path = DATA_DIR / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        logger.info(f"Saved {path}")


def load_history(dt: date | None = None) -> dict:
    """Load history for a specific date (default: today)."""
    dt = dt or date.today()
    path = HISTORY_DIR / f"{dt.isoformat()}.yaml"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


async def save_history(data: dict, dt: date | None = None) -> None:
    """Save history for a specific date (default: today)."""
    dt = dt or date.today()
    filename = f"history/{dt.isoformat()}.yaml"
    lock = _get_lock(filename)
    async with lock:
        path = HISTORY_DIR / f"{dt.isoformat()}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        logger.info(f"Saved history: {path}")


def load_yesterday_summary() -> str:
    """Load yesterday's history as a text summary for the prompt."""
    yesterday = date.today() - timedelta(days=1)
    hist = load_history(yesterday)
    if not hist:
        return "No data for yesterday"

    lines = []
    tasks = hist.get("tasks", [])
    for t in tasks:
        status_icon = {"done": "\u2705", "skipped": "\u23ed", "partial": "\ud83d\udcdd"}.get(t.get("status", ""), "\u2753")
        lines.append(f"{status_icon} {t.get('title', '???')} [{t.get('status', '?')}]")

    skipped = hist.get("skipped_tasks", [])
    for s in skipped:
        if s.get("times_skipped", 0) >= 2:
            lines.append(f"\u26a0\ufe0f \"{s.get('task_id', '???')}\" skipped {s['times_skipped']} times total")

    energy = hist.get("energy")
    if energy:
        lines.append(f"Energy: {energy}/5")

    return "\n".join(lines) if lines else "No tasks recorded yesterday"


def get_skipped_tasks_history(days: int = 7) -> list[dict]:
    """Get tasks skipped multiple times in the last N days."""
    skip_counts: dict[str, int] = {}
    for i in range(days):
        dt = date.today() - timedelta(days=i + 1)
        hist = load_history(dt)
        for task in hist.get("tasks", []):
            if task.get("status") == "skipped":
                title = task.get("title", "???")
                skip_counts[title] = skip_counts.get(title, 0) + 1

    return [
        {"title": title, "times_skipped": count}
        for title, count in skip_counts.items()
        if count >= 2
    ]


def load_recipe(recipe_name: str) -> str:
    """Load a recipe .md file from recipes/ directory."""
    path = RECIPES_DIR / f"{recipe_name}.md"
    if not path.exists():
        logger.warning(f"Recipe not found: {path}")
        return ""
    return path.read_text(encoding="utf-8")


def build_system_prompt(**kwargs) -> str:
    """Build system prompt from Jinja2 template + context data."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("system_prompt.md")
    return template.render(**kwargs)


# --- Conversation memory ---

def add_to_memory(role: str, text: str) -> None:
    """Add a message to conversation memory (in-memory, last 10)."""
    _conversation_memory.append({"role": role, "text": text[:500]})


def get_conversation_memory() -> str:
    """Get last 10 messages as text for prompt context."""
    if not _conversation_memory:
        return ""
    lines = []
    for msg in _conversation_memory:
        prefix = "User" if msg["role"] == "user" else "Bot"
        lines.append(f"{prefix}: {msg['text']}")
    return "\n".join(lines)


# --- Grimoire RAG ---

async def grimoire_retrieve(query: str) -> str | None:
    """Retrieve from Grimoire knowledge base via HTTP API.

    Uses the fast retrieve endpoint (no LLM, raw graph data):
    POST http://localhost:8879/api/projects/{project}/retrieve

    Fallback: POST http://localhost:8879/api/search (slower, uses LLM)
    Returns text result or None if unavailable.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Try fast retrieve endpoint first (raw context, no LLM)
            resp = await client.post(
                f"{GRIMOIRE_API_URL}/api/projects/{GRIMOIRE_PROJECT}/retrieve",
                json={"query": query, "mode": "hybrid"},
            )
            if resp.status_code == 200:
                data = resp.json()
                parts = []

                # Extract entities
                for e in data.get("entities", [])[:10]:
                    name = e.get("name", "")
                    desc = e.get("description", "")
                    if name and desc:
                        parts.append(f"- {name}: {desc}")

                # Extract relationship descriptions
                for r in data.get("relationships", [])[:10]:
                    desc = r.get("description", "")
                    if desc:
                        parts.append(f"- {desc}")

                # Extract chunks (main content)
                for c in data.get("chunks", [])[:5]:
                    content = c.get("content", "")
                    if content:
                        parts.append(content[:500])

                # Also use context field if present
                context = data.get("context", "")
                if context:
                    parts.insert(0, context[:1000])

                if parts:
                    combined = "\n".join(parts)
                    logger.info(f"[grimoire] retrieved {len(combined)} chars for query '{query[:50]}'")
                    return combined[:3000]

            # If retrieve returned nothing useful, that's ok
            if resp.status_code != 200:
                logger.warning(f"[grimoire] HTTP {resp.status_code} for query '{query[:50]}'")

            return None

    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as e:
        logger.warning(f"[grimoire] unavailable: {e}")
        return None
    except Exception as e:
        logger.warning(f"[grimoire] unexpected error: {e}")
        return None


# --- Context assembly (enhanced for all recipes) ---

def _load_recent_progress(days: int = 7) -> str:
    """Load recent progress summary for drift/evening context."""
    lines = []
    for i in range(days):
        dt = date.today() - timedelta(days=i)
        hist = load_history(dt)
        if not hist or not hist.get("tasks"):
            continue
        done = [t for t in hist["tasks"] if t.get("status") == "done"]
        total = len(hist["tasks"])
        day_str = dt.strftime("%a %d/%m")
        if done:
            done_titles = ", ".join(t.get("title", "?")[:30] for t in done)
            lines.append(f"{day_str}: {len(done)}/{total} done ({done_titles})")
        else:
            lines.append(f"{day_str}: 0/{total} done")

    return "\n".join(lines) if lines else "No recent history"


def assemble_context(recipe_name: str, **extra) -> dict:
    """Load all context needed for a given recipe.

    Returns dict with all context keys needed by the system prompt template.
    Supports recipe-specific context loading.
    """
    # Base context: always loaded
    context = {
        "strategy": load_yaml("strategy.yaml").get("strategy", {}),
        "intents": load_yaml("intents.yaml"),
        "goals": load_yaml("goals.yaml"),
        "user_model": load_yaml("user_model.yaml").get("user_model", {}),
        "recipe_instruction": load_recipe(recipe_name),
        "yesterday": load_yesterday_summary(),
        "skipped_tasks": get_skipped_tasks_history(),
    }

    # Recipe-specific additions
    if recipe_name == "free_chat":
        context["conversation_memory"] = get_conversation_memory()

    elif recipe_name == "drift_alert":
        context["recent_progress"] = _load_recent_progress()

    elif recipe_name in ("domain_question", "interview_prep"):
        # Grimoire results added asynchronously by caller via extra
        context["grimoire_results"] = extra.get("grimoire_results", "")

    elif recipe_name == "recruiter_reply":
        context["recruiter_message"] = extra.get("recruiter_message", "")

    # Add user message if provided
    if extra.get("user_message"):
        context["user_message"] = extra["user_message"]

    return context


def data_files_exist() -> bool:
    """Check if the core data files have been populated."""
    required = ["strategy.yaml", "intents.yaml", "goals.yaml", "user_model.yaml"]
    for f in required:
        path = DATA_DIR / f
        if not path.exists():
            return False
        data = load_yaml(f)
        if not data:
            return False
    return True
