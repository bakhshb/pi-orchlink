"""Focused component: job/conversation projection from messages.

Lifted from ``memory.py``; preserves every method, body, and side effect.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from orchlink.broker.state import (
    ACTIVE_ACTIVITY_STATUSES,
    is_active_job_status,
    is_talk_message_type,
    job_kind_for,
    job_matches_id,
)
from orchlink.broker.storage.memory_event_log import MemoryEventLog
from orchlink.broker.storage.memory_state import (
    DEFAULT_JOB_HEARTBEAT_MS,
    InMemoryBrokerState,
    MessageProjectionContext,
    matches_project,
    new_job_lease,
)
from orchlink.core.job_lifecycle import BrokerJobLifecycle, TalkJobCommand, TaskJobCommand
from orchlink.core.models import Conversation, Job, TalkJobPayload, TaskJobPayload, TaskProjection
from orchlink.core.states import JobStatus
from orchlink.core.views import (
    conversation_to_wire,
    task_projection_from_job,
    task_projection_to_wire,
    task_result_to_wire,
)


class MemoryJobProjector:
    def __init__(
        self,
        state: InMemoryBrokerState,
        job_lifecycle: BrokerJobLifecycle,
        now: Callable[[], str],
        event_log: MemoryEventLog,
    ) -> None:
        self._state = state
        self._job_lifecycle = job_lifecycle
        self._now = now
        self._event_log = event_log

    @staticmethod
    def project_id_for(message: dict[str, Any]) -> str:
        return str(message.get("project_id") or "default")

    @staticmethod
    def job_mode_for_context(message: MessageProjectionContext) -> str:
        return message.mode()

    def task_key(self, project_id: str | None, task_id: str) -> str:
        return f"{project_id or 'default'}:{task_id}"

    def store_task_projection_locked(self, task_key: str, job: Job) -> dict[str, Any]:
        projection = task_projection_from_job(job)
        self._state.tasks[task_key] = projection
        return task_projection_to_wire(projection)

    def task_projection_locked(self, task_key: str) -> TaskProjection | None:
        return self._state.tasks.get(task_key)

    def has_task_projection_locked(self, task_key: str) -> bool:
        return task_key in self._state.tasks

    def matching_task_projections_locked(self, task_id: str) -> list[dict[str, Any]]:
        return [task_projection_to_wire(task) for key, task in self._state.tasks.items() if key.endswith(f":{task_id}") or key == task_id]

    def task_projection_values_locked(self, project_id: str | None = None) -> list[dict[str, Any]]:
        projections = [task_projection_to_wire(task) for task in self._state.tasks.values()]
        return [task for task in projections if matches_project(task, project_id)]

    def update_task_projection_locked(self, task_key: str, updates: dict[str, Any]) -> None:
        projection = self._state.tasks.get(task_key)
        if projection is not None:
            self._state.tasks[task_key] = projection.with_updates(updates)

    def conversation_key(self, project_id: str | None, conversation_id: str) -> str:
        return f"{project_id or 'default'}:{conversation_id}"

    def task_job_for_message_locked(self, message: MessageProjectionContext) -> Job | None:
        if not message.task_id:
            return None
        task_key = self.task_key(message.project_id, message.task_id)
        existing_job = self._state.task_jobs.get(task_key)
        if existing_job is not None:
            return existing_job
        if not self.has_task_projection_locked(task_key) and message.message_type != "TASK":
            return None
        command = TaskJobCommand(
            task_id=message.task_id,
            project_id=message.project_id,
            conversation_id=message.conversation_id or None,
            from_agent=message.from_agent,
            to_agent=message.to_agent,
            mode=self.job_mode_for_context(message),
        )
        job = self._job_lifecycle.tasks.create(command)
        self._state.task_jobs[task_key] = job
        return job

    def transition_task_job_locked(self, message: MessageProjectionContext, status: str) -> Job | None:
        job = self.task_job_for_message_locked(message)
        if job is None:
            return None
        job = self._job_lifecycle.tasks.transition(job, status)
        self._state.task_jobs[self.task_key(job.project_id, job.id)] = job
        return job

    def hide_stale_heartbeat_locked(self, job: dict[str, Any]) -> dict[str, Any]:
        status = str(job.get("status") or "").upper()
        if job.get("last_activity_type") == "heartbeat" and status not in ACTIVE_ACTIVITY_STATUSES:
            job.pop("last_activity_at", None)
            job.pop("last_activity_type", None)
            job.pop("last_activity_tool", None)
            job.pop("last_activity_preview", None)
        return job

    def upsert_task_locked(self, message: MessageProjectionContext, status: str) -> Job | None:
        if not message.task_id:
            return None
        task_key = self.task_key(message.project_id, message.task_id)
        job = self.transition_task_job_locked(message, status)
        if job is None:
            return None
        # M3: acquire a job lease when the work is dispatched to a worker.
        if job.status == JobStatus.DELIVERED.value and job.lease is None and message.to_agent:
            job = job.with_lease(new_job_lease(message.to_agent, DEFAULT_JOB_HEARTBEAT_MS, 1))
        now = self._now()
        existing = self.task_projection_locked(task_key)
        existing_payload = job.payload if isinstance(job.payload, TaskJobPayload) else TaskJobPayload()
        is_reply = message.message_type != "TASK"
        payload = TaskJobPayload(
            conversation_id=message.conversation_id or existing_payload.conversation_id or (existing.conversation_id if existing else None),
            mode=(existing_payload.mode or (existing.mode if existing else None)) if is_reply else self.job_mode_for_context(message),
            delivery=(existing_payload.delivery or (existing.delivery if existing else None) or "async") if is_reply else message.delivery,
            from_agent=(existing_payload.from_agent or (existing.from_agent if existing else None)) if is_reply else message.from_agent,
            to_agent=(existing_payload.to_agent or (existing.to_agent if existing else None)) if is_reply else message.to_agent,
            worker_name=(existing_payload.worker_name or (existing.to_agent.rsplit(".", 1)[-1] if existing and existing.to_agent else None)) if is_reply else message.to_agent.rsplit(".", 1)[-1],
            created_at=existing_payload.created_at or (existing.created_at if existing else None) or message.created_at or now,
            updated_at=now,
            preview=message.preview(),
            message_id=(existing_payload.message_id or (existing.message_id if existing else None)) if is_reply else message.message_id,
            correlation_id=message.correlation_id or existing_payload.correlation_id or (existing.correlation_id if existing else None),
            message_type=message.message_type,
            last_activity_at=message.last_activity_at or existing_payload.last_activity_at or (existing.last_activity_at if existing else None),
            last_activity_type=message.last_activity_type or existing_payload.last_activity_type or (existing.last_activity_type if existing else None),
            last_activity_tool=message.last_activity_tool or existing_payload.last_activity_tool or (existing.last_activity_tool if existing else None),
            last_activity_preview=message.last_activity_preview or existing_payload.last_activity_preview or (existing.last_activity_preview if existing else None),
        )
        job = self._job_lifecycle.tasks.with_payload(job, payload)
        self._state.task_jobs[task_key] = job
        self.store_task_projection_locked(task_key, job)
        return job

    def touch_conversation_locked(self, message: MessageProjectionContext, status: str | None = None) -> None:
        if not message.conversation_id:
            return
        if not is_talk_message_type(message.message_type):
            return
        conversation_key = self.conversation_key(message.project_id, message.conversation_id)
        now = self._now()

        # Job is the canonical talk job lifecycle; project its current frame.
        job = self._state.talk_jobs.get(conversation_key)
        if job is None:
            command = TalkJobCommand(
                conversation_id=message.conversation_id,
                project_id=message.project_id,
                from_agent=message.from_agent,
                to_agent=message.to_agent,
                turn=message.turn,
                max_turns=message.max_turns,
            )
            job = self._job_lifecycle.talk.create(command)

        # Compose the next Conversation via immutable helpers (with_* / replace),
        # rather than rebuilding a wire-shaped dict in place.
        base_record = self._state.conversations.get(conversation_key)
        if base_record is None:
            participants = tuple(agent for agent in (message.from_agent, message.to_agent) if agent)
            base_record = Conversation(
                conversation_id=message.conversation_id,
                project_id=message.project_id,
                participants=participants,
                status="CLOSED" if message.message_type == "CHAT_CLOSE" else "OPEN",
                turn=message.turn,
                max_turns=message.max_turns,
                from_agent=message.from_agent or None,
                to_agent=message.to_agent or None,
                message_type=message.message_type or "CHAT_START",
                created_at=now,
                updated_at=now,
            )
            base_payload = None
        else:
            base_payload = job.payload if isinstance(job.payload, TalkJobPayload) else TalkJobPayload()

        next_status = status or base_record.status or "OPEN"
        if message.message_type == "CHAT_CLOSE":
            next_status = "CLOSED"
        elif next_status not in {"CLOSED", "TIMEOUT", "FAILED", "CANCELLED"}:
            next_status = "OPEN"
        next_turn = int(message.turn or base_record.turn or job.turn or 1)
        next_max_turns = int(message.max_turns or base_record.max_turns or job.max_turns or 6)
        if next_turn >= next_max_turns and message.message_type == "CHAT_REPLY":
            next_status = "CLOSED"

        # Compose the next participants tuple (preserving order, no duplicates).
        next_participants: list[str] = list(base_record.participants)
        for agent in (message.from_agent, message.to_agent):
            if agent and agent not in next_participants:
                next_participants.append(str(agent))

        preview = message.preview()
        next_record = (
            base_record
            .with_participants(tuple(next_participants), now)
            .with_turn(next_turn, max_turns=next_max_turns)
            .with_status(next_status, now)
            .with_payload(
                status=next_status,
                turn=next_turn,
                max_turns=next_max_turns,
                message_type=message.message_type,
                last_message_preview=preview,
                preview=preview,
                now=now,
            )
        )
        # First-touch identities (from/to/worker_name) and timestamps preserved
        # by the immutable `replace` helper — only set if currently empty.
        next_record = replace(
            next_record,
            from_agent=next_record.from_agent
            or (base_payload.from_agent if base_payload else None)
            or (message.from_agent if message.from_agent else None),
            to_agent=next_record.to_agent
            or (base_payload.to_agent if base_payload else None)
            or (message.to_agent if message.to_agent else None),
            worker_name=next_record.worker_name
            or (base_payload.worker_name if base_payload else None)
            or (message.to_agent.rsplit(".", 1)[-1] if message.to_agent else None),
            created_at=next_record.created_at or (base_payload.created_at if base_payload else None) or now,
            last_activity_at=next_record.last_activity_at or (base_payload.last_activity_at if base_payload else None),
            last_activity_type=next_record.last_activity_type or (base_payload.last_activity_type if base_payload else None),
            last_activity_tool=next_record.last_activity_tool or (base_payload.last_activity_tool if base_payload else None),
            last_activity_preview=next_record.last_activity_preview or (base_payload.last_activity_preview if base_payload else None),
        )

        # Keep the Job's payload in sync with the helper-produced Conversation.
        job_payload = TalkJobPayload(
            participants=next_record.participants,
            wire_status=next_record.status,
            from_agent=next_record.from_agent,
            to_agent=next_record.to_agent,
            worker_name=next_record.worker_name,
            created_at=next_record.created_at,
            updated_at=now,
            last_message_preview=next_record.last_message_preview,
            preview=next_record.preview,
            message_type=next_record.message_type,
            last_activity_at=next_record.last_activity_at,
            last_activity_type=next_record.last_activity_type,
            last_activity_tool=next_record.last_activity_tool,
            last_activity_preview=next_record.last_activity_preview,
        )
        job = self._job_lifecycle.talk.transition(job, next_record.status)
        job = self._job_lifecycle.talk.with_payload(
            job, job_payload, turn=next_record.turn, max_turns=next_record.max_turns
        )
        self._state.talk_jobs[conversation_key] = job
        self._state.conversations[conversation_key] = next_record

    def get_task_result_locked(self, task_id: str, project_id: str | None = None) -> dict[str, Any]:
        task_key = self.task_key(project_id, task_id) if project_id is not None else task_id
        if project_id is not None:
            if task_key in self._state.results_by_task:
                return task_result_to_wire(self._state.results_by_task[task_key])
            task = self.task_projection_locked(task_key)
            if task is not None:
                task_wire = task_projection_to_wire(task)
                return {"status": task.status or "QUEUED", "project_id": project_id, "task_id": task_id, "job": task_wire}
        else:
            result_matches = [task_result_to_wire(result) for key, result in self._state.results_by_task.items() if key.endswith(f":{task_id}") or key == task_id]
            if len(result_matches) == 1:
                return result_matches[0]
            task_matches = self.matching_task_projections_locked(task_id)
            if len(task_matches) == 1:
                return {"status": task_matches[0].get("status", "QUEUED"), "project_id": task_matches[0].get("project_id"), "task_id": task_id, "job": task_matches[0]}
        return {"status": "missing", "project_id": project_id, "task_id": task_id, "error": "Task not found."}

    def list_jobs_locked(
        self,
        limit: int = 50,
        project_id: str | None = None,
        active: bool = False,
        status: str | None = None,
        kind: str | None = None,
        item_id: str | None = None,
    ) -> list[dict[str, Any]]:
        jobs = self.task_projection_values_locked(project_id)
        jobs.extend(conversation_to_wire(conversation) for conversation in self._state.conversations.values() if matches_project(conversation_to_wire(conversation), project_id))
        if active:
            jobs = [job for job in jobs if is_active_job_status(job.get("status"))]
        if status:
            expected_status = status.upper()
            jobs = [job for job in jobs if str(job.get("status") or "").upper() == expected_status]
        if kind:
            expected_kind = kind.lower()
            jobs = [job for job in jobs if job_kind_for(job) == expected_kind]
        if item_id:
            jobs = [job for job in jobs if job_matches_id(job, item_id)]
        jobs = [self.hide_stale_heartbeat_locked(job) for job in jobs]
        jobs.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
        return jobs[:limit]

    def list_conversations_locked(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return [conversation_to_wire(conversation) for conversation in self._state.conversations.values() if matches_project(conversation_to_wire(conversation), project_id)]


__all__ = ["MemoryJobProjector"]