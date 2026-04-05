"""MCP server for Chief of Staff -- 7 tools for state CRUD.

Used by:
  1. Claude Code sync (Claude Code reads/writes state via MCP tools)
  2. Claude CLI during plan generation (read state + update in one reasoning chain)

NOT used for:
  Buttons (bot updates YAML directly)
  Commands /today, /status (bot reads YAML directly)
"""

import logging

logger = logging.getLogger("cos.mcp")


# Placeholder -- Phase 3 implementation
# Tools: complete_task, postpone_task, update_progress,
#         add_task, get_progress, get_today_plan, get_user_model

def complete_task(task_id: str, note: str = "") -> dict:
    """Mark a task from today's plan as completed."""
    raise NotImplementedError("Phase 3")


def postpone_task(task_id: str, reason: str = "") -> dict:
    """Postpone task to tomorrow. System tracks skip count."""
    raise NotImplementedError("Phase 3")


def update_progress(goal_id: str, progress: str) -> dict:
    """Update goal progress. E.g.: '37/50', '1/3'."""
    raise NotImplementedError("Phase 3")


def add_task(title: str, intent_id: str = "") -> dict:
    """Add a task to today's plan."""
    raise NotImplementedError("Phase 3")


def get_progress(intent_id: str = "") -> dict:
    """Get progress for all goals or a specific intent."""
    raise NotImplementedError("Phase 3")


def get_today_plan() -> dict:
    """Get today's plan."""
    raise NotImplementedError("Phase 3")


def get_user_model() -> dict:
    """Get user model (stack, preferences, narrative)."""
    raise NotImplementedError("Phase 3")


if __name__ == "__main__":
    logger.info("COS MCP server -- Phase 3")
