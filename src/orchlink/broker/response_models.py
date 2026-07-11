"""FastAPI response models for broker routes.

These models document the stable top-level HTTP response shapes while allowing
existing nested wire dictionaries to pass through unchanged.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class BrokerResponse(BaseModel):
    """Base response that preserves existing extra top-level fields."""

    model_config = ConfigDict(extra="allow")


class HealthResponse(BrokerResponse):
    status: str
    service: str
    version: str
    capabilities: list[str]


class RegisterAgentResponse(BrokerResponse):
    status: str
    agent_id: str


class SessionResponse(BrokerResponse):
    status: str
    session: dict[str, Any]


class SessionsResponse(BrokerResponse):
    project_id: str | None = None
    sessions: list[dict[str, Any]]


class MessageSendResponse(BrokerResponse):
    status: str


class WaitReplyResponse(BrokerResponse):
    status: str


class NextMessageResponse(BrokerResponse):
    status: str
    message: dict[str, Any] | None = None


class EventsResponse(BrokerResponse):
    events: list[dict[str, Any]]
    last_event_id: int


class ActivityRecordResponse(BrokerResponse):
    status: str


class ActivityListResponse(BrokerResponse):
    activity: list[dict[str, Any]]


class TaskActivityResponse(BrokerResponse):
    project_id: str | None = None
    task_id: str
    activity: list[dict[str, Any]]


class JobsResponse(BrokerResponse):
    project_id: str | None = None
    jobs: list[dict[str, Any]]


class TaskResultResponse(BrokerResponse):
    status: str


class TranscriptListResponse(BrokerResponse):
    project_id: str | None = None
    task_id: str
    events: list[dict[str, Any]]
    next_seq: int


class BrokerStatusResponse(BrokerResponse):
    broker: str
    broker_host: str | None = None
    broker_port: int | None = None
    agent_count: int
    agents: list[dict[str, Any]]
    session_count: int
    sessions: list[dict[str, Any]]
    active_message_count: int
    active_messages: list[dict[str, Any]]
    conversation_count: int
    conversations: list[dict[str, Any]]
    job_count: int
    jobs: list[dict[str, Any]]
    pending_reply_count: int
    recent_events: list[dict[str, Any]]
    # G019 AC-8: latest worker telemetry records, one per task. Additive;
    # older callers ignore it. Drives the inline worker tree's tool-count
    # and session-context rendering through the existing status poll.
    telemetry_count: int = 0
    telemetry: list[dict[str, Any]] = []


class JournalResponse(BrokerResponse):
    project_id: str | None = None
    entries: list[dict[str, Any]]
    last_seq: int


class JournalAppendResponse(BrokerResponse):
    status: str
    seq: int | None = None




class TaskTelemetryResponse(BrokerResponse):
    """Result of a task telemetry update (G019 AC-5).

    ``status`` is one of ``"recorded"`` or ``"rejected"``. When
    ``"rejected"``, ``reason`` carries a stable machine-readable code:
    ``"stale-job-lease"``, ``"stale-session-lease"``, ``"terminal-task"``,
    ``"unknown-task"``, ``"internal-error"``, or ``"invalid-task"``.
    """

    status: str = "recorded"
    reason: str | None = None
    task_id: str | None = None
    project_id: str | None = None
    updated_at: str | None = None

__all__ = [
    "ActivityListResponse",
    "ActivityRecordResponse",
    "BrokerResponse",
    "BrokerStatusResponse",
    "EventsResponse",
    "HealthResponse",
    "JobsResponse",
    "JournalAppendResponse",
    "JournalResponse",
    "MessageSendResponse",
    "NextMessageResponse",
    "RegisterAgentResponse",
    "SessionResponse",
    "SessionsResponse",
    "TaskActivityResponse",
    "TaskResultResponse",
    "TaskTelemetryResponse",
    "TranscriptListResponse",
    "WaitReplyResponse",
]
