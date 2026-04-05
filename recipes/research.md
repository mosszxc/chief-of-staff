# Research Recipe

Research specific knowledge gaps for a new goal. You are given ONLY the areas that need research — areas already covered in the knowledge base are provided as context but do NOT need research.

## Your task

For each gap area, provide:
1. Best proven approaches/methods in 2026
2. Realistic timelines given user context
3. Common pitfalls for beginners
4. Specific actionable recommendations

Then synthesize into ONE recommended approach.

## Input format

Goal: [user's goal]
Areas to research (gaps): [list of areas with no existing knowledge]
Existing knowledge (from base): [summaries of what we already know]
User context: [user_model summary]

## Output Format

Reply with ONLY a JSON object. No markdown fences, no explanation.

```json
{
  "findings": {
    "area_name": {
      "summary": "2-3 sentence summary of findings",
      "approaches": ["approach A", "approach B"],
      "recommended": "which approach and why",
      "pitfalls": ["pitfall 1", "pitfall 2"],
      "timeline": "realistic timeline estimate"
    }
  },
  "recommendation": "Overall recommended approach in 2-3 sentences, considering ALL areas together",
  "approach_options": [
    {"id": "A", "label": "Short label for approach A", "description": "1 sentence"},
    {"id": "B", "label": "Short label for approach B", "description": "1 sentence"}
  ]
}
```

## Rules
- Language: Russian (except JSON keys)
- Be specific and actionable, not generic
- Reference user's context (time constraints, existing skills)
- 2-3 approach options maximum
- Each approach must be realistically executable given user's schedule
- DO NOT research areas marked as "existing" — they are context only
