"""Canonical broker job lifecycles — owned by `orchlink.core`.

This module owns lifecycle decisions and the dispatch tables that drive
`Job.transition(event)`. Storage and the broker wire/protocol/API glue live
elsewhere and only consume these primitives. The module MUST NOT import from
`orchlink.broker.*`; it depends only on the domain models and the canonical
status tables under `orchlink.core`.

`Job` lifecycle methods are the primary driver for obvious transitions;
`JobEvent` remains the audit/event primitive and is exposed through
`Job.transition(event)` as the escape hatch.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from typing import Callable

from orchlink.core.models import (
    JOB_EVENT_STATUS,
    Job,
    JobEvent,
    JobEventType,
    JobRoute,
    TalkJobPayload,
    TaskJobPayload,
)
from orchlink.core.states import (
    ALLOWED_JOB_TRANSITIONS,
    CANONICAL_TERMINAL_STATUSES,
    JobStatus,
    normalize_status,
)


# Task-status -> JobEventType mapping used by the task job lifecycle to plan
# forward transitions across the canonical lifecycle. PENDING, IN_PROGRESS,
# COMPLETED, and RECLAIMABLE are the protocol-side aliases accepted by the
# public API; the canonical event types are listed in `JobEventType`.
TASK_STATUS_JOB_EVENTS: dict[str, JobEventType] = {
    "PENDING": JobEventType.QUEUED,
    "QUEUED": JobEventType.QUEUED,
    "DELIVERED": JobEventType.DELIVERED,
    "RUNNING": JobEventType.STARTED,
    "IN_PROGRESS": JobEventType.STARTED,
    "DONE": JobEventType.REPLIED,
    "COMPLETED": JobEventType.REPLIED,
    "FAILED": JobEventType.FAILED,
    "TIMEOUT": JobEventType.TIMED_OUT,
    "CANCELLED": JobEventType.CANCELLED,
}


# Dispatch table: JobEventType -> Job lifecycle method. Used by lifecycle
# facades to drive obvious forward transitions (`CREATED -> QUEUED -> RUNNING`
# and terminal transitions such as `RUNNING -> DONE`) without hand-building
# `JobEvent` instances inline. Each call returns a new `Job` whose status /
# lease reflect the lifecycle method. Mismatched or unmapped event types fall
# back to `Job.transition(JobEvent(...))`.
LIFECYCLE_FOR_EVENT: dict[JobEventType, Callable[[Job], Job]] = {
    JobEventType.QUEUED: Job.queue,
    JobEventType.DELIVERED: Job.deliver,
    JobEventType.STARTED: Job.start,
    JobEventType.REPLIED: Job.reply,
    JobEventType.FAILED: Job.fail,
    JobEventType.TIMED_OUT: Job.timeout,
    JobEventType.CANCELLED: Job.cancel,
    JobEventType.CLOSED: Job.close,
}


@dataclass(frozen=True)
class TaskJobCommand:
    """Typed command for creating a canonical task job."""

    task_id: str
    project_id: str
    conversation_id: str | None
    from_agent: str
    to_agent: str
    mode: str


@dataclass(frozen=True)
class TalkJobCommand:
    """Typed command for creating a canonical talk job."""

    conversation_id: str
    project_id: str
    from_agent: str
    to_agent: str
    mode: str = "TALK"
    turn: int = 1
    max_turns: int = 6


class TaskJobLifecycle:
    """Create and transition canonical task jobs."""

    preferred_statuses = (
        JobStatus.QUEUED.value,
        JobStatus.DELIVERED.value,
        JobStatus.RUNNING.value,
        JobStatus.DONE.value,
        JobStatus.FAILED.value,
        JobStatus.TIMEOUT.value,
        JobStatus.CANCELLED.value,
    )

    def create(self, command: TaskJobCommand) -> Job:
        return Job(
            id=command.task_id,
            kind="task",
            project_id=command.project_id,
            task_id=command.task_id,
            conversation_id=command.conversation_id,
            route=JobRoute(
                from_agent=command.from_agent,
                to_agent=command.to_agent,
            ),
            mode=command.mode,
            status=JobStatus.CREATED.value,
            payload=TaskJobPayload(),
        )

    def transition_path(self, current_status: str, target_status: str) -> list[JobEventType]:
        current_status = normalize_status(current_status)
        target_status = normalize_status(target_status)
        if current_status == target_status:
            return []
        queue: deque[tuple[str, list[JobEventType]]] = deque([(current_status, [])])
        seen = {current_status}
        while queue:
            status, path = queue.popleft()
            allowed_statuses = ALLOWED_JOB_TRANSITIONS.get(status, frozenset())
            for next_status in self.preferred_statuses:
                if next_status not in allowed_statuses or next_status in seen or next_status not in TASK_STATUS_JOB_EVENTS:
                    continue
                next_path = [*path, TASK_STATUS_JOB_EVENTS[next_status]]
                if next_status == target_status:
                    return next_path
                seen.add(next_status)
                queue.append((next_status, next_path))
        raise ValueError(f"Invalid task job status transition: {current_status} -> {target_status}")

    def transition(self, job: Job, status: str) -> Job:
        event_type = TASK_STATUS_JOB_EVENTS.get(normalize_status(status))
        if event_type is None:
            return job
        target_status = JOB_EVENT_STATUS[event_type]
        # CREATED has no lifecycle method on `Job`, so any transition that begins
        # at CREATED still has to construct the first event inline.
        try:
            for path_event_type in self.transition_path(job.status, target_status):
                method = LIFECYCLE_FOR_EVENT.get(path_event_type)
                if method is not None:
                    job = method(job)
                else:
                    job = job.transition(
                        JobEvent(type=path_event_type, project_id=job.project_id, job_id=job.id)
                    )
        except ValueError:
            if job.status in CANONICAL_TERMINAL_STATUSES:
                return job
            raise
        return job

    def with_payload(self, job: Job, payload: TaskJobPayload) -> Job:
        return replace(job, payload=payload)


class TalkJobLifecycle:
    """Create canonical talk jobs and map conversation wire statuses."""

    def create(self, command: TalkJobCommand) -> Job:
        return Job(
            id=command.conversation_id,
            kind="talk",
            project_id=command.project_id,
            conversation_id=command.conversation_id,
            route=JobRoute(
                from_agent=command.from_agent,
                to_agent=command.to_agent,
            ),
            mode=command.mode,
            status=JobStatus.CREATED.value,
            turn=command.turn,
            max_turns=command.max_turns,
            payload=TalkJobPayload(),
        )

    def canonical_status_for_wire(self, wire_status: str) -> str:
        normalized = normalize_status(wire_status)
        if normalized == "OPEN":
            return JobStatus.RUNNING.value
        if normalized == "CLOSED":
            return JobStatus.CLOSED.value
        if normalized == "TIMEOUT":
            return JobStatus.TIMEOUT.value
        if normalized == "FAILED":
            return JobStatus.FAILED.value
        if normalized == "CANCELLED":
            return JobStatus.CANCELLED.value
        return JobStatus.RUNNING.value

    # Dispatch table mapping talk wire targets to Job lifecycle methods.
    TALK_LIFECYCLE_FOR_TARGET: dict[str, Callable[[Job], Job]] = {
        JobStatus.CLOSED.value: Job.close,
        JobStatus.TIMEOUT.value: Job.timeout,
        JobStatus.FAILED.value: Job.fail,
        JobStatus.CANCELLED.value: Job.cancel,
        JobStatus.RUNNING.value: Job.start,
    }

    def transition(self, job: Job, wire_status: str) -> Job:
        target = self.canonical_status_for_wire(wire_status)
        if job.status == target:
            return job
        # CREATED cannot reach terminal or RUNNING in one step; route through
        # QUEUED -> STARTED first using the Job lifecycle methods.
        if job.status == JobStatus.CREATED.value and target == JobStatus.RUNNING.value:
            try:
                return job.queue().start()
            except ValueError:
                if job.status in CANONICAL_TERMINAL_STATUSES:
                    return job
                raise
        method = self.TALK_LIFECYCLE_FOR_TARGET.get(target)
        if method is None:
            return job
        try:
            if job.status == JobStatus.CREATED.value:
                job = job.queue().start()
            return method(job)
        except ValueError:
            if job.status in CANONICAL_TERMINAL_STATUSES:
                return job
            raise

    def with_payload(self, job: Job, payload: TalkJobPayload, turn: int, max_turns: int) -> Job:
        return replace(job, payload=payload, turn=turn, max_turns=max_turns)


class BrokerJobLifecycle:
    """Facade for broker lifecycle operations."""

    def __init__(self) -> None:
        self.tasks = TaskJobLifecycle()
        self.talk = TalkJobLifecycle()


__all__ = [
    "LIFECYCLE_FOR_EVENT",
    "TASK_STATUS_JOB_EVENTS",
    "BrokerJobLifecycle",
    "TalkJobCommand",
    "TalkJobLifecycle",
    "TaskJobCommand",
    "TaskJobLifecycle",
]
