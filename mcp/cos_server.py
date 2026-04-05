#!/usr/bin/env python3
"""Chief of Staff MCP Server — state CRUD tools for Claude Code.

JSON-RPC over stdio (same pattern as Grimoire MCP server).

Tools:
  - get_today_plan      Read today's history
  - get_progress        Read goals progress (all or by intent)
  - get_user_model      Read user model
  - complete_task       Mark task done
  - postpone_task       Skip task, increment counter
  - add_task            Add task to today's plan
  - update_progress     Update goal progress string
  - create_intent       Create a new intent in intents.yaml
  - create_goal         Add a goal to an existing intent
  - ingest_research     Save research facts to Grimoire (cos project)
"""

import json
import os
import sys
import urllib.request
from datetime import date, datetime
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
HISTORY_DIR = DATA_DIR / "history"

GRIMOIRE_URL = os.environ.get("GRIMOIRE_URL", "http://localhost:8879")


# --- YAML helpers ---

def _load_yaml(filename: str) -> dict:
    """Load YAML file from data/ directory."""
    import yaml
    path = DATA_DIR / filename
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_yaml(filename: str, data: dict) -> None:
    """Save YAML file to data/ directory."""
    import yaml
    path = DATA_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _load_history(dt: date | None = None) -> dict:
    """Load history for a date (default: today)."""
    import yaml
    dt = dt or date.today()
    path = HISTORY_DIR / f"{dt.isoformat()}.yaml"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_history(data: dict, dt: date | None = None) -> None:
    """Save history for a date (default: today)."""
    import yaml
    dt = dt or date.today()
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = HISTORY_DIR / f"{dt.isoformat()}.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _grimoire_api(method: str, path: str, body: dict | None = None, timeout: int = 30) -> dict:
    """Call Grimoire HTTP API."""
    url = f"{GRIMOIRE_URL}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except Exception as e:
        return {"error": str(e)}


# --- Tool implementations ---

def tool_get_today_plan() -> str:
    """Get today's plan from history."""
    history = _load_history()
    if not history or not history.get("tasks"):
        return "No plan for today. Run /today in Telegram or generate via morning scheduler."
    return json.dumps(history, ensure_ascii=False, indent=2, default=str)


def tool_get_progress(intent_id: str = "") -> str:
    """Get progress for all goals or filtered by intent."""
    goals_data = _load_yaml("goals.yaml")
    goals = goals_data.get("goals", [])
    if not goals:
        return "No goals found."

    if intent_id:
        goals = [g for g in goals if g.get("intent") == intent_id]
        if not goals:
            return f"No goals found for intent '{intent_id}'."

    return json.dumps(goals, ensure_ascii=False, indent=2, default=str)


def tool_get_user_model() -> str:
    """Get user model."""
    data = _load_yaml("user_model.yaml")
    if not data:
        return "No user model found."
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def tool_complete_task(task_id: str, note: str = "") -> str:
    """Mark a task as completed in today's history."""
    history = _load_history()
    if not history or not history.get("tasks"):
        return f"Error: no plan for today. Cannot complete task '{task_id}'."

    for task in history["tasks"]:
        if task.get("id") == task_id:
            task["status"] = "done"
            if note:
                task["note"] = note
            _save_history(history)

            # Update goal timestamp if linked
            goal_id = task.get("goal_id")
            if goal_id:
                goals_data = _load_yaml("goals.yaml")
                for goal in goals_data.get("goals", []):
                    if goal.get("id") == goal_id:
                        goal["updated_at"] = datetime.now().isoformat()
                        goal["updated_by"] = "claude_code"
                        break
                _save_yaml("goals.yaml", goals_data)

            return f"Task '{task_id}' marked as done."

    return f"Error: task '{task_id}' not found in today's plan."


def tool_postpone_task(task_id: str, reason: str = "") -> str:
    """Postpone a task. Increments skip counter."""
    history = _load_history()
    if not history or not history.get("tasks"):
        return f"Error: no plan for today. Cannot postpone task '{task_id}'."

    for task in history["tasks"]:
        if task.get("id") == task_id:
            task["status"] = "skipped"
            if reason:
                task["note"] = reason

            # Update skip counter
            skipped = history.get("skipped_tasks", [])
            existing = next((s for s in skipped if s.get("task_id") == task_id), None)
            if existing:
                existing["times_skipped"] = existing.get("times_skipped", 0) + 1
                if reason:
                    existing["reason"] = reason
            else:
                entry = {"task_id": task_id, "times_skipped": 1}
                if reason:
                    entry["reason"] = reason
                skipped.append(entry)
            history["skipped_tasks"] = skipped

            _save_history(history)
            return f"Task '{task_id}' postponed."

    return f"Error: task '{task_id}' not found in today's plan."


def tool_add_task(title: str, intent_id: str = "") -> str:
    """Add a new task to today's plan."""
    history = _load_history()
    if not history:
        history = {
            "date": date.today().isoformat(),
            "plan_reasoning": "",
            "tasks": [],
            "skipped_tasks": [],
            "energy": None,
        }

    tasks = history.get("tasks", [])
    new_id = f"{date.today().isoformat()}_{len(tasks) + 1}"

    task = {
        "id": new_id,
        "title": title,
        "intent": intent_id,
        "goal_id": None,
        "progress_delta": None,
        "status": "pending",
    }
    tasks.append(task)
    history["tasks"] = tasks
    _save_history(history)

    return f"Task added: '{title}' (id: {new_id})"


