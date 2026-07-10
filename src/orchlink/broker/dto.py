"""FastAPI request DTOs for broker routes.

These Pydantic models are HTTP-boundary objects. Where the broker has typed
commands, DTOs expose explicit mappers instead of making routes pass arbitrary
store dictionaries.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from orchlink.core.models import SessionAcquire, SessionHeartbeat, SessionRelease, WorkerActivityInput
from orchlink.core.views import (
    session_acquire_from_wire,
    session_heartbeat_from_wire,
    session_release_from_wire,
    worker_activity_from_wire,
)


class BrokerBody(BaseModel):
    """Base for small broker request bodies that tolerate older extra keys."""

    model_config = ConfigDict(extra="allow")

    def to_store_dict(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class SessionAcquireBody(BrokerBody):
    lease_id: str | None = None
    project_id: str | None = None
    agent_id: str | None = None
    role: str | None = None
    worker_name: str | None = None
    pid: int | None = None
    session_id: str | None = None
    lease_grace_seconds: int | None = None
    ready: bool | None = None
    runtime_mode: str | None = None
    backend: str | None = None
    model: str | None = None
    thinking: str | None = None
    supervisor_pid: int | None = None
    pi_pid: int | None = None
    project_dir: str | None = None

    def to_command(self) -> SessionAcquire:
        return session_acquire_from_wire(self.to_store_dict())


class SessionHeartbeatBody(BrokerBody):
    project_id: str | None = None
    ready: bool | None = None
    runtime_mode: str | None = None
    backend: str | None = None
    model: str | None = None
    thinking: str | None = None
    supervisor_pid: int | None = None
    pi_pid: int | None = None
    worker_name: str | None = None
    project_dir: str | None = None

    def to_command(self, lease_id: str, project_id: str | None = None) -> SessionHeartbeat:
        return session_heartbeat_from_wire(lease_id, project_id=project_id, heartbeat=self.to_store_dict())


class SessionReleaseBody(BrokerBody):
    project_id: str | None = None
    reason: str | None = None

    def to_command(self, lease_id: str, project_id: str | None = None) -> SessionRelease:
        return session_release_from_wire(lease_id, reason=str(self.reason or ""), project_id=project_id)


class MessageStatusBody(BrokerBody):
    status: str | None = None
    session_lease_id: str | None = None


class CancelWorkBody(BrokerBody):
    project_id: str | None = None
    reason: str | None = None


class JobHeartbeatBody(BrokerBody):
    project_id: str | None = None
    holder: str | None = None
    epoch: Any = None
    heartbeat_ms: int | None = None


class JobReclaimBody(BrokerBody):
    project_id: str | None = None
    holder: str | None = None


class ActivityBody(BrokerBody):
    project_id: str | None = None
    task_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    agent_id: str | None = None
    session_lease_id: str | None = None
    activity_type: str | None = None
    phase: str | None = None
    tool_name: str | None = None
    detail: str | None = None
    status: str | None = None
    mode: str | None = None

    def to_command(self) -> WorkerActivityInput:
        return worker_activity_from_wire(self.to_store_dict())


class JournalAppendBody(BrokerBody):
    project_id: str | None = None
    actor: str | None = None
    action: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    before: str | None = None
    after: str | None = None
    meta: dict[str, Any] | None = None


__all__ = [
    "ActivityBody",
    "BrokerBody",
    "CancelWorkBody",
    "JobHeartbeatBody",
    "JobReclaimBody",
    "JournalAppendBody",
    "MessageStatusBody",
    "SessionAcquireBody",
    "SessionHeartbeatBody",
    "SessionReleaseBody",
]
