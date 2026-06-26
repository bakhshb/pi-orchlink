"""Canonical broker job state machines.

Storage owns concurrency and wire indexes; this module owns lifecycle decisions.
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from typing import Any

from orchlink.broker.state import TASK_STATUS_JOB_EVENTS
from orchlink.core.models import Job, JobEvent, JobEventType, JobRoute
from orchlink.core.states import ALLOWED_JOB_TRANSITIONS, CANONICAL_TERMINAL_STATUSES, JobStatus, normalize_status


class TaskJobStateMachine:
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

    def create(self, message: dict[str, Any], project_id: str, mode: str) -> Job:
        task_id = str(message.get("task_id") or "")
        return Job(
            id=task_id,
            kind="task",
            project_id=project_id,
            task_id=task_id,
            conversation_id=message.get("conversation_id"),
            route=JobRoute(
                from_agent=str(message.get("from_agent") or ""),
                to_agent=str(message.get("to_agent") or ""),
            ),
            mode=mode,
            status=JobStatus.CREATED.value,
            payload={},
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
        target_event = JobEvent(type=event_type, project_id=job.project_id, job_id=job.id)
        try:
            for path_event_type in self.transition_path(job.status, target_event.status):
                job = job.transition(JobEvent(type=path_event_type, project_id=job.project_id, job_id=job.id))
        except ValueError:
            if job.status in CANONICAL_TERMINAL_STATUSES:
                return job
            raise
        return job

    def with_payload(self, job: Job, payload: dict[str, Any]) -> Job:
        return replace(job, payload=dict(payload))


class TalkJobStateMachine:
    """Create canonical talk jobs while preserving legacy OPEN wire status."""

    terminal_wire_statuses = {"CLOSED", "TIMEOUT", "FAILED", "CANCELLED"}

    def create(self, message: dict[str, Any], project_id: str, mode: str = "TALK") -> Job:
        conversation_id = str(message.get("conversation_id") or "")
        return Job(
            id=conversation_id,
            kind="talk",
            project_id=project_id,
            conversation_id=conversation_id,
            route=JobRoute(
                from_agent=str(message.get("from_agent") or ""),
                to_agent=str(message.get("to_agent") or ""),
            ),
            mode=mode,
            status=JobStatus.CREATED.value,
            turn=int(message.get("turn") or 1),
            max_turns=int(message.get("max_turns") or 6),
            payload={},
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

    def transition(self, job: Job, wire_status: str) -> Job:
        target = self.canonical_status_for_wire(wire_status)
        if job.status == target:
            return job
        if job.status == JobStatus.CREATED.value and target == JobStatus.RUNNING.value:
            return job.transition(JobEvent(type=JobEventType.QUEUED, project_id=job.project_id, job_id=job.id)).transition(
                JobEvent(type=JobEventType.STARTED, project_id=job.project_id, job_id=job.id)
            )
        event_type = {
            JobStatus.CLOSED.value: JobEventType.CLOSED,
            JobStatus.TIMEOUT.value: JobEventType.TIMED_OUT,
            JobStatus.FAILED.value: JobEventType.FAILED,
            JobStatus.CANCELLED.value: JobEventType.CANCELLED,
            JobStatus.RUNNING.value: JobEventType.STARTED,
        }.get(target)
        if event_type is None:
            return job
        try:
            if job.status == JobStatus.CREATED.value:
                job = job.transition(JobEvent(type=JobEventType.QUEUED, project_id=job.project_id, job_id=job.id)).transition(
                    JobEvent(type=JobEventType.STARTED, project_id=job.project_id, job_id=job.id)
                )
            return job.transition(JobEvent(type=event_type, project_id=job.project_id, job_id=job.id))
        except ValueError:
            if job.status in CANONICAL_TERMINAL_STATUSES:
                return job
            raise

    def with_payload(self, job: Job, payload: dict[str, Any], turn: int, max_turns: int) -> Job:
        return replace(job, payload=dict(payload), turn=turn, max_turns=max_turns)


class BrokerStateMachine:
    """Facade for broker lifecycle operations."""

    def __init__(self) -> None:
        self.tasks = TaskJobStateMachine()
        self.talk = TalkJobStateMachine()
