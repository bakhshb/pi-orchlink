"""Shared in-memory state dataclass and small helpers used by every focused
broker storage component.

Lifted out of ``memory.py`` so the focused components can import the state
container without circular references to ``MemoryMessageStore``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from orchlink.broker.state import is_talk_message_type
from orchlink.core.models import (
    ActivityRecord,
    Agent,
    BrokerEvent,
    Conversation,
    Job,
    JobLease,
    ReplyResult,
    Session,
    StoredMessage,
    TaskProjection,
    TaskResult,
    WaitBlocker,
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


def new_job_lease(holder: str, heartbeat_ms: int | None, epoch: int) -> JobLease:
    """Build a fresh typed lease with an expiry derived from the heartbeat interval."""
    return JobLease.fresh(
        holder,
        heartbeat_ms or DEFAULT_JOB_HEARTBEAT_MS,
        epoch,
        grace_multiplier=JOB_LEASE_GRACE_MULTIPLIER,
    )


def session_belongs_to_project(session: Session, project_id: str | None) -> bool:
    """Return True when a stored ``Session`` belongs to the named project.

    Mirrors the dict-based ``_same_project`` semantics used elsewhere so the
    session registry can compare ``Session`` objects without dict round-trips.
    Passing ``project_id=None`` matches every project.
    """
    return project_id is None or str(session.project_id or "default") == str(project_id)


def matches_project(item: dict[str, Any], project_id: str | None) -> bool:
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


__all__ = [
    "DEFAULT_JOB_HEARTBEAT_MS",
    "InMemoryBrokerState",
    "InboxItem",
    "JOB_LEASE_GRACE_MULTIPLIER",
    "MessageProjectionContext",
    "matches_project",
    "new_job_lease",
    "session_belongs_to_project",
]