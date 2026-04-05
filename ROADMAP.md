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
# Проверить что claude --print работает из Python
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
# Создать структуру из SPEC.md Section 10
# pyproject.toml, .env.example, bot/, mcp/, recipes/, templates/, data/
pip install -e .
```
**Done:** `python -m bot.main` запускается (пусть пока падает — главное import работает).

---

## Phase 1: Утренний план + кнопки (3-5 дней)

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

| # | Критерий | Как проверить |
|---|---------|---------------|
| 1 | В 08:00 приходит план с reasoning | Подождать утро или вызвать /today |
| 2 | План содержит 3-5 задач привязанных к intents | Проверить что задачи из goals.yaml |
| 3 | План компактный, reasoning по кнопке [💡] | Нажать кнопку — раскрывается |
| 4 | Кнопка ✅ отмечает задачу done в history | Нажать → проверить YAML |
| 5 | Кнопка ⏭ откладывает задачу, times_skipped++ | Нажать → проверить YAML |
| 6 | Кнопка 📝 спрашивает "что успел?" | Нажать → бот спрашивает → ответить → YAML обновлён |
| 7 | /start → onboarding (файл или 5 вопросов) | Очистить data/ → /start → пройти flow |
| 8 | /today → план на сегодня (если нет — генерирует) | Вызвать до 08:00 |
| 9 | /debug → последний вызов claude (recipe, время) | Вызвать после любого действия |
| 10 | Claude timeout → fallback сообщение | Поставить timeout=1 → проверить |

### Definition of Done
```
Утром пришёл план → нажал кнопки → YAML обновился → /debug показывает что произошло.
Можно пользоваться каждый день.
```

---

## Phase 2: Вечер + свободный чат + Grimoire (неделя 2)

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

| # | Критерий | Как проверить |
|---|---------|---------------|
| 1 | В 22:00 приходит итог дня с кнопками энергии | Подождать вечер |
| 2 | "Сделал portfolio" → бот понимает, обновляет goal | Написать текстом |
| 3 | "Что мы знаем про X?" → ответ из Grimoire (cos) | Спросить после ingestion |
| 4 | "Подготовь к интервью в Y" → recipe с контекстом | Спросить → проверить что есть case stories |
| 5 | "Рекрутер написал: ..." → драфт ответа | Переслать текст рекрутера |
| 6 | Первая строка ответа = индикатор маршрута | Проверить для разных типов |
| 7 | Drift alert НЕ приходит в выходные | Проверить в субботу |
| 8 | Drift alert приходит после 3 рабочих дней без прогресса | Не отмечать 3 дня → проверить |
| 9 | "Typing..." при ожидании Claude | Написать любой текст → видеть индикатор |
| 10 | Долгая операция → "🔍 Исследую тему..." | Спросить что-то требующее web search |

### Definition of Done
```
Полный цикл: утро (план) → день (кнопки + текст) → вечер (итог) → drift если нужен.
Свободный чат работает с контекстом. Grimoire отвечает на доменные вопросы.
```

---

## Phase 3: New Intent + Claude Code sync + patterns (неделя 3)

### Что делаем
```
mcp/cos_server.py    — MCP server (7 tools) для Claude Code
cos-mcp.json         — MCP config
bot/patterns.py      — task_avoidance, blocking_stale, streak detection
handlers/messages    — goal_change → challenge → redirect to Claude Code
```

### Acceptance Criteria

| # | Критерий | Как проверить |
|---|---------|---------------|
| 1 | Claude Code: `today()` показывает план | Открыть CC → вызвать tool |
| 2 | Claude Code: `goal_update()` обновляет прогресс | Обновить → проверить в Telegram |
| 3 | "Хочу выучить X" → бот: "Давай в Claude Code" | Написать в Telegram |
| 4 | В Claude Code: ASSESS→RESEARCH→DECOMPOSE→METHOD→SAVE | Пройти workflow для новой цели |
| 5 | Новый intent появился в утреннем плане | Создать вечером → проверить утром |
| 6 | Задача отложена 3+ раз → бот предлагает другой формат | Откладывать одну задачу 3 дня |
| 7 | "Может в DA?" → challenge с данными из стратегии | Написать → проверить что не соглашается сразу |
| 8 | Research results → ingest в Grimoire (cos) | Создать intent → проверить что данные в cos |
| 9 | TTL warning если данные устарели | Подождать TTL → спросить ту же тему |
| 10 | asyncio.Lock не даёт race condition на YAML | Нажать 3 кнопки быстро подряд |

### Definition of Done
```
Два интерфейса (Telegram + Claude Code) видят одни данные.
Новые цели создаются через workflow. Паттерны (avoidance, drift) работают.
Знания из research сохраняются в Grimoire и переиспользуются.
```

---

## Phase 4: Polish + Daily Use (неделя 4+)

```
Не планировать заранее. Использовать каждый день → фиксить что мешает.
Возможные улучшения:
  - Prompt tuning (план слишком длинный / короткий / generic)
  - Новые recipes по мере надобности
  - Grimoire ingestion pipeline тюнинг
  - Evening check-in timing (20:00 vs 22:00)
```

---

## Порядок работы

```
Сейчас:     Phase 0 (prerequisites)         ← 30 мин руками
Потом:      Phase 1 (план + кнопки)          ← Claude Code делает
Через 5д:   Phase 2 (вечер + чат + Grimoire) ← Claude Code делает
Через 12д:  Phase 3 (intents + sync)         ← Claude Code делает
Через 19д:  Phase 4 (daily use + polish)     ← ты используешь, Claude Code чинит
```