def tool_update_progress(goal_id: str, progress: str) -> str:
    """Update goal progress string in goals.yaml."""
    goals_data = _load_yaml("goals.yaml")
    goals = goals_data.get("goals", [])

    for goal in goals:
        if goal.get("id") == goal_id:
            old_progress = goal.get("progress", "?")
            goal["progress"] = progress
            goal["updated_at"] = datetime.now().isoformat()
            goal["updated_by"] = "claude_code"
            _save_yaml("goals.yaml", goals_data)

            # Also update in intents.yaml for consistency
            intents_data = _load_yaml("intents.yaml")
            for intent in intents_data.get("intents", []):
                for g in intent.get("goals", []):
                    full_id = f"{intent['id']}/{g['id']}"
                    if full_id == goal_id or g.get("id") == goal_id:
                        g["progress"] = progress
                        g["updated_at"] = datetime.now().isoformat()
                        g["updated_by"] = "claude_code"
                        break
            _save_yaml("intents.yaml", intents_data)

            return f"Goal '{goal_id}' progress: {old_progress} -> {progress}"

    return f"Error: goal '{goal_id}' not found."


def tool_create_intent(
    id: str,
    title: str,
    priority: str = "P2",
    deadline: str = "",
    success: str = "",
    methodology: str = "",
) -> str:
    """Create a new intent in intents.yaml."""
    intents_data = _load_yaml("intents.yaml")
    intents = intents_data.get("intents", [])

    # Check for duplicate
    for intent in intents:
        if intent.get("id") == id:
            return f"Error: intent '{id}' already exists."

    new_intent = {
        "id": id,
        "title": title,
        "priority": priority,
    }
    if deadline:
        new_intent["deadline"] = deadline
    if success:
        new_intent["success"] = success
    if methodology:
        new_intent["methodology"] = methodology
    new_intent["goals"] = []

    intents.append(new_intent)
    intents_data["intents"] = intents
    _save_yaml("intents.yaml", intents_data)

    return f"Intent created: [{priority}] {title} (id: {id})"


def tool_create_goal(intent_id: str, id: str, title: str, progress: str = "0%") -> str:
    """Add a goal to an existing intent in intents.yaml and goals.yaml."""
    intents_data = _load_yaml("intents.yaml")
    target_intent = None

    for intent in intents_data.get("intents", []):
        if intent.get("id") == intent_id:
            target_intent = intent
            break

    if not target_intent:
        return f"Error: intent '{intent_id}' not found."

    # Check for duplicate goal
    for g in target_intent.get("goals", []):
        if g.get("id") == id:
            return f"Error: goal '{id}' already exists in intent '{intent_id}'."

    now = datetime.now().isoformat()

    # Add to intents.yaml
    new_goal = {
        "id": id,
        "title": title,
        "progress": progress,
        "updated_at": now,
        "updated_by": "claude_code",
    }
    if "goals" not in target_intent:
        target_intent["goals"] = []
    target_intent["goals"].append(new_goal)
    _save_yaml("intents.yaml", intents_data)

    # Add to goals.yaml
    goals_data = _load_yaml("goals.yaml")
    if "goals" not in goals_data:
        goals_data["goals"] = []
    goals_data["goals"].append({
        "id": f"{intent_id}/{id}",
        "intent": intent_id,
        "title": title,
        "progress": progress,
        "updated_at": now,
        "updated_by": "claude_code",
    })
    _save_yaml("goals.yaml", goals_data)

    return f"Goal created: {title} ({progress}) in intent '{intent_id}'"


def tool_ingest_research(
    query: str,
    facts: str,
    source_type: str = "research",
    ttl_days: int = 30,
) -> str:
    """Ingest research facts into Grimoire (cos project).

    Pre-processes facts and adds metadata before sending to Grimoire API.
    """
    now = datetime.now().isoformat()
    metadata_header = (
        f"[Research: {query}]\n"
        f"Source type: {source_type}\n"
        f"Researched at: {now}\n"
        f"TTL: {ttl_days} days\n"
        f"---\n\n"
    )
    text = metadata_header + facts

    result = _grimoire_api(
        "POST",
        "/api/projects/cos/documents",
        {"text": text, "source": f"research:{query[:80]}"},
        timeout=120,
    )

    if result.get("status") in ("ok", "queued"):
        chars = result.get("chars", len(text))
        return f"Research ingested into Grimoire (cos): {chars} chars. Query: '{query}'. TTL: {ttl_days}d."
    elif "error" in result:
        return f"Error ingesting research: {result['error']}"
    else:
        return f"Unexpected response: {json.dumps(result, ensure_ascii=False)}"


# --- JSON-RPC server ---

