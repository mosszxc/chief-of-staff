"""Drift detection cron job: 15:00 Moscow -> check progress -> push if needed.

Only sends push if 3+ working days without P1 progress.
Does NOT fire on weekends.
"""

import logging

logger = logging.getLogger("cos.scheduler.drift")


async def check_drift(bot, chat_id: int):
    """Check for drift and send alert if needed.

    1. Load recent history (7 days)
    2. Count working days without P1 progress
    3. If >= 3 days: send drift alert with facts
    4. If < 3 days: do nothing (no push)
    """
    logger.info("Drift check triggered")
    # Phase 2: implement full flow
