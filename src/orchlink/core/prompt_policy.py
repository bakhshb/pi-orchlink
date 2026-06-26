from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class TaskPromptPolicy:
    """Central policy for worker task prompts and reply expectations."""

    default_mode: str = "PLAN"
    valid_task_modes: frozenset[str] = field(default_factory=lambda: frozenset({"DISCUSS", "PLAN", "DO", "REVIEW"}))

    def default_expected_reply(self) -> list[str]:
        """Return the default worker reply schema.

        Empty means the worker should answer naturally unless the lead requested a
        shape in the task prompt.
        """
        return []

    def infer_mode(self, message: str, default: str | None = None) -> str:
        explicit = re.search(r"(?im)^\s*MODE\s*:\s*(DISCUSS|PLAN|DO|REVIEW)\b", message)
        if explicit:
            return explicit.group(1).upper()

        lowered = message.lower()
        if re.search(r"\b(review|reviewing|reviewed|audit)\b", lowered) or re.search(
            r"\binspect\b.*\b(changes?|diff|patch|pr)\b", lowered
        ):
            return "REVIEW"
        if re.search(r"\b(implement|add|fix|update|edit|change|remove|write|create)\b", lowered):
            return "DO"
        if re.search(r"\b(discuss|compare|decide|recommend|tradeoff|trade-off|opinion)\b", lowered):
            return "DISCUSS"
        if re.search(r"\b(plan|propose|approach)\b", lowered):
            return "PLAN"
        return (default or self.default_mode).upper()

    def normalize_mode(self, mode: str | None, message: str) -> str:
        selected = (mode or self.infer_mode(message)).upper()
        return selected if selected in self.valid_task_modes else self.default_mode

    def worker_task_guidance(self) -> str:
        return (
            "Use your judgment for whether this needs discussion, planning, review, or implementation. "
            "Stay inside the requested scope, do not edit forbidden files, ask a blocker question if the request "
            "is unsafe, unclear, or too broad, and reply in the shape the lead asked for. If no shape is requested, "
            "answer naturally and concisely."
        )

    def lead_task_prompt_guidance_markdown(self) -> str:
        return """Write worker prompts in natural language. Do not force `MODE:`/`TASK_ID:` blocks or a universal checklist. The `-t` CLI option already carries the task ID; the worker can infer whether you need discussion, planning, review, or implementation from your request.

Usually include only what helps the worker act safely:

- current context
- exact allowed files, paths, or behavior
- forbidden files, paths, or behavior
- whether edits are allowed
- tests or checks the worker may run

Optional:

- desired reply shape, only when you care about the format
- whether you will wait or work on a different scope, when it affects coordination

Short, obvious tasks can be short. Risky, broad, review, or implementation tasks should include enough scope to prevent accidental edits. Do not ask the worker to scan the whole repository unless necessary."""

    def lead_reply_guidance_markdown(self) -> str:
        return (
            "Do not require `TYPE:` labels or a fixed result schema unless you truly need them. "
            "If you do not request a shape, accept a concise answer that fits the task."
        )

    def worker_task_behavior_markdown(self) -> str:
        return """Use judgment from the lead's wording:

- Discuss or recommend when asked for tradeoffs or a decision.
- Plan when asked for an approach; do not edit unless implementation is clearly allowed.
- Review when asked to inspect; report findings and do not edit unless explicitly allowed.
- Implement only when the lead clearly allows edits and gives a safe scope."""

    def worker_reply_guidance_markdown(self) -> str:
        return (
            "Follow the lead's requested reply shape. If the lead requests no shape, reply concisely in the shape "
            "that best fits the work. Do not invent `TYPE:` labels or a fixed summary/changed/tests template unless "
            "the lead asked for it."
        )

    def talk_constraints(self) -> list[str]:
        return [
            "Reply conversationally.",
            "Do not edit files.",
            "Challenge weak assumptions.",
            "Recommend a practical decision.",
            "Do not read every file for a vague repo-opinion question; use high-signal files or ask before a broad scan.",
        ]

    def render_markdown_template(self, template: str) -> str:
        replacements = {
            "{{LEAD_TASK_PROMPT_GUIDANCE}}": self.lead_task_prompt_guidance_markdown(),
            "{{LEAD_REPLY_GUIDANCE}}": self.lead_reply_guidance_markdown(),
            "{{WORKER_TASK_BEHAVIOR}}": self.worker_task_behavior_markdown(),
            "{{WORKER_REPLY_GUIDANCE}}": self.worker_reply_guidance_markdown(),
        }
        rendered = template
        for marker, value in replacements.items():
            rendered = rendered.replace(marker, value)
        return rendered


@dataclass(frozen=True)
class ResultPromptPolicy:
    """Central policy for how delivered worker results steer the lead."""

    def lead_reconcile_guidance(self) -> str:
        return (
            "Stop any unrelated work now and reconcile this with your current state before calling more tools. "
            "If it changes the plan, state what changed. If it leaves open questions, ask a follow-up. "
            "If it confirms the plan, continue with the agreed workload split."
        )
