"""Small domain models for Orchlink jobs and events."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any, Literal

from orchlink.core.envelope import AgentRegistration, MessageEnvelope
from orchlink.core.states import JOB_STATUS_LIFECYCLE, CANONICAL_TERMINAL_STATUSES, JobStatus, normalize_status, require_transition


TranscriptEventKind = Literal["assistant_delta", "tool", "status", "system"]


# ... rest of file continues ...

JobKind = Literal["task", "talk"]
SessionRole = Literal["lead", "work", "worker"]
AgentRole = Literal["lead", "work", "worker"]


@dataclass(frozen=True)
class TranscriptEvent:
    """Visible-assistant transcript event for a task."""

    seq: int
    time: str
    project_id: str
    task_id: str
    agent_id: str | None
    worker_name: str | None
    kind: str
    text: str
    tool_name: str | None = None

    def to_wire_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "time": self.time,
            "project_id": self.project_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "worker_name": self.worker_name,
            "kind": self.kind,
            "text": self.text,
            "tool_name": self.tool_name,
        }


@dataclass(frozen=True)
class TranscriptBatch:
    """Worker-submitted batch of transcript events before broker sequencing."""

    project_id: str
    task_id: str
    agent_id: str | None
    worker_name: str | None
    batch_id: str
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> "TranscriptBatch":
        return cls(
            project_id=str(data.get("project_id") or "default"),
            task_id=str(data.get("task_id") or ""),
            agent_id=str(data["agent_id"]) if data.get("agent_id") is not None else None,
            worker_name=str(data["worker_name"]) if data.get("worker_name") is not None else None,
            batch_id=str(data.get("batch_id") or ""),
            events=list(data.get("events") or []),
        )


class TranscriptTruncation:
    """Marker returned when a read cursor predates retained per-task history."""

    def __init__(self, truncated_before_sequence: int) -> None:
        self.truncated_before_sequence = truncated_before_sequence

    def to_event(self, project_id: str, task_id: str) -> dict[str, Any]:
        return {
            "seq": self.truncated_before_sequence,
            "time": datetime.now(timezone.utc).isoformat(),
            "project_id": project_id,
            "task_id": task_id,
            "agent_id": None,
            "worker_name": None,
            "kind": "system",
            "text": "Earlier transcript history was dropped by retention. This is not the complete output.",
            "tool_name": None,
        }


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


class JobEventType(StrEnum):
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    DELIVERED = "DELIVERED"
    STARTED = "STARTED"
    REPLIED = "REPLIED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


JOB_EVENT_STATUS: dict[JobEventType, str] = {
    JobEventType.CREATED: JobStatus.CREATED.value,
    JobEventType.QUEUED: JobStatus.QUEUED.value,
    JobEventType.DELIVERED: JobStatus.DELIVERED.value,
    JobEventType.STARTED: JobStatus.RUNNING.value,
    JobEventType.REPLIED: JobStatus.DONE.value,
    JobEventType.FAILED: JobStatus.FAILED.value,
    JobEventType.TIMED_OUT: JobStatus.TIMEOUT.value,
    JobEventType.CANCELLED: JobStatus.CANCELLED.value,
    JobEventType.CLOSED: JobStatus.CLOSED.value,
}


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


@dataclass(frozen=True)
class JobRoute:
    from_agent: str
    to_agent: str


@dataclass(frozen=True)
class JobLease:
    """Canonical task lease owned by a `Job`.

    Wire/API code still sees plain dictionaries through `core.views.lease_to_wire`;
    internals should use these fields and lifecycle helpers instead of
    `dict.get(...)`.
    """

    holder: str
    expires_at: str
    epoch: int
    heartbeat_ms: int

    @classmethod
    def fresh(
        cls,
        holder: str,
        heartbeat_ms: int | None,
        epoch: int,
        *,
        grace_multiplier: int = 6,
        now: datetime | None = None,
    ) -> "JobLease":
        hb = max(int(heartbeat_ms or 15000), 1000)
        base = now or datetime.now(timezone.utc)
        expires_at = (base + timedelta(milliseconds=hb * grace_multiplier)).isoformat()
        return cls(holder=str(holder), expires_at=expires_at, epoch=int(epoch), heartbeat_ms=hb)

    def renew(self, heartbeat_ms: int | None = None, *, grace_multiplier: int = 6) -> "JobLease":
        return self.fresh(
            self.holder,
            heartbeat_ms or self.heartbeat_ms,
            self.epoch,
            grace_multiplier=grace_multiplier,
        )

    def reclaim(self, holder: str, *, grace_multiplier: int = 6) -> "JobLease":
        return self.fresh(
            holder,
            self.heartbeat_ms,
            self.epoch + 1,
            grace_multiplier=grace_multiplier,
        )

    def matches(self, holder: str, epoch: int) -> bool:
        return self.holder == str(holder) and self.epoch == int(epoch)

    def to_wire_dict(self) -> dict[str, Any]:
        from orchlink.core.views import lease_to_wire

        return lease_to_wire(self) or {}

    def expires_at_datetime(self) -> datetime | None:
        try:
            return datetime.fromisoformat(self.expires_at)
        except (TypeError, ValueError):
            return None

    def is_active(self, now: datetime | None = None) -> bool:
        expires_at = self.expires_at_datetime()
        if expires_at is None:
            return False
        return (now or datetime.now(timezone.utc)) < expires_at


@dataclass(frozen=True)
class TaskJobPayload:
    """Typed internal payload for canonical task jobs."""

    conversation_id: str | None = None
    mode: str | None = None
    delivery: str = "async"
    from_agent: str | None = None
    to_agent: str | None = None
    worker_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    preview: str = ""
    message_id: str | None = None
    correlation_id: str | None = None
    message_type: str | None = None
    last_activity_at: str | None = None
    last_activity_type: str | None = None
    last_activity_tool: str | None = None
    last_activity_preview: str | None = None

    def to_wire_dict(self) -> dict[str, Any]:
        from orchlink.core.views import task_job_payload_to_wire

        return task_job_payload_to_wire(self)


@dataclass(frozen=True)
class TalkJobPayload:
    """Typed internal payload for canonical talk jobs."""

    participants: tuple[str, ...] = ()
    wire_status: str | None = None
    from_agent: str | None = None
    to_agent: str | None = None
    worker_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    last_message_preview: str = ""
    preview: str = ""
    message_type: str | None = None
    last_activity_at: str | None = None
    last_activity_type: str | None = None
    last_activity_tool: str | None = None
    last_activity_preview: str | None = None

    def to_wire_dict(self) -> dict[str, Any]:
        from orchlink.core.views import talk_job_payload_to_wire

        return talk_job_payload_to_wire(self)


@dataclass(frozen=True)
class Job:
    id: str
    kind: JobKind
    project_id: str
    route: JobRoute
    mode: str
    status: str = JobStatus.CREATED.value
    task_id: str | None = None
    conversation_id: str | None = None
    turn: int = 1
    max_turns: int = 1
    payload: TaskJobPayload | TalkJobPayload = field(default_factory=TaskJobPayload)
    # M3 job lease: None when the job has never been dispatched or has reached
    # a terminal state. Wire serialization lives in `core.views.lease_to_wire`.
    lease: JobLease | None = None

    def __post_init__(self) -> None:
        normalized_status = normalize_status(self.status)
        if normalized_status not in JOB_STATUS_LIFECYCLE:
            raise ValueError(f"Unsupported canonical job status: {self.status}")
        if self.kind == "task" and not self.task_id:
            raise ValueError("Task jobs require task_id")
        if self.kind == "talk" and not self.conversation_id:
            raise ValueError("Talk jobs require conversation_id")
        if self.turn < 1:
            raise ValueError("turn must be >= 1")
        if self.max_turns < self.turn:
            raise ValueError("max_turns must be >= turn")
        if self.kind == "task" and not isinstance(self.payload, TaskJobPayload):
            raise TypeError("Task jobs require TaskJobPayload")
        if self.kind == "talk" and not isinstance(self.payload, TalkJobPayload):
            raise TypeError("Talk jobs require TalkJobPayload")
        object.__setattr__(self, "status", normalized_status)

    def transition(self, event: JobEvent) -> Job:
        return advance_job(self, event)

    def _apply(self, event_type: JobEventType) -> Job:
        return self.transition(JobEvent(type=event_type, project_id=self.project_id, job_id=self.id))

    def queue(self) -> Job:
        """Return this job moved to QUEUED."""
        return self._apply(JobEventType.QUEUED)

    def deliver(self) -> Job:
        """Return this job moved to DELIVERED."""
        return self._apply(JobEventType.DELIVERED)

    def start(self) -> Job:
        """Return this job moved to RUNNING."""
        return self._apply(JobEventType.STARTED)

    def reply(self) -> Job:
        """Return this job moved to DONE."""
        return self._apply(JobEventType.REPLIED)

    def fail(self) -> Job:
        """Return this job moved to FAILED."""
        return self._apply(JobEventType.FAILED)

    def timeout(self) -> Job:
        """Return this job moved to TIMEOUT."""
        return self._apply(JobEventType.TIMED_OUT)

    def cancel(self) -> Job:
        """Return this job moved to CANCELLED."""
        return self._apply(JobEventType.CANCELLED)

    def close(self) -> Job:
        """Return this job moved to CLOSED."""
        return self._apply(JobEventType.CLOSED)

    def with_lease(self, lease: JobLease | None) -> Job:
        """Return this job with a refreshed lease reference."""
        return replace(self, lease=lease)

    def make_reclaimable(self) -> Job:
        """Return this job moved into the internal RECLAIMABLE state."""
        return replace(self, status=require_transition(self.status, JobStatus.RECLAIMABLE.value))

    def reclaim_with_lease(self, lease: JobLease) -> Job:
        """Return this expired/reclaimable job running under a new lease."""
        reclaimable = self.make_reclaimable()
        return replace(
            reclaimable,
            status=require_transition(reclaimable.status, JobStatus.RUNNING.value),
            lease=lease,
        )


@dataclass(frozen=True)
class JobEvent:
    type: JobEventType
    project_id: str
    job_id: str
    status: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            event_type = JobEventType(str(self.type).upper())
        except ValueError as exc:
            raise ValueError(f"Unsupported job event type: {self.type}") from exc
        expected_status = JOB_EVENT_STATUS[event_type]
        target_status = normalize_status(self.status or expected_status)
        if target_status not in JOB_STATUS_LIFECYCLE:
            raise ValueError(f"Unsupported canonical job status: {self.status}")
        if target_status != expected_status:
            raise ValueError(f"Job event status mismatch: {event_type.value} expects {expected_status}, got {target_status}")
        object.__setattr__(self, "type", event_type)
        object.__setattr__(self, "status", target_status)


@dataclass(frozen=True)
class Agent:
    """Registered broker participant."""

    project_id: str
    agent_id: str
    role: AgentRole
    display_name: str
    capabilities: tuple[str, ...] = ()

    @classmethod
    def from_registration(cls, agent: AgentRegistration) -> "Agent":
        return cls(
            project_id=agent.project_id,
            agent_id=agent.agent_id,
            role=agent.role,
            display_name=agent.display_name,
            capabilities=tuple(agent.capabilities),
        )


@dataclass(frozen=True)
class SessionAcquire:
    """Typed command for acquiring a worker/lead session."""

    project_id: str
    agent_id: str
    role: SessionRole
    lease_id: str | None = None
    worker_name: str | None = None
    pid: int | None = None
    session_id: str | None = None
    lease_grace_seconds: int | None = None
    ready: bool = False
    runtime_mode: str | None = None
    backend: str | None = None
    model: str | None = None
    thinking: str | None = None
    supervisor_pid: int | None = None
    pi_pid: int | None = None
    project_dir: str | None = None


@dataclass(frozen=True)
class SessionHeartbeat:
    """Typed command for refreshing session liveness/metadata."""

    lease_id: str
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


@dataclass(frozen=True)
class SessionRelease:
    """Typed command for releasing a session lease."""

    lease_id: str
    reason: str = ""
    project_id: str | None = None


@dataclass(frozen=True)
class Session:
    lease_id: str
    project_id: str
    agent_id: str
    role: SessionRole
    worker_name: str | None = None
    status: str = SessionStatus.ACTIVE.value
    pid: int | None = None
    session_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    last_heartbeat_at: str | None = None
    ended_at: str | None = None
    ended_reason: str | None = None
    lease_grace_seconds: int = 25
    ready: bool = False
    ready_at: str | None = None
    last_ready_heartbeat_at: str | None = None
    runtime_mode: str | None = None
    backend: str | None = None
    model: str | None = None
    thinking: str | None = None
    supervisor_pid: int | None = None
    pi_pid: int | None = None
    project_dir: str | None = None
    settled_work: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        try:
            status = SessionStatus(str(self.status).upper())
        except ValueError as exc:
            raise ValueError(f"Unsupported session status: {self.status}") from exc
        object.__setattr__(self, "status", status.value)

    def heartbeat(self, now: str) -> Session:
        """Return this session with a control-plane heartbeat recorded."""
        return replace(self, updated_at=now, last_heartbeat_at=now)

    def mark_ready(self, now: str) -> Session:
        """Return this session marked ready, preserving the first ready timestamp."""
        return replace(
            self,
            ready=True,
            ready_at=self.ready_at or now,
            last_ready_heartbeat_at=now,
            updated_at=now,
        )

    def release(self, now: str, reason: str = "") -> Session:
        """Return this session released by an operator or normal shutdown."""
        return replace(
            self,
            status=SessionStatus.RELEASED.value,
            ended_at=now,
            ended_reason=reason,
            updated_at=now,
            ready=False,
        )

    def expire(self, now: str, reason: str = "") -> Session:
        """Return this session expired after its lease grace elapsed."""
        return replace(
            self,
            status=SessionStatus.EXPIRED.value,
            ended_at=now,
            ended_reason=reason,
            updated_at=now,
            ready=False,
        )


@dataclass(frozen=True)
class TaskProjection:
    """Cached task/job row kept internally as a typed record."""

    kind: str
    project_id: str
    task_id: str
    conversation_id: str | None = None
    mode: str | None = None
    delivery: str = "async"
    status: str = JobStatus.CREATED.value
    from_agent: str | None = None
    to_agent: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    preview: str = ""
    message_id: str | None = None
    correlation_id: str | None = None
    message_type: str | None = None
    last_activity_at: str | None = None
    last_activity_type: str | None = None
    last_activity_tool: str | None = None
    last_activity_preview: str | None = None
    lease: JobLease | None = None

    def with_updates(self, updates: dict[str, Any]) -> "TaskProjection":
        allowed = set(self.__dataclass_fields__)
        return replace(self, **{key: value for key, value in updates.items() if key in allowed})

    def to_wire_dict(self) -> dict[str, Any]:
        from orchlink.core.views import task_projection_to_wire

        return task_projection_to_wire(self)


@dataclass(frozen=True)
class TaskResult:
    """Stored task result with typed reply/job references.

    Public wait/get callers still receive dictionaries via
    ``orchlink.core.views.task_result_to_wire``.
    """

    status: str
    project_id: str
    task_id: str
    reply: StoredMessage | None = None
    job: StoredMessage | TaskProjection | None = None
    error: str | None = None

    def to_wire_dict(self) -> dict[str, Any]:
        from orchlink.core.views import task_result_to_wire

        return task_result_to_wire(self)


@dataclass(frozen=True)
class ReplyResult:
    """Typed reply waiter result for a successful worker reply."""

    correlation_id: str
    reply: StoredMessage


@dataclass(frozen=True)
class WaitBlocker:
    """Typed waiter result for cancellation/timeout/missing work."""

    status: str
    correlation_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    error: str = ""
    summary: str = ""
    reason: str | None = None


@dataclass(frozen=True)
class WorkerActivityInput:
    """Typed command for recording worker activity."""

    project_id: str = "default"
    task_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    agent_id: str | None = None
    session_lease_id: str | None = None
    activity_type: str = "activity"
    phase: str | None = None
    tool_name: str | None = None
    detail: str = ""
    status: str = "RUNNING"
    mode: str | None = None

    @property
    def preview(self) -> str:
        return str(self.detail or self.phase or self.activity_type or "")[:300]

    def to_record(self, record_id: int, timestamp: str) -> "ActivityRecord":
        return ActivityRecord(
            id=record_id,
            time=timestamp,
            project_id=self.project_id,
            task_id=self.task_id,
            conversation_id=self.conversation_id,
            message_id=self.message_id,
            agent_id=self.agent_id,
            session_lease_id=self.session_lease_id,
            activity_type=self.activity_type,
            phase=self.phase,
            tool_name=self.tool_name,
            detail=self.detail[:500],
            status=self.status,
            mode=self.mode,
        )


@dataclass(frozen=True)
class BrokerEventContext:
    """Typed command/context for writing a broker event."""

    event_type: str
    fields: dict[str, Any] = field(default_factory=dict)
    preview: str | None = None

    @classmethod
    def from_fields(cls, event_type: str, **fields: Any) -> "BrokerEventContext":
        preview = fields.pop("preview", None)
        return cls(event_type=event_type, fields=dict(fields), preview=None if preview is None else str(preview))


@dataclass(frozen=True)
class BrokerEvent:
    """Internal broker event record.

    The broker keeps this typed record in memory; API and JSONL boundaries use
    `core.views.broker_event_to_wire` for dict projection.
    """

    id: int
    time: str
    type: str
    preview: str = ""
    fields: dict[str, Any] = field(default_factory=dict)

    def to_wire_dict(self) -> dict[str, Any]:
        from orchlink.core.views import broker_event_to_wire

        return broker_event_to_wire(self)


@dataclass(frozen=True)
class ActivityRecord:
    """Internal worker-activity record."""

    id: int
    time: str
    project_id: str
    task_id: str | None = None
    conversation_id: str | None = None
    message_id: str | None = None
    agent_id: str | None = None
    session_lease_id: str | None = None
    activity_type: str = "activity"
    phase: str | None = None
    tool_name: str | None = None
    detail: str = ""
    status: str = "RUNNING"
    mode: str | None = None

    def to_wire_dict(self) -> dict[str, Any]:
        from orchlink.core.views import activity_record_to_wire

        return activity_record_to_wire(self)


def advance_job(job: Job, event: JobEvent) -> Job:
    if event.project_id != job.project_id:
        raise ValueError(f"Job event project mismatch: {event.project_id} != {job.project_id}")
    if event.job_id != job.id:
        raise ValueError(f"Job event id mismatch: {event.job_id} != {job.id}")
    target_status = require_transition(job.status, event.status)
    # M3: reaching a terminal state clears the lease so a stale holder cannot
    # renew or reply against completed/cancelled work.
    if target_status in CANONICAL_TERMINAL_STATUSES:
        return replace(job, status=target_status, lease=None)
    return replace(job, status=target_status)


@dataclass(frozen=True)
class StoredMessage:
    """Broker storage record for an active Orchlink message.

    The record owns a validated `MessageEnvelope` plus broker lifecycle
    metadata (`status`, `created_at`, `queued_at`, `updated_at`). JSON/dict
    projection belongs in `orchlink.core.views.stored_message_to_wire`.
    """

    envelope: MessageEnvelope
    status: str
    created_at: str | None = None
    queued_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope, now: str) -> "StoredMessage":
        """Construct a StoredMessage from a validated MessageEnvelope.

        The initial storage status follows the broker's enqueue convention:
        ``"QUEUED"`` for normal messages and ``"CLOSED"`` for ``CHAT_CLOSE``
        envelopes (which short-circuit to a terminal state on enqueue). Wire
        dictionaries are decoded by `orchlink.core.views`, not this domain type.
        """
        initial_status = "CLOSED" if str(envelope.type) == "CHAT_CLOSE" else "QUEUED"
        return cls(
            envelope=envelope,
            status=initial_status,
            created_at=now,
            queued_at=now,
            updated_at=now,
        )

    def to_wire_dict(self) -> dict[str, Any]:
        from orchlink.core.views import stored_message_to_wire

        return stored_message_to_wire(self)

    def with_status(self, status: str, now: str) -> "StoredMessage":
        """Return a new StoredMessage with the given broker status and updated_at."""
        return replace(self, status=status, updated_at=now)


@dataclass(frozen=True)
class Conversation:
    """Broker storage record for an active Orchlink conversation.

    Lifecycle changes are immutable methods on this class. JSON/dict
    projection belongs in `orchlink.core.views.conversation_to_wire`.
    """

    conversation_id: str
    project_id: str
    participants: tuple[str, ...]
    status: str
    turn: int
    max_turns: int
    from_agent: str | None = None
    to_agent: str | None = None
    message_type: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    last_message_preview: str = ""
    preview: str = ""
    last_activity_at: str | None = None
    last_activity_type: str | None = None
    last_activity_tool: str | None = None
    last_activity_preview: str | None = None
    worker_name: str | None = None

    # --- Lifecycle helpers (each returns a new immutable Conversation) ---

    def with_status(self, status: str, now: str) -> "Conversation":
        return replace(self, status=status, updated_at=now)

    def with_turn(self, turn: int, max_turns: int | None = None) -> "Conversation":
        return replace(self, turn=turn, max_turns=self.max_turns if max_turns is None else max_turns)

    def with_participants(self, participants: tuple[str, ...], now: str) -> "Conversation":
        return replace(self, participants=tuple(participants), updated_at=now)

    def with_payload(
        self,
        *,
        status: str,
        turn: int | None = None,
        max_turns: int | None = None,
        message_type: str | None = None,
        last_message_preview: str | None = None,
        preview: str | None = None,
        now: str,
    ) -> "Conversation":
        return replace(
            self,
            status=status,
            turn=self.turn if turn is None else turn,
            max_turns=self.max_turns if max_turns is None else max_turns,
            message_type=self.message_type if message_type is None else message_type,
            last_message_preview=self.last_message_preview if last_message_preview is None else last_message_preview,
            preview=self.preview if preview is None else preview,
            updated_at=now,
        )

    def touch(
        self,
        activity_at: str,
        activity_type: str | None,
        activity_tool: str | None,
        activity_preview: str | None,
        now: str,
    ) -> "Conversation":
        return replace(
            self,
            last_activity_at=activity_at,
            last_activity_type=activity_type,
            last_activity_tool=activity_tool,
            last_activity_preview=activity_preview,
            last_message_preview=activity_preview or self.last_message_preview,
            preview=activity_preview or self.preview,
            updated_at=now,
        )

    def close(self, now: str, status: str = "CLOSED") -> "Conversation":
        return replace(self, status=status, updated_at=now)

    # --- Wire view ---

    def to_wire_dict(self) -> dict[str, Any]:
        from orchlink.core.views import conversation_to_wire

        return conversation_to_wire(self)


__all__ = [
    "ActivityRecord",
    "Agent",
    "BrokerEvent",
    "BrokerEventContext",
    "Conversation",
    "Job",
    "JobLease",
    "JobEvent",
    "JobEventType",
    "JobRoute",
    "Session",
    "SessionAcquire",
    "SessionHeartbeat",
    "SessionRelease",
    "SessionStatus",
    "StoredMessage",
    "TalkJobPayload",
    "TaskJobPayload",
    "TaskProjection",
    "TaskResult",
    "WorkerActivityInput",
    "advance_job",
]