TOOLS = [
    {
        "name": "get_today_plan",
        "description": "Get today's task plan from history. Returns tasks with statuses, reasoning, and progress deltas.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_progress",
        "description": "Get progress for all goals, or filter by intent_id. Shows goal titles, progress strings, and last update info.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "intent_id": {
                    "type": "string",
                    "description": "Filter by intent ID (e.g., 'h1-offer'). Omit for all goals.",
                },
            },
        },
    },
    {
        "name": "get_user_model",
        "description": "Get the user model: identity, knowledge stack, career capital, preferences, narrative with case stories.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "complete_task",
        "description": "Mark a task from today's plan as completed. Optionally add a note about what was done.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID (e.g., '2026-04-05_1')"},
                "note": {"type": "string", "description": "Optional completion note"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "postpone_task",
        "description": "Postpone a task to tomorrow. System tracks skip count. Optionally provide a reason.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID to postpone"},
                "reason": {"type": "string", "description": "Optional reason for postponing"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "add_task",
        "description": "Add a new task to today's plan.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Task title"},
                "intent_id": {"type": "string", "description": "Optional intent ID to link the task to"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_progress",
        "description": "Update a goal's progress string. E.g., '37/50', '1/3', '50%'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "string", "description": "Goal ID (e.g., 'h1-offer/applications')"},
                "progress": {"type": "string", "description": "New progress string"},
            },
            "required": ["goal_id", "progress"],
        },
    },
    {
        "name": "create_intent",
        "description": "Create a new intent (goal area) in intents.yaml. Part of the New Intent Workflow (ASSESS -> RESEARCH -> DECOMPOSE -> METHOD -> SAVE).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "description": "Unique intent ID (e.g., 'learn-korean', 'launch-saas')"},
                "title": {"type": "string", "description": "Human-readable title"},
                "priority": {"type": "string", "description": "Priority: P1, P2, P3", "default": "P2"},
                "deadline": {"type": "string", "description": "Optional deadline (YYYY-MM-DD)"},
                "success": {"type": "string", "description": "Success criteria"},
                "methodology": {"type": "string", "description": "Daily methodology from RESEARCH+METHOD steps"},
            },
            "required": ["id", "title"],
        },
    },
    {
        "name": "create_goal",
        "description": "Add a goal to an existing intent. Goals are measurable milestones within an intent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "intent_id": {"type": "string", "description": "Parent intent ID"},
                "id": {"type": "string", "description": "Goal ID within the intent (e.g., 'vocab', 'sessions')"},
                "title": {"type": "string", "description": "Goal title"},
                "progress": {"type": "string", "description": "Initial progress (e.g., '0/10', '0%')", "default": "0%"},
            },
            "required": ["intent_id", "id", "title"],
        },
    },
    {
        "name": "ingest_research",
        "description": "Save research findings to Grimoire knowledge base (cos project). Use after RESEARCH step in New Intent Workflow to persist domain knowledge for reuse.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Research query / topic"},
                "facts": {"type": "string", "description": "Research findings (already extracted by Claude Code)"},
                "source_type": {"type": "string", "description": "Type: research, market_data, methodology, etc.", "default": "research"},
                "ttl_days": {"type": "integer", "description": "Days before data is considered stale", "default": 30},
            },
            "required": ["query", "facts"],
        },
    },
]


def handle_request(request: dict) -> dict | None:
    """Handle a JSON-RPC request."""
    method = request.get("method")
    params = request.get("params", {})
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "cos", "version": "1.0.0"},
            },
        }

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }

    if method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})

        try:
            if tool_name == "get_today_plan":
                text = tool_get_today_plan()

            elif tool_name == "get_progress":
                text = tool_get_progress(args.get("intent_id", ""))

            elif tool_name == "get_user_model":
                text = tool_get_user_model()

            elif tool_name == "complete_task":
                text = tool_complete_task(args["task_id"], args.get("note", ""))

            elif tool_name == "postpone_task":
                text = tool_postpone_task(args["task_id"], args.get("reason", ""))

            elif tool_name == "add_task":
                text = tool_add_task(args["title"], args.get("intent_id", ""))

            elif tool_name == "update_progress":
                text = tool_update_progress(args["goal_id"], args["progress"])

            elif tool_name == "create_intent":
                text = tool_create_intent(
                    id=args["id"],
                    title=args["title"],
                    priority=args.get("priority", "P2"),
                    deadline=args.get("deadline", ""),
                    success=args.get("success", ""),
                    methodology=args.get("methodology", ""),
                )

            elif tool_name == "create_goal":
                text = tool_create_goal(
                    intent_id=args["intent_id"],
                    id=args["id"],
                    title=args["title"],
                    progress=args.get("progress", "0%"),
                )

            elif tool_name == "ingest_research":
                text = tool_ingest_research(
                    query=args["query"],
                    facts=args["facts"],
                    source_type=args.get("source_type", "research"),
                    ttl_days=args.get("ttl_days", 30),
                )

            else:
                raise ValueError(f"Unknown tool: {tool_name}")

            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": text}]},
            }

        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {e}"}],
                    "isError": True,
                },
            }

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def main():
    """Read JSON-RPC requests from stdin, write responses to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            if response is not None:
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            pass


if __name__ == "__main__":
    main()
