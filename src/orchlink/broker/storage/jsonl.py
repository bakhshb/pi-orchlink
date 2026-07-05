"""Optional JSONL-backed broker store.

The store keeps MemoryMessageStore's behavior and appends a snapshot after each
mutating operation. On startup it restores the latest snapshot from the journal.
This is intentionally local and simple: no server process, no SQLite migration,
and no extra dependency.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from orchlink.broker.state import is_terminal_status
from orchlink.broker.storage.base import ActivityInput, AgentInput, MessageInput, SessionAcquireInput, SessionHeartbeatInput
from orchlink.broker.storage.memory import MemoryMessageStore
from orchlink.core.models import Job, JobRoute, SessionAcquire, SessionHeartbeat, WorkerActivityInput
from orchlink.core.views import (
    activity_record_from_wire,
    activity_record_to_wire,
    agent_input_to_agent,
    agent_to_wire,
    broker_event_from_wire,
    broker_event_to_wire,
    conversation_from_wire,
    conversation_to_wire,
    job_payload,
    lease_from_wire,
    lease_to_wire,
    message_input_to_wire,
    session_from_wire,
    session_to_wire,
    stored_message_from_wire,
    stored_message_to_wire,
    talk_job_payload_from_wire,
    task_job_payload_from_wire,
    task_projection_from_wire,
    task_projection_to_wire,
    task_result_from_wire,
    task_result_to_wire,
)

T = TypeVar("T")


def _job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "project_id": job.project_id,
        "route": {"from_agent": job.route.from_agent, "to_agent": job.route.to_agent},
        "mode": job.mode,
        "status": job.status,
        "task_id": job.task_id,
        "conversation_id": job.conversation_id,
        "turn": job.turn,
        "max_turns": job.max_turns,
        "payload": job_payload(job),
        "lease": lease_to_wire(job.lease),
    }


def _job_from_dict(data: dict[str, Any]) -> Job:
    route = data.get("route") or {}
    kind = str(data.get("kind") or "task")
    payload_data = dict(data.get("payload") or {})
    payload = talk_job_payload_from_wire(payload_data) if kind == "talk" else task_job_payload_from_wire(payload_data)
    return Job(
        id=str(data.get("id") or data.get("task_id") or data.get("conversation_id") or ""),
        kind=kind,
        project_id=str(data.get("project_id") or "default"),
        route=JobRoute(from_agent=str(route.get("from_agent") or ""), to_agent=str(route.get("to_agent") or "")),
        mode=str(data.get("mode") or "PLAN"),
        status=str(data.get("status") or "CREATED"),
        task_id=data.get("task_id"),
        conversation_id=data.get("conversation_id"),
        turn=int(data.get("turn") or 1),
        max_turns=int(data.get("max_turns") or 1),
        payload=payload,
        lease=lease_from_wire(data.get("lease")),
    )

def _agent_to_journal_request(agent: AgentInput) -> dict[str, Any]:
    """Render an agent input as JSON-serializable request data for JSONL."""
    return agent_to_wire(agent_input_to_agent(agent))


def _message_to_journal_request(message: MessageInput) -> dict[str, Any]:
    """Render a message input as JSON-serializable request data for JSONL."""
    return message_input_to_wire(message)


def _drop_none(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def _session_acquire_to_journal_request(session: SessionAcquireInput) -> dict[str, Any]:
    if isinstance(session, SessionAcquire):
        return _drop_none(asdict(session))
    raise TypeError(f"Unsupported session acquire input: {type(session).__name__}")


def _session_heartbeat_to_journal_request(heartbeat: SessionHeartbeatInput | None) -> dict[str, Any] | None:
    if heartbeat is None:
        return None
    if isinstance(heartbeat, SessionHeartbeat):
        data = _drop_none(asdict(heartbeat))
        data.pop("lease_id", None)
        data.pop("project_id", None)
        return data
    raise TypeError(f"Unsupported session heartbeat input: {type(heartbeat).__name__}")


def _activity_to_journal_request(activity: ActivityInput) -> dict[str, Any]:
    if isinstance(activity, WorkerActivityInput):
        return _drop_none(asdict(activity))
    raise TypeError(f"Unsupported activity input: {type(activity).__name__}")


class JsonlMessageStore(MemoryMessageStore):
    """Memory store with local snapshot replay."""

    def __init__(self, path: str | Path, require_peer_sessions: bool = False, session_grace_seconds: int = 25) -> None:
        super().__init__(require_peer_sessions=require_peer_sessions, session_grace_seconds=session_grace_seconds)
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._load_latest_snapshot()

    def _snapshot(self) -> dict[str, Any]:
        return {
            "agents": {key: agent_to_wire(agent) for key, agent in self._state.agents.items()},
            "active_messages": {key: stored_message_to_wire(stored) for key, stored in self._state.active_messages.items()},
            "tasks": {key: task_projection_to_wire(task) for key, task in self._state.tasks.items()},
            "task_jobs": {key: _job_to_dict(job) for key, job in self._state.task_jobs.items()},
            "results_by_task": {key: task_result_to_wire(result) for key, result in self._state.results_by_task.items()},
            "conversations": {
                key: conversation_to_wire(conversation)
                for key, conversation in self._state.conversations.items()
            },
            "talk_jobs": {key: _job_to_dict(job) for key, job in self._state.talk_jobs.items()},
            "events": [broker_event_to_wire(event) for event in self._state.events],
            "activity": [activity_record_to_wire(activity) for activity in self._state.activity],
            "sessions": {key: session_to_wire(value) for key, value in self._state.sessions.items()},
            "next_event_id": self._state.next_event_id,
            "next_activity_id": self._state.next_activity_id,
        }

    def _restore_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._state.agents = {str(key): agent_input_to_agent(value) for key, value in (snapshot.get("agents") or {}).items()}
        # Active messages are reconstituted as StoredMessage via the same
        # boundary validation used at enqueue.
        self._state.active_messages = {
            str(key): stored_message_from_wire(dict(value))
            for key, value in (snapshot.get("active_messages") or {}).items()
        }
        self._state.tasks = {str(key): task_projection_from_wire(value) for key, value in (snapshot.get("tasks") or {}).items()}
        self._state.task_jobs = {str(key): _job_from_dict(value) for key, value in (snapshot.get("task_jobs") or {}).items()}
        self._state.results_by_task = {str(key): task_result_from_wire(value) for key, value in (snapshot.get("results_by_task") or {}).items()}
        self._state.conversations = {
            str(key): conversation_from_wire(dict(value))
            for key, value in (snapshot.get("conversations") or {}).items()
        }
        self._state.talk_jobs = {str(key): _job_from_dict(value) for key, value in (snapshot.get("talk_jobs") or {}).items()}
        self._state.events = [broker_event_from_wire(value) for value in (snapshot.get("events") or [])]
        self._state.activity = [activity_record_from_wire(value) for value in (snapshot.get("activity") or [])]
        self._state.sessions = {str(key): session_from_wire(value) for key, value in (snapshot.get("sessions") or {}).items()}
        self._state.next_event_id = int(snapshot.get("next_event_id") or (self._state.events[-1].id + 1 if self._state.events else 1))
        self._state.next_activity_id = int(snapshot.get("next_activity_id") or (self._state.activity[-1].id + 1 if self._state.activity else 1))
        self._state.inboxes = {agent_id: asyncio.Queue() for agent_id in self._state.agents}
        for stored in self._state.active_messages.values():
            if is_terminal_status(stored.status):
                continue
            to_agent = str(stored.envelope.to_agent or "")
            if to_agent:
                self._state.inboxes.setdefault(to_agent, asyncio.Queue()).put_nowait(stored)

    def _load_latest_snapshot(self) -> None:
        if not self.path.is_file():
            return
        latest: dict[str, Any] | None = None
        with self.path.open("r", encoding="utf-8") as file:
            for line in file:
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                snapshot = record.get("snapshot")
                if isinstance(snapshot, dict):
                    latest = snapshot
        if latest is not None:
            self._restore_snapshot(latest)

    async def _journal(self, operation: str, request: dict[str, Any], result: Any) -> None:
        record = {
            "time": self._now(),
            "operation": operation,
            "request": request,
            "result": result,
            "snapshot": self._snapshot(),
        }
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, sort_keys=True, default=str) + "\n")

    async def _recorded(self, operation: str, request: dict[str, Any], call: Callable[[], Awaitable[T]]) -> T:
        result = await call()
        await self._journal(operation, request, result)
        return result

    async def register_agent(self, agent: AgentInput) -> dict[str, Any]:
        return await self._recorded(
            "register_agent",
            {"agent": _agent_to_journal_request(agent)},
            lambda: MemoryMessageStore.register_agent(self, agent),
        )

    async def enqueue_message(self, message: MessageInput, create_waiter: bool = False) -> dict[str, Any]:
        return await self._recorded(
            "enqueue_message",
            {"message": _message_to_journal_request(message), "create_waiter": create_waiter},
            lambda: MemoryMessageStore.enqueue_message(self, message, create_waiter=create_waiter),
        )

    async def save_reply(
        self,
        message_id: str,
        reply: MessageInput,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
        session_lease_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._recorded(
            "save_reply",
            {"message_id": message_id, "reply": _message_to_journal_request(reply), "session_lease_id": session_lease_id},
            lambda: MemoryMessageStore.save_reply(
                self,
                message_id,
                reply,
                lease_epoch=lease_epoch,
                lease_holder=lease_holder,
                session_lease_id=session_lease_id,
            ),
        )

    async def update_message_status(
        self,
        message_id: str,
        status: str,
        session_lease_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._recorded(
            "update_message_status",
            {"message_id": message_id, "status": status, "session_lease_id": session_lease_id},
            lambda: MemoryMessageStore.update_message_status(self, message_id, status, session_lease_id=session_lease_id),
        )

    async def record_activity(self, activity: ActivityInput) -> dict[str, Any]:
        return await self._recorded(
            "record_activity",
            {"activity": _activity_to_journal_request(activity)},
            lambda: MemoryMessageStore.record_activity(self, activity),
        )

    async def acquire_session(self, session: SessionAcquireInput) -> dict[str, Any]:
        return await self._recorded(
            "acquire_session",
            {"session": _session_acquire_to_journal_request(session)},
            lambda: MemoryMessageStore.acquire_session(self, session),
        )

    async def heartbeat_session(
        self,
        lease_id: str,
        project_id: str | None = None,
        heartbeat: SessionHeartbeatInput | None = None,
    ) -> dict[str, Any]:
        return await self._recorded(
            "heartbeat_session",
            {"lease_id": lease_id, "project_id": project_id, "heartbeat": _session_heartbeat_to_journal_request(heartbeat)},
            lambda: MemoryMessageStore.heartbeat_session(self, lease_id, project_id=project_id, heartbeat=heartbeat),
        )

    async def release_session(self, lease_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        return await self._recorded(
            "release_session",
            {"lease_id": lease_id, "reason": reason, "project_id": project_id},
            lambda: MemoryMessageStore.release_session(self, lease_id, reason=reason, project_id=project_id),
        )

    async def expire_sessions(self) -> list[dict[str, Any]]:
        expired = await MemoryMessageStore.expire_sessions(self)
        if expired:
            await self._journal("expire_sessions", {}, expired)
        return expired

    async def cancel_work(self, item_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        return await self._recorded(
            "cancel_work",
            {"item_id": item_id, "reason": reason, "project_id": project_id},
            lambda: MemoryMessageStore.cancel_work(self, item_id, reason=reason, project_id=project_id),
        )

    async def heartbeat_job(self, task_id: str, holder: str, epoch: int, project_id: str | None = None, heartbeat_ms: int | None = None) -> dict[str, Any]:
        return await self._recorded(
            "heartbeat_job",
            {"task_id": task_id, "holder": holder, "epoch": epoch, "project_id": project_id, "heartbeat_ms": heartbeat_ms},
            lambda: MemoryMessageStore.heartbeat_job(self, task_id, holder, epoch, project_id=project_id, heartbeat_ms=heartbeat_ms),
        )

    async def reclaim_job(self, task_id: str, holder: str, project_id: str | None = None) -> dict[str, Any]:
        return await self._recorded(
            "reclaim_job",
            {"task_id": task_id, "holder": holder, "project_id": project_id},
            lambda: MemoryMessageStore.reclaim_job(self, task_id, holder, project_id=project_id),
        )

    async def close_conversation(self, conversation_id: str, message: MessageInput) -> dict[str, Any]:
        return await self._recorded(
            "close_conversation",
            {"conversation_id": conversation_id, "message": _message_to_journal_request(message)},
            lambda: MemoryMessageStore.close_conversation(self, conversation_id, message),
        )
