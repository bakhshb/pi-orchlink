"""Small domain models for Orchlink jobs and events."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any, Literal

from orchlink.core.states import JOB_STATUS_LIFECYCLE, CANONICAL_TERMINAL_STATUSES, JobStatus, normalize_status, require_transition


JobKind = Literal["task", "talk"]
SessionRole = Literal["lead", "work", "worker"]


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class JobEventType(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    DELIVERED = "DELIVERED"
    STARTED = "STARTED"
    REPLIED = "REPLIED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


JOB_EVENT_STATUS: dict[JobEventType, str] = {
    JobEventType.CREATED: JobStatus.CREATED.value,
    JobEventType.QUEUED: JobStatus.QUEUED.value,
    JobEventType.DELIVERED: JobStatus.DELIVERED.value,
    JobEventType.STARTED: JobStatus.RUNNING.value,
    JobEventType.REPLIED: JobStatus.DONE.value,
    JobEventType.FAILED: JobStatus.FAILED.value,
    JobEventType.TIMED_OUT: JobStatus.TIMEOUT.value,
    JobEventType.CANCELLED: JobStatus.CANCELLED.value,
    JobEventType.CLOSED: JobStatus.CLOSED.value,
}


@dataclass(frozen=True)
class JobRoute:
    from_agent: str
    to_agent: str


@dataclass(frozen=True)
class Job:
    id: str
    kind: JobKind
    project_id: str
    route: JobRoute
    mode: str
    status: str = JobStatus.CREATED.value
    task_id: str | None = None
    conversation_id: str | None = None
    turn: int = 1
    max_turns: int = 1
    payload: dict[str, Any] = field(default_factory=dict)
    # M3 job lease: {holder, expires_at, epoch, heartbeat_ms}. None when the job
    # has never been dispatched or has reached a terminal state.
    lease: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        normalized_status = normalize_status(self.status)
        if normalized_status not in JOB_STATUS_LIFECYCLE:
            raise ValueError(f"Unsupported canonical job status: {self.status}")
        if self.kind == "task" and not self.task_id:
            raise ValueError("Task jobs require task_id")
        if self.kind == "talk" and not self.conversation_id:
            raise ValueError("Talk jobs require conversation_id")
        if self.turn < 1:
            raise ValueError("turn must be >= 1")
        if self.max_turns < self.turn:
            raise ValueError("max_turns must be >= turn")
        object.__setattr__(self, "status", normalized_status)

    def transition(self, event: JobEvent) -> Job:
        return advance_job(self, event)


@dataclass(frozen=True)
class JobEvent:
    type: JobEventType
    project_id: str
    job_id: str
    status: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            event_type = JobEventType(str(self.type).upper())
        except ValueError as exc:
            raise ValueError(f"Unsupported job event type: {self.type}") from exc
        expected_status = JOB_EVENT_STATUS[event_type]
        target_status = normalize_status(self.status or expected_status)
        if target_status not in JOB_STATUS_LIFECYCLE:
            raise ValueError(f"Unsupported canonical job status: {self.status}")
        if target_status != expected_status:
            raise ValueError(f"Job event status mismatch: {event_type.value} expects {expected_status}, got {target_status}")
        object.__setattr__(self, "type", event_type)
        object.__setattr__(self, "status", target_status)


@dataclass(frozen=True)
class Session:
    lease_id: str
    project_id: str
    agent_id: str
    role: SessionRole
    status: str = SessionStatus.ACTIVE.value
    pid: int | None = None
    session_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    last_heartbeat_at: str | None = None
    ended_at: str | None = None
    ended_reason: str | None = None
    lease_grace_seconds: int = 25
    settled_work: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        try:
            status = SessionStatus(str(self.status).upper())
        except ValueError as exc:
            raise ValueError(f"Unsupported session status: {self.status}") from exc
        object.__setattr__(self, "status", status.value)


@dataclass(frozen=True)
class Event:
    type: str
    project_id: str
    job_id: str
    status: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


def advance_job(job: Job, event: JobEvent) -> Job:
    if event.project_id != job.project_id:
        raise ValueError(f"Job event project mismatch: {event.project_id} != {job.project_id}")
    if event.job_id != job.id:
        raise ValueError(f"Job event id mismatch: {event.job_id} != {job.id}")
    target_status = require_transition(job.status, event.status)
    # M3: reaching a terminal state clears the lease so a stale holder cannot
    # renew or reply against completed/cancelled work.
    if target_status in CANONICAL_TERMINAL_STATUSES:
        return replace(job, status=target_status, lease=None)
    return replace(job, status=target_status)
