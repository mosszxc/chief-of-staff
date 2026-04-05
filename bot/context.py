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
GRIMOIRE_TTL_DAYS = 30  # warn if data older than this

# Dual-project routing keywords
RUBICK_KEYWORDS = [
    "маркетинг", "копирайтинг", "оффер", "vsl", "воронк", "лидген",
    "продаж", "landing", "реклам", "конверси", "таргет", "трафик",
    "позиционирован", "копирайт", "бренд", "контент", "offer",
    "психолог", "аудитори", "сегмент",
]
COS_KEYWORDS = [
    "карьер", "работу ", "работа ", "работе ", "работой",
    "интервью", "зарплат", "виз", "резюме",
    "вакансi", "вакансий", "стартап", "рынок труда", "технолог",
    "langgraph", "ai agent", "portfolio", "linkedin", "github",
    "собеседован", "рекрут", "нанима", "найм",
]


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


# --- Grimoire RAG (dual-project routing) ---

def _select_grimoire_project(query: str) -> str:
    """Auto-select Grimoire project based on query keywords.

    Returns: "rubick", "cos", or "both" if ambiguous.
    """
    lower = query.lower()
    rubick_score = sum(1 for kw in RUBICK_KEYWORDS if kw in lower)
    cos_score = sum(1 for kw in COS_KEYWORDS if kw in lower)

    if rubick_score > 0 and cos_score == 0:
        return "rubick"
    if cos_score > 0 and rubick_score == 0:
        return "cos"
    if rubick_score > 0 and cos_score > 0:
        return "both"
    # Neither matched -- default to both
    return "both"


async def _retrieve_from_project(client: httpx.AsyncClient, project: str, query: str) -> str | None:
    """Retrieve from a single Grimoire project. Returns formatted text or None."""
    try:
        resp = await client.post(
            f"{GRIMOIRE_API_URL}/api/projects/{project}/retrieve",
            json={"query": query, "mode": "hybrid"},
        )
        if resp.status_code != 200:
            logger.warning(f"[grimoire/{project}] HTTP {resp.status_code}")
            return None

        data = resp.json()
        parts = []

        # Extract context field if present
        context = data.get("context", "")
        if context:
            parts.append(context[:1000])

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

        if parts:
            combined = "\n".join(parts)
            logger.info(f"[grimoire/{project}] retrieved {len(combined)} chars for '{query[:50]}'")
            return combined[:2000]

        return None

    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError) as e:
        logger.warning(f"[grimoire/{project}] unavailable: {e}")
        return None
    except Exception as e:
        logger.warning(f"[grimoire/{project}] error: {e}")
        return None


# Minimum entity count to consider an area "covered" in Grimoire.
# RAG semantic search always returns closest matches, even if irrelevant.
# We need at least a few entities with actual content to trust the coverage.
_MIN_ENTITIES_FOR_COVERAGE = 3
# Minimum chunks for coverage (chunks contain the actual source text)
_MIN_CHUNKS_FOR_COVERAGE = 1


async def _validate_relevance(area: str, entities: list[str]) -> bool:
    """LLM-validate that Grimoire entities are actually relevant to the area.

    RAG semantic search returns closest matches even if irrelevant.
    Haiku checks: do these entities contain knowledge about this area?
    Cost: ~$0.0001 per call.
    """
    from bot.claude import call_claude_safe

    entities_str = ", ".join(entities[:15])  # max 15 to keep prompt short
    prompt = (
        f"Вопрос: содержат ли эти сущности из базы знаний реальную информацию "
        f"по теме \"{area}\"?\n\n"
        f"Сущности: {entities_str}\n\n"
        f"Ответь ОДНИМ словом: ДА или НЕТ. "
        f"ДА = сущности действительно про эту тему. "
        f"НЕТ = сущности про другое, просто похожие слова."
    )
    result = await call_claude_safe(prompt, model="haiku", recipe="relevance_check")
    if result is None:
        return True  # fallback: trust threshold if LLM unavailable
    return "ДА" in result.upper()


async def _check_project_coverage(client: httpx.AsyncClient, project: str, area: str) -> dict | None:
    """Check if a Grimoire project has meaningful coverage for an area.

    Returns dict with {entities, chunks, data} if covered, None if not.
    Two-stage validation:
      1. Threshold check (entity/chunk count)
      2. LLM relevance check (Haiku validates entities are actually about this topic)
    """
    try:
        resp = await client.post(
            f"{GRIMOIRE_API_URL}/api/projects/{project}/retrieve",
            json={"query": area, "mode": "hybrid"},
        )
        if resp.status_code != 200:
            return None

        raw = resp.json()
        entities = raw.get("entities", [])
        n_entities = len(entities)
        n_chunks = len(raw.get("chunks", []))

        # Stage 1: threshold check
        if n_entities < _MIN_ENTITIES_FOR_COVERAGE and n_chunks < _MIN_CHUNKS_FOR_COVERAGE:
            logger.debug(f"[coverage/{project}] '{area}': below threshold (ent={n_entities}, chunks={n_chunks})")
            return None

        # Stage 2: LLM relevance validation
        entity_names = [e.get("name", e) if isinstance(e, dict) else str(e) for e in entities[:15]]
        is_relevant = await _validate_relevance(area, entity_names)
        if not is_relevant:
            logger.info(f"[coverage/{project}] '{area}': threshold passed but LLM says NOT relevant")
            return None

        # Format the data for downstream use
        data = await _retrieve_from_project(client, project, area)
        if not data:
            return None

        return {
            "entities": n_entities,
            "chunks": n_chunks,
            "data": data,
        }

    except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError):
        return None
    except Exception as e:
        logger.warning(f"[coverage/{project}] error for '{area}': {e}")
        return None


