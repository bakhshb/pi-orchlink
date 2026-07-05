from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable

from orchlink.broker.state import (
    ACTIVE_ACTIVITY_STATUSES,
    BUSY_MESSAGE_STATUSES,
    TERMINAL_MESSAGE_STATUSES,
    WORKER_BOUND_TYPES,
    canonical_job_event_for_broker_event,
    is_active_job_status,
    is_active_session_status,
    is_busy_status,
    is_talk_message_type,
    is_terminal_status,
    job_kind_for,
    job_matches_id,
    reply_job_status,
)
from orchlink.core.job_lifecycle import BrokerJobLifecycle, TalkJobCommand, TaskJobCommand
from orchlink.broker.storage.base import (
    ActivityInput,
    AgentInput,
    LeaseConflictError,
    MessageInput,
    MessageStore,
    MessageStoreBusy,
    SessionAcquireInput,
    SessionHeartbeatInput,
)
from orchlink.core.models import ActivityRecord, Agent, BrokerEvent, BrokerEventContext, Conversation, Job, JobLease, ReplyResult, Session, SessionAcquire, SessionHeartbeat, SessionRelease, StoredMessage, TalkJobPayload, TaskJobPayload, TaskProjection, TaskResult, WaitBlocker, WorkerActivityInput
from orchlink.core.states import JobStatus
from orchlink.core.views import (
    agent_input_to_agent,
    agent_to_wire,
    conversation_from_wire,
    conversation_to_wire,
    lease_to_wire,
    message_input_to_stored,
    reply_message_to_wire,
    reply_result_to_wire,
    session_acquire_from_wire,
    session_heartbeat_from_wire,
    session_release_from_wire,
    session_to_wire,
    stored_message_to_wire,
    talk_job_to_wire,
    task_projection_from_job,
    task_projection_to_wire,
    task_result_to_wire,
    wait_blocker_to_wire,
    worker_activity_from_wire,
)



DEFAULT_JOB_HEARTBEAT_MS = 15000
JOB_LEASE_GRACE_MULTIPLIER = 6
InboxItem = StoredMessage


@dataclass(frozen=True)
class MessageProjectionContext:
    """Typed context for projecting stored messages into jobs/events."""

    project_id: str
    conversation_id: str
    task_id: str | None
    message_id: str
    correlation_id: str
    from_agent: str
    to_agent: str
    message_type: str
    status: str
    turn: int
    max_turns: int
    delivery: str
    payload: dict[str, Any]
    requires_reply: bool = True
    timeout_seconds: int = 0
    created_at: str | None = None
    queued_at: str | None = None
    updated_at: str | None = None
    last_activity_at: str | None = None
    last_activity_type: str | None = None
    last_activity_tool: str | None = None
    last_activity_preview: str | None = None

    @classmethod
    def from_stored(
        cls,
        stored: StoredMessage,
        *,
        status: str | None = None,
        updated_at: str | None = None,
        last_activity_at: str | None = None,
        last_activity_type: str | None = None,
        last_activity_tool: str | None = None,
        last_activity_preview: str | None = None,
    ) -> "MessageProjectionContext":
        envelope = stored.envelope
        payload = envelope.payload.model_dump(mode="json") if hasattr(envelope.payload, "model_dump") else dict(envelope.payload or {})
        return cls(
            project_id=str(envelope.project_id or "default"),
            conversation_id=str(envelope.conversation_id or ""),
            task_id=str(envelope.task_id) if envelope.task_id is not None else None,
            message_id=str(envelope.message_id),
            correlation_id=str(envelope.correlation_id),
            from_agent=str(envelope.from_agent or ""),
            to_agent=str(envelope.to_agent or ""),
            message_type=str(envelope.type or ""),
            status=str(status or stored.status or envelope.status or ""),
            turn=int(envelope.turn or 1),
            max_turns=int(envelope.max_turns or 6),
            delivery=str(envelope.delivery or "async"),
            payload=payload,
            requires_reply=bool(envelope.requires_reply),
            timeout_seconds=int(envelope.timeout_seconds or 0),
            created_at=stored.created_at,
            queued_at=stored.queued_at,
            updated_at=updated_at or stored.updated_at,
            last_activity_at=last_activity_at,
            last_activity_type=last_activity_type,
            last_activity_tool=last_activity_tool,
            last_activity_preview=last_activity_preview,
        )

    def mode(self) -> str:
        mode = self.payload.get("mode")
        if mode:
            return str(mode)
        if is_talk_message_type(self.message_type):
            return "TALK"
        return "PLAN"

    def payload_preview(self) -> str:
        for key in ("message", "intent", "topic", "summary", "stdout"):
            value = self.payload.get(key)
            if value:
                return str(value)
        return ""

    def preview(self) -> str:
        return self.payload_preview()[:300]


def _new_lease(holder: str, heartbeat_ms: int | None, epoch: int) -> JobLease:
    """Build a fresh typed lease with an expiry derived from the heartbeat interval."""
    return JobLease.fresh(
        holder,
        heartbeat_ms or DEFAULT_JOB_HEARTBEAT_MS,
        epoch,
        grace_multiplier=JOB_LEASE_GRACE_MULTIPLIER,
    )


def _session_project(session: Session, project_id: str | None) -> bool:
    """Return True when a stored ``Session`` belongs to the named project.

    Mirrors the dict-based ``_same_project`` semantics used elsewhere so the
    session registry can compare ``Session`` objects without dict round-trips.
    Passing ``project_id=None`` matches every project.
    """
    return project_id is None or str(session.project_id or "default") == str(project_id)


def _matches_project(item: dict[str, Any], project_id: str | None) -> bool:
    """Project-id filter shared by every focused component.

    ``None`` matches every project. The string comparison uses ``"default"``
    as the implicit project for records without an explicit project id.
    """
    return project_id is None or str(item.get("project_id") or "default") == str(project_id)


@dataclass
class InMemoryBrokerState:
    agents: dict[str, Agent] = field(default_factory=dict)
    inboxes: dict[str, asyncio.Queue[InboxItem]] = field(default_factory=dict)
    active_messages: dict[str, StoredMessage] = field(default_factory=dict)
    tasks: dict[str, TaskProjection] = field(default_factory=dict)
    task_jobs: dict[str, Job] = field(default_factory=dict)
    results_by_task: dict[str, TaskResult] = field(default_factory=dict)
    conversations: dict[str, Conversation] = field(default_factory=dict)
    talk_jobs: dict[str, Job] = field(default_factory=dict)
    pending_replies: dict[str, asyncio.Future[ReplyResult | WaitBlocker]] = field(default_factory=dict)
    task_waiters: dict[str, list[asyncio.Future[TaskResult]]] = field(default_factory=dict)
    events: list[BrokerEvent] = field(default_factory=list)
    activity: list[ActivityRecord] = field(default_factory=list)
    sessions: dict[str, Session] = field(default_factory=dict)
    next_event_id: int = 1
    next_activity_id: int = 1


