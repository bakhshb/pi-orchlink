import asyncio
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
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
from orchlink.broker.state_machine import BrokerStateMachine
from orchlink.broker.storage.base import LeaseConflictError, MessageStore, MessageStoreBusy
from orchlink.core.models import Job, Session
from orchlink.core.states import JobStatus, require_transition
from orchlink.core.views import lease_to_wire, talk_job_to_wire, task_job_to_wire



DEFAULT_JOB_HEARTBEAT_MS = 15000
JOB_LEASE_GRACE_MULTIPLIER = 6


def _new_lease(holder: str, heartbeat_ms: int | None, epoch: int) -> dict[str, Any]:
    """Build a fresh lease dict with an expiry derived from the heartbeat interval."""
    hb = max(int(heartbeat_ms or DEFAULT_JOB_HEARTBEAT_MS), 1000)
    ttl_ms = hb * JOB_LEASE_GRACE_MULTIPLIER
    expires_at = (datetime.now(timezone.utc) + timedelta(milliseconds=ttl_ms)).isoformat()
    return {"holder": holder, "expires_at": expires_at, "epoch": epoch, "heartbeat_ms": hb}


@dataclass
class InMemoryBrokerState:
    agents: dict[str, dict[str, Any]] = field(default_factory=dict)
    inboxes: dict[str, asyncio.Queue[dict[str, Any]]] = field(default_factory=dict)
    active_messages: dict[str, dict[str, Any]] = field(default_factory=dict)
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    task_jobs: dict[str, Job] = field(default_factory=dict)
    results_by_task: dict[str, dict[str, Any]] = field(default_factory=dict)
    conversations: dict[str, dict[str, Any]] = field(default_factory=dict)
    talk_jobs: dict[str, Job] = field(default_factory=dict)
    pending_replies: dict[str, asyncio.Future[dict[str, Any]]] = field(default_factory=dict)
    task_waiters: dict[str, list[asyncio.Future[dict[str, Any]]]] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    activity: list[dict[str, Any]] = field(default_factory=list)
    sessions: dict[str, dict[str, Any]] = field(default_factory=dict)
    next_event_id: int = 1
    next_activity_id: int = 1


class MemoryEventLog:
    def __init__(
        self,
        state: InMemoryBrokerState,
        now: Callable[[], str],
        job_mode: Callable[[dict[str, Any]], str],
        same_project: Callable[[dict[str, Any], str | None], bool],
    ) -> None:
        self._state = state
        self._now = now
        self._job_mode = job_mode
        self._same_project = same_project
        # Observability-only audit journal (M1). Late-bound via attach_journal
        # so the journal can be wired after the store is constructed.
        self._journal: Any = None

    def payload_preview(self, payload: dict[str, Any]) -> str:
        for key in ("message", "intent", "topic", "summary", "stdout"):
            value = payload.get(key)
            if value:
                return str(value)
        return ""

    def message_preview(self, message: dict[str, Any]) -> str:
        return self.payload_preview(message.get("payload") or {})

    def append_event_locked(self, event_type: str, **fields: Any) -> dict[str, Any]:
        payload = fields.get("payload") or {}
        preview = fields.pop("preview", None)
        if preview is None and isinstance(payload, dict):
            preview = self.payload_preview(payload)
        event = {
            "id": self._state.next_event_id,
            "time": self._now(),
            "type": event_type,
            "preview": str(preview or "")[:300],
            **fields,
        }
        job_event = canonical_job_event_for_broker_event(event_type, fields)
        if job_event is not None:
            event["job_event"] = job_event
        self._state.next_event_id += 1
        self._state.events.append(event)
        if len(self._state.events) > 1000:
            self._state.events = self._state.events[-1000:]
        if self._journal is not None:
            try:
                self._journal.record_broker_event(event)
            except Exception:
                # Observability-only: never fail a transition for the journal.
                pass
        return event

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
            "mode": self._job_mode(message),
            "delivery": message.get("delivery"),
            "status": status or message.get("status"),
            "turn": message.get("turn"),
            "max_turns": message.get("max_turns"),
            "payload": payload,
        }

    def activity_preview(self, activity: dict[str, Any]) -> str:
        detail = str(activity.get("detail") or activity.get("phase") or activity.get("activity_type") or "")
        tool_name = str(activity.get("tool_name") or "")
        if tool_name and detail:
            return f"{tool_name}: {detail}"
        return tool_name or detail

    def record_activity_locked(
        self,
        activity: dict[str, Any],
        apply_activity_to_work: Callable[[dict[str, Any], str], None],
    ) -> dict[str, Any]:
        timestamp = self._now()
        stored = {
            "id": self._state.next_activity_id,
            "time": timestamp,
            "project_id": str(activity.get("project_id") or "default"),
            "task_id": activity.get("task_id"),
            "conversation_id": activity.get("conversation_id"),
            "message_id": activity.get("message_id"),
            "agent_id": activity.get("agent_id"),
            "activity_type": str(activity.get("activity_type") or "activity"),
            "phase": activity.get("phase"),
            "tool_name": activity.get("tool_name"),
            "detail": str(activity.get("detail") or "")[:500],
            "status": str(activity.get("status") or "RUNNING"),
            "mode": activity.get("mode"),
        }
        self._state.next_activity_id += 1
        self._state.activity.append(stored)
        if len(self._state.activity) > 1000:
            self._state.activity = self._state.activity[-1000:]
        apply_activity_to_work(stored, timestamp)
        self.append_event_locked(
            "worker_activity",
            project_id=stored.get("project_id"),
            task_id=stored.get("task_id"),
            conversation_id=stored.get("conversation_id"),
            message_id=stored.get("message_id"),
            from_agent=stored.get("agent_id"),
            message_type="ACTIVITY",
            mode=stored.get("mode"),
            status=stored.get("status"),
            payload=stored,
            preview=self.activity_preview(stored),
        )
        return {"status": "recorded", "activity_id": stored["id"]}

    def list_activity_locked(
        self,
        item_id: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        selected = [dict(item) for item in self._state.activity if self._same_project(item, project_id)]
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
            dict(event)
            for event in self._state.events
            if int(event["id"]) > since and self._same_project(event, project_id)
        ]
        return selected[-limit:]


