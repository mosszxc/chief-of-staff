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
bot/context.py       — dual-project Grimoire routing (rubick + cos)
handlers/messages    — goal_change → challenge → redirect to Claude Code
```

### Acceptance Criteria

**Must:**
1. Claude Code видит те же данные (MCP tools: today, goal_update)
2. New Intent Workflow: ASSESS→RESEARCH→DECOMPOSE→METHOD→SAVE в Claude Code
3. Research → pre-process → ingest в Grimoire (cos) с metadata + TTL
4. Dual-project routing: доменные вопросы → rubick (маркетинг) или cos (карьера), бот выбирает сам

**Nice-to-have:**
5. Intent iteration ("переделай метод") работает в Claude Code
6. Strategy pivot с challenge ("дропаю цель" → данные из стратегии)
7. Re-research trigger при истёкшем TTL
8. Fallback: если один проект пустой → попробовать второй

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

## Phase 4: New Intent Pipeline (неделя 4)

**Outcome:** "хочу выучить X" → 3-шаговый pipeline с checkpoint'ами, не монолит.

### Почему pipeline, не один вызов

```
Монолит: 5-10 мин ожидания → fake progress → ошибка в ASSESS = всё в мусор
Pipeline: 3 шага по 10-120 сек → реальный контент → ошибка ловится рано
```

### Что делаем

```
bot/context.py       — check_knowledge_coverage() + knowledge reuse engine
bot/handlers/messages — pipeline FSM (3 шага с checkpoint'ами)
recipes/assess.md    — промпт для ASSESS (определить домены)
recipes/research.md  — промпт для RESEARCH (только пробелы)
recipes/decompose.md — промпт для DECOMPOSE+METHOD
```

```
Pipeline в Telegram (4 шага с checkpoint'ами):

STEP 0: CLARIFY (5-10 сек, sonnet)
  Claude генерирует 2-4 уточняющих вопроса под тип цели (нельзя захардкодить)
  Юзер отвечает → контекст передаётся в ASSESS
  → Без уточнений ASSESS работает generic ("юридика" vs "регистрация ИП в РФ")

STEP 1: ASSESS + KNOWLEDGE CHECK (10-20 сек, sonnet + Grimoire HTTP)
  Часть A: Claude определяет ОБЛАСТИ ЗНАНИЙ с учётом уточнений
    Не "юридика" а "регистрация ИП/самозанятость в РФ"
  Часть B: check_knowledge_coverage() → Grimoire semantic search + LLM relevance validation
  Часть C: Показать юзеру ✅/❌/⚠️ + "Пропустил что-то? [👍 Ок] [💬 Добавить]"
  → Юзер может добавить/убрать области → checkpoint перед ресёрчем

STEP 2: RESEARCH (60-120 сек, sonnet + web) — ТОЛЬКО пробелы
  → Ресёрчит ТОЛЬКО домены с ❌ и ⚠️
  → Existing knowledge (✅) подтягивается из Grimoire без ресёрча
  → Новые знания → ingest в cos
  → "Нашёл 3 подхода: A, B, C. Рекомендую B."
  → [👍 Подход B] [💬 Другой]

STEP 3: DECOMPOSE + METHOD (30 сек, sonnet)
  → existing (Grimoire) + new research → один план
  → [👍 Принять] [💬 Переделать]
  Переделать → ТОЛЬКО шаг 3
  Принять → сохранить + сразу новый план дня
```

### Knowledge Reuse Engine

```
Grimoire check = HTTP, 0 токенов, <100ms
Web search + Claude = тысячи токенов, 60+ сек

1-й intent:  5 доменов × 0 в базе = 5 ресёрчей
5-й intent:  5 доменов × 4 в базе = 1 ресёрч (экономия 80%)

Знания НАКАПЛИВАЮТСЯ → каждый intent быстрее и дешевле.
```

### Acceptance Criteria

**Must:**
1. ASSESS показывает домены + coverage (✅/❌/⚠️) → юзер подтверждает
2. RESEARCH ресёрчит ТОЛЬКО пробелы, existing берёт из Grimoire
3. DECOMPOSE собирает всё → план → "Принять" сохраняет + новый план сразу
4. "Переделать" на шаге 3 НЕ повторяет research

**Nice-to-have:**
5. Research results → auto-ingest в Grimoire (cos) с TTL
6. Strategy change ("дропаю цель") → challenge pipeline

### Definition of Done
```
"Хочу выучить корейский" → 3 шага в Telegram → intent создан → завтра в плане.
Каждый шаг: юзер видит реальный контент и может поправить.
```

---

## Phase 5: Daily Use + Polish (неделя 5+)

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
Через 19д:  Phase 4 (new intent pipeline)     ← Claude Code делает
Через 24д:  Phase 5 (daily use + polish)     ← ты используешь, Claude Code чинит
```
