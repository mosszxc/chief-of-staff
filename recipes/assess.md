# Assess Recipe

User wants to start something new. Determine what KNOWLEDGE AREAS are needed to plan this goal well.

## Your task

Given the user's goal and their profile, list 3-7 knowledge areas needed.

Think about:
- What domains of expertise are relevant?
- What practical knowledge is needed?
- Consider user's existing skills — don't list areas they already master
- Consider user's context (constraints, current goals)

## Output Format

Reply with ONLY a JSON array of strings. No markdown fences, no explanation. Just the JSON array.

Example for "хочу открыть бизнес":
["продукт/MVP", "финансы/unit economics", "маркетинг", "юридика", "продажи/первые клиенты"]

Example for "хочу выучить корейский":
["грамматика/хангыль", "лексика/словарный запас", "аудирование", "разговорная практика", "культурный контекст"]

## Rules
- Language: Russian for area names
- 3-7 areas, no more
- Each area = 1-3 words, descriptive
- Be specific to the goal, not generic
- Consider what the user already knows (from user_model) — skip those areas
- Areas should map to searchable knowledge domains
