"""Claude CLI subprocess wrapper.

All LLM calls go through this module. Never import anthropic directly.
Uses asyncio.create_subprocess_exec to avoid blocking the event loop.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("cos.claude")

# Timeouts per model
MODEL_TIMEOUTS = {
    "haiku": 45,
    "sonnet": 120,
}

# Project root for CWD
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ClaudeCallInfo:
    """Info about the last Claude call, for /debug."""
    recipe: str = ""
    model: str = ""
    elapsed: float = 0.0
    success: bool = False
    error: str = ""
    prompt_len: int = 0
    response_len: int = 0
    timestamp: float = 0.0

    def summary(self) -> str:
        status = "OK" if self.success else f"FAIL: {self.error}"
        return (
            f"Recipe: {self.recipe}\n"
            f"Model: {self.model}\n"
            f"Elapsed: {self.elapsed:.1f}s\n"
            f"Status: {status}\n"
            f"Prompt: {self.prompt_len} chars\n"
            f"Response: {self.response_len} chars"
        )


# Global last call info for /debug
last_call_info = ClaudeCallInfo()


async def call_claude(
    prompt: str,
    model: str = "sonnet",
    timeout: int | None = None,
    mcp_config: str | None = None,
    recipe: str = "unknown",
) -> str | None:
    """Call Claude via CLI subscription. Async -- does not block the bot.

    Args:
        prompt: The prompt text to send.
        model: 'haiku' or 'sonnet'.
        timeout: Max seconds to wait (default: per-model timeout).
        mcp_config: Path to MCP config JSON, or None to skip.
        recipe: Name of the recipe (for logging/debug).

    Returns:
        Claude's text response, or None on timeout/error.
    """
    global last_call_info

    if timeout is None:
        timeout = MODEL_TIMEOUTS.get(model, 120)

    cmd = [
        "claude", "--print",
        "--dangerously-skip-permissions",
        "-p", prompt,
        "--model", model,
    ]
    if mcp_config:
        cmd.extend(["--mcp-config", mcp_config])

    info = ClaudeCallInfo(
        recipe=recipe,
        model=model,
        prompt_len=len(prompt),
        timestamp=time.time(),
    )

    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        elapsed = time.monotonic() - start
        info.elapsed = elapsed

        stdout_text = stdout.decode().strip()
        stderr_text = stderr.decode().strip() if stderr else ""

        # --print mode: stdout IS the response (plain text)
        if stdout_text:
            info.success = True
            info.response_len = len(stdout_text)
            last_call_info = info
            logger.info(f"[claude] recipe={recipe} model={model} elapsed={elapsed:.1f}s len={len(stdout_text)}")
            return stdout_text

        info.error = stderr_text[:200] if stderr_text else "empty output"
        last_call_info = info
        logger.warning(f"[claude] empty output, model={model}, stderr={info.error}")
        return None

    except asyncio.TimeoutError:
        elapsed = time.monotonic() - start
        info.elapsed = elapsed
        info.error = f"timeout after {timeout}s"
        last_call_info = info
        logger.warning(f"[claude] timeout after {timeout}s, model={model}, recipe={recipe}")
        # Kill the process if still running
        try:
            proc.kill()
        except Exception:
            pass
        return None
    except Exception as e:
        elapsed = time.monotonic() - start
        info.elapsed = elapsed
        info.error = str(e)
        last_call_info = info
        logger.error(f"[claude] error: {e}, model={model}, recipe={recipe}")
        return None


async def call_claude_safe(
    prompt: str,
    model: str = "sonnet",
    timeout: int | None = None,
    recipe: str = "unknown",
) -> str | None:
    """Wrapper with 1 retry on failure. No MCP for Phase 1."""
    for attempt in range(2):
        result = await call_claude(prompt, model=model, timeout=timeout, recipe=recipe)
        if result is not None:
            return result
        if attempt == 0:
            logger.warning(f"[claude] retry 1, recipe={recipe}")
    return None
