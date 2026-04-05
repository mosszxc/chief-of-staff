# CLAUDE.md

## Project

**Chief of Staff** — Telegram-бот, который каждый день говорит что делать. Не Todoist — AI-планировщик с reasoning, контекстом и памятью.

Полная спека: `SPEC.md` — читай ПЕРЕД любой работой.

## Architecture

```
Telegram (aiogram 3.x)
  → Router (claude --model haiku)
  → Context Engine (recipe → собранный промпт)
  → Claude Code CLI (subprocess: claude --print -p "...")
  → MCP Server (cos_server.py — tools для CRUD)
  → YAML state (data/)
  → APScheduler (cron: утро/вечер/drift)
```

LLM вызовы через Claude Code CLI подписку, НЕ через Anthropic API.

## Key Files

```
bot/main.py          — entry point (aiogram + scheduler)
bot/claude.py        — call_claude() subprocess wrapper
bot/router.py        — message → enum (PLAN/COMPLETE/CHAT/...)
bot/context.py       — recipe → assembled prompt
mcp/cos_server.py    — MCP tools (complete_task, postpone_task, ...)
templates/system_prompt.md — Jinja2 system prompt
recipes/*.md         — промпты per recipe type
data/*.yaml          — state (intents, goals, user_model, history)
```

## Commands

```bash
# Run bot
python -m bot.main

# Run MCP server (for Claude Code integration)
python mcp/cos_server.py
```

## Stack

```
Python 3.11+
aiogram 3.x          — Telegram bot framework (async)
APScheduler 3.x      — cron jobs (morning/evening/drift)
PyYAML               — state read/write
httpx                 — Grimoire API calls
Jinja2                — system prompt templating
```

## Conventions

1. **State = YAML files in data/.** Read/write through helper functions, never raw open().
2. **LLM = Claude CLI subprocess.** Always through `bot/claude.py:call_claude()`. Never import anthropic.
3. **MCP tools = only way to modify state from LLM.** Claude CLI calls MCP tools, MCP tools update YAML.
4. **Recipes = .md files in recipes/.** One file per recipe type. Contains instruction for LLM.
5. **System prompt = Jinja2 template.** Filled by `bot/context.py` from YAML data. Not by AI.
6. **Logging** to `data/logs/cos.log`. Log every claude call: recipe, model, elapsed time.
7. **Timezone = Europe/Moscow.** Hardcoded in scheduler config.

## Claude CLI Call Pattern

```python
subprocess.run(
    ["claude", "--print", "-p", prompt,
     "--model", model,
     "--output-format", "stream-json",
     "--mcp-config", "cos-mcp.json"],
    capture_output=True, text=True, timeout=timeout
)
```

Flags:
- `--print` — non-interactive, stdout output
- `--model haiku` for simple (COMPLETE, POSTPONE, STATUS)
- `--model sonnet` for complex (PLAN, CHAT, INTERVIEW, GOAL_CHANGE)
- `--mcp-config cos-mcp.json` — connects MCP tools
- timeout: 30s (haiku), 60s (sonnet)

## Router Enum

```
PLAN → daily_plan recipe (sonnet)
COMPLETE → MCP tool: complete_task (haiku)
POSTPONE → MCP tool: postpone_task (haiku)
ADD_TASK → MCP tool: add_task (haiku)
STATUS → MCP tool: get_progress (haiku)
CHAT → free_chat recipe (sonnet)
DOMAIN → domain_question recipe + Grimoire (sonnet)
INTERVIEW → interview_prep recipe (sonnet)
RECRUITER → recruiter_reply recipe (sonnet)
GOAL_CHANGE → goal_change recipe + challenge (sonnet)
```

## Anti-Patterns

- Do NOT use Anthropic API directly — all LLM through Claude CLI subprocess
- Do NOT store state in memory — always YAML files in data/
- Do NOT send more than 3 push notifications per day
- Do NOT implement auto-actions (HH apply, send emails) — separate project
- Do NOT use --resume sessions — each call is stateless (--print -p)
- Do NOT hardcode prompts in Python — use recipes/*.md and templates/

## Implementation Order

```
Phase 1 (3-5 days): bot/main.py + bot/claude.py + data/*.yaml + morning plan + buttons
Phase 2 (week 2):   bot/router.py + bot/context.py + recipes/ + Grimoire integration
Phase 3 (week 3):   mcp/cos_server.py + drift detection + Claude Code sync
```

Start with Phase 1. Get morning plan working in Telegram first. Everything else — after.
