from __future__ import annotations

from orchlink.goal.models import Goal


def derivation_prompt(goal: Goal, source_text: str) -> str:
    if goal.source == "plan":
        source_guidance = (
            "This source is an implementation plan. Extract outcome-based acceptance criteria that prove the plan achieved "
            "its intended result. Do not treat plan steps as completion, and do not treat attempts or task completion as acceptance criteria."
        )
    else:
        source_guidance = "This source is a PRD or product goal. Extract product or behavior completion criteria."
    return f"""Derive acceptance criteria and an implementation plan for goal {goal.id}: {goal.title}.

{source_guidance} Return exactly three fenced blocks:

```acceptance
# Acceptance criteria for {goal.id}: {goal.title}

```yaml
acceptance:
- id: AC-1
  text: "..."
  type: objective
  priority: core
  depends_on: []
  check: ""
  source: "source.md"
  confidence: medium
  status: pending
  blocker: null
```
```

```plan
# Plan for {goal.id}: {goal.title}

- Step 1...
```

```coverage
# Coverage

- AC-1 covers source requirement: ...
- Uncovered: none
- Low confidence: none
- Invented: none
- Plan gaps: none
```

Rules:
- Use stable AC IDs.
- Mark subjective criteria as type: subjective.
- Mark optional/polish/docs as priority: noncore when appropriate.
- Include check commands only when you are confident they are correct.
- In coverage, list uncovered source requirements, low-confidence ACs, invented ACs, and plan-to-AC gaps.
- Do not implement. Do not mark the goal done.

Source:
{source_text}
""".strip()

