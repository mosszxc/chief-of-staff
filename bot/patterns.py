"""Pattern detection from task history.

Scans last 7 days of history to detect:
  - task_avoidance: same task title postponed 3+ times
  - blocking_stale: blocking goal not progressing 5+ days
  - streak: 5+ consecutive days with >80% completion rate
"""

import logging
from datetime import date, timedelta

from bot.context import load_history, load_yaml

logger = logging.getLogger("cos.patterns")


def detect_task_avoidance(days: int = 7) -> list[dict]:
    """Detect tasks postponed 3+ times in the last N days.

    Returns list of {title, times_skipped, last_seen} dicts.
    """
    skip_counts: dict[str, dict] = {}

    for i in range(days):
        dt = date.today() - timedelta(days=i)
        hist = load_history(dt)
        for task in hist.get("tasks", []):
            if task.get("status") == "skipped":
                title = task.get("title", "???")
                if title not in skip_counts:
                    skip_counts[title] = {"title": title, "times_skipped": 0, "last_seen": dt.isoformat()}
                skip_counts[title]["times_skipped"] += 1

    return [
        entry for entry in skip_counts.values()
        if entry["times_skipped"] >= 3
    ]


def detect_blocking_stale(days_threshold: int = 5) -> list[dict]:
    """Detect blocking goals with no progress for 5+ days.

    A goal is "blocking" if the intent has a `blocking` field in intents.yaml.
    Staleness is measured from updated_at.

    Returns list of {goal_id, title, days_stale, blocks} dicts.
    """
    intents_data = load_yaml("intents.yaml")
    goals_data = load_yaml("goals.yaml")
    stale = []

    # Find blocking intents
    blocking_intents = {}
    for intent in intents_data.get("intents", []):
        if intent.get("blocking"):
            blocking_intents[intent["id"]] = intent["blocking"]

    if not blocking_intents:
        return []

    today = date.today()

    # Check goals of blocking intents
    for goal in goals_data.get("goals", []):
        intent_id = goal.get("intent", "")
        if intent_id not in blocking_intents:
            continue

        updated_at = goal.get("updated_at")
        if updated_at:
            try:
                if "T" in str(updated_at):
                    last_update = date.fromisoformat(str(updated_at).split("T")[0])
                else:
                    last_update = date.fromisoformat(str(updated_at))
                days_since = (today - last_update).days
            except (ValueError, TypeError):
                days_since = days_threshold + 1
        else:
            # Never updated = definitely stale
            days_since = days_threshold + 1

        if days_since >= days_threshold:
            stale.append({
                "goal_id": goal.get("id", "?"),
                "title": goal.get("title", "???"),
                "days_stale": days_since,
                "blocks": blocking_intents[intent_id],
            })

    return stale


def detect_streak(days: int = 10) -> dict | None:
    """Detect a completion streak: 5+ consecutive days with >80% done.

    Scans from today backwards, counting consecutive days.
    Returns {streak_days, avg_completion} or None.
    """
    streak_days = 0
    total_completion = 0.0

    for i in range(days):
        dt = date.today() - timedelta(days=i)
        hist = load_history(dt)
        tasks = hist.get("tasks", [])

        if not tasks:
            break

        done = sum(1 for t in tasks if t.get("status") == "done")
        total = len(tasks)
        pct = done / total if total > 0 else 0

        if pct >= 0.8:
            streak_days += 1
            total_completion += pct
        else:
            break

    if streak_days >= 5:
        return {
            "streak_days": streak_days,
            "avg_completion": round(total_completion / streak_days * 100),
        }
    return None


def detect_all_patterns() -> dict:
    """Run all pattern detections. Returns dict with all findings.

    Returns:
        {
            "avoidance": [...],     # tasks postponed 3+ times
            "blocking_stale": [...], # blocking goals stale 5+ days
            "streak": {...} | None,  # positive streak if active
        }
    """
    result = {
        "avoidance": detect_task_avoidance(),
        "blocking_stale": detect_blocking_stale(),
        "streak": detect_streak(),
    }

    # Log findings
    if result["avoidance"]:
        titles = [a["title"][:30] for a in result["avoidance"]]
        logger.info(f"[patterns] avoidance detected: {titles}")
    if result["blocking_stale"]:
        ids = [b["goal_id"] for b in result["blocking_stale"]]
        logger.info(f"[patterns] stale blocking goals: {ids}")
    if result["streak"]:
        logger.info(f"[patterns] streak: {result['streak']['streak_days']} days")

    return result


def format_patterns_for_prompt(patterns: dict) -> str:
    """Format detected patterns as text for the system prompt.

    Returns a string to inject into the morning plan context.
    """
    lines = []

    avoidance = patterns.get("avoidance", [])
    if avoidance:
        lines.append("AVOIDANCE PATTERNS:")
        for a in avoidance:
            lines.append(f"  - \"{a['title']}\" postponed {a['times_skipped']} times in 7 days. Consider: shorter format, different time, or drop entirely.")

    blocking = patterns.get("blocking_stale", [])
    if blocking:
        lines.append("STALE BLOCKERS:")
        for b in blocking:
            lines.append(f"  - \"{b['title']}\" ({b['goal_id']}) — no progress {b['days_stale']} days. Blocks: {b['blocks']}. This is critical.")

    streak = patterns.get("streak")
    if streak:
        lines.append(f"STREAK: {streak['streak_days']} days with >{streak['avg_completion']}% completion. Momentum is good.")

    return "\n".join(lines) if lines else ""
