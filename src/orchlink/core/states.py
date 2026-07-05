"""Canonical job lifecycle helpers.

This module is intentionally independent from FastAPI, Typer, and storage. The
broker accepts protocol-level aliases at its boundaries, but domain code should
reason in terms of the canonical JobStatus lifecycle below.
"""

from enum import StrEnum


class JobStatus(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    DELIVERED = "DELIVERED"
    RUNNING = "RUNNING"
    RECLAIMABLE = "RECLAIMABLE"
    DONE = "DONE"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


JOB_STATUS_LIFECYCLE = tuple(status.value for status in JobStatus)

# Protocol/job-view aliases accepted at the boundary and projected into the
# canonical lifecycle above.
BUSY_MESSAGE_STATUSES = {"PENDING", "QUEUED", "DELIVERED", "RUNNING", "IN_PROGRESS"}
ACTIVE_ACTIVITY_STATUSES = {"DELIVERED", "RUNNING", "IN_PROGRESS"}
ACTIVE_JOB_STATUSES = BUSY_MESSAGE_STATUSES | {JobStatus.RECLAIMABLE.value, "OPEN"}
FAILED_STATUSES = {"FAILED", "TIMEOUT", "CANCELLED"}
TERMINAL_MESSAGE_STATUSES = {"DONE", "COMPLETED", "FAILED", "TIMEOUT", "CANCELLED", "CLOSED"}
CANONICAL_TERMINAL_STATUSES = {JobStatus.DONE.value, JobStatus.FAILED.value, JobStatus.TIMEOUT.value, JobStatus.CANCELLED.value, JobStatus.CLOSED.value}

ALLOWED_JOB_TRANSITIONS: dict[str, frozenset[str]] = {
    JobStatus.CREATED.value: frozenset({JobStatus.QUEUED.value, JobStatus.CANCELLED.value}),
    JobStatus.QUEUED.value: frozenset({JobStatus.DELIVERED.value, JobStatus.RUNNING.value, JobStatus.TIMEOUT.value, JobStatus.CANCELLED.value}),
    JobStatus.DELIVERED.value: frozenset({JobStatus.RUNNING.value, JobStatus.RECLAIMABLE.value, JobStatus.DONE.value, JobStatus.FAILED.value, JobStatus.TIMEOUT.value, JobStatus.CANCELLED.value}),
    JobStatus.RUNNING.value: frozenset({JobStatus.RECLAIMABLE.value, JobStatus.DONE.value, JobStatus.FAILED.value, JobStatus.TIMEOUT.value, JobStatus.CANCELLED.value, JobStatus.CLOSED.value}),
    JobStatus.RECLAIMABLE.value: frozenset({JobStatus.RUNNING.value, JobStatus.DONE.value, JobStatus.FAILED.value, JobStatus.TIMEOUT.value, JobStatus.CANCELLED.value, JobStatus.CLOSED.value}),
    JobStatus.DONE.value: frozenset(),
    JobStatus.FAILED.value: frozenset(),
    JobStatus.TIMEOUT.value: frozenset(),
    JobStatus.CANCELLED.value: frozenset(),
    JobStatus.CLOSED.value: frozenset(),
}


def normalize_status(value: object) -> str:
    return str(value or "").upper()


def is_busy_status(value: object) -> bool:
    return normalize_status(value) in BUSY_MESSAGE_STATUSES


def is_terminal_status(value: object) -> bool:
    return normalize_status(value) in TERMINAL_MESSAGE_STATUSES


def is_active_job_status(value: object) -> bool:
    return normalize_status(value) in ACTIVE_JOB_STATUSES


def is_active_activity_status(value: object) -> bool:
    return normalize_status(value) in ACTIVE_ACTIVITY_STATUSES


def can_transition(current: object, next_status: object) -> bool:
    current_status = normalize_status(current)
    target_status = normalize_status(next_status)
    return target_status in ALLOWED_JOB_TRANSITIONS.get(current_status, frozenset())


def require_transition(current: object, next_status: object) -> str:
    current_status = normalize_status(current)
    target_status = normalize_status(next_status)
    if not can_transition(current_status, target_status):
        raise ValueError(f"Invalid job status transition: {current_status} -> {target_status}")
    return target_status


def reply_job_status(message_type: object, reply_status: object = "DONE") -> str:
    if str(message_type or "").upper() == "CHAT_CLOSE":
        return JobStatus.CLOSED.value
    if normalize_status(reply_status or "DONE") in FAILED_STATUSES:
        return JobStatus.FAILED.value
    return JobStatus.DONE.value
