"""Claude CLI subprocess wrapper.

All LLM calls go through this module. Never import anthropic directly.
"""

import asyncio
import json
import logging
import time

logger = logging.getLogger("cos.claude")


async def call_claude(
    prompt: str,
    model: str = "sonnet",
    timeout: int = 60,
    mcp_config: str | None = "cos-mcp.json",
) -> str | None:
    """Call Claude via CLI subscription. Async -- does not block the bot.

    Args:
        prompt: The prompt text to send.
        model: 'haiku' or 'sonnet'.
        timeout: Max seconds to wait.
        mcp_config: Path to MCP config JSON, or None to skip.

    Returns:
        Claude's text response, or None on timeout/error.
    """
    cmd = [
        "claude", "--print", "-p", prompt,
        "--model", model,
        "--output-format", "stream-json",
    ]
    if mcp_config:
        cmd.extend(["--mcp-config", mcp_config])

    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        elapsed = time.monotonic() - start

        # Parse stream-json: look for result message
        for line in stdout.decode().strip().split("\n"):
            if not line.strip():
                continue
            try:
                parsed = json.loads(line)
                if parsed.get("type") == "result":
                    logger.info(f"[claude] model={model} elapsed={elapsed:.1f}s")
                    return parsed.get("result", "")
            except json.JSONDecodeError:
                continue

        logger.warning(f"[claude] no result in output, model={model}")
        return stdout.decode().strip() or None

    except asyncio.TimeoutError:
        logger.warning(f"[claude] timeout after {timeout}s, model={model}")
        return None
    except Exception as e:
        logger.error(f"[claude] error: {e}")
        return None


async def call_claude_safe(
    prompt: str,
    model: str = "sonnet",
    timeout: int = 60,
) -> str | None:
    """Wrapper with 1 retry and fallback."""
    for attempt in range(2):
        result = await call_claude(prompt, model, timeout)
        if result is not None:
            return result
        logger.warning(f"[claude] retry {attempt + 1}")
    return None