class MemorySessionRegistry:
    def __init__(
        self,
        state: InMemoryBrokerState,
        now: Callable[[], str],
        parse_time: Callable[[Any], datetime | None],
        same_project: Callable[[dict[str, Any], str | None], bool],
        append_event: Callable[..., dict[str, Any]],
        session_grace_seconds: int,
    ) -> None:
        self._state = state
        self._now = now
        self._parse_time = parse_time
        self._same_project = same_project
        self._append_event = append_event
        self._session_grace_seconds = session_grace_seconds

    def active_session_locked(self, agent_id: str, project_id: str | None = None) -> dict[str, Any] | None:
        for session in self._state.sessions.values():
            if not self._same_project(session, project_id):
                continue
            if session.get("agent_id") != agent_id:
                continue
            if is_active_session_status(session.get("status")):
                return session
        return None

    def active_session_count_locked(self, project_id: str | None = None) -> int:
        return sum(1 for session in self._state.sessions.values() if self._same_project(session, project_id) and is_active_session_status(session.get("status")))

    def expire_sessions_locked(self, on_session_ended: Callable[[str, str, str], list[str]]) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        expired: list[dict[str, Any]] = []
        for session in list(self._state.sessions.values()):
            if not is_active_session_status(session.get("status")):
                continue
            last_seen = self._parse_time(session.get("last_heartbeat_at") or session.get("updated_at"))
            if last_seen is None:
                continue
            grace = int(session.get("lease_grace_seconds") or self._session_grace_seconds)
            if (now - last_seen).total_seconds() < grace:
                continue
            session["status"] = "EXPIRED"
            session["ended_at"] = self._now()
            session["updated_at"] = session["ended_at"]
            reason = f"Session heartbeat expired: {session.get('agent_id')}"
            session["ended_reason"] = reason
            settled = on_session_ended(str(session.get("agent_id")), str(session.get("project_id") or "default"), reason)
            session["settled_work"] = settled
            expired.append(dict(session))
            self._append_event(
                "session_expired",
                project_id=session.get("project_id"),
                agent_id=session.get("agent_id"),
                role=session.get("role"),
                lease_id=session.get("lease_id"),
                status="EXPIRED",
                payload=session,
                preview=reason,
            )
        return expired

    def acquire_session_locked(self, session: dict[str, Any]) -> dict[str, Any]:
        now = self._now()
        lease_id = str(session.get("lease_id") or f"lease-{uuid.uuid4()}")
        stored = asdict(
            Session(
                lease_id=lease_id,
                project_id=str(session.get("project_id") or "default"),
                agent_id=str(session.get("agent_id") or ""),
                role=str(session.get("role") or "work"),
                pid=session.get("pid"),
                session_id=session.get("session_id"),
                status="ACTIVE",
                created_at=now,
                updated_at=now,
                last_heartbeat_at=now,
                lease_grace_seconds=int(session.get("lease_grace_seconds") or self._session_grace_seconds),
            )
        )
        self._state.sessions[lease_id] = stored
        self._append_event(
            "session_acquired",
            project_id=stored["project_id"],
            agent_id=stored["agent_id"],
            role=stored["role"],
            lease_id=lease_id,
            status="ACTIVE",
            payload=stored,
            preview=f"session active {stored['agent_id']}",
        )
        return dict(stored)

    def heartbeat_session_locked(self, lease_id: str, project_id: str | None = None) -> dict[str, Any]:
        session = self._state.sessions.get(lease_id)
        if session is None or not self._same_project(session, project_id):
            raise ValueError(f"Session not found: {lease_id}")
        if not is_active_session_status(session.get("status")):
            return dict(session)
        now = self._now()
        session["last_heartbeat_at"] = now
        session["updated_at"] = now
        return dict(session)

    def release_session_locked(
        self,
        lease_id: str,
        reason: str,
        project_id: str | None,
        on_session_ended: Callable[[str, str, str], list[str]],
    ) -> dict[str, Any]:
        session = self._state.sessions.get(lease_id)
        if session is None or not self._same_project(session, project_id):
            raise ValueError(f"Session not found: {lease_id}")
        if is_active_session_status(session.get("status")):
            session["status"] = "RELEASED"
            session["ended_at"] = self._now()
            session["updated_at"] = session["ended_at"]
            session["ended_reason"] = reason or "Session exited."
            settled = on_session_ended(
                str(session.get("agent_id")),
                str(session.get("project_id") or "default"),
                reason or f"Session exited: {session.get('agent_id')}",
            )
            session["settled_work"] = settled
            self._append_event(
                "session_released",
                project_id=session.get("project_id"),
                agent_id=session.get("agent_id"),
                role=session.get("role"),
                lease_id=lease_id,
                status="RELEASED",
                payload=session,
                preview=reason or f"session released {session.get('agent_id')}",
            )
        return dict(session)

    def list_sessions_locked(self, project_id: str | None = None, active: bool = False) -> list[dict[str, Any]]:
        sessions = [dict(session) for session in self._state.sessions.values() if self._same_project(session, project_id)]
        if active:
            sessions = [session for session in sessions if is_active_session_status(session.get("status"))]
        sessions.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
        return sessions


