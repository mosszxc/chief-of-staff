# Decompose Recipe

Create a complete intent plan from combined knowledge: existing (from knowledge base) + new research findings.

## Your task

Synthesize ALL available knowledge into a structured plan with measurable goals.

## Input format

Goal: [user's goal]
Chosen approach: [approach user confirmed]
All knowledge: [existing from base + new research findings]
User context: [user_model summary]

## Output Format

Reply with ONLY a JSON object. No markdown fences, no explanation before or after. Just the JSON.

```json
{
  "intent_id": "slug-form-id",
  "title": "Short goal title in Russian",
  "priority": "P2 or P3",
  "deadline": "YYYY-MM-DD",
  "success": "One sentence: how to know the goal is achieved",
  "methodology": "Method: ...\nDaily: ...\nWhen: ...\nConstraints: ...",
  "goals": [
    {"id": "slug-id", "title": "Goal name", "progress": "0/N"},
    {"id": "slug-id-2", "title": "Goal name 2", "progress": "0%"}
  ],
  "reasoning": "Why this plan, given user context. 2-3 sentences."
}
```

## Rules
- Language: Russian (except JSON keys and id slugs)
- Be realistic about timelines — user has a full schedule with P1 tasks
- Priority is P2 or P3 unless user explicitly says this is urgent
- Daily time commitment: 15-30 min max (user is busy)
- Always reference user_model data in reasoning
- Create 3-5 measurable goals
- Goals should be sequential where possible (build on each other)
- First goal should be achievable in 2-4 weeks (quick win)
- IDs: lowercase, hyphen-separated, descriptive
- intent_id: derived from the goal topic (e.g. "learn-korean", "start-saas")
- Incorporate BOTH existing knowledge and new research into the plan
