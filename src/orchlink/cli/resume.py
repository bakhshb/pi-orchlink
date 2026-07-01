"""Plain-text `orch resume` report rendering.

This module owns the rendering of the recovery / status report produced by
``orch resume``. The renderer is intentionally pure — it takes a fully
populated ``ResumeState`` and returns a string — so the layout is testable
without spinning up a broker or hitting HTTP.

Output layout (in order, all plain text, one section per line):

- ``Active task or goal:``     one line, plain text
- ``Lead/work sessions:``      one line per session, plain text
- ``Last broker checkpoint:``  timestamp string
- ``Drifted leases:``          one line per drift; absent when there are none
- ``Recommended next:``        one verb line plus a short reason

The renderer picks one of three states — ``normal``, ``idle``, or
``stale/interrupted`` — and emits a final ``Safe to continue.`` or
``Needs intervention.`` line that an operator can act on without reading
any other Orchlink command.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Literal, Sequence

from orchlink.broker.checkpoint import Checkpoint, DriftedLease


ResumeMode = Literal["normal", "idle", "stale/interrupted"]


@dataclass
class SessionSummary:
    role: str  # "lead" | "work" | "worker" | other
    state: str  # "active" | "released" | "expired" | "unknown"
    detail: str = ""

    def render_line(self) -> str:
        # Plain-text one-liner; no ANSI codes.
        if self.detail:
            return f"{self.role}: {self.state} ({self.detail})"
        return f"{self.role}: {self.state}"


@dataclass
class ActiveTaskSummary:
    task_id: str
    kind: str  # "task" | "talk" | "goal"
    state: str  # e.g. "DELIVERED", "RUNNING", "BLOCKING", "idle"
    title: str = ""

    def render_line(self) -> str:
        if self.title:
            return f"{self.task_id} [{self.kind}, {self.state}]: {self.title}"
        return f"{self.task_id} [{self.kind}, {self.state}]"


@dataclass
class ResumeState:
    """Inputs to ``render_resume_report``. All optional except ``mode``."""

    mode: ResumeMode
    active: list[ActiveTaskSummary] = field(default_factory=list)
    sessions: list[SessionSummary] = field(default_factory=list)
    checkpoint: Checkpoint | None = None
    drifted_leases: list[DriftedLease] = field(default_factory=list)
    recommended_next: str = ""
    recommended_reason: str = ""

    def is_stale(self) -> bool:
        return self.mode == "stale/interrupted"


def _humanize_timestamp(ts: str | None) -> str:
    if not ts:
        return "unknown"
    parsed = _try_parse_iso(ts)
    if parsed is None:
        return ts
    delta = datetime.now(timezone.utc) - parsed
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return f"{ts} (future)"
    if seconds < 60:
        return f"{ts} ({seconds}s ago)"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{ts} ({minutes}m{sec}s ago)"
    hours, min = divmod(minutes, 60)
    if hours < 48:
        return f"{ts} ({hours}h{min}m ago)"
    days, hr = divmod(hours, 24)
    return f"{ts} ({days}d{hr}h ago)"


def _try_parse_iso(value: str) -> datetime | None:
    try:
        # Accept both Z-suffixed and offset-suffixed ISO timestamps.
        cleaned = value.replace("Z", "+00:00") if value.endswith("Z") else value
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None


def _drift_line(drift: DriftedLease) -> str:
    if drift.current_epoch is None:
        return (
            f"- {drift.task_id}: missing_after_restart "
            f"(previous_epoch={drift.previous_epoch}, "
            f"previous_holder={drift.previous_holder})"
        )
    return (
        f"- {drift.task_id}: {drift.reason} "
        f"(previous_epoch={drift.previous_epoch}, "
        f"current_epoch={drift.current_epoch}, "
        f"previous_holder={drift.previous_holder}, "
        f"current_holder={drift.current_holder})"
    )


def _drift_recommendation(drifts: Sequence[DriftedLease]) -> tuple[str, str]:
    """Pick a recommended verb for the drift case.

    Returns ``(verb, reason)``. Prefers the most pessimistic first drift for
    the verb argument so the operator sees a concrete identifier.
    """
    if not drifts:
        return ("", "")
    first = drifts[0]
    if first.reason == "missing_after_restart":
        return (
            f"orch cancel {first.task_id}",
            f"Task {first.task_id} had no live lease after the restart; "
            "cancel it before resending to avoid a duplicate job.",
        )
    if first.reason == "epoch_changed":
        return (
            f"orch get {first.task_id}",
            f"Task {first.task_id} was re-acquired during downtime; "
            "read the latest result before deciding whether to cancel.",
        )
    if first.reason == "holder_changed":
        return (
            f"orch cancel {first.task_id}",
            f"Task {first.task_id} moved to a different holder during "
            "downtime; cancel and decide whether to retry.",
        )
    return ("orch jobs", "Inspect jobs and decide which drift to address first.")


def _recommendation(state: ResumeState) -> tuple[str, str]:
    if state.recommended_next:
        return (state.recommended_next, state.recommended_reason)
    if state.mode == "stale/interrupted":
        return _drift_recommendation(state.drifted_leases)
    if state.mode == "idle":
        return ("orch lead", "No active work. Open lead to keep the loop responsive.")
    if state.active:
        first = state.active[0]
        if first.kind == "goal":
            return (
                f"orch goal show {first.task_id}",
                f"Continue working goal {first.task_id}.",
            )
        if first.state.upper() in {"BLOCKING", "DELIVERED"}:
            return (
                f"orch get {first.task_id}",
                f"Task {first.task_id} is waiting for a result; read or wait on it.",
            )
        return (
            f"orch jobs --id {first.task_id}",
            f"Task {first.task_id} is in state {first.state}; check its route.",
        )
    return ("orch lead", "Start lead so the agent loop can pick up new work.")


def render_resume_report(
    state: ResumeState,
    *,
    banner: str = "Orchlink resume",
) -> str:
    """Render a plain-text, greppable resume report from ``state``."""
    lines: list[str] = [banner, ""]

    # 1) Active task / goal.
    if state.active:
        lines.append("Active task or goal:")
        lines.extend(f"  {item.render_line()}" for item in state.active)
    else:
        lines.append("Active task or goal: (none)")
    lines.append("")

    # 2) Lead / work sessions.
    if state.sessions:
        lines.append("Lead/work sessions:")
        lines.extend(f"  {session.render_line()}" for session in state.sessions)
    else:
        lines.append("Lead/work sessions: (none reported by broker)")
    lines.append("")

    # 3) Last broker checkpoint timestamp.
    if state.checkpoint is not None:
        lines.append("Last broker checkpoint:")
        lines.append(f"  {_humanize_timestamp(state.checkpoint.last_checkpoint_at)}")
    else:
        lines.append("Last broker checkpoint: (no checkpoint file found)")
    lines.append("")

    # 4) Drifted leases (only present in stale/interrupted state).
    if state.drifted_leases:
        lines.append("Drifted leases since checkpoint:")
        lines.extend(_drift_line(d) for d in state.drifted_leases)
        lines.append("")

    # 5) Recommended next verb + reason.
    verb, reason = _recommendation(state)
    if verb:
        lines.append(f"Recommended next: {verb}")
    if reason:
        lines.append(f"  {reason}")
    lines.append("")

    # 6) Trailing verdict line so the operator does not have to read further.
    if state.mode == "stale/interrupted":
        lines.append("Needs intervention.")
    else:
        lines.append("Safe to continue.")
    lines.append("")

    return "\n".join(lines)


def resume_state_from_checkpoint(
    checkpoint: Checkpoint,
    drifted_leases: Iterable[DriftedLease],
    *,
    active: Sequence[ActiveTaskSummary] | None = None,
    sessions: Sequence[SessionSummary] | None = None,
) -> ResumeState:
    """Build a ``ResumeState`` from a checkpoint + drift result.

    Used by the ``orch resume`` CLI command and by tests. The mode is decided
    here: if there are drifts the state is ``stale/interrupted``; if both the
    checkpoint is empty and there is no live activity the state is ``idle``;
    otherwise it is ``normal``.
    """
    drifts = list(drifted_leases)
    has_active = bool(active)
    checkpoint_non_empty = bool(checkpoint and checkpoint.leases)
    if drifts:
        mode: ResumeMode = "stale/interrupted"
    elif not has_active and not checkpoint_non_empty:
        mode = "idle"
    else:
        mode = "normal"
    return ResumeState(
        mode=mode,
        active=list(active or []),
        sessions=list(sessions or []),
        checkpoint=checkpoint,
        drifted_leases=drifts,
    )


__all__ = [
    "ActiveTaskSummary",
    "ResumeMode",
    "ResumeState",
    "SessionSummary",
    "render_resume_report",
    "resume_state_from_checkpoint",
]
