"""Goal Mode worker dispatch and reply parsing.

The dispatcher owns the broker-client call and the typed reply/blocker parsing
that the goal runner used to inline. GoalRunner composes this module instead of
reaching into ``orchlink.client`` or hand-parsing wire dicts.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from orchlink.client import ask_worker_sync as _default_ask_worker_sync
from orchlink.goal.checks import CheckResult
from orchlink.goal.models import Goal
from orchlink.goal.worker_reply import compact_worker_result, parse_worker_reply


AskFn = Callable[..., dict[str, Any]]


class GoalDispatcher:
    """Send a goal task to a worker and surface typed replies.

    ``ask_fn`` is injectable so tests can monkeypatch
    ``orchlink.goal.runner.ask_worker_sync`` and have the dispatch pick up the
    patched function (the runner forwards its module-level ``ask_worker_sync``
    reference at construction time).
    """

    def __init__(self, config: dict[str, Any], *, ask_fn: AskFn | None = None) -> None:
        self._config = config
        self._ask_fn = ask_fn or _default_ask_worker_sync

    def dispatch(
        self,
        *,
        worker: str,
        task_id: str,
        prompt: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        """Send the prompt to the worker and return the raw broker wire dict."""
        return self._ask_fn(
            config=self._config,
            worker=worker,
            task_id=task_id,
            message=prompt,
            timeout_seconds=timeout_seconds,
            wait=True,
        )


def reply_kind(result: dict[str, Any]) -> str:
    """Return the upper-case worker reply kind (e.g. ``RESULT``, ``BLOCKER``)."""
    return parse_worker_reply(result).kind.value


def parse_blocker(
    result: dict[str, Any],
    *,
    task_id: str,
    criterion_id: str | None = None,
) -> dict[str, Any]:
    """Extract a blocker dict from a BLOCKER reply, falling back to ambiguity."""
    parsed = parse_worker_reply(result, task_id=task_id, criterion_id=criterion_id)
    if parsed.blocker is None:
        return {"type": "ambiguity", "task_id": task_id, "criterion_id": criterion_id, "message": parsed.summary}
    return parsed.blocker.to_dict()


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    """Compact a worker result into the shape stored on the goal history."""
    return compact_worker_result(result)


def result_summary(result: dict[str, Any]) -> str:
    """Short summary text from a worker reply (truncated to 4000 chars)."""
    return parse_worker_reply(result).summary[:4000]


def format_failed_checks(failed: list[CheckResult]) -> str:
    """Render a human-readable summary of failing objective checks."""
    lines: list[str] = []
    for item in failed:
        lines.append(f"Check failed: {item.command} (exit {item.exit_code})")
        if item.stdout.strip():
            lines.append(item.stdout[-1000:])
        if item.stderr.strip():
            lines.append(item.stderr[-1000:])
    return "\n".join(lines)


def parse_derivation_reply(result: dict[str, Any], goal: Goal) -> tuple[str, str, str | None]:
    """Extract acceptance, plan, and optional coverage markdown from a derivation reply."""
    reply = result.get("reply") or {}
    payload = reply.get("payload") or {}
    output = str(payload.get("summary") or payload.get("stdout") or "")
    acceptance = str(payload.get("acceptance") or "") or _labeled_fenced_block(output, "acceptance")
    plan = str(payload.get("plan") or "") or _labeled_fenced_block(output, "plan")
    coverage = str(payload.get("coverage") or "") or _labeled_fenced_block(output, "coverage")
    if not acceptance:
        yaml_block = _acceptance_yaml_block(output)
        if yaml_block:
            acceptance = f"# Acceptance criteria for {goal.id}: {goal.title}\n\n```yaml\n{yaml_block.rstrip()}\n```"
    if not acceptance:
        acceptance = output.strip() or f"# Acceptance criteria for {goal.id}: {goal.title}\n"
    if not plan:
        plan = f"# Plan for {goal.id}: {goal.title}\n\nReview derived acceptance criteria and fill in the execution plan.\n"
    return acceptance.rstrip() + "\n", plan.rstrip() + "\n", (coverage.rstrip() + "\n" if coverage else None)


def _labeled_fenced_block(text: str, label: str) -> str:
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        if re.match(rf"^\s*```{label}\s*$", line, flags=re.IGNORECASE):
            start = index + 1
            break
    if start is None:
        for match in re.finditer(rf"^\s*```{label}\s*$", text, flags=re.IGNORECASE | re.MULTILINE):
            return _labeled_fenced_block(text[match.start() :], label)
        return ""

    end = len(lines)
    next_label = re.compile(r"^\s*```(?:acceptance|plan|coverage)\s*$", flags=re.IGNORECASE)
    for index in range(start, len(lines)):
        if next_label.match(lines[index]):
            end = index
            break
    section = lines[start:end]
    while section and not section[-1].strip():
        section.pop()
    if section and section[-1].strip() == "```":
        section.pop()
    while section and not section[-1].strip():
        section.pop()
    return "\n".join(section).strip()


def _acceptance_yaml_block(text: str) -> str:
    for match in re.finditer(r"```(?:yaml|yml)\s*\n(.*?)\n```", text, flags=re.IGNORECASE | re.DOTALL):
        block = match.group(1)
        if "acceptance:" in block or "acceptance_criteria:" in block or "criteria:" in block or "acs:" in block:
            return block.strip()
    return ""


__all__ = [
    "AskFn",
    "GoalDispatcher",
    "compact_result",
    "format_failed_checks",
    "parse_blocker",
    "parse_derivation_reply",
    "reply_kind",
    "result_summary",
]