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


class BrokerStatusResponse(BrokerResponse):
    broker: str
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


class JournalResponse(BrokerResponse):
    project_id: str | None = None
    entries: list[dict[str, Any]]
    last_seq: int


class JournalAppendResponse(BrokerResponse):
    status: str
    seq: int | None = None


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
    "WaitReplyResponse",
]
