"""Context engine: recipe -> assembled prompt.

Reads YAML state + strategy + optional Grimoire RAG,
assembles a system prompt via Jinja2 template.

All YAML writes go through save_yaml() which uses an asyncio.Lock per file.
"""

import asyncio
import logging
import os
from datetime import date, timedelta
from pathlib import Path

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


def assemble_context(recipe_name: str) -> dict:
    """Load all context needed for a given recipe.

    Returns dict with all context keys needed by the system prompt template.
    """
    context = {
        "strategy": load_yaml("strategy.yaml").get("strategy", {}),
        "intents": load_yaml("intents.yaml"),
        "goals": load_yaml("goals.yaml"),
        "user_model": load_yaml("user_model.yaml").get("user_model", {}),
        "recipe_instruction": load_recipe(recipe_name),
        "yesterday": load_yesterday_summary(),
        "skipped_tasks": get_skipped_tasks_history(),
    }
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