async def check_knowledge_coverage(areas: list[str]) -> dict:
    """Check which knowledge areas are covered in Grimoire.

    For each area, queries both rubick and cos projects.
    Returns dict of area -> {status, source, needs_research, data}.

    Status:
      "ok"      - has meaningful knowledge (enough entities/chunks)
      "expired" - has knowledge, but TTL expired (needs refresh)
      "missing" - no meaningful knowledge found (needs full research)
    """
    coverage = {}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            for area in areas:
                rubick_result = None
                cos_result = None

                # Query both projects
                try:
                    rubick_result = await _check_project_coverage(client, "rubick", area)
                except Exception as e:
                    logger.warning(f"[coverage/rubick] error for '{area}': {e}")

                try:
                    cos_result = await _check_project_coverage(client, "cos", area)
                except Exception as e:
                    logger.warning(f"[coverage/cos] error for '{area}': {e}")

                has_knowledge = bool(rubick_result) or bool(cos_result)

                # TTL check placeholder — proper TTL needs doc metadata from Grimoire
                ttl_ok = True

                if has_knowledge and ttl_ok:
                    status = "ok"
                elif has_knowledge and not ttl_ok:
                    status = "expired"
                else:
                    status = "missing"

                # Pick the best source (prefer the one with more data)
                if rubick_result and cos_result:
                    # Both have data — pick the one with more entities
                    if rubick_result["entities"] >= cos_result["entities"]:
                        source = "rubick"
                        data = rubick_result["data"]
                    else:
                        source = "cos"
                        data = cos_result["data"]
                elif rubick_result:
                    source = "rubick"
                    data = rubick_result["data"]
                elif cos_result:
                    source = "cos"
                    data = cos_result["data"]
                else:
                    source = None
                    data = None

                coverage[area] = {
                    "status": status,
                    "source": source,
                    "needs_research": status != "ok",
                    "data": data,
                }

                logger.info(f"[coverage] '{area}' -> {status} (source={source})")

    except (httpx.ConnectError, httpx.TimeoutException) as e:
        # Grimoire is offline — mark all areas as missing (graceful fallback)
        logger.warning(f"[coverage] Grimoire unavailable: {e} — all areas marked as missing")
        for area in areas:
            coverage[area] = {
                "status": "missing",
                "source": None,
                "needs_research": True,
                "data": None,
            }
    except Exception as e:
        logger.warning(f"[coverage] unexpected error: {e} — all areas marked as missing")
        for area in areas:
            coverage[area] = {
                "status": "missing",
                "source": None,
                "needs_research": True,
                "data": None,
            }

    return coverage


async def grimoire_retrieve(query: str, project: str = "auto") -> str | None:
    """Retrieve from Grimoire knowledge base with dual-project routing.

    project="auto" (default): auto-selects rubick/cos/both based on keywords.
    project="rubick": marketing, copy, psychology, VSL, business strategy.
    project="cos": career, job market, tech, interview prep.

    Fallback: if primary project returns nothing, tries the other.
    """
    if project == "auto":
        project = _select_grimoire_project(query)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if project == "both":
                # Query both projects, merge results
                import asyncio
                results = await asyncio.gather(
                    _retrieve_from_project(client, "rubick", query),
                    _retrieve_from_project(client, "cos", query),
                    return_exceptions=True,
                )
                parts = []
                for i, (proj, result) in enumerate(zip(["rubick", "cos"], results)):
                    if isinstance(result, Exception):
                        continue
                    if result:
                        parts.append(f"[{proj}]\n{result}")

                if parts:
                    combined = "\n\n---\n\n".join(parts)
                    return combined[:3000]

                return None

            else:
                # Single project query
                result = await _retrieve_from_project(client, project, query)
                if result:
                    return result

                # Fallback: try the other project
                fallback = "cos" if project == "rubick" else "rubick"
                logger.info(f"[grimoire] primary '{project}' empty, trying fallback '{fallback}'")
                result = await _retrieve_from_project(client, fallback, query)
                return result

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
    from bot.patterns import detect_all_patterns, format_patterns_for_prompt

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

    # Pattern detection for morning plan and drift
    if recipe_name in ("daily_plan", "drift_alert"):
        patterns = detect_all_patterns()
        patterns_text = format_patterns_for_prompt(patterns)
        if patterns_text:
            context["detected_patterns"] = patterns_text

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
