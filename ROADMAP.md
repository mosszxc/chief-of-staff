# Roadmap — Chief of Staff

---

## Phase 0: Prerequisites (до первой строки кода)

### 0.1 Telegram Bot Setup
```
1. @BotFather → /newbot → "Chief of Staff" → получить TELEGRAM_BOT_TOKEN
2. Написать боту любое сообщение → получить chat_id
3. Записать в .env
```
**Done:** `.env` заполнен, бот отвечает на /start эхом.

### 0.2 Smoke Test: Claude CLI subprocess
```bash
python3 -c "
import asyncio, subprocess
result = subprocess.run(
    ['claude', '--print', '-p', 'Скажи ок', '--model', 'haiku'],
    capture_output=True, text=True, timeout=45
)
print(result.stdout[:200])
print('EXIT:', result.returncode)
"
```
**Done:** stdout содержит ответ Claude, exit code = 0.

### 0.3 Data Files
Заполнить из `~/Ким_Михаил_Карьерная_Стратегия_v2.md`:
```
data/strategy.yaml    — horizons, hard_constraints, anti_patterns, blocking_chain
data/intents.yaml     — 4-5 intents из h1 с goals
data/goals.yaml       — goals с текущим прогрессом
data/user_model.yaml  — identity, knowledge_stack, preferences, narrative
```
**Done:** все 4 файла существуют, PyYAML их парсит без ошибок.

### 0.4 Project Skeleton
```bash
cd ~/chief-of-staff
pip install -e .
```
**Done:** `python -m bot.main` запускается (import работает).

---

## Phase 1: Утренний план + кнопки (3-5 дней)

**Outcome:** каждое утро получаю полезный план в Telegram, отмечаю задачи кнопками.

### Что делаем
```
bot/main.py         — aiogram polling + APScheduler
bot/claude.py       — call_claude() async subprocess wrapper
bot/render.py       — JSON plan → Telegram message с кнопками
scheduler/morning   — cron 08:00 → daily_plan → send
handlers/callbacks  — кнопки ✅/📝/⏭ → обновить YAML
handlers/commands   — /start (onboarding), /today, /debug
```

### Acceptance Criteria

**Must:**
1. Утренний план приходит и он **полезный** (не generic список, а reasoning с контекстом)
2. Кнопки ✅/📝/⏭ работают — нажал, YAML обновился, прогресс виден
3. /start → onboarding заполняет data/ (файл или вопросы)

**Nice-to-have:**
4. /debug показывает последний вызов claude
5. Fallback при timeout Claude CLI

### Definition of Done
```
Утром пришёл план → нажал кнопки → YAML обновился.
Можно пользоваться каждый день.
```

### Checkpoint
```
Прежде чем Phase 2 — ответить:
- План утром реально помогает или generic?
- Формат удобный или надо менять?
- Claude CLI стабилен или постоянные таймауты?
- Что изменилось в понимании задачи?
```

---

## Phase 2: Вечер + свободный чат + Grimoire (неделя 2)

**Outcome:** полный дневной цикл (утро → день → вечер), могу писать боту свободным текстом.

### Что делаем
```
scheduler/evening    — cron 22:00 → evening_summary
scheduler/drift      — cron 15:00 → drift_check (push если дрейф)
bot/router.py        — двухуровневый: Python (кнопки) + Haiku (текст)
bot/context.py       — context engine: recipe → собранный промпт
recipes/*.md         — промпты для каждого recipe
templates/system_prompt.md — Jinja2 шаблон
```

### Acceptance Criteria

**Must:**
1. Вечерний итог приходит, энергия 1-5 записывается
2. Свободный текст ("сделал portfolio") → бот понимает, обновляет goal
3. Drift alert работает в рабочие дни, НЕ работает в выходные

**Nice-to-have:**
4. Grimoire (cos) отвечает на доменные вопросы
5. "Typing..." и прогрессивные статусы при ожидании

### Definition of Done
```
Полный цикл: утро (план) → день (кнопки + текст) → вечер (итог) → drift если нужен.
Свободный чат работает с контекстом.
```

### Checkpoint
```
Прежде чем Phase 3 — ответить:
- Вечерний check-in заполняю или игнорирую?
- Drift alert помогает или раздражает?
- Какие типы сообщений пишу чаще всего? Роутер справляется?
- Grimoire нужен или хватает без него?
```

---

## Phase 3: New Intent + Claude Code sync + patterns (неделя 3)

**Outcome:** могу создавать новые цели через workflow, Claude Code и Telegram видят одни данные.

### Что делаем
```
mcp/cos_server.py    — MCP server (7 tools) для Claude Code
cos-mcp.json         — MCP config
bot/patterns.py      — task_avoidance, blocking_stale, streak detection
handlers/messages    — goal_change → challenge → redirect to Claude Code
```

### Acceptance Criteria

**Must:**
1. Claude Code видит те же данные (MCP tools: today, goal_update)
2. New Intent Workflow: ASSESS→RESEARCH→DECOMPOSE→METHOD→SAVE в Claude Code
3. Research → pre-process → ingest в Grimoire (cos) с metadata + TTL

**Nice-to-have:**
4. Intent iteration ("переделай метод") работает в Claude Code
5. Strategy pivot с challenge ("дропаю цель" → данные из стратегии)
6. Re-research trigger при истёкшем TTL

### Definition of Done
```
Два интерфейса видят одни данные. Новые цели через workflow.
Research сохраняется в Grimoire и переиспользуется.
```

### Checkpoint
```
Прежде чем Phase 4 — ответить:
- Workflow создания целей реально использую?
- MCP sync нужен или хватает Telegram?
- Паттерны (avoidance 3x) помогают?
- Что убрать / что добавить?
```

---

## Phase 4: Daily Use + Polish (неделя 4+)

**Outcome:** система работает каждый день, улучшается по ходу использования.

Не планировать заранее. Использовать → фиксить что мешает.

```
Возможные направления:
  - Prompt tuning (план generic → конкретнее)
  - Evening timing (22:00 → 20:00?)
  - Новые recipes по мере надобности
  - Grimoire ingestion pipeline тюнинг
  - Compact mode / detailed mode toggle
```

---

## Timeline

```
Сейчас:     Phase 0 (prerequisites)         ← 30 мин руками
Потом:      Phase 1 (план + кнопки)          ← Claude Code делает
Через 5д:   Phase 2 (вечер + чат + Grimoire) ← Claude Code делает
Через 12д:  Phase 3 (intents + sync)         ← Claude Code делает
Через 19д:  Phase 4 (daily use + polish)     ← ты используешь, Claude Code чинит
```
