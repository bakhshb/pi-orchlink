"""FastAPI request DTOs for broker routes.

These Pydantic models are HTTP-boundary objects. Where the broker has typed
commands, DTOs expose explicit mappers instead of making routes pass arbitrary
store dictionaries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orchlink.core.models import TaskTelemetry, TranscriptBatch

from pydantic import BaseModel, ConfigDict, Field

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


class TranscriptEventBody(BrokerBody):
    kind: str | None = "assistant_delta"
    text: str | None = None
    tool_name: str | None = None


class TranscriptBatchBody(BrokerBody):
    project_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    worker_name: str | None = None
    batch_id: str | None = None
    events: list[TranscriptEventBody] = Field(default_factory=list)

    def to_command(self) -> "TranscriptBatch":
        from orchlink.core.models import TranscriptBatch

        return TranscriptBatch(
            project_id=str(self.project_id or "default"),
            task_id=str(self.task_id or ""),
            agent_id=str(self.agent_id) if self.agent_id is not None else None,
            worker_name=str(self.worker_name) if self.worker_name is not None else None,
            batch_id=str(self.batch_id or ""),
            events=[event.model_dump(exclude_none=True) for event in self.events],
        )


class JournalAppendBody(BrokerBody):
    project_id: str | None = None
    actor: str | None = None
    action: str | None = None
    target_type: str | None = None
    target_id: str | None = None
    before: str | None = None
    after: str | None = None
    meta: dict[str, Any] | None = None


class TaskTelemetryBody(BrokerBody):
    """Worker-submitted telemetry payload (G019 AC-5).

    Privacy boundary: the body accepts ONLY numeric metrics, the worker
    name, and lease metadata. There is no path for a worker to send prompt
    body, hidden reasoning, tool arguments, raw tool output, provider data,
    environment value, secret, or authorization data through this DTO.
    """

    project_id: str | None = None
    worker_name: str | None = None
    tokens: int | None = None
    context_window: int | None = None
    percent: float | None = None
    tool_count: int = 0

    def to_command(self) -> "TaskTelemetry":
        # Imported locally to avoid the TYPE_CHECKING runtime-import
        # cycle; the broker startup imports TaskTelemetry normally.
        from orchlink.core.models import TaskTelemetry

        return TaskTelemetry(
            project_id=str(self.project_id or "default"),
            task_id="",  # task_id comes from the URL path on the broker side.
            worker_name=str(self.worker_name or ""),
            tokens=self.tokens,
            context_window=self.context_window,
            percent=self.percent,
            tool_count=max(0, int(self.tool_count or 0)),
        )


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
    "TaskTelemetryBody",
    "TranscriptBatchBody",
    "TranscriptEventBody",
]