class MemoryJobProjector:
    def __init__(
        self,
        state: InMemoryBrokerState,
        state_machine: BrokerStateMachine,
        now: Callable[[], str],
        project_id_for: Callable[[dict[str, Any]], str],
        same_project: Callable[[dict[str, Any], str | None], bool],
        message_preview: Callable[[dict[str, Any]], str],
    ) -> None:
        self._state = state
        self._state_machine = state_machine
        self._now = now
        self._project_id_for = project_id_for
        self._same_project = same_project
        self._message_preview = message_preview

    def task_key(self, project_id: str | None, task_id: str) -> str:
        return f"{project_id or 'default'}:{task_id}"

    def conversation_key(self, project_id: str | None, conversation_id: str) -> str:
        return f"{project_id or 'default'}:{conversation_id}"

    def job_mode(self, message: dict[str, Any]) -> str:
        payload = message.get("payload") or {}
        mode = payload.get("mode")
        if mode:
            return str(mode)
        if is_talk_message_type(message.get("type")):
            return "TALK"
        return "PLAN"

    def task_job_for_message_locked(self, message: dict[str, Any]) -> Job | None:
        task_id = message.get("task_id")
        if not task_id:
            return None
        project_id = self._project_id_for(message)
        task_key = self.task_key(project_id, str(task_id))
        existing_job = self._state.task_jobs.get(task_key)
        if existing_job is not None:
            return existing_job
        if task_key not in self._state.tasks and message.get("type") != "TASK":
            return None
        job = self._state_machine.tasks.create(message, project_id=project_id, mode=self.job_mode(message))
        self._state.task_jobs[task_key] = job
        return job

    def transition_task_job_locked(self, message: dict[str, Any], status: str) -> Job | None:
        job = self.task_job_for_message_locked(message)
        if job is None:
            return None
        job = self._state_machine.tasks.transition(job, status)
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

    def upsert_task_locked(self, message: dict[str, Any], status: str) -> None:
        task_id = message.get("task_id")
        if not task_id:
            return
        project_id = self._project_id_for(message)
        task_key = self.task_key(project_id, str(task_id))
        job = self.transition_task_job_locked(message, status)
        if job is None:
            return
        # M3: acquire a job lease when the work is dispatched to a worker.
        if job.status == JobStatus.DELIVERED.value and job.lease is None and message.get("to_agent"):
            job = replace(job, lease=_new_lease(str(message.get("to_agent")), DEFAULT_JOB_HEARTBEAT_MS, 1))
        now = self._now()
        existing = self._state.tasks.get(task_key, {})
        existing_payload = dict(job.payload or {})
        is_reply = bool(existing_payload or existing) and message.get("type") != "TASK"
        payload = {
            "conversation_id": message.get("conversation_id") or existing_payload.get("conversation_id") or existing.get("conversation_id"),
            "mode": (existing_payload.get("mode") or existing.get("mode")) if is_reply else self.job_mode(message),
            "delivery": (existing_payload.get("delivery") or existing.get("delivery")) if is_reply else message.get("delivery", "async"),
            "from_agent": (existing_payload.get("from_agent") or existing.get("from_agent")) if is_reply else message.get("from_agent"),
            "to_agent": (existing_payload.get("to_agent") or existing.get("to_agent")) if is_reply else message.get("to_agent"),
            "created_at": existing_payload.get("created_at") or existing.get("created_at") or now,
            "updated_at": now,
            "preview": self._message_preview(message)[:300],
            "message_id": (existing_payload.get("message_id") or existing.get("message_id")) if is_reply else message.get("message_id"),
            "correlation_id": message.get("correlation_id") or existing_payload.get("correlation_id") or existing.get("correlation_id"),
            "message_type": message.get("type"),
            "last_activity_at": message.get("last_activity_at") or existing_payload.get("last_activity_at") or existing.get("last_activity_at"),
            "last_activity_type": message.get("last_activity_type") or existing_payload.get("last_activity_type") or existing.get("last_activity_type"),
            "last_activity_tool": message.get("last_activity_tool") or existing_payload.get("last_activity_tool") or existing.get("last_activity_tool"),
            "last_activity_preview": message.get("last_activity_preview") or existing_payload.get("last_activity_preview") or existing.get("last_activity_preview"),
        }
        job = self._state_machine.tasks.with_payload(job, payload)
        self._state.task_jobs[task_key] = job
        self._state.tasks[task_key] = task_job_to_wire(job)
        # Mirror the canonical lease onto the wire message so /agents/{id}/next
        # can hand the epoch to the worker for heartbeat renewal.
        message["lease"] = lease_to_wire(job.lease)

    def touch_conversation_locked(self, message: dict[str, Any], status: str | None = None) -> None:
        conversation_id = message.get("conversation_id")
        if not conversation_id:
            return
        message_type = str(message.get("type") or "")
        if not is_talk_message_type(message_type):
            return
        project_id = self._project_id_for(message)
        conversation_key = self.conversation_key(project_id, str(conversation_id))
        now = self._now()
        existing = self._state.conversations.get(conversation_key, {})
        job = self._state.talk_jobs.get(conversation_key)
        if job is None:
            job = self._state_machine.talk.create(message, project_id=project_id)

        next_status = status or existing.get("status") or "OPEN"
        if message_type == "CHAT_CLOSE":
            next_status = "CLOSED"
        elif next_status not in {"CLOSED", "TIMEOUT", "FAILED", "CANCELLED"}:
            next_status = "OPEN"
        turn = int(message.get("turn") or existing.get("turn") or job.turn or 1)
        max_turns = int(message.get("max_turns") or existing.get("max_turns") or job.max_turns or 6)
        if turn >= max_turns and message_type == "CHAT_REPLY":
            next_status = "CLOSED"
        participants = list(existing.get("participants") or (job.payload or {}).get("participants") or [])
        for agent in (message.get("from_agent"), message.get("to_agent")):
            if agent and agent not in participants:
                participants.append(agent)
        job = self._state_machine.talk.transition(job, next_status)
        payload = {
            "participants": participants,
            "wire_status": next_status,
            "from_agent": existing.get("from_agent") or (job.payload or {}).get("from_agent") or message.get("from_agent"),
            "to_agent": existing.get("to_agent") or (job.payload or {}).get("to_agent") or message.get("to_agent"),
            "created_at": existing.get("created_at") or (job.payload or {}).get("created_at") or now,
            "updated_at": now,
            "last_message_preview": self._message_preview(message)[:300],
            "preview": self._message_preview(message)[:300],
            "message_type": message_type,
            "last_activity_at": existing.get("last_activity_at") or (job.payload or {}).get("last_activity_at"),
            "last_activity_type": existing.get("last_activity_type") or (job.payload or {}).get("last_activity_type"),
            "last_activity_tool": existing.get("last_activity_tool") or (job.payload or {}).get("last_activity_tool"),
            "last_activity_preview": existing.get("last_activity_preview") or (job.payload or {}).get("last_activity_preview"),
        }
        job = self._state_machine.talk.with_payload(job, payload, turn=turn, max_turns=max_turns)
        self._state.talk_jobs[conversation_key] = job
        self._state.conversations[conversation_key] = talk_job_to_wire(job)

    def get_task_result_locked(self, task_id: str, project_id: str | None = None) -> dict[str, Any]:
        task_key = self.task_key(project_id, task_id) if project_id is not None else task_id
        if project_id is not None:
            if task_key in self._state.results_by_task:
                return dict(self._state.results_by_task[task_key])
            if task_key in self._state.tasks:
                return {"status": self._state.tasks[task_key].get("status", "QUEUED"), "project_id": project_id, "task_id": task_id, "job": dict(self._state.tasks[task_key])}
        else:
            result_matches = [dict(result) for key, result in self._state.results_by_task.items() if key.endswith(f":{task_id}") or key == task_id]
            if len(result_matches) == 1:
                return result_matches[0]
            task_matches = [dict(task) for key, task in self._state.tasks.items() if key.endswith(f":{task_id}") or key == task_id]
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
        jobs = [dict(task) for task in self._state.tasks.values() if self._same_project(task, project_id)]
        jobs.extend(dict(conversation) for conversation in self._state.conversations.values() if self._same_project(conversation, project_id))
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
        return [dict(conversation) for conversation in self._state.conversations.values() if self._same_project(conversation, project_id)]


