"""Typed Goal Mode lifecycle primitives.

This module owns Goal Mode status/event vocabulary and goal status transition
validation. Storage, CLI, and runner code may persist or display these values,
but lifecycle mutations should pass through :mod:`orchlink.goal.policy`.
"""

from __future__ import annotations

from enum import StrEnum


class GoalLifecycleError(ValueError):
    """Raised when a goal lifecycle value or transition is invalid."""


class GoalStatus(StrEnum):
    DRAFT = "draft"
    READY = "ready"
    RUNNING = "running"
    GATED = "gated"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class GateStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class AcceptanceStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"
    FAILED = "failed"
    DEFERRED = "deferred"
    HUMAN_APPROVED = "human-approved"


class GoalEventType(StrEnum):
    CREATED = "created"
    GATE_APPROVED = "gate_approved"
    GATE_REJECTED = "gate_rejected"
    TASK_DISPATCHED = "task_dispatched"
    DERIVATION_DISPATCHED = "derivation_dispatched"
    AUDIT_DISPATCHED = "audit_dispatched"
    TASK_RESULT = "task_result"
    VERIFIED_DONE = "verified_done"
    WORKER_BLOCKER = "worker_blocker"
    BROKER_BLOCKED = "broker_blocked"
    DERIVATION_BLOCKED = "derivation_blocked"
    AUDIT_BLOCKED = "audit_blocked"
    CAP_REACHED = "cap_reached"
    MANUAL_VERIFICATION_REQUIRED = "manual_verification_required"
    SUBJECTIVE_SIGNOFF_REQUIRED = "subjective_signoff_required"
    SUBJECTIVE_APPROVED = "subjective_approved"
    GATE_REQUIRED = "gate_required"
    CANCELLED = "cancelled"
    CANCEL_TASK_FAILED = "cancel_task_failed"
    ARTIFACTS_WRITTEN = "artifacts_written"
    EVIDENCE = "evidence"
    BLOCKER = "blocker"
    GAP_DETECTED = "gap_detected"
    DEFERRED = "deferred"
    DERIVED = "derived"
    AUDIT = "audit"
    TRIAL_RECORDED = "trial_recorded"


GOAL_STATUSES = tuple(status.value for status in GoalStatus)
GATE_STATUSES = tuple(status.value for status in GateStatus)
ACCEPTANCE_STATUSES = tuple(status.value for status in AcceptanceStatus)
GOAL_EVENT_TYPES = tuple(event.value for event in GoalEventType)

ALLOWED_GOAL_TRANSITIONS: dict[GoalStatus, frozenset[GoalStatus]] = {
    GoalStatus.DRAFT: frozenset({GoalStatus.READY, GoalStatus.BLOCKED, GoalStatus.CANCELLED}),
    GoalStatus.READY: frozenset(
        {GoalStatus.DRAFT, GoalStatus.RUNNING, GoalStatus.GATED, GoalStatus.BLOCKED, GoalStatus.DONE, GoalStatus.CANCELLED}
    ),
    GoalStatus.RUNNING: frozenset({GoalStatus.GATED, GoalStatus.BLOCKED, GoalStatus.DONE, GoalStatus.CANCELLED}),
    GoalStatus.GATED: frozenset({GoalStatus.RUNNING, GoalStatus.BLOCKED, GoalStatus.DONE, GoalStatus.CANCELLED}),
    GoalStatus.BLOCKED: frozenset({GoalStatus.RUNNING, GoalStatus.GATED, GoalStatus.DONE, GoalStatus.CANCELLED}),
    GoalStatus.DONE: frozenset(),
    GoalStatus.CANCELLED: frozenset(),
}


def normalize_goal_status(value: object) -> GoalStatus:
    try:
        return GoalStatus(str(value or ""))
    except ValueError as exc:
        raise GoalLifecycleError(f"Unknown goal status: {value!r}") from exc


def normalize_gate_status(value: object) -> GateStatus:
    try:
        return GateStatus(str(value or ""))
    except ValueError as exc:
        raise GoalLifecycleError(f"Unknown gate status: {value!r}") from exc


def normalize_acceptance_status(value: object) -> AcceptanceStatus:
    try:
        return AcceptanceStatus(str(value or ""))
    except ValueError as exc:
        raise GoalLifecycleError(f"Unknown acceptance status: {value!r}") from exc


def normalize_goal_event_type(value: object) -> GoalEventType:
    try:
        return GoalEventType(str(value or ""))
    except ValueError as exc:
        raise GoalLifecycleError(f"Unknown goal event type: {value!r}") from exc


def goal_event_type_or_none(value: object) -> GoalEventType | None:
    try:
        return normalize_goal_event_type(value)
    except GoalLifecycleError:
        return None


def can_transition_goal(current: object, target: object) -> bool:
    current_status = normalize_goal_status(current)
    target_status = normalize_goal_status(target)
    if current_status == target_status:
        return True
    return target_status in ALLOWED_GOAL_TRANSITIONS[current_status]


def require_goal_transition(current: object, target: object) -> GoalStatus:
    current_status = normalize_goal_status(current)
    target_status = normalize_goal_status(target)
    if current_status == target_status:
        return target_status
    if target_status not in ALLOWED_GOAL_TRANSITIONS[current_status]:
        raise GoalLifecycleError(f"Invalid goal status transition: {current_status.value} -> {target_status.value}")
    return target_status


__all__ = [
    "ACCEPTANCE_STATUSES",
    "ALLOWED_GOAL_TRANSITIONS",
    "GATE_STATUSES",
    "GOAL_EVENT_TYPES",
    "GOAL_STATUSES",
    "AcceptanceStatus",
    "GateStatus",
    "GoalEventType",
    "GoalLifecycleError",
    "GoalStatus",
    "can_transition_goal",
    "goal_event_type_or_none",
    "normalize_acceptance_status",
    "normalize_gate_status",
    "normalize_goal_event_type",
    "normalize_goal_status",
    "require_goal_transition",
]
