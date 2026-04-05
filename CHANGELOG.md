# Changelog

## 2026-04-05 — Phase 0 + Phase 1

### Phase 0: Skeleton
- Project structure created (bot/, mcp/, recipes/, templates/, data/)
- Data files extracted from career strategy into YAML
- Smoke test: Claude CLI subprocess works (haiku ~4.6s, sonnet ~27s)

### Phase 1: Morning plan + buttons
- aiogram bot with APScheduler (08:00 morning, 22:00 evening)
- Claude CLI async subprocess (asyncio, not blocking)
- Daily plan generation via Sonnet with context from YAML
- Inline buttons: ✅ 📝 ⏭ per task (numbered)
- /start onboarding, /today, /status, /debug
- YAML state with asyncio.Lock per file
- Evening summary with energy 1-5

### UX fixes
- Compact plan format (blocker one-liner, no inline context hints)
- Buttons labeled with task number (1 ✅, 2 ✅...) to avoid confusion
- Full reasoning hidden behind [💡 Почему?] button