class MemoryWorkQueue:
    def __init__(
        self,
        state: InMemoryBrokerState,
        now: Callable[[], str],
        project_id_for: Callable[[dict[str, Any]], str],
        same_project: Callable[[dict[str, Any], str | None], bool],
        task_key: Callable[[str | None, str], str],
        conversation_key: Callable[[str | None, str], str],
        active_session: Callable[[str, str | None], dict[str, Any] | None],
        peer_offline_detail: Callable[[dict[str, Any]], dict[str, Any]],
        append_event: Callable[..., dict[str, Any]],
        event_fields: Callable[[dict[str, Any], str | None], dict[str, Any]],
        upsert_task: Callable[[dict[str, Any], str], None],
        touch_conversation: Callable[[dict[str, Any], str | None], None],
        require_peer_sessions: bool,
    ) -> None:
        self._state = state
        self._now = now
        self._project_id_for = project_id_for
        self._same_project = same_project
        self._task_key = task_key
        self._conversation_key = conversation_key
        self._active_session = active_session
        self._peer_offline_detail = peer_offline_detail
        self._append_event = append_event
        self._event_fields = event_fields
        self._upsert_task = upsert_task
        self._touch_conversation = touch_conversation
        self._require_peer_sessions = require_peer_sessions

    def assert_conversation_can_receive_locked(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type") or "")
        if message_type not in {"CHAT_TURN", "CHAT_REPLY"}:
            return
        project_id = self._project_id_for(message)
        conversation_id = str(message.get("conversation_id") or "")
        conversation = self._state.conversations.get(self._conversation_key(project_id, conversation_id))
        if not conversation:
            raise ValueError(f"Conversation not found: {conversation_id}")
        if conversation.get("status") != "OPEN":
            raise ValueError(f"Conversation is not open: {conversation_id}")
        current_turn = int(conversation.get("turn") or 1)
        max_turns = int(conversation.get("max_turns") or 6)
        if current_turn >= max_turns:
            raise ValueError(f"Conversation reached max turns: {conversation_id}")

    def busy_detail(self, blocker: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
        blocking_id = blocker.get("task_id") or blocker.get("conversation_id") or blocker.get("message_id")
        return {
            "error": "worker_busy",
            "message": "Worker already has pending work. Wait for that reply before sending another task or talk turn.",
            "blocking_id": blocking_id,
            "blocking_kind": blocker.get("kind") or ("conversation" if blocker.get("conversation_id") and not blocker.get("task_id") else "task"),
            "blocking_status": blocker.get("status"),
            "blocking_type": blocker.get("message_type") or blocker.get("type"),
            "requested_type": message.get("type"),
            "requested_task_id": message.get("task_id"),
            "requested_conversation_id": message.get("conversation_id"),
        }

    def assert_worker_lane_free_locked(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type") or "")
        if message_type not in WORKER_BOUND_TYPES:
            return

        to_agent = message.get("to_agent")
        project_id = self._project_id_for(message)
        if self._require_peer_sessions and to_agent and self._active_session(str(to_agent), project_id) is None:
            raise MessageStoreBusy(self._peer_offline_detail(message))
        for active in self._state.active_messages.values():
            if not self._same_project(active, project_id):
                continue
            if active.get("to_agent") != to_agent:
                continue
            if str(active.get("status") or "").upper() not in BUSY_MESSAGE_STATUSES:
                continue
            raise MessageStoreBusy(self.busy_detail(active, message))

        conversation_id = str(message.get("conversation_id") or "")
        if message_type in {"CHAT_TURN", "CHAT_CLOSE"}:
            return
        for conversation in self._state.conversations.values():
            if not self._same_project(conversation, project_id):
                continue
            if conversation.get("status") != "OPEN":
                continue
            if to_agent not in conversation.get("participants", []):
                continue
            if conversation.get("conversation_id") == conversation_id:
                continue
            raise MessageStoreBusy(self.busy_detail(conversation, message))

    def resolve_task_waiters_locked(self, task_key: str, result: dict[str, Any]) -> None:
        waiters = self._state.task_waiters.pop(task_key, [])
        for future in waiters:
            if not future.done():
                future.set_result(result)

    def register_agent_locked(self, agent: dict[str, Any]) -> dict[str, Any]:
        agent_id = agent["agent_id"]
        stored_agent = dict(agent)
        self._state.agents[agent_id] = stored_agent
        self._state.inboxes.setdefault(agent_id, asyncio.Queue())
        self._append_event(
            "agent_registered",
            project_id=stored_agent.get("project_id"),
            agent_id=agent_id,
            role=stored_agent.get("role"),
            preview=f"registered {agent_id}",
        )
        return dict(stored_agent)

    def enqueue_message_locked(self, message: dict[str, Any], create_waiter: bool = False) -> tuple[dict[str, Any], asyncio.Queue[dict[str, Any]], dict[str, Any]]:
        message_id = message["message_id"]
        to_agent = message["to_agent"]
        correlation_id = message["correlation_id"]
        self.assert_conversation_can_receive_locked(message)
        self.assert_worker_lane_free_locked(message)
        stored_message = dict(message)
        now = self._now()
        stored_message["status"] = "CLOSED" if message.get("type") == "CHAT_CLOSE" else "QUEUED"
        stored_message.setdefault("created_at", now)
        stored_message["queued_at"] = now
        stored_message["updated_at"] = now
        self._state.active_messages[message_id] = stored_message
        if stored_message.get("type") == "CHAT_CLOSE":
            self._touch_conversation(stored_message, "CLOSED")
        elif is_talk_message_type(stored_message.get("type")):
            self._touch_conversation(stored_message, None)
        else:
            self._upsert_task(stored_message, "QUEUED")
        inbox = self._state.inboxes.setdefault(to_agent, asyncio.Queue())
        if create_waiter and message.get("requires_reply", False):
            self._state.pending_replies.setdefault(
                correlation_id,
                asyncio.get_running_loop().create_future(),
            )
        self._append_event(
            "message_queued",
            **self._event_fields(stored_message, stored_message["status"]),
        )
        return {"status": "queued", "message_id": message_id}, inbox, stored_message

    def inbox_for_agent_locked(self, agent_id: str) -> asyncio.Queue[dict[str, Any]]:
        return self._state.inboxes.setdefault(agent_id, asyncio.Queue())

    def deliver_message_locked(self, message: dict[str, Any]) -> dict[str, Any] | None:
        message_id = str(message.get("message_id"))
        active_message = self._state.active_messages.get(message_id)
        if active_message and is_terminal_status(active_message.get("status")):
            return None

        delivered = dict(active_message or message)
        if delivered.get("status") != "CLOSED":
            delivered["status"] = "DELIVERED"
        delivered["updated_at"] = self._now()
        if message_id in self._state.active_messages:
            self._state.active_messages[message_id]["status"] = delivered["status"]
            self._state.active_messages[message_id]["updated_at"] = delivered["updated_at"]
        if delivered.get("task_id") and delivered.get("type") == "TASK":
            self._upsert_task(delivered, delivered["status"])
        self._touch_conversation(delivered, None)
        self._append_event(
            "message_delivered",
            **self._event_fields(delivered, delivered["status"]),
        )
        return delivered

    def save_reply_locked(self, message_id: str, reply: dict[str, Any], lease_epoch: int | None = None, lease_holder: str | None = None) -> tuple[dict[str, Any], asyncio.Queue[dict[str, Any]] | None, dict[str, Any] | None]:
        correlation_id = reply["correlation_id"]
        stored_reply = dict(reply)
        job_status = reply_job_status(stored_reply.get("type"), stored_reply.get("status"))
        task_id = stored_reply.get("task_id")
        # M3: reject stale-holder replies when the caller asserts a lease epoch.
        # Backward compatible: if no epoch is provided, the reply is accepted
        # (current Pi extension behavior before lease-aware replies).
        if task_id and lease_epoch is not None:
            task_key = self._task_key(self._project_id_for(stored_reply), str(task_id))
            job = self._state.task_jobs.get(task_key)
            lease = job.lease if job is not None else None
            if lease is not None:
                if int(lease.get("epoch") or 0) != int(lease_epoch) or str(lease.get("holder") or "") != str(lease_holder or ""):
                    raise LeaseConflictError(
                        f"Stale lease reply for {task_id}: holder/epoch mismatch (lease epoch={lease.get('epoch')}, reply epoch={lease_epoch})."
                    )
        active = self._state.active_messages.get(message_id)
        if active and str(active.get("status") or "").upper() in {"TIMEOUT", "CANCELLED"}:
            self._append_event(
                "late_reply_ignored",
                **self._event_fields(stored_reply, str(active.get("status") or "")),
                preview="Late worker reply ignored because the original work is no longer active.",
            )
            return {"status": "reply_ignored", "correlation_id": correlation_id}, None, None

        future = self._state.pending_replies.get(correlation_id)
        if message_id in self._state.active_messages:
            self._state.active_messages[message_id]["status"] = job_status
            self._state.active_messages[message_id]["updated_at"] = self._now()
        if task_id:
            result = {"status": job_status, "project_id": self._project_id_for(stored_reply), "task_id": str(task_id), "reply": stored_reply}
            task_key = self._task_key(self._project_id_for(stored_reply), str(task_id))
            self._state.results_by_task[task_key] = result
            self._upsert_task(stored_reply, job_status)
            self.resolve_task_waiters_locked(task_key, result)
            self.resolve_task_waiters_locked(str(task_id), result)
        if is_talk_message_type(stored_reply.get("type")):
            self._touch_conversation(stored_reply, "OPEN" if job_status == "DONE" else job_status)
        reply_inbox = self._state.inboxes.setdefault(str(stored_reply["to_agent"]), asyncio.Queue())
        self._append_event(
            "reply_received",
            **self._event_fields(stored_reply, job_status),
        )
        if future is not None and not future.done():
            future.set_result(stored_reply)
        return {"status": "reply_received", "correlation_id": correlation_id}, reply_inbox, stored_reply

    def update_message_status_locked(self, message_id: str, normalized_status: str) -> dict[str, Any]:
        message = self._state.active_messages.get(message_id)
        if message is None:
            raise ValueError(f"Message not found: {message_id}")
        if is_terminal_status(message.get("status")):
            return {"status": str(message.get("status")), "message_id": message_id}
        message["status"] = normalized_status
        message["updated_at"] = self._now()
        if message.get("task_id") and message.get("type") == "TASK":
            self._upsert_task(message, normalized_status)
        if is_talk_message_type(message.get("type")):
            self._touch_conversation(message, normalized_status)
        self._append_event(
            "message_status",
            **self._event_fields(message, normalized_status),
        )
        return {"status": normalized_status, "message_id": message_id}

    def inactive_work_message_locked(self, item_id: str, project_id: str | None = None) -> str:
        for result in self._state.results_by_task.values():
            if not self._same_project(result.get("job") or result.get("reply") or result, project_id):
                continue
            if str(result.get("task_id") or "") == item_id:
                return f"No active work found: {item_id} (already {result.get('status', 'DONE')})."
        for task in self._state.tasks.values():
            if not self._same_project(task, project_id):
                continue
            if str(task.get("task_id") or "") == item_id:
                status = str(task.get("status") or "UNKNOWN")
                if status.upper() in TERMINAL_MESSAGE_STATUSES:
                    return f"No active work found: {item_id} (already {status})."
        for conversation in self._state.conversations.values():
            if not self._same_project(conversation, project_id):
                continue
            if str(conversation.get("conversation_id") or "") == item_id:
                status = str(conversation.get("status") or "UNKNOWN")
                if status.upper() != "OPEN":
                    return f"No active work found: {item_id} (already {status})."
        return f"No active work found: {item_id}."

    def cancel_work_locked(self, item_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        cancelled: list[str] = []
        targets = [
            message
            for message in self._state.active_messages.values()
            if self._same_project(message, project_id)
            and (
                str(message.get("message_id") or "") == item_id
                or str(message.get("task_id") or "") == item_id
                or str(message.get("conversation_id") or "") == item_id
            )
        ]
        targets = [message for message in targets if not is_terminal_status(message.get("status"))]
        if not targets:
            conversation = None
            if project_id is not None:
                conversation = self._state.conversations.get(self._conversation_key(project_id, item_id))
            else:
                matches = [item for item in self._state.conversations.values() if item.get("conversation_id") == item_id]
                conversation = matches[0] if len(matches) == 1 else None
            if conversation and conversation.get("status") == "OPEN":
                self._touch_conversation(
                    {
                        "project_id": conversation.get("project_id"),
                        "conversation_id": item_id,
                        "from_agent": conversation.get("from_agent"),
                        "to_agent": conversation.get("to_agent"),
                        "type": "CHAT_TURN",
                        "turn": conversation.get("turn"),
                        "max_turns": conversation.get("max_turns"),
                        "payload": {"summary": reason or "Conversation cancelled."},
                    },
                    "CANCELLED",
                )
                conversation = self._state.conversations.get(self._conversation_key(str(conversation.get("project_id") or "default"), item_id), conversation)
                self._append_event(
                    "work_cancelled",
                    project_id=conversation.get("project_id"),
                    conversation_id=item_id,
                    mode="TALK",
                    status="CANCELLED",
                    preview=reason or "Conversation cancelled.",
                )
                return {"status": "cancelled", "item_id": item_id, "cancelled": [item_id]}
            raise ValueError(self.inactive_work_message_locked(item_id, project_id=project_id))

        for message in targets:
            message["status"] = "CANCELLED"
            message["updated_at"] = self._now()
            message_id = str(message.get("message_id") or "")
            if message_id:
                cancelled.append(message_id)
            task_id = message.get("task_id")
            if task_id:
                result = {
                    "status": "CANCELLED",
                    "project_id": self._project_id_for(message),
                    "task_id": str(task_id),
                    "error": reason or "Work was cancelled.",
                    "job": dict(message),
                }
                task_key = self._task_key(self._project_id_for(message), str(task_id))
                self._state.results_by_task[task_key] = result
                self._upsert_task(message, "CANCELLED")
                self.resolve_task_waiters_locked(task_key, result)
                self.resolve_task_waiters_locked(str(task_id), result)
            if is_talk_message_type(message.get("type")):
                self._touch_conversation(message, "CANCELLED")
            future = self._state.pending_replies.get(str(message.get("correlation_id") or ""))
            if future is not None and not future.done():
                future.set_result({
                    "type": "BLOCKER",
                    "status": "CANCELLED",
                    "correlation_id": message.get("correlation_id"),
                    "payload": {"summary": reason or "Work was cancelled."},
                })
            self._append_event(
                "work_cancelled",
                **self._event_fields(message, "CANCELLED"),
                preview=reason or "Work was cancelled.",
            )
        return {"status": "cancelled", "item_id": item_id, "cancelled": cancelled}

    def close_conversation_locked(self, conversation_id: str, message: dict[str, Any]) -> dict[str, Any]:
        project_id = self._project_id_for(message) if message else None
        conversation = self._state.conversations.get(self._conversation_key(project_id, conversation_id)) if project_id else None
        if conversation is None and project_id is None:
            matches = [item for item in self._state.conversations.values() if item.get("conversation_id") == conversation_id]
            conversation = matches[0] if len(matches) == 1 else None
        if conversation is None:
            raise ValueError(f"Conversation not found: {conversation_id}")
        close_message = dict(message or {})
        close_message.setdefault("project_id", conversation.get("project_id"))
        close_message.setdefault("conversation_id", conversation_id)
        close_message.setdefault("from_agent", conversation.get("from_agent"))
        close_message.setdefault("to_agent", conversation.get("to_agent"))
        close_message.setdefault("type", "CHAT_CLOSE")
        close_message.setdefault("turn", min(int(conversation.get("turn") or 1) + 1, int(conversation.get("max_turns") or 6)))
        close_message.setdefault("max_turns", int(conversation.get("max_turns") or 6))
        self._touch_conversation(close_message, "CLOSED")
        conversation = self._state.conversations.get(self._conversation_key(str(close_message.get("project_id") or "default"), conversation_id), conversation)
        self._append_event(
            "conversation_closed",
            project_id=conversation.get("project_id"),
            conversation_id=conversation_id,
            from_agent=close_message.get("from_agent"),
            to_agent=close_message.get("to_agent"),
            message_type="CHAT_CLOSE",
            mode="TALK",
            delivery="conversation",
            status="CLOSED",
            payload=message.get("payload") if message else {},
        )
        return {"status": "closed", "conversation_id": conversation_id}

    def reply_future_locked(self, correlation_id: str) -> asyncio.Future[dict[str, Any]]:
        return self._state.pending_replies.setdefault(
            correlation_id,
            asyncio.get_running_loop().create_future(),
        )

    def timeout_reply_locked(self, correlation_id: str) -> None:
        message = next(
            (item for item in self._state.active_messages.values() if item.get("correlation_id") == correlation_id),
            {},
        )
        if message:
            message["status"] = "TIMEOUT"
            if message.get("task_id"):
                self._upsert_task(message, "TIMEOUT")
            if is_talk_message_type(message.get("type")):
                self._touch_conversation(message, "TIMEOUT")
        self._append_event(
            "timeout",
            **self._event_fields(message, "TIMEOUT"),
            preview="Worker did not reply before timeout.",
        )

    def cleanup_reply_waiter_locked(self, correlation_id: str, future: asyncio.Future[dict[str, Any]]) -> None:
        if self._state.pending_replies.get(correlation_id) is future:
            self._state.pending_replies.pop(correlation_id, None)

    def prepare_task_wait_locked(self, task_id: str, project_id: str | None = None) -> tuple[dict[str, Any] | None, str, asyncio.Future[dict[str, Any]] | None]:
        task_key = self._task_key(project_id, task_id) if project_id is not None else task_id
        if project_id is not None and task_key in self._state.results_by_task:
            return dict(self._state.results_by_task[task_key]), task_key, None
        if project_id is not None and task_key not in self._state.tasks:
            return {"status": "missing", "project_id": project_id, "task_id": task_id, "error": "Task not found."}, task_key, None
        if project_id is None:
            matches = [dict(result) for key, result in self._state.results_by_task.items() if key.endswith(f":{task_id}") or key == task_id]
            if len(matches) == 1:
                return matches[0], task_key, None
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._state.task_waiters.setdefault(task_key, []).append(future)
        return None, task_key, future

    def cleanup_task_waiter_locked(self, task_key: str, future: asyncio.Future[dict[str, Any]]) -> None:
        waiters = self._state.task_waiters.get(task_key, [])
        self._state.task_waiters[task_key] = [item for item in waiters if item is not future]
        if not self._state.task_waiters[task_key]:
            self._state.task_waiters.pop(task_key, None)

    def list_active_messages_locked(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return [dict(message) for message in self._state.active_messages.values() if self._same_project(message, project_id)]

    def pending_reply_count_locked(self) -> int:
        return len(self._state.pending_replies)


class MemoryMessageStore(MessageStore):
    def __init__(self, require_peer_sessions: bool = False, session_grace_seconds: int = 25) -> None:
        self.require_peer_sessions = require_peer_sessions
        self.session_grace_seconds = session_grace_seconds
        self._state = InMemoryBrokerState()
        # Observability-only audit journal (M1); attached via attach_journal.
        self.journal: Any = None
        self._state_machine = BrokerStateMachine()
        self._event_log = MemoryEventLog(self._state, self._now, self._job_mode, self._same_project)
        self._job_projector = MemoryJobProjector(
            self._state,
            self._state_machine,
            self._now,
            self._project_id,
            self._same_project,
            self._message_preview,
        )
        self._session_registry = MemorySessionRegistry(
            self._state,
            self._now,
            self._parse_time,
            self._same_project,
            self._append_event_locked,
            session_grace_seconds,
        )
        self._work_queue = MemoryWorkQueue(
            self._state,
            self._now,
            self._project_id,
            self._same_project,
            self._task_key,
            self._conversation_key,
            self._active_session_locked,
            self._peer_offline_detail,
            self._append_event_locked,
            self._event_fields,
            self._upsert_task_locked,
            self._touch_conversation_locked,
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

    def _project_id(self, message: dict[str, Any]) -> str:
        return str(message.get("project_id") or "default")

    def _task_key(self, project_id: str | None, task_id: str) -> str:
        return self._job_projector.task_key(project_id, task_id)

    def _conversation_key(self, project_id: str | None, conversation_id: str) -> str:
        return self._job_projector.conversation_key(project_id, conversation_id)

    def _same_project(self, item: dict[str, Any], project_id: str | None) -> bool:
        return project_id is None or str(item.get("project_id") or "default") == str(project_id)

    def _active_session_locked(self, agent_id: str, project_id: str | None = None) -> dict[str, Any] | None:
        return self._session_registry.active_session_locked(agent_id, project_id=project_id)

    def _active_session_count_locked(self, project_id: str | None = None) -> int:
        return self._session_registry.active_session_count_locked(project_id=project_id)

    def _active_job_count_locked(self, project_id: str | None = None) -> int:
        return sum(1 for message in self._state.active_messages.values() if self._same_project(message, project_id) and is_busy_status(message.get("status")))

    def _peer_offline_detail(self, message: dict[str, Any]) -> dict[str, Any]:
        return {
            "error": "peer_offline",
            "message": f"Recipient session is offline: {message.get('to_agent')}",
            "peer": message.get("to_agent"),
            "requested_type": message.get("type"),
            "requested_task_id": message.get("task_id"),
            "requested_conversation_id": message.get("conversation_id"),
        }

    def _payload_preview(self, payload: dict[str, Any]) -> str:
        return self._event_log.payload_preview(payload)

    def _message_preview(self, message: dict[str, Any]) -> str:
        return self._event_log.message_preview(message)

    def _append_event_locked(self, event_type: str, **fields: Any) -> dict[str, Any]:
        return self._event_log.append_event_locked(event_type, **fields)

    def attach_journal(self, journal: Any) -> None:
        """Wire the observability audit journal (M1).

        Once attached, every broker event recorded via ``append_event_locked``
        is mirrored into the journal. The journal is observability-only and
        never the source of truth.
        """
        self.journal = journal
        self._event_log._journal = journal

    def _settle_work_for_offline_agent_locked(self, agent_id: str, project_id: str, reason: str) -> list[str]:
        settled: list[str] = []
        for message in list(self._state.active_messages.values()):
            if not self._same_project(message, project_id):
                continue
            if message.get("from_agent") != agent_id and message.get("to_agent") != agent_id:
                continue
            if not is_busy_status(message.get("status")):
                continue
            message["status"] = "CANCELLED"
            message["updated_at"] = self._now()
            task_id = message.get("task_id")
            if task_id:
                result = {
                    "status": "CANCELLED",
                    "project_id": self._project_id(message),
                    "task_id": str(task_id),
                    "error": reason,
                    "job": dict(message),
                }
                task_key = self._task_key(self._project_id(message), str(task_id))
                self._state.results_by_task[task_key] = result
                self._upsert_task_locked(message, "CANCELLED")
                self._resolve_task_waiters_locked(task_key, result)
                self._resolve_task_waiters_locked(str(task_id), result)
                settled.append(str(task_id))
            if is_talk_message_type(message.get("type")):
                self._touch_conversation_locked(message, status="CANCELLED")
                if message.get("conversation_id"):
                    settled.append(str(message["conversation_id"]))
            future = self._state.pending_replies.get(str(message.get("correlation_id") or ""))
            if future is not None and not future.done():
                future.set_result({
                    "type": "BLOCKER",
                    "status": "CANCELLED",
                    "correlation_id": message.get("correlation_id"),
                    "payload": {"summary": reason, "error": "peer_offline"},
                })
            self._append_event_locked(
                "work_cancelled",
                **self._event_fields(message, status="CANCELLED"),
                preview=reason,
            )
        return settled

    def _expire_sessions_locked(self) -> list[dict[str, Any]]:
        return self._session_registry.expire_sessions_locked(self._settle_work_for_offline_agent_locked)

    def _expire_timed_out_messages_locked(self) -> None:
        self._expire_sessions_locked()
        now = datetime.now(timezone.utc)
        for message in list(self._state.active_messages.values()):
            status = str(message.get("status") or "").upper()
            if status not in BUSY_MESSAGE_STATUSES:
                continue
            try:
                timeout_seconds = int(message.get("timeout_seconds") or 0)
            except (TypeError, ValueError):
                timeout_seconds = 0
            if timeout_seconds <= 0:
                continue
            started_at = self._parse_time(message.get("created_at") or message.get("queued_at"))
            if started_at is None:
                continue
            if (now - started_at).total_seconds() < timeout_seconds:
                continue

            message["status"] = "TIMEOUT"
            message["updated_at"] = self._now()
            task_id = message.get("task_id")
            if task_id:
                result = {
                    "status": "TIMEOUT",
                    "project_id": self._project_id(message),
                    "task_id": str(task_id),
                    "error": "Task exceeded its timeout before the worker replied.",
                    "job": dict(message),
                }
                task_key = self._task_key(self._project_id(message), str(task_id))
                self._state.results_by_task[task_key] = result
                self._upsert_task_locked(message, "TIMEOUT")
                self._resolve_task_waiters_locked(task_key, result)
                self._resolve_task_waiters_locked(str(task_id), result)
            if is_talk_message_type(message.get("type")):
                self._touch_conversation_locked(message, status="TIMEOUT")
            future = self._state.pending_replies.get(str(message.get("correlation_id") or ""))
            if future is not None and not future.done():
                future.set_result({
                    "type": "BLOCKER",
                    "status": "TIMEOUT",
                    "correlation_id": message.get("correlation_id"),
                    "payload": {"summary": "Worker did not reply before the timeout."},
                })
            self._append_event_locked(
                "timeout",
                **self._event_fields(message, status="TIMEOUT"),
                preview="Work exceeded the timeout before the worker replied.",
            )

    def _job_mode(self, message: dict[str, Any]) -> str:
        return self._job_projector.job_mode(message)

    def _task_job_for_message_locked(self, message: dict[str, Any]) -> Job | None:
        return self._job_projector.task_job_for_message_locked(message)

    def _transition_task_job_locked(self, message: dict[str, Any], status: str) -> Job | None:
        return self._job_projector.transition_task_job_locked(message, status)

    def _hide_stale_heartbeat_locked(self, job: dict[str, Any]) -> dict[str, Any]:
        return self._job_projector.hide_stale_heartbeat_locked(job)

    def _event_fields(self, message: dict[str, Any], status: str | None = None) -> dict[str, Any]:
        return self._event_log.event_fields(message, status=status)

    def _activity_preview(self, activity: dict[str, Any]) -> str:
        return self._event_log.activity_preview(activity)

    def _apply_activity_to_work_locked(self, activity: dict[str, Any], timestamp: str) -> None:
        project_id = str(activity.get("project_id") or "default")
        task_id = str(activity.get("task_id") or "")
        conversation_id = str(activity.get("conversation_id") or "")
        message_id = str(activity.get("message_id") or "")
        preview = str(activity.get("detail") or activity.get("phase") or activity.get("activity_type") or "")[:300]

        if conversation_id:
            conversation_key = self._conversation_key(project_id, conversation_id)
            conversation = self._state.conversations.get(conversation_key)
            if conversation:
                conversation["updated_at"] = timestamp
                conversation["last_activity_at"] = timestamp
                conversation["last_activity_type"] = activity.get("activity_type")
                conversation["last_activity_tool"] = activity.get("tool_name")
                conversation["last_activity_preview"] = preview
                talk_job = self._state.talk_jobs.get(conversation_key)
                if talk_job is not None:
                    payload = dict(talk_job.payload or {})
                    payload.update(
                        {
                            "updated_at": timestamp,
                            "last_activity_at": timestamp,
                            "last_activity_type": activity.get("activity_type"),
                            "last_activity_tool": activity.get("tool_name"),
                            "last_activity_preview": preview,
                        }
                    )
                    talk_job = self._state_machine.talk.with_payload(talk_job, payload, turn=talk_job.turn, max_turns=talk_job.max_turns)
                    self._state.talk_jobs[conversation_key] = talk_job
                    self._state.conversations[conversation_key] = talk_job_to_wire(talk_job)

        target_message: dict[str, Any] | None = None
        if message_id:
            target_message = self._state.active_messages.get(message_id)
        if target_message is None:
            target_message = next(
                (
                    item
                    for item in self._state.active_messages.values()
                    if self._same_project(item, project_id)
                    and (
                        (task_id and str(item.get("task_id") or "") == task_id)
                        or (conversation_id and str(item.get("conversation_id") or "") == conversation_id)
                    )
                ),
                None,
            )
        if target_message and str(target_message.get("status") or "").upper() in BUSY_MESSAGE_STATUSES:
            target_message["status"] = "RUNNING"
            target_message["updated_at"] = timestamp
            target_message["last_activity_at"] = timestamp
            target_message["last_activity_type"] = activity.get("activity_type")
            target_message["last_activity_tool"] = activity.get("tool_name")
            target_message["last_activity_preview"] = preview
            if target_message.get("task_id") and target_message.get("type") == "TASK":
                self._upsert_task_locked(target_message, "RUNNING")

        if task_id:
            task_key = self._task_key(project_id, task_id)
            task = self._state.tasks.get(task_key)
            if task:
                if str(task.get("status") or "").upper() in BUSY_MESSAGE_STATUSES:
                    task["status"] = "RUNNING"
                task["updated_at"] = timestamp
                task["last_activity_at"] = timestamp
                task["last_activity_type"] = activity.get("activity_type")
                task["last_activity_tool"] = activity.get("tool_name")
                task["last_activity_preview"] = preview

    def _upsert_task_locked(self, message: dict[str, Any], status: str) -> None:
        self._job_projector.upsert_task_locked(message, status)

    def _touch_conversation_locked(self, message: dict[str, Any], status: str | None = None) -> None:
        self._job_projector.touch_conversation_locked(message, status=status)

    def _assert_conversation_can_receive_locked(self, message: dict[str, Any]) -> None:
        self._work_queue.assert_conversation_can_receive_locked(message)

    def _busy_detail(self, blocker: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
        return self._work_queue.busy_detail(blocker, message)

    def _assert_worker_lane_free_locked(self, message: dict[str, Any]) -> None:
        self._work_queue.assert_worker_lane_free_locked(message)

    def _resolve_task_waiters_locked(self, task_key: str, result: dict[str, Any]) -> None:
        self._work_queue.resolve_task_waiters_locked(task_key, result)

    async def register_agent(self, agent: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            return self._work_queue.register_agent_locked(agent)

    async def enqueue_message(
        self,
        message: dict[str, Any],
        create_waiter: bool = False,
    ) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            result, inbox, stored_message = self._work_queue.enqueue_message_locked(message, create_waiter=create_waiter)

        await inbox.put(stored_message)
        return result

    async def get_next_message(self, agent_id: str, wait_seconds: int) -> dict[str, Any] | None:
        async with self._lock:
            self._expire_timed_out_messages_locked()
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
                delivered = self._work_queue.deliver_message_locked(message)
                if delivered is None:
                    if wait_seconds <= 0 or loop.time() >= deadline:
                        return None
                    continue
            return delivered

    async def save_reply(self, message_id: str, reply: dict[str, Any], lease_epoch: int | None = None, lease_holder: str | None = None) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            result, reply_inbox, stored_reply = self._work_queue.save_reply_locked(message_id, reply, lease_epoch=lease_epoch, lease_holder=lease_holder)

        if reply_inbox is not None and stored_reply is not None:
            await reply_inbox.put(stored_reply)
        return result

    async def update_message_status(self, message_id: str, status: str) -> dict[str, Any]:
        normalized_status = status.upper()
        if normalized_status not in BUSY_MESSAGE_STATUSES | TERMINAL_MESSAGE_STATUSES:
            raise ValueError(f"Unsupported status: {status}")
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._work_queue.update_message_status_locked(message_id, normalized_status)

    async def record_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._event_log.record_activity_locked(activity, self._apply_activity_to_work_locked)

    async def list_activity(
        self,
        item_id: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return self._event_log.list_activity_locked(item_id=item_id, limit=limit, project_id=project_id)

    async def acquire_session(self, session: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._session_registry.acquire_session_locked(session)

    async def heartbeat_session(self, lease_id: str, project_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._session_registry.heartbeat_session_locked(lease_id, project_id=project_id)

    async def release_session(self, lease_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._session_registry.release_session_locked(
                lease_id,
                reason=reason,
                project_id=project_id,
                on_session_ended=self._settle_work_for_offline_agent_locked,
            )

    async def expire_sessions(self) -> list[dict[str, Any]]:
        async with self._lock:
            return self._expire_sessions_locked()

    async def list_sessions(self, project_id: str | None = None, active: bool = False) -> list[dict[str, Any]]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._session_registry.list_sessions_locked(project_id=project_id, active=active)

    async def can_auto_stop(self, project_id: str | None = None) -> bool:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._active_session_count_locked(project_id) == 0 and self._active_job_count_locked(project_id) == 0

    def _inactive_work_message_locked(self, item_id: str, project_id: str | None = None) -> str:
        return self._work_queue.inactive_work_message_locked(item_id, project_id=project_id)

    async def cancel_work(self, item_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._work_queue.cancel_work_locked(item_id, reason=reason, project_id=project_id)

    def _find_task_job_locked(self, task_id: str, project_id: str | None = None) -> tuple[str, Job]:
        if project_id is not None:
            task_key = self._task_key(project_id, task_id)
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
        if str(lease.get("holder") or "") != str(holder) or int(lease.get("epoch") or 0) != int(epoch):
            raise LeaseConflictError(f"Stale lease heartbeat for {task_id}: holder/epoch mismatch.")
        hb = heartbeat_ms or lease.get("heartbeat_ms") or DEFAULT_JOB_HEARTBEAT_MS
        new_lease = _new_lease(str(holder), hb, int(epoch))
        job = replace(job, lease=new_lease)
        self._state.task_jobs[task_key] = job
        self._state.tasks[task_key] = task_job_to_wire(job)
        self._append_event_locked(
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
        )
        return {"status": "renewed", "task_id": task_id, "lease": lease_to_wire(new_lease)}

    def reclaim_job_locked(self, task_id: str, holder: str, project_id: str | None = None) -> dict[str, Any]:
        task_key, job = self._find_task_job_locked(task_id, project_id=project_id)
        lease = job.lease
        if lease is None:
            raise LeaseConflictError(f"Job {task_id} is not reclaimable (no lease).")
        now = datetime.now(timezone.utc)
        expires_at = self._parse_time(lease.get("expires_at"))
        not_expired = expires_at is not None and now < expires_at
        if not_expired:
            # Idempotent: the current holder reclaiming a still-valid lease is a no-op.
            if str(lease.get("holder") or "") == str(holder):
                return {"status": "active", "task_id": task_id, "lease": lease_to_wire(lease), "reclaimed": False}
            raise LeaseConflictError(f"Job {task_id} lease has not expired.")
        new_epoch = int(lease.get("epoch") or 0) + 1
        new_lease = _new_lease(str(holder), lease.get("heartbeat_ms") or DEFAULT_JOB_HEARTBEAT_MS, new_epoch)
        # Transition RUNNING/DELIVERED -> RECLAIMABLE -> RUNNING with the new lease.
        # Uses require_transition directly because RECLAIMABLE is not a reply-driven
        # status in the job event map; the state machine's transition_path cannot
        # route through it, which is intentional.
        job = replace(job, status=require_transition(job.status, JobStatus.RECLAIMABLE.value))
        job = replace(job, status=require_transition(job.status, JobStatus.RUNNING.value), lease=new_lease)
        self._state.task_jobs[task_key] = job
        self._state.tasks[task_key] = task_job_to_wire(job)
        self._append_event_locked(
            "job_reclaimed",
            project_id=job.project_id,
            task_id=str(task_id),
            from_agent=holder,
            to_agent=holder,
            message_type="LEASE",
            mode=job.mode,
            status=job.status,
            payload={},
            preview=f"lease reclaimed epoch={new_epoch}",
        )
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

    async def close_conversation(self, conversation_id: str, message: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._work_queue.close_conversation_locked(conversation_id, message)

    async def wait_for_reply(self, correlation_id: str, timeout_seconds: int) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            future = self._work_queue.reply_future_locked(correlation_id)

        try:
            reply = await asyncio.wait_for(future, timeout=timeout_seconds)
            return {"status": "completed", "correlation_id": correlation_id, "reply": reply}
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
            if result is not None:
                return result
            assert future is not None

        try:
            return await asyncio.wait_for(future, timeout=timeout_seconds)
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
            return [dict(agent) for agent in self._state.agents.values()]

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