class MemoryEventLog:
    def __init__(
        self,
        state: InMemoryBrokerState,
        now: Callable[[], str],
    ) -> None:
        self._state = state
        self._now = now
        # Observability-only audit journal (M1). Late-bound via attach_journal
        # so the journal can be wired after the store is constructed.
        self._journal: Any = None

    @staticmethod
    def job_mode(message: dict[str, Any]) -> str:
        """Resolve the canonical job mode for a stored message dict.

        Mirrors the projection used by ``MemoryJobProjector.job_mode`` so the
        event log can surface a mode label without taking a callback.
        """
        payload = message.get("payload") or {}
        mode = payload.get("mode")
        if mode:
            return str(mode)
        if is_talk_message_type(message.get("type")):
            return "TALK"
        return "PLAN"

    @staticmethod
    def matches_project(item: dict[str, Any] | BrokerEvent | ActivityRecord, project_id: str | None) -> bool:
        if isinstance(item, BrokerEvent):
            item = item.to_wire_dict()
        elif isinstance(item, ActivityRecord):
            item = item.to_wire_dict()
        return _matches_project(item, project_id)

    def payload_preview(self, payload: dict[str, Any]) -> str:
        for key in ("message", "intent", "topic", "summary", "stdout"):
            value = payload.get(key)
            if value:
                return str(value)
        return ""

    def message_preview(self, message: dict[str, Any]) -> str:
        return self.payload_preview(message.get("payload") or {})

    def append_event_locked(self, context: BrokerEventContext) -> dict[str, Any]:
        payload = context.fields["payload"] if "payload" in context.fields else {}
        preview = context.preview
        if preview is None and isinstance(payload, dict):
            preview = self.payload_preview(payload)
        event_fields = dict(context.fields)
        job_event = canonical_job_event_for_broker_event(context.event_type, event_fields)
        if job_event is not None:
            event_fields["job_event"] = job_event
        event = BrokerEvent(
            id=self._state.next_event_id,
            time=self._now(),
            type=context.event_type,
            preview=str(preview or "")[:300],
            fields=event_fields,
        )
        self._state.next_event_id += 1
        self._state.events.append(event)
        if len(self._state.events) > 1000:
            self._state.events = self._state.events[-1000:]
        event_wire = event.to_wire_dict()
        if self._journal is not None:
            try:
                self._journal.record_broker_event(event_wire)
            except Exception:
                # Observability-only: never fail a transition for the journal.
                pass
        return event_wire

    def event_fields(self, message: dict[str, Any], status: str | None = None) -> dict[str, Any]:
        payload = message.get("payload") or {}
        return {
            "project_id": message.get("project_id"),
            "task_id": message.get("task_id"),
            "conversation_id": message.get("conversation_id"),
            "message_id": message.get("message_id"),
            "correlation_id": message.get("correlation_id"),
            "from_agent": message.get("from_agent"),
            "to_agent": message.get("to_agent"),
            "message_type": message.get("type"),
            "mode": self.job_mode(message),
            "delivery": message.get("delivery"),
            "status": status or message.get("status"),
            "turn": message.get("turn"),
            "max_turns": message.get("max_turns"),
            "payload": payload,
        }

    def event_context(
        self,
        event_type: str,
        message: dict[str, Any],
        status: str | None = None,
        preview: str | None = None,
    ) -> BrokerEventContext:
        """Build a typed event context from a message wire view."""
        return BrokerEventContext.from_fields(
            event_type,
            **self.event_fields(message, status),
            preview=preview,
        )

    def activity_preview(self, activity: WorkerActivityInput | ActivityRecord) -> str:
        detail = str(activity.detail or activity.phase or activity.activity_type or "")
        tool_name = str(activity.tool_name or "")
        if tool_name and detail:
            return f"{tool_name}: {detail}"
        return tool_name or detail

    def record_activity_locked(
        self,
        activity: WorkerActivityInput,
        apply_activity_to_work: Callable[[ActivityRecord, str], None],
    ) -> dict[str, Any]:
        timestamp = self._now()
        stored = activity.to_record(self._state.next_activity_id, timestamp)
        stored_wire = stored.to_wire_dict()
        self._state.next_activity_id += 1
        self._state.activity.append(stored)
        if len(self._state.activity) > 1000:
            self._state.activity = self._state.activity[-1000:]
        apply_activity_to_work(stored, timestamp)
        self.append_event_locked(
            BrokerEventContext.from_fields(
                "worker_activity",
                project_id=stored.project_id,
                task_id=stored.task_id,
                conversation_id=stored.conversation_id,
                message_id=stored.message_id,
                from_agent=stored.agent_id,
                message_type="ACTIVITY",
                mode=stored.mode,
                status=stored.status,
                payload=stored_wire,
                preview=self.activity_preview(stored),
            )
        )
        return {"status": "recorded", "activity_id": stored.id}

    def list_activity_locked(
        self,
        item_id: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        selected = [item.to_wire_dict() for item in self._state.activity if self.matches_project(item, project_id)]
        if item_id:
            selected = [
                item
                for item in selected
                if str(item.get("task_id") or "") == item_id
                or str(item.get("conversation_id") or "") == item_id
                or str(item.get("message_id") or "") == item_id
            ]
        return selected[-limit:]

    def list_events_locked(self, since: int = 0, limit: int = 100, project_id: str | None = None) -> list[dict[str, Any]]:
        selected = [
            event.to_wire_dict()
            for event in self._state.events
            if event.id > since and self.matches_project(event, project_id)
        ]
        return selected[-limit:]


class MemorySessionStore:
    def __init__(
        self,
        state: InMemoryBrokerState,
        now: Callable[[], str],
        parse_time: Callable[[Any], datetime | None],
        event_log: "MemoryEventLog",
        session_grace_seconds: int,
    ) -> None:
        self._state = state
        self._now = now
        self._parse_time = parse_time
        self._event_log = event_log
        self._session_grace_seconds = session_grace_seconds

    def active_sessions_for_agent_locked(self, agent_id: str, project_id: str | None = None) -> list[Session]:
        return [
            session
            for session in self._state.sessions.values()
            if _session_project(session, project_id)
            and session.agent_id == agent_id
            and is_active_session_status(session.status)
        ]

    def active_session_locked(self, agent_id: str, project_id: str | None = None) -> Session | None:
        sessions = self.active_sessions_for_agent_locked(agent_id, project_id=project_id)
        return sessions[0] if sessions else None

    def assert_poll_lease_locked(
        self,
        agent_id: str,
        project_id: str | None = None,
        lease_id: str | None = None,
    ) -> None:
        """Require a current session lease before polling when a session exists."""
        sessions = self.active_sessions_for_agent_locked(agent_id, project_id=project_id)
        if not sessions:
            return
        if not lease_id:
            raise LeaseConflictError(f"Session lease required for active agent: {agent_id}")
        for session in sessions:
            if session.lease_id == lease_id:
                return
        raise LeaseConflictError(f"Stale or inactive session lease for agent: {agent_id}")

    def assert_active_session_lease_locked(
        self,
        agent_id: str,
        project_id: str | None = None,
        lease_id: str | None = None,
    ) -> None:
        """Validate an asserted session lease; empty lease means no assertion."""
        if not lease_id:
            return
        for session in self.active_sessions_for_agent_locked(agent_id, project_id=project_id):
            if session.lease_id == lease_id:
                return
        raise LeaseConflictError(f"Stale or inactive session lease for agent: {agent_id}")

    def active_session_count_locked(self, project_id: str | None = None) -> int:
        return sum(
            1
            for session in self._state.sessions.values()
            if _session_project(session, project_id)
            and is_active_session_status(session.status)
        )

    def expire_sessions_locked(self, on_session_ended: Callable[[str, str, str], list[str]]) -> list[Session]:
        now = datetime.now(timezone.utc)
        expired: list[Session] = []
        for session in list(self._state.sessions.values()):
            if not is_active_session_status(session.status):
                continue
            last_seen = self._parse_time(session.last_heartbeat_at or session.updated_at)
            if last_seen is None:
                continue
            grace = int(session.lease_grace_seconds or self._session_grace_seconds)
            if (now - last_seen).total_seconds() < grace:
                continue
            reason = f"Session heartbeat expired: {session.agent_id}"
            expired_session = replace(
                session.expire(self._now(), reason),
                settled_work=on_session_ended(str(session.agent_id), str(session.project_id or "default"), reason),
            )
            self._state.sessions[session.lease_id] = expired_session
            expired.append(expired_session)
            self._event_log.append_event_locked(BrokerEventContext.from_fields(
                "session_expired",
                project_id=session.project_id,
                agent_id=session.agent_id,
                role=session.role,
                lease_id=session.lease_id,
                status=expired_session.status,
                payload=session_to_wire(expired_session),
                preview=reason,
            ))
        return expired

    def acquire_session_locked(self, command: SessionAcquire) -> dict[str, Any]:
        now = self._now()
        lease_id = str(command.lease_id or f"lease-{uuid.uuid4()}")
        project_id = str(command.project_id or "default")
        agent_id = str(command.agent_id or "")
        worker_name = str(command.worker_name or "")
        for active in self._state.sessions.values():
            if not _session_project(active, project_id):
                continue
            if not is_active_session_status(active.status):
                continue
            if active.lease_id == lease_id:
                continue
            active_worker_name = str(active.worker_name or "")
            if active.agent_id == agent_id:
                raise LeaseConflictError(f"Active session already exists for agent: {agent_id}")
            if worker_name and active_worker_name == worker_name:
                raise LeaseConflictError(f"Active session already exists for worker name: {worker_name}")
        ready = bool(command.ready)
        stored = Session(
            lease_id=lease_id,
            project_id=project_id,
            agent_id=agent_id,
            role=str(command.role or "work"),
            worker_name=command.worker_name,
            pid=command.pid,
            session_id=command.session_id,
            status="ACTIVE",
            created_at=now,
            updated_at=now,
            last_heartbeat_at=now,
            lease_grace_seconds=int(command.lease_grace_seconds or self._session_grace_seconds),
            ready=ready,
            ready_at=now if ready else None,
            last_ready_heartbeat_at=now if ready else None,
            runtime_mode=command.runtime_mode,
            backend=command.backend,
            model=command.model,
            thinking=command.thinking,
            supervisor_pid=command.supervisor_pid,
            pi_pid=command.pi_pid,
        )
        self._state.sessions[lease_id] = stored
        wire = session_to_wire(stored)
        self._event_log.append_event_locked(BrokerEventContext.from_fields(
            "session_acquired",
            project_id=stored.project_id,
            agent_id=stored.agent_id,
            role=stored.role,
            lease_id=lease_id,
            status="ACTIVE",
            payload=wire,
            preview=f"session active {stored.agent_id}",
        ))
        return wire

    def heartbeat_session_locked(self, command: SessionHeartbeat) -> dict[str, Any]:
        session = self._state.sessions.get(command.lease_id)
        if session is None or not _session_project(session, command.project_id):
            raise ValueError(f"Session not found: {command.lease_id}")
        if not is_active_session_status(session.status):
            return session_to_wire(session)
        now = self._now()
        updated = session.heartbeat(now)
        if command.ready is True:
            updated = updated.mark_ready(now)
        metadata: dict[str, Any] = {}
        for key in ("runtime_mode", "backend", "model", "thinking", "supervisor_pid", "pi_pid", "worker_name"):
            value = getattr(command, key)
            if value not in {None, ""}:
                metadata[key] = value
        if metadata:
            updated = replace(updated, **metadata)
        self._state.sessions[command.lease_id] = updated
        return session_to_wire(updated)

    def release_session_locked(
        self,
        command: SessionRelease,
        on_session_ended: Callable[[str, str, str], list[str]],
    ) -> dict[str, Any]:
        session = self._state.sessions.get(command.lease_id)
        if session is None or not _session_project(session, command.project_id):
            raise ValueError(f"Session not found: {command.lease_id}")
        if is_active_session_status(session.status):
            release_reason = command.reason or "Session exited."
            settled = on_session_ended(
                str(session.agent_id),
                str(session.project_id or "default"),
                command.reason or f"Session exited: {session.agent_id}",
            )
            released = replace(
                session.release(self._now(), release_reason),
                settled_work=settled,
            )
            self._state.sessions[command.lease_id] = released
            self._event_log.append_event_locked(BrokerEventContext.from_fields(
                "session_released",
                project_id=session.project_id,
                agent_id=session.agent_id,
                role=session.role,
                lease_id=command.lease_id,
                status=released.status,
                payload=session_to_wire(released),
                preview=release_reason or f"session released {session.agent_id}",
            ))
            return session_to_wire(released)
        return session_to_wire(session)

    def list_sessions_locked(self, project_id: str | None = None, active: bool = False) -> list[dict[str, Any]]:
        sessions = [
            session
            for session in self._state.sessions.values()
            if _session_project(session, project_id)
        ]    
        if active:
            sessions = [session for session in sessions if is_active_session_status(session.status)]
        sessions = list(sessions)
        sessions.sort(key=lambda item: str(item.updated_at or item.created_at or ""), reverse=True)
        return [session_to_wire(s) for s in sessions]



class MemoryActivityStore:
    """Focused component for activity records.

    Owns activity storage and listing. Wraps the existing `MemoryEventLog`
    helpers so the event log remains the source of truth for the audit
    journal, while this component owns the activity lifecycle surface that
    the facade exposes (`record_activity`, `list_activity`).

    Shares `InMemoryBrokerState`, the clock, and the `MemoryEventLog` with
    the facade. Holds no independent state copy.
    """

    def __init__(
        self,
        state: "InMemoryBrokerState",
        event_log: "MemoryEventLog",
        now: Callable[[], str],
        apply_activity_to_work: Callable[[ActivityRecord, str], None],
    ) -> None:
        self._state = state
        self._event_log = event_log
        self._now = now
        self._apply_activity_to_work = apply_activity_to_work

    def record_activity_locked(self, activity: WorkerActivityInput) -> dict[str, Any]:
        return self._event_log.record_activity_locked(
            activity,
            self._apply_activity_to_work,
        )

    def list_activity_locked(
        self,
        item_id: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._event_log.list_activity_locked(
            item_id=item_id,
            limit=limit,
            project_id=project_id,
        )


class MemoryJobProjector:
    def __init__(
        self,
        state: InMemoryBrokerState,
        job_lifecycle: BrokerJobLifecycle,
        now: Callable[[], str],
        event_log: "MemoryEventLog",
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
        return [task for task in projections if _matches_project(task, project_id)]

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
            job = job.with_lease(_new_lease(message.to_agent, DEFAULT_JOB_HEARTBEAT_MS, 1))
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
        jobs.extend(conversation_to_wire(conversation) for conversation in self._state.conversations.values() if _matches_project(conversation_to_wire(conversation), project_id))
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
        return [conversation_to_wire(conversation) for conversation in self._state.conversations.values() if _matches_project(conversation_to_wire(conversation), project_id)]


class MemoryWorkQueue:
    def __init__(
        self,
        state: InMemoryBrokerState,
        now: Callable[[], str],
        event_log: "MemoryEventLog",
        session_store: "MemorySessionStore",
        job_projector: "MemoryJobProjector",
        require_peer_sessions: bool,
    ) -> None:
        self._state = state
        self._now = now
        self._event_log = event_log
        self._session_store = session_store
        self._job_projector = job_projector
        self._require_peer_sessions = require_peer_sessions

    @staticmethod
    def peer_offline_detail(stored: StoredMessage) -> dict[str, Any]:
        envelope = stored.envelope
        return {
            "error": "peer_offline",
            "message": f"Recipient session is offline: {envelope.to_agent}",
            "peer": envelope.to_agent,
            "requested_type": envelope.type,
            "requested_task_id": envelope.task_id,
            "requested_conversation_id": envelope.conversation_id,
        }

    def assert_conversation_can_receive_locked(self, stored: StoredMessage) -> None:
        envelope = stored.envelope
        if envelope.type not in {"CHAT_TURN", "CHAT_REPLY"}:
            return
        project_id = str(envelope.project_id or "default")
        conversation_id = str(envelope.conversation_id or "")
        conversation_record = self._state.conversations.get(self._job_projector.conversation_key(project_id, conversation_id))
        if conversation_record is None:
            raise ValueError(f"Conversation not found: {conversation_id}")
        if conversation_record.status != "OPEN":
            raise ValueError(f"Conversation is not open: {conversation_id}")
        if conversation_record.turn >= conversation_record.max_turns:
            raise ValueError(f"Conversation reached max turns: {conversation_id}")

    def busy_detail(self, blocker: dict[str, Any], stored: StoredMessage) -> dict[str, Any]:
        envelope = stored.envelope
        blocking_id = blocker.get("task_id") or blocker.get("conversation_id") or blocker.get("message_id")
        return {
            "error": "worker_busy",
            "message": "Worker already has pending work. Wait for that reply before sending another task or talk turn.",
            "blocking_id": blocking_id,
            "blocking_kind": blocker.get("kind") or ("conversation" if blocker.get("conversation_id") and not blocker.get("task_id") else "task"),
            "blocking_status": blocker.get("status"),
            "blocking_type": blocker.get("message_type") or blocker.get("type"),
            "requested_type": envelope.type,
            "requested_task_id": envelope.task_id,
            "requested_conversation_id": envelope.conversation_id,
        }

    def assert_worker_target_free_locked(self, stored: StoredMessage) -> None:
        envelope = stored.envelope
        message_type = str(envelope.type or "")
        if message_type not in WORKER_BOUND_TYPES:
            return

        to_agent = envelope.to_agent
        project_id = str(envelope.project_id or "default")
        if self._require_peer_sessions and to_agent and self._session_store.active_session_locked(str(to_agent), project_id) is None:
            raise MessageStoreBusy(self.peer_offline_detail(stored))
        for active_stored in self._state.active_messages.values():
            if str(active_stored.envelope.project_id or "default") != project_id:
                continue
            if active_stored.envelope.to_agent != to_agent:
                continue
            if str(active_stored.status or "").upper() not in BUSY_MESSAGE_STATUSES:
                continue
            raise MessageStoreBusy(self.busy_detail(stored_message_to_wire(active_stored), stored))

        conversation_id = str(envelope.conversation_id or "")
        if message_type in {"CHAT_TURN", "CHAT_CLOSE"}:
            return
        for conversation_record in self._state.conversations.values():
            if str(conversation_record.project_id or "default") != project_id:
                continue
            if conversation_record.status != "OPEN":
                continue
            if to_agent not in conversation_record.participants:
                continue
            if conversation_record.conversation_id == conversation_id:
                continue
            raise MessageStoreBusy(self.busy_detail(conversation_to_wire(conversation_record), stored))

    def resolve_task_waiters_locked(self, task_key: str, result: TaskResult) -> None:
        waiters = self._state.task_waiters.pop(task_key, [])
        for future in waiters:
            if not future.done():
                future.set_result(result)

    def store_task_result_locked(self, result: TaskResult) -> dict[str, Any]:
        result_wire = task_result_to_wire(result)
        task_key = self._job_projector.task_key(result.project_id, result.task_id)
        self._state.results_by_task[task_key] = result
        self.resolve_task_waiters_locked(task_key, result)
        self.resolve_task_waiters_locked(result.task_id, result)
        return result_wire

    def register_agent_locked(self, agent: Agent) -> dict[str, Any]:
        agent_id = agent.agent_id
        self._state.agents[agent_id] = agent
        self._state.inboxes.setdefault(agent_id, asyncio.Queue())
        self._event_log.append_event_locked(BrokerEventContext.from_fields(
            "agent_registered",
            project_id=agent.project_id,
            agent_id=agent_id,
            role=agent.role,
            preview=f"registered {agent_id}",
        ))
        return agent_to_wire(agent)

    def enqueue_message_locked(self, stored: StoredMessage, create_waiter: bool = False) -> tuple[dict[str, Any], asyncio.Queue[InboxItem], StoredMessage]:
        """Store a validated `StoredMessage` as the next active message.

        The work queue accepts a `StoredMessage` (validated envelope + broker
        lifecycle metadata) at the storage boundary. Wire-dict consumers use
        the centralized `stored_message_to_wire` serializer; the in-memory
        record stays the canonical domain object.
        """
        envelope = stored.envelope
        message_id = envelope.message_id
        to_agent = envelope.to_agent
        correlation_id = envelope.correlation_id
        self.assert_conversation_can_receive_locked(stored)
        self.assert_worker_target_free_locked(stored)
        # Active messages are stored as `StoredMessage` (validated envelope + broker metadata).
        self._state.active_messages[message_id] = stored
        message_context = MessageProjectionContext.from_stored(stored)
        if envelope.type == "CHAT_CLOSE":
            self._job_projector.touch_conversation_locked(message_context, "CLOSED")
        elif is_talk_message_type(envelope.type):
            self._job_projector.touch_conversation_locked(message_context, None)
        else:
            self._job_projector.upsert_task_locked(message_context, "QUEUED")
        # Wire dict view for downstream event/API boundaries only.
        message = stored_message_to_wire(stored)
        inbox = self._state.inboxes.setdefault(to_agent, asyncio.Queue())
        if create_waiter and envelope.requires_reply:
            self._state.pending_replies.setdefault(
                correlation_id,
                asyncio.get_running_loop().create_future(),
            )
        self._event_log.append_event_locked(
            self._event_log.event_context("message_queued", message, message["status"])
        )
        return {"status": "queued", "message_id": message_id}, inbox, stored

    def inbox_for_agent_locked(self, agent_id: str) -> asyncio.Queue[InboxItem]:
        return self._state.inboxes.setdefault(agent_id, asyncio.Queue())

    def deliver_message_locked(self, item: InboxItem) -> dict[str, Any] | None:
        message_id = item.envelope.message_id
        active_stored = self._state.active_messages.get(message_id)
        if active_stored and is_terminal_status(active_stored.status):
            return None

        now_str = self._now()
        if active_stored is not None:
            next_status = "CLOSED" if str(active_stored.status or "") == "CLOSED" else "DELIVERED"
            active_stored = active_stored.with_status(next_status, now_str)
            self._state.active_messages[message_id] = active_stored
            delivered = stored_message_to_wire(active_stored)
            delivered_context = MessageProjectionContext.from_stored(active_stored, status=next_status, updated_at=now_str)
            if active_stored.envelope.task_id and active_stored.envelope.type == "TASK":
                job = self._job_projector.upsert_task_locked(delivered_context, next_status)
                if job is not None:
                    delivered["lease"] = lease_to_wire(job.lease)
            self._job_projector.touch_conversation_locked(delivered_context, None)
        else:
            delivered = reply_message_to_wire(item)
            if delivered.get("status") != "CLOSED":
                delivered["status"] = "DELIVERED"
            delivered["updated_at"] = now_str
        self._event_log.append_event_locked(
            self._event_log.event_context("message_delivered", delivered, delivered["status"])
        )
        return delivered

    def save_reply_locked(self, message_id: str, reply: StoredMessage, lease_epoch: int | None = None, lease_holder: str | None = None) -> tuple[dict[str, Any], asyncio.Queue[InboxItem] | None, StoredMessage | None]:
        envelope = reply.envelope
        correlation_id = envelope.correlation_id
        reply_wire = reply_message_to_wire(reply)
        job_status = reply_job_status(envelope.type, envelope.status)
        task_id = envelope.task_id
        project_id = str(envelope.project_id or "default")
        # Reject stale-holder replies when the caller asserts a lease epoch.
        # If no epoch is provided, session-lease fencing is the authority.
        if task_id and lease_epoch is not None:
            task_key = self._job_projector.task_key(project_id, str(task_id))
            job = self._state.task_jobs.get(task_key)
            lease = job.lease if job is not None else None
            if lease is not None:
                if not lease.matches(str(lease_holder or ""), int(lease_epoch)):
                    raise LeaseConflictError(
                        f"Stale lease reply for {task_id}: holder/epoch mismatch (lease epoch={lease.epoch}, reply epoch={lease_epoch})."
                    )
        active = self._state.active_messages.get(message_id)
        if active is not None and str(active.status or "").upper() in {"TIMEOUT", "CANCELLED"}:
            self._event_log.append_event_locked(
                self._event_log.event_context(
                    "late_reply_ignored",
                    reply_wire,
                    str(active.status or ""),
                    preview="Late worker reply ignored because the original work is no longer active.",
                )
            )
            return {"status": "reply_ignored", "correlation_id": correlation_id}, None, None

        future = self._state.pending_replies.get(correlation_id)
        if active is not None:
            self._state.active_messages[message_id] = active.with_status(job_status, self._now())
        reply_context = MessageProjectionContext.from_stored(reply, status=job_status)
        if task_id:
            result = TaskResult(status=job_status, project_id=project_id, task_id=str(task_id), reply=reply)
            self._job_projector.upsert_task_locked(reply_context, job_status)
            self.store_task_result_locked(result)
        if is_talk_message_type(envelope.type):
            self._job_projector.touch_conversation_locked(reply_context, "OPEN" if job_status == "DONE" else job_status)
        reply_inbox = self._state.inboxes.setdefault(str(envelope.to_agent), asyncio.Queue())
        self._event_log.append_event_locked(
            self._event_log.event_context("reply_received", reply_wire, job_status)
        )
        if future is not None and not future.done():
            future.set_result(ReplyResult(correlation_id=str(correlation_id), reply=reply))
        return {"status": "reply_received", "correlation_id": correlation_id}, reply_inbox, reply

    def update_message_status_locked(self, message_id: str, normalized_status: str) -> dict[str, Any]:
        stored = self._state.active_messages.get(message_id)
        if stored is None:
            raise ValueError(f"Message not found: {message_id}")
        if is_terminal_status(stored.status):
            return {"status": str(stored.status), "message_id": message_id}
        now_str = self._now()
        updated_stored = stored.with_status(normalized_status, now_str)
        self._state.active_messages[message_id] = updated_stored
        context = MessageProjectionContext.from_stored(updated_stored, status=normalized_status, updated_at=now_str)
        message = stored_message_to_wire(updated_stored)
        if updated_stored.envelope.task_id and updated_stored.envelope.type == "TASK":
            self._job_projector.upsert_task_locked(context, normalized_status)
        if is_talk_message_type(updated_stored.envelope.type):
            self._job_projector.touch_conversation_locked(context, normalized_status)
        self._event_log.append_event_locked(
            self._event_log.event_context("message_status", message, normalized_status)
        )
        return {"status": normalized_status, "message_id": message_id}

    def inactive_work_message_locked(self, item_id: str, project_id: str | None = None) -> str:
        for result in self._state.results_by_task.values():
            if project_id is not None and str(result.project_id or "default") != str(project_id):
                continue
            if result.task_id == item_id:
                return f"No active work found: {item_id} (already {result.status or 'DONE'})."
        for task in self._state.tasks.values():
            if project_id is not None and str(task.project_id or "default") != str(project_id):
                continue
            if task.task_id == item_id:
                status = str(task.status or "UNKNOWN")
                if status.upper() in TERMINAL_MESSAGE_STATUSES:
                    return f"No active work found: {item_id} (already {status})."
        for conversation_record in self._state.conversations.values():
            if project_id is not None and str(conversation_record.project_id or "default") != str(project_id):
                continue
            if conversation_record.conversation_id == item_id:
                status = str(conversation_record.status or "UNKNOWN")
                if status.upper() != "OPEN":
                    return f"No active work found: {item_id} (already {status})."
        return f"No active work found: {item_id}."

    def cancel_work_locked(self, item_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        cancelled: list[str] = []
        targets: list[StoredMessage] = [
            stored
            for stored in self._state.active_messages.values()
            if (project_id is None or str(stored.envelope.project_id or "default") == str(project_id))
            and (
                str(stored.envelope.message_id) == item_id
                or str(stored.envelope.task_id or "") == item_id
                or str(stored.envelope.conversation_id or "") == item_id
            )
            and not is_terminal_status(stored.status)
        ]
        if not targets:
            if project_id is not None:
                conversation = self._state.conversations.get(self._job_projector.conversation_key(project_id, item_id))
            else:
                matches = [record for record in self._state.conversations.values() if record.conversation_id == item_id]
                conversation = matches[0] if len(matches) == 1 else None
            if conversation is not None and conversation.status == "OPEN":
                self._job_projector.touch_conversation_locked(
                    MessageProjectionContext(
                        project_id=conversation.project_id,
                        conversation_id=item_id,
                        task_id=None,
                        message_id="",
                        correlation_id="",
                        from_agent=str(conversation.from_agent or ""),
                        to_agent=str(conversation.to_agent or ""),
                        message_type="CHAT_TURN",
                        status="CANCELLED",
                        turn=conversation.turn,
                        max_turns=conversation.max_turns,
                        delivery="conversation",
                        payload={"summary": reason or "Conversation cancelled."},
                    ),
                    "CANCELLED",
                )
                self._event_log.append_event_locked(BrokerEventContext.from_fields(
                    "work_cancelled",
                    project_id=conversation.project_id,
                    conversation_id=item_id,
                    mode="TALK",
                    status="CANCELLED",
                    preview=reason or "Conversation cancelled.",
                ))
                return {"status": "cancelled", "item_id": item_id, "cancelled": [item_id]}
            raise ValueError(self.inactive_work_message_locked(item_id, project_id=project_id))

        for stored in targets:
            now_str = self._now()
            new_stored = stored.with_status("CANCELLED", now_str)
            self._state.active_messages[new_stored.envelope.message_id] = new_stored
            message = stored_message_to_wire(new_stored)
            context = MessageProjectionContext.from_stored(new_stored, status="CANCELLED", updated_at=now_str)
            message_id = new_stored.envelope.message_id
            if message_id:
                cancelled.append(message_id)
            task_id = new_stored.envelope.task_id
            if task_id:
                result = TaskResult(
                    status="CANCELLED",
                    project_id=new_stored.envelope.project_id,
                    task_id=str(task_id),
                    error=reason or "Work was cancelled.",
                    job=new_stored,
                )
                self._job_projector.upsert_task_locked(context, "CANCELLED")
                self.store_task_result_locked(result)
            if is_talk_message_type(new_stored.envelope.type):
                self._job_projector.touch_conversation_locked(context, "CANCELLED")
            future = self._state.pending_replies.get(str(new_stored.envelope.correlation_id or ""))
            if future is not None and not future.done():
                future.set_result(WaitBlocker(
                    status="CANCELLED",
                    correlation_id=str(new_stored.envelope.correlation_id or ""),
                    error=reason or "Work was cancelled.",
                    summary=reason or "Work was cancelled.",
                ))
            self._event_log.append_event_locked(
                self._event_log.event_context(
                    "work_cancelled",
                    message,
                    "CANCELLED",
                    preview=reason or "Work was cancelled.",
                )
            )
        return {"status": "cancelled", "item_id": item_id, "cancelled": cancelled}

    def close_conversation_locked(self, conversation_id: str, stored: StoredMessage) -> dict[str, Any]:
        envelope = stored.envelope
        project_id = str(envelope.project_id or "default")
        conversation_record = self._state.conversations.get(self._job_projector.conversation_key(project_id, conversation_id))
        if conversation_record is None:
            matches = [record for record in self._state.conversations.values() if record.conversation_id == conversation_id]
            conversation_record = matches[0] if len(matches) == 1 else None
        if conversation_record is None:
            raise ValueError(f"Conversation not found: {conversation_id}")
        turn = min(max(int(envelope.turn or 1), int(conversation_record.turn or 1) + 1), int(conversation_record.max_turns or envelope.max_turns or 6))
        close_context = MessageProjectionContext.from_stored(stored, status="CLOSED")
        close_context = replace(
            close_context,
            project_id=conversation_record.project_id,
            conversation_id=conversation_id,
            from_agent=close_context.from_agent or str(conversation_record.from_agent or ""),
            to_agent=close_context.to_agent or str(conversation_record.to_agent or ""),
            message_type="CHAT_CLOSE",
            status="CLOSED",
            turn=turn,
            max_turns=conversation_record.max_turns,
            delivery="conversation",
        )
        self._job_projector.touch_conversation_locked(close_context, "CLOSED")
        self._event_log.append_event_locked(BrokerEventContext.from_fields(
            "conversation_closed",
            project_id=conversation_record.project_id,
            conversation_id=conversation_id,
            from_agent=close_context.from_agent,
            to_agent=close_context.to_agent,
            message_type="CHAT_CLOSE",
            mode="TALK",
            delivery="conversation",
            status="CLOSED",
            payload=close_context.payload,
        ))
        return {"status": "closed", "conversation_id": conversation_id}

    def reply_future_locked(self, correlation_id: str) -> asyncio.Future[ReplyResult | WaitBlocker]:
        return self._state.pending_replies.setdefault(
            correlation_id,
            asyncio.get_running_loop().create_future(),
        )

    def timeout_reply_locked(self, correlation_id: str) -> None:
        stored_match = next(
            (stored for stored in self._state.active_messages.values() if stored.envelope.correlation_id == correlation_id),
            None,
        )
        if stored_match is not None:
            now_str = self._now()
            new_stored = stored_match.with_status("TIMEOUT", now_str)
            self._state.active_messages[new_stored.envelope.message_id] = new_stored
            message = stored_message_to_wire(new_stored)
            context = MessageProjectionContext.from_stored(new_stored, status="TIMEOUT", updated_at=now_str)
            if new_stored.envelope.task_id:
                self._job_projector.upsert_task_locked(context, "TIMEOUT")
            if is_talk_message_type(new_stored.envelope.type):
                self._job_projector.touch_conversation_locked(context, "TIMEOUT")
        else:
            message: dict[str, Any] = {}
        self._event_log.append_event_locked(
            self._event_log.event_context(
                "timeout",
                message,
                "TIMEOUT",
                preview="Worker did not reply before timeout.",
            )
        )

    def cleanup_reply_waiter_locked(self, correlation_id: str, future: asyncio.Future[ReplyResult | WaitBlocker]) -> None:
        if self._state.pending_replies.get(correlation_id) is future:
            self._state.pending_replies.pop(correlation_id, None)

    def prepare_task_wait_locked(self, task_id: str, project_id: str | None = None) -> tuple[TaskResult | WaitBlocker | None, str, asyncio.Future[TaskResult] | None]:
        task_key = self._job_projector.task_key(project_id, task_id) if project_id is not None else task_id
        if project_id is not None and task_key in self._state.results_by_task:
            return self._state.results_by_task[task_key], task_key, None
        if project_id is not None and not self._job_projector.has_task_projection_locked(task_key):
            return WaitBlocker(status="missing", project_id=project_id, task_id=task_id, error="Task not found."), task_key, None
        if project_id is None:
            matches = [result for key, result in self._state.results_by_task.items() if key.endswith(f":{task_id}") or key == task_id]
            if len(matches) == 1:
                return matches[0], task_key, None
        future: asyncio.Future[TaskResult] = asyncio.get_running_loop().create_future()
        self._state.task_waiters.setdefault(task_key, []).append(future)
        return None, task_key, future

    def cleanup_task_waiter_locked(self, task_key: str, future: asyncio.Future[TaskResult]) -> None:
        waiters = self._state.task_waiters.get(task_key, [])
        self._state.task_waiters[task_key] = [item for item in waiters if item is not future]
        if not self._state.task_waiters[task_key]:
            self._state.task_waiters.pop(task_key, None)

    def list_active_messages_locked(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return [dict(stored_message_to_wire(stored)) for stored in self._state.active_messages.values() if _matches_project(stored_message_to_wire(stored), project_id)]

    def pending_reply_count_locked(self) -> int:
        return len(self._state.pending_replies)


class MemoryMessageStore(MessageStore):
    def __init__(self, require_peer_sessions: bool = False, session_grace_seconds: int = 25) -> None:
        self.require_peer_sessions = require_peer_sessions
        self.session_grace_seconds = session_grace_seconds
        self._state = InMemoryBrokerState()
        # Observability-only audit journal (M1); attached via attach_journal.
        self.journal: Any = None
        self._job_lifecycle = BrokerJobLifecycle()
        # Components are wired with direct references (state, clock, and
        # each other) rather than callbacks. Cross-component refs are
        # established in dependency order below so every focused component
        # owns its own logic without the facade acting as a proxy.
        self._event_log = MemoryEventLog(self._state, self._now)
        self._job_projector = MemoryJobProjector(
            self._state,
            self._job_lifecycle,
            self._now,
            self._event_log,
        )
        self._session_store = MemorySessionStore(
            self._state,
            self._now,
            self._parse_time,
            self._event_log,
            session_grace_seconds,
        )
        self._activity_store = MemoryActivityStore(
            self._state,
            self._event_log,
            self._now,
            self._apply_activity_to_work_locked,
        )
        self._work_queue = MemoryWorkQueue(
            self._state,
            self._now,
            self._event_log,
            self._session_store,
            self._job_projector,
            require_peer_sessions,
        )
        self._lock = asyncio.Lock()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _parse_time(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _active_job_count_locked(self, project_id: str | None = None) -> int:
        return sum(
            1 for stored in self._state.active_messages.values()
            if (project_id is None or str(stored.envelope.project_id or "default") == str(project_id)) and is_busy_status(stored.status)
        )

    def attach_journal(self, journal: Any) -> None:
        """Wire the observability audit journal (M1).

        Once attached, every broker event recorded via
        ``MemoryEventLog.append_event_locked`` is mirrored into the journal.
        The journal is observability-only and never the source of truth.
        """
        self.journal = journal
        self._event_log._journal = journal

    def _settle_work_for_offline_agent_locked(self, agent_id: str, project_id: str, reason: str) -> list[str]:
        settled: list[str] = []
        for stored in list(self._state.active_messages.values()):
            if str(stored.envelope.project_id or "default") != str(project_id):
                continue
            if stored.envelope.from_agent != agent_id and stored.envelope.to_agent != agent_id:
                continue
            if not is_busy_status(stored.status):
                continue
            now_str = self._now()
            new_stored = stored.with_status("CANCELLED", now_str)
            self._state.active_messages[new_stored.envelope.message_id] = new_stored
            message = stored_message_to_wire(new_stored)
            context = MessageProjectionContext.from_stored(new_stored, status="CANCELLED", updated_at=now_str)
            task_id = new_stored.envelope.task_id
            if task_id:
                result = TaskResult(
                    status="CANCELLED",
                    project_id=new_stored.envelope.project_id,
                    task_id=str(task_id),
                    error=reason,
                    job=new_stored,
                )
                self._job_projector.upsert_task_locked(context, "CANCELLED")
                self._work_queue.store_task_result_locked(result)
                settled.append(str(task_id))
            if is_talk_message_type(new_stored.envelope.type):
                self._job_projector.touch_conversation_locked(context, status="CANCELLED")
                if new_stored.envelope.conversation_id:
                    settled.append(str(new_stored.envelope.conversation_id))
            future = self._state.pending_replies.get(str(new_stored.envelope.correlation_id or ""))
            if future is not None and not future.done():
                future.set_result(WaitBlocker(
                    status="CANCELLED",
                    correlation_id=str(new_stored.envelope.correlation_id or ""),
                    error="peer_offline",
                    summary=reason,
                    reason=reason,
                ))
            self._event_log.append_event_locked(
                self._event_log.event_context(
                    "work_cancelled",
                    message,
                    "CANCELLED",
                    preview=reason,
                )
            )
        return settled

    def _expire_sessions_locked(self) -> list[dict[str, Any]]:
        return self._session_store.expire_sessions_locked(self._settle_work_for_offline_agent_locked)

    def _expire_timed_out_messages_locked(self) -> None:
        self._expire_sessions_locked()
        now = datetime.now(timezone.utc)
        for stored in list(self._state.active_messages.values()):
            message_id = stored.envelope.message_id
            status = str(stored.status or "").upper()
            if status not in BUSY_MESSAGE_STATUSES:
                continue
            timeout_seconds = int(stored.envelope.timeout_seconds or 0)
            if timeout_seconds <= 0:
                continue
            started_at = self._parse_time(stored.created_at or stored.queued_at)
            if started_at is None:
                continue
            if (now - started_at).total_seconds() < timeout_seconds:
                continue

            now_str = self._now()
            new_stored = stored.with_status("TIMEOUT", now_str)
            self._state.active_messages[message_id] = new_stored
            message = stored_message_to_wire(new_stored)
            context = MessageProjectionContext.from_stored(new_stored, status="TIMEOUT", updated_at=now_str)
            task_id = new_stored.envelope.task_id
            if task_id:
                result = TaskResult(
                    status="TIMEOUT",
                    project_id=str(new_stored.envelope.project_id or "default"),
                    task_id=str(task_id),
                    error="Task exceeded its timeout before the worker replied.",
                    job=new_stored,
                )
                self._job_projector.upsert_task_locked(context, "TIMEOUT")
                self._work_queue.store_task_result_locked(result)
            if is_talk_message_type(new_stored.envelope.type):
                self._job_projector.touch_conversation_locked(context, status="TIMEOUT")
            future = self._state.pending_replies.get(str(new_stored.envelope.correlation_id or ""))
            if future is not None and not future.done():
                future.set_result(WaitBlocker(
                    status="TIMEOUT",
                    correlation_id=str(new_stored.envelope.correlation_id or ""),
                    error="Worker did not reply before the timeout.",
                    summary="Worker did not reply before the timeout.",
                ))
            self._event_log.append_event_locked(
                self._event_log.event_context(
                    "timeout",
                    message,
                    "TIMEOUT",
                    preview="Work exceeded the timeout before the worker replied.",
                )
            )

    def _apply_activity_to_work_locked(self, activity: ActivityRecord, timestamp: str) -> None:
        project_id = str(activity.project_id or "default")
        task_id = str(activity.task_id or "")
        conversation_id = str(activity.conversation_id or "")
        message_id = str(activity.message_id or "")
        preview = str(activity.detail or activity.phase or activity.activity_type or "")[:300]

        if conversation_id:
            conversation_key = self._job_projector.conversation_key(project_id, conversation_id)
            conversation = self._state.conversations.get(conversation_key)
            if conversation is not None:
                touched = conversation.touch(
                    activity_at=timestamp,
                    activity_type=activity.activity_type,
                    activity_tool=activity.tool_name,
                    activity_preview=preview,
                    now=timestamp,
                )
                self._state.conversations[conversation_key] = touched
                talk_job = self._state.talk_jobs.get(conversation_key)
                if talk_job is not None:
                    current_payload = talk_job.payload if isinstance(talk_job.payload, TalkJobPayload) else TalkJobPayload()
                    payload = replace(
                        current_payload,
                        updated_at=timestamp,
                        last_activity_at=timestamp,
                        last_activity_type=activity.activity_type,
                        last_activity_tool=activity.tool_name,
                        last_activity_preview=preview,
                    )
                    talk_job = self._job_lifecycle.talk.with_payload(talk_job, payload, turn=talk_job.turn, max_turns=talk_job.max_turns)
                    self._state.talk_jobs[conversation_key] = talk_job
                    self._state.conversations[conversation_key] = conversation_from_wire(talk_job_to_wire(talk_job))

        target_stored: StoredMessage | None = None
        if message_id:
            target_stored = self._state.active_messages.get(message_id)
        if target_stored is None:
            target_stored = next(
                (
                    stored
                    for stored in self._state.active_messages.values()
                    if str(stored.envelope.project_id or "default") == project_id
                    and (
                        (task_id and str(stored.envelope.task_id or "") == task_id)
                        or (conversation_id and str(stored.envelope.conversation_id or "") == conversation_id)
                    )
                ),
                None,
            )
        if target_stored is not None and str(target_stored.status or "").upper() in BUSY_MESSAGE_STATUSES:
            new_stored = target_stored.with_status("RUNNING", timestamp)
            self._state.active_messages[new_stored.envelope.message_id] = new_stored
            context = MessageProjectionContext.from_stored(
                new_stored,
                status="RUNNING",
                updated_at=timestamp,
                last_activity_at=timestamp,
                last_activity_type=activity.activity_type,
                last_activity_tool=activity.tool_name,
                last_activity_preview=preview,
            )
            if new_stored.envelope.task_id and new_stored.envelope.type == "TASK":
                self._job_projector.upsert_task_locked(context, "RUNNING")

        if task_id:
            task_key = self._job_projector.task_key(project_id, task_id)
            task = self._job_projector.task_projection_locked(task_key)
            if task:
                updates = {
                    "updated_at": timestamp,
                    "last_activity_at": timestamp,
                    "last_activity_type": activity.activity_type,
                    "last_activity_tool": activity.tool_name,
                    "last_activity_preview": preview,
                }
                if str(task.status or "").upper() in BUSY_MESSAGE_STATUSES:
                    updates["status"] = "RUNNING"
                self._job_projector.update_task_projection_locked(task_key, updates)

    def _coerce_to_agent(self, agent: AgentInput) -> Agent:
        return agent_input_to_agent(agent)

    async def register_agent(self, agent: AgentInput) -> dict[str, Any]:
        stored_agent = self._coerce_to_agent(agent)
        async with self._lock:
            return self._work_queue.register_agent_locked(stored_agent)

    def _coerce_to_stored(self, message: MessageInput) -> StoredMessage:
        """Convert an enqueue input into the active-message domain record."""
        return message_input_to_stored(message, now=self._now())

    async def enqueue_message(
        self,
        message: MessageInput,
        create_waiter: bool = False,
    ) -> dict[str, Any]:
        stored = self._coerce_to_stored(message)
        async with self._lock:
            self._expire_timed_out_messages_locked()
            result, inbox, stored_message = self._work_queue.enqueue_message_locked(stored, create_waiter=create_waiter)

        await inbox.put(stored_message)
        return result

    async def get_next_message(
        self,
        agent_id: str,
        wait_seconds: int,
        lease_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any] | None:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            self._session_store.assert_poll_lease_locked(agent_id, project_id=project_id, lease_id=lease_id)
            inbox = self._work_queue.inbox_for_agent_locked(agent_id)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        while True:
            timeout = max(0, deadline - loop.time()) if wait_seconds > 0 else 0
            try:
                message = await asyncio.wait_for(inbox.get(), timeout=timeout)
            except asyncio.TimeoutError:
                return None

            async with self._lock:
                self._expire_timed_out_messages_locked()
                self._session_store.assert_poll_lease_locked(agent_id, project_id=project_id, lease_id=lease_id)
                delivered = self._work_queue.deliver_message_locked(message)
                if delivered is None:
                    if wait_seconds <= 0 or loop.time() >= deadline:
                        return None
                    continue
            return delivered

    async def save_reply(
        self,
        message_id: str,
        reply: MessageInput,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
        session_lease_id: str | None = None,
    ) -> dict[str, Any]:
        stored_reply = self._coerce_to_stored(reply)
        async with self._lock:
            self._expire_timed_out_messages_locked()
            if session_lease_id:
                self._session_store.assert_active_session_lease_locked(
                    str(stored_reply.envelope.from_agent or ""),
                    project_id=str(stored_reply.envelope.project_id or "default"),
                    lease_id=session_lease_id,
                )
            result, reply_inbox, reply_message = self._work_queue.save_reply_locked(message_id, stored_reply, lease_epoch=lease_epoch, lease_holder=lease_holder)

        if reply_inbox is not None and reply_message is not None:
            await reply_inbox.put(reply_message)
        return result

    async def update_message_status(
        self,
        message_id: str,
        status: str,
        session_lease_id: str | None = None,
    ) -> dict[str, Any]:
        # Use the canonical status normalizer from `core.states` so the
        # storage layer agrees with the domain status vocabulary rather than
        # re-implementing ad-hoc upper-casing.
        from orchlink.core.states import normalize_status

        normalized_status = normalize_status(status)
        if normalized_status not in BUSY_MESSAGE_STATUSES | TERMINAL_MESSAGE_STATUSES:
            raise ValueError(f"Unsupported status: {status}")
        async with self._lock:
            self._expire_timed_out_messages_locked()
            if session_lease_id:
                stored = self._state.active_messages.get(message_id)
                if stored is not None:
                    self._session_store.assert_active_session_lease_locked(
                        str(stored.envelope.to_agent or ""),
                        project_id=str(stored.envelope.project_id or "default"),
                        lease_id=session_lease_id,
                    )
            return self._work_queue.update_message_status_locked(message_id, normalized_status)

    async def record_activity(self, activity: ActivityInput) -> dict[str, Any]:
        command = worker_activity_from_wire(activity)
        async with self._lock:
            self._expire_timed_out_messages_locked()
            session_lease_id = str(command.session_lease_id or "")
            if session_lease_id:
                self._session_store.assert_active_session_lease_locked(
                    str(command.agent_id or ""),
                    project_id=str(command.project_id or "default"),
                    lease_id=session_lease_id,
                )
            return self._activity_store.record_activity_locked(command)

    async def list_activity(
        self,
        item_id: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return self._activity_store.list_activity_locked(
                item_id=item_id,
                limit=limit,
                project_id=project_id,
            )

    async def acquire_session(self, session: SessionAcquireInput) -> dict[str, Any]:
        command = session_acquire_from_wire(session)
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._session_store.acquire_session_locked(command)

    async def heartbeat_session(
        self,
        lease_id: str,
        project_id: str | None = None,
        heartbeat: SessionHeartbeatInput | None = None,
    ) -> dict[str, Any]:
        command = session_heartbeat_from_wire(lease_id, project_id=project_id, heartbeat=heartbeat)
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._session_store.heartbeat_session_locked(command)

    async def release_session(self, lease_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        command = session_release_from_wire(lease_id, reason=reason, project_id=project_id)
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._session_store.release_session_locked(
                command,
                on_session_ended=self._settle_work_for_offline_agent_locked,
            )

    async def expire_sessions(self) -> list[dict[str, Any]]:
        async with self._lock:
            return self._expire_sessions_locked()

    async def list_sessions(self, project_id: str | None = None, active: bool = False) -> list[dict[str, Any]]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._session_store.list_sessions_locked(project_id=project_id, active=active)

    async def can_auto_stop(self, project_id: str | None = None) -> bool:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._session_store.active_session_count_locked(project_id) == 0 and self._active_job_count_locked(project_id) == 0

    async def cancel_work(self, item_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._work_queue.cancel_work_locked(item_id, reason=reason, project_id=project_id)

    def _find_task_job_locked(self, task_id: str, project_id: str | None = None) -> tuple[str, Job]:
        if project_id is not None:
            task_key = self._job_projector.task_key(project_id, task_id)
            job = self._state.task_jobs.get(task_key)
            if job is not None:
                return task_key, job
        matches = [(key, job) for key, job in self._state.task_jobs.items() if key.endswith(f":{task_id}") or key == task_id]
        if len(matches) == 1:
            return matches[0]
        raise ValueError(f"Job not found: {task_id}")

    def heartbeat_job_locked(
        self,
        task_id: str,
        holder: str,
        epoch: int,
        project_id: str | None = None,
        heartbeat_ms: int | None = None,
    ) -> dict[str, Any]:
        task_key, job = self._find_task_job_locked(task_id, project_id=project_id)
        lease = job.lease
        if lease is None:
            raise LeaseConflictError(f"Job {task_id} has no lease to renew.")
        if not lease.matches(str(holder), int(epoch)):
            raise LeaseConflictError(f"Stale lease heartbeat for {task_id}: holder/epoch mismatch.")
        hb = heartbeat_ms or lease.heartbeat_ms or DEFAULT_JOB_HEARTBEAT_MS
        new_lease = lease.renew(heartbeat_ms=hb, grace_multiplier=JOB_LEASE_GRACE_MULTIPLIER)
        job = job.with_lease(new_lease)
        self._state.task_jobs[task_key] = job
        self._job_projector.store_task_projection_locked(task_key, job)
        self._event_log.append_event_locked(BrokerEventContext.from_fields(
            "job_heartbeat",
            project_id=job.project_id,
            task_id=str(task_id),
            from_agent=holder,
            to_agent=holder,
            message_type="LEASE",
            mode=job.mode,
            status=job.status,
            payload={},
            preview=f"lease renewed epoch={epoch}",
        ))
        return {"status": "renewed", "task_id": task_id, "lease": lease_to_wire(new_lease)}

    def reclaim_job_locked(self, task_id: str, holder: str, project_id: str | None = None) -> dict[str, Any]:
        task_key, job = self._find_task_job_locked(task_id, project_id=project_id)
        lease = job.lease
        if lease is None:
            raise LeaseConflictError(f"Job {task_id} is not reclaimable (no lease).")
        now = datetime.now(timezone.utc)
        if lease.is_active(now):
            # Idempotent: the current holder reclaiming a still-valid lease is a no-op.
            if lease.holder == str(holder):
                return {"status": "active", "task_id": task_id, "lease": lease_to_wire(lease), "reclaimed": False}
            raise LeaseConflictError(f"Job {task_id} lease has not expired.")
        new_lease = lease.reclaim(str(holder), grace_multiplier=JOB_LEASE_GRACE_MULTIPLIER)
        # Transition RUNNING/DELIVERED -> RECLAIMABLE -> RUNNING with the new lease.
        job = job.reclaim_with_lease(new_lease)
        self._state.task_jobs[task_key] = job
        self._job_projector.store_task_projection_locked(task_key, job)
        self._event_log.append_event_locked(BrokerEventContext.from_fields(
            "job_reclaimed",
            project_id=job.project_id,
            task_id=str(task_id),
            from_agent=holder,
            to_agent=holder,
            message_type="LEASE",
            mode=job.mode,
            status=job.status,
            payload={},
            preview=f"lease reclaimed epoch={new_lease.epoch}",
        ))
        return {"status": "reclaimed", "task_id": task_id, "lease": lease_to_wire(new_lease), "reclaimed": True}

    async def heartbeat_job(
        self,
        task_id: str,
        holder: str,
        epoch: int,
        project_id: str | None = None,
        heartbeat_ms: int | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self.heartbeat_job_locked(task_id, holder, epoch, project_id=project_id, heartbeat_ms=heartbeat_ms)

    async def reclaim_job(self, task_id: str, holder: str, project_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self.reclaim_job_locked(task_id, holder, project_id=project_id)

    async def close_conversation(self, conversation_id: str, message: MessageInput) -> dict[str, Any]:
        stored = self._coerce_to_stored(message)
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._work_queue.close_conversation_locked(conversation_id, stored)

    async def wait_for_reply(self, correlation_id: str, timeout_seconds: int) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            future = self._work_queue.reply_future_locked(correlation_id)

        try:
            reply_result = await asyncio.wait_for(future, timeout=timeout_seconds)
            if isinstance(reply_result, ReplyResult):
                return reply_result_to_wire(reply_result)
            return wait_blocker_to_wire(reply_result)
        except asyncio.TimeoutError:
            async with self._lock:
                self._work_queue.timeout_reply_locked(correlation_id)
            return {
                "status": "timeout",
                "correlation_id": correlation_id,
                "error": "Worker did not reply before timeout.",
            }
        finally:
            if future.done() or future.cancelled():
                async with self._lock:
                    self._work_queue.cleanup_reply_waiter_locked(correlation_id, future)

    async def wait_for_task(self, task_id: str, timeout_seconds: int, project_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            result, task_key, future = self._work_queue.prepare_task_wait_locked(task_id, project_id=project_id)
            if isinstance(result, TaskResult):
                return task_result_to_wire(result)
            if isinstance(result, WaitBlocker):
                return wait_blocker_to_wire(result)
            assert future is not None

        try:
            completed = await asyncio.wait_for(future, timeout=timeout_seconds)
            return task_result_to_wire(completed)
        except asyncio.TimeoutError:
            async with self._lock:
                self._work_queue.cleanup_task_waiter_locked(task_key, future)
            return {"status": "WAIT_TIMEOUT", "project_id": project_id, "task_id": task_id, "error": "No task result arrived before the wait timeout."}

    async def get_task_result(self, task_id: str, project_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._job_projector.get_task_result_locked(task_id, project_id=project_id)

    async def list_jobs(
        self,
        limit: int = 50,
        project_id: str | None = None,
        active: bool = False,
        status: str | None = None,
        kind: str | None = None,
        item_id: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._job_projector.list_jobs_locked(
                limit=limit,
                project_id=project_id,
                active=active,
                status=status,
                kind=kind,
                item_id=item_id,
            )

    async def list_agents(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [agent_to_wire(agent) for agent in self._state.agents.values()]

    async def list_active_messages(self, project_id: str | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._work_queue.list_active_messages_locked(project_id=project_id)

    async def list_conversations(self, project_id: str | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._job_projector.list_conversations_locked(project_id=project_id)

    async def list_events(self, since: int = 0, limit: int = 100, project_id: str | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            return self._event_log.list_events_locked(since=since, limit=limit, project_id=project_id)

    async def pending_reply_count(self) -> int:
        async with self._lock:
            return self._work_queue.pending_reply_count_locked()
