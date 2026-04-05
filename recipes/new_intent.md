# New Intent Recipe

User wants a new goal / intent. You must research, plan, and decompose it.

## Process

### STEP 1: ASSESS
Look at user_model:
- What relevant skills/experience does the user already have?
- How does this fit with current intents and strategy?
- What hard constraints apply (runway, time, priorities)?

### STEP 2: RESEARCH
Using your knowledge:
- What are the best proven methods for this goal in 2026?
- What's a realistic timeline given the user's context?
- What pitfalls do beginners hit?

### STEP 3: DECOMPOSE
Create 3-5 measurable goals:
- Each goal has a clear metric (number, percentage, pass/fail)
- Goals are sequential where possible (build on each other)
- First goal should be achievable in 2-4 weeks (quick win)

### STEP 4: METHOD
Daily plan:
- Specific activities with time limits
- When in the day (after P1 tasks, morning, evening)
- Constraints: what NOT to do (avoid common traps)
- Tools/resources to use

## Output Format

Reply with ONLY a JSON object. No markdown fences, no explanation before or after. Just the JSON.

```json
{
  "intent_id": "slug-form-id",
  "title": "Краткое название цели",
  "priority": "P2 or P3 (P1 only if truly critical)",
  "deadline": "YYYY-MM-DD (realistic)",
  "success": "Одно предложение: как понять что цель достигнута",
  "methodology": "Метод: ...\nЕжедневно: ...\nКогда: ...\nОграничения: ...",
  "goals": [
    {"id": "slug-id", "title": "Название", "progress": "0/N"},
    {"id": "slug-id-2", "title": "Название 2", "progress": "0%"}
  ],
  "reasoning": "Почему именно этот план, учитывая контекст юзера. 2-3 предложения."
}
```

## Rules
- Language: Russian (except JSON keys and id slugs)
- Be realistic about timelines — user has a full schedule with P1 tasks
- Priority is P2 or P3 unless user explicitly says this is urgent
- Daily time commitment: 15-30 min max (user is busy)
- Always reference user_model data in reasoning
- IDs: lowercase, hyphen-separated, descriptive (e.g. "vocab-500", "hangul-reading")
- intent_id: derived from the goal topic (e.g. "learn-korean", "start-saas")
