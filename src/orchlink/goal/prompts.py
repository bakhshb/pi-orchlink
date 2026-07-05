"""Goal Mode prompt construction.

Goal workers receive prompts built from the goal's stored artifacts
(``source.md``, ``acceptance.md``, ``plan.md``, ``coverage.md``) and the
selected acceptance criterion. Prompt construction lives here so the runner
and CLI can compose prompts without owning the file-reading boilerplate.
"""

from __future__ import annotations

from pathlib import Path

from orchlink.goal.models import Goal
from orchlink.goal.store import GoalStore


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


def worker_prompt(
    store: GoalStore,
    goal_id: str,
    *,
    selected_criterion_text: str = "",
    previous_summary: str = "",
) -> str:
    """Prompt for the per-step worker task that implements the next core AC slice.

    ``selected_criterion_text`` is the pre-formatted "<id> <text>" string for
    the next acceptance criterion to focus on (empty when none is selected).
    """
    goal = store.load(goal_id)
    goal_dir = store.goal_dir(goal_id)
    source = _read(goal_dir / "source.md")
    acceptance = _read(goal_dir / "acceptance.md")
    plan = _read(goal_dir / "plan.md")
    selected_block = f"\nSelected acceptance criterion for this slice: {selected_criterion_text}\n" if selected_criterion_text else ""
    previous = f"\nPrevious failed checks/gaps:\n{previous_summary}\n" if previous_summary else ""
    return f"""Implement the next useful slice for goal {goal.id}: {goal.title}.

You are the maker. Do not claim the whole goal is done. The goal runner will verify checks and decide completion.

Source:
{source}

Acceptance criteria:
{acceptance}

Plan:
{plan}
{selected_block}{previous}
Scope rules:
- Make the smallest useful change for unverified acceptance criteria.
- If you hit a non-core blocker, report it and continue any unblocked core work.
- If a core blocker prevents all useful work, report the exact blocker question.
- Reply with files changed, checks run, risks, and blockers.
""".strip()


def audit_prompt(store: GoalStore, goal_id: str) -> str:
    """Prompt for the audit worker task that reviews a goal without mutating it."""
    goal = store.load(goal_id)
    goal_dir = store.goal_dir(goal_id)
    source = _read(goal_dir / "source.md")
    acceptance = _read(goal_dir / "acceptance.md")
    plan = _read(goal_dir / "plan.md")
    coverage = _read(goal_dir / "coverage.md")
    return f"""Audit goal {goal.id}: {goal.title} against its source, acceptance criteria, plan, coverage, and recorded evidence.

Do not edit files. Do not mark the goal done. Return gaps, risks, missing evidence, uncovered requirements, and whether the lead should proceed.

Source:
{source}

Acceptance criteria:
{acceptance}

Plan:
{plan}

Coverage:
{coverage}
""".strip()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


__all__ = [
    "audit_prompt",
    "derivation_prompt",
    "worker_prompt",
]