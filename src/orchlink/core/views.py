"""Wire-view serializers for canonical Orchlink domain models.

The broker stores canonical OOP models internally, but the CLI/API still expose
stable dict shapes. Keep those conversions here so storage code does not hand-roll
public response rows in multiple places.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from orchlink.core.envelope import AgentRegistration, MessageEnvelope, envelope_to_dict
from orchlink.core.models import ActivityRecord, Agent, BrokerEvent, Conversation, Job, JobLease, ReplyResult, Session, SessionAcquire, SessionHeartbeat, SessionRelease, StoredMessage, TalkJobPayload, TaskJobPayload, TaskProjection, TaskResult, WaitBlocker, WorkerActivityInput


def job_payload(job: Job) -> dict[str, Any]:
    payload = job.payload
    if hasattr(payload, "to_wire_dict"):
        return payload.to_wire_dict()
    return dict(payload or {})


def agent_input_to_agent(agent: Any) -> Agent:
    """Coerce a decoded boundary value into an Agent domain object."""
    if isinstance(agent, Agent):
        return agent
    if isinstance(agent, AgentRegistration):
        return Agent.from_registration(agent)
    if not isinstance(agent, dict):
        raise TypeError(f"Agent input must be dict, AgentRegistration, or Agent; got {type(agent).__name__}")
    return Agent(
        project_id=str(agent.get("project_id") or "default"),
        agent_id=str(agent.get("agent_id") or ""),
        role=str(agent.get("role") or "worker"),
        display_name=str(agent.get("display_name") or agent.get("agent_id") or ""),
        capabilities=tuple(str(item) for item in (agent.get("capabilities") or [])),
    )


def agent_to_wire(agent: Agent) -> dict[str, Any]:
    """Serialize a registered broker participant."""
    return {
        "project_id": agent.project_id,
        "agent_id": agent.agent_id,
        "role": agent.role,
        "display_name": agent.display_name,
        "capabilities": list(agent.capabilities),
    }


def session_acquire_from_wire(data: dict[str, Any] | SessionAcquire) -> SessionAcquire:
    """Build a typed session-acquire command from request/snapshot data."""
    if isinstance(data, SessionAcquire):
        return data
    worker_name = data.get("worker_name")
    return SessionAcquire(
        lease_id=str(data["lease_id"]) if data.get("lease_id") is not None else None,
        project_id=str(data.get("project_id") or "default"),
        agent_id=str(data.get("agent_id") or ""),
        role=str(data.get("role") or "work"),
        worker_name=str(worker_name) if worker_name is not None else None,
        pid=data.get("pid"),
        session_id=str(data["session_id"]) if data.get("session_id") is not None else None,
        lease_grace_seconds=data.get("lease_grace_seconds"),
        ready=bool(data.get("ready")),
        runtime_mode=str(data["runtime_mode"]) if data.get("runtime_mode") is not None else None,
        backend=str(data["backend"]) if data.get("backend") is not None else None,
        model=str(data["model"]) if data.get("model") is not None else None,
        thinking=str(data["thinking"]) if data.get("thinking") is not None else None,
        supervisor_pid=data.get("supervisor_pid"),
        pi_pid=data.get("pi_pid"),
    )


def session_heartbeat_from_wire(
    lease_id: str,
    project_id: str | None = None,
    heartbeat: dict[str, Any] | SessionHeartbeat | None = None,
) -> SessionHeartbeat:
    """Build a typed session-heartbeat command from request data."""
    if isinstance(heartbeat, SessionHeartbeat):
        return heartbeat
    data = heartbeat or {}
    return SessionHeartbeat(
        lease_id=str(lease_id),
        project_id=project_id,
        ready=bool(data.get("ready")) if "ready" in data else None,
        runtime_mode=str(data["runtime_mode"]) if data.get("runtime_mode") is not None else None,
        backend=str(data["backend"]) if data.get("backend") is not None else None,
        model=str(data["model"]) if data.get("model") is not None else None,
        thinking=str(data["thinking"]) if data.get("thinking") is not None else None,
        supervisor_pid=data.get("supervisor_pid"),
        pi_pid=data.get("pi_pid"),
        worker_name=str(data["worker_name"]) if data.get("worker_name") is not None else None,
    )


def session_release_from_wire(lease_id: str, reason: str = "", project_id: str | None = None) -> SessionRelease:
    """Build a typed session-release command from request path/body data."""
    return SessionRelease(lease_id=str(lease_id), reason=str(reason or ""), project_id=project_id)


def session_from_wire(data: dict[str, Any]) -> Session:
    """Restore a Session domain object from decoded JSON/wire data."""
    return Session(
        lease_id=str(data.get("lease_id") or ""),
        project_id=str(data.get("project_id") or "default"),
        agent_id=str(data.get("agent_id") or ""),
        role=str(data.get("role") or "work"),
        worker_name=data.get("worker_name"),
        status=str(data.get("status") or "ACTIVE"),
        pid=data.get("pid"),
        session_id=data.get("session_id"),
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        last_heartbeat_at=data.get("last_heartbeat_at"),
        ended_at=data.get("ended_at"),
        ended_reason=data.get("ended_reason"),
        lease_grace_seconds=int(data.get("lease_grace_seconds") or 25),
        ready=bool(data.get("ready") or False),
        ready_at=data.get("ready_at"),
        last_ready_heartbeat_at=data.get("last_ready_heartbeat_at"),
        runtime_mode=data.get("runtime_mode"),
        backend=data.get("backend"),
        model=data.get("model"),
        thinking=data.get("thinking"),
        supervisor_pid=data.get("supervisor_pid"),
        pi_pid=data.get("pi_pid"),
        settled_work=list(data.get("settled_work") or []),
    )


def session_to_wire(session: Session) -> dict[str, Any]:
    """Serialize a canonical `Session` into the API/wire dict shape."""
    from dataclasses import asdict

    return asdict(session)


def task_job_payload_from_wire(data: dict[str, Any]) -> TaskJobPayload:
    """Restore a typed task job payload from decoded boundary data."""
    return TaskJobPayload(
        conversation_id=str(data["conversation_id"]) if data.get("conversation_id") is not None else None,
        mode=str(data["mode"]) if data.get("mode") is not None else None,
        delivery=str(data.get("delivery") or "async"),
        from_agent=str(data["from_agent"]) if data.get("from_agent") is not None else None,
        to_agent=str(data["to_agent"]) if data.get("to_agent") is not None else None,
        worker_name=str(data["worker_name"]) if data.get("worker_name") is not None else None,
        created_at=str(data["created_at"]) if data.get("created_at") is not None else None,
        updated_at=str(data["updated_at"]) if data.get("updated_at") is not None else None,
        preview=str(data.get("preview") or ""),
        message_id=str(data["message_id"]) if data.get("message_id") is not None else None,
        correlation_id=str(data["correlation_id"]) if data.get("correlation_id") is not None else None,
        message_type=str(data["message_type"]) if data.get("message_type") is not None else None,
        last_activity_at=str(data["last_activity_at"]) if data.get("last_activity_at") is not None else None,
        last_activity_type=str(data["last_activity_type"]) if data.get("last_activity_type") is not None else None,
        last_activity_tool=str(data["last_activity_tool"]) if data.get("last_activity_tool") is not None else None,
        last_activity_preview=str(data["last_activity_preview"]) if data.get("last_activity_preview") is not None else None,
    )


def talk_job_payload_from_wire(data: dict[str, Any]) -> TalkJobPayload:
    """Restore a typed talk job payload from decoded boundary data."""
    participants_value = data.get("participants") or ()
    return TalkJobPayload(
        participants=tuple(str(agent) for agent in participants_value if agent),
        wire_status=str(data["wire_status"]) if data.get("wire_status") is not None else None,
        from_agent=str(data["from_agent"]) if data.get("from_agent") is not None else None,
        to_agent=str(data["to_agent"]) if data.get("to_agent") is not None else None,
        worker_name=str(data["worker_name"]) if data.get("worker_name") is not None else None,
        created_at=str(data["created_at"]) if data.get("created_at") is not None else None,
        updated_at=str(data["updated_at"]) if data.get("updated_at") is not None else None,
        last_message_preview=str(data.get("last_message_preview") or ""),
        preview=str(data.get("preview") or ""),
        message_type=str(data["message_type"]) if data.get("message_type") is not None else None,
        last_activity_at=str(data["last_activity_at"]) if data.get("last_activity_at") is not None else None,
        last_activity_type=str(data["last_activity_type"]) if data.get("last_activity_type") is not None else None,
        last_activity_tool=str(data["last_activity_tool"]) if data.get("last_activity_tool") is not None else None,
        last_activity_preview=str(data["last_activity_preview"]) if data.get("last_activity_preview") is not None else None,
    )


def task_projection_from_wire(data: dict[str, Any]) -> TaskProjection:
    """Restore a cached task projection from decoded JSON/wire data."""
    return TaskProjection(
        kind=str(data.get("kind") or "task"),
        project_id=str(data.get("project_id") or "default"),
        task_id=str(data.get("task_id") or ""),
        conversation_id=str(data["conversation_id"]) if data.get("conversation_id") is not None else None,
        mode=str(data["mode"]) if data.get("mode") is not None else None,
        delivery=str(data.get("delivery") or "async"),
        status=str(data.get("status") or "CREATED"),
        from_agent=str(data["from_agent"]) if data.get("from_agent") is not None else None,
        to_agent=str(data["to_agent"]) if data.get("to_agent") is not None else None,
        created_at=str(data["created_at"]) if data.get("created_at") is not None else None,
        updated_at=str(data["updated_at"]) if data.get("updated_at") is not None else None,
        preview=str(data.get("preview") or ""),
        message_id=str(data["message_id"]) if data.get("message_id") is not None else None,
        correlation_id=str(data["correlation_id"]) if data.get("correlation_id") is not None else None,
        message_type=str(data["message_type"]) if data.get("message_type") is not None else None,
        last_activity_at=str(data["last_activity_at"]) if data.get("last_activity_at") is not None else None,
        last_activity_type=str(data["last_activity_type"]) if data.get("last_activity_type") is not None else None,
        last_activity_tool=str(data["last_activity_tool"]) if data.get("last_activity_tool") is not None else None,
        last_activity_preview=str(data["last_activity_preview"]) if data.get("last_activity_preview") is not None else None,
        lease=lease_from_wire(data.get("lease")),
    )


def task_projection_to_wire(projection: TaskProjection) -> dict[str, Any]:
    """Serialize a typed task projection into the existing task/job row shape."""
    return {
        "kind": projection.kind,
        "project_id": projection.project_id,
        "task_id": projection.task_id,
        "conversation_id": projection.conversation_id,
        "mode": projection.mode,
        "delivery": projection.delivery,
        "status": projection.status,
        "from_agent": projection.from_agent,
        "to_agent": projection.to_agent,
        "created_at": projection.created_at,
        "updated_at": projection.updated_at,
        "preview": projection.preview,
        "message_id": projection.message_id,
        "correlation_id": projection.correlation_id,
        "message_type": projection.message_type,
        "last_activity_at": projection.last_activity_at,
        "last_activity_type": projection.last_activity_type,
        "last_activity_tool": projection.last_activity_tool,
        "last_activity_preview": projection.last_activity_preview,
        "lease": lease_to_wire(projection.lease),
    }


def task_projection_from_job(job: Job) -> TaskProjection:
    """Build the cached task projection for a canonical task Job."""
    return task_projection_from_wire(task_job_to_wire(job))


def task_result_from_wire(data: dict[str, Any]) -> TaskResult:
    """Restore a TaskResult from decoded JSON/wire data."""
    return TaskResult(
        status=str(data.get("status") or ""),
        project_id=str(data.get("project_id") or "default"),
        task_id=str(data.get("task_id") or ""),
        reply=stored_message_from_wire(dict(data["reply"])) if isinstance(data.get("reply"), dict) else None,
        job=stored_message_from_wire(dict(data["job"])) if isinstance(data.get("job"), dict) else None,
        error=str(data["error"]) if data.get("error") is not None else None,
    )


def task_result_to_wire(result: TaskResult) -> dict[str, Any]:
    """Serialize a stored task result for wait/get callers."""
    wire: dict[str, Any] = {
        "status": result.status,
        "project_id": result.project_id,
        "task_id": result.task_id,
    }
    if result.reply is not None:
        wire["reply"] = reply_message_to_wire(result.reply)
    if result.job is not None:
        if isinstance(result.job, StoredMessage):
            wire["job"] = stored_message_to_wire(result.job)
        elif isinstance(result.job, TaskProjection):
            wire["job"] = task_projection_to_wire(result.job)
    if result.error is not None:
        wire["error"] = result.error
    return wire


def reply_result_to_wire(result: ReplyResult) -> dict[str, Any]:
    return {"status": "completed", "correlation_id": result.correlation_id, "reply": reply_message_to_wire(result.reply)}


def wait_blocker_to_wire(blocker: WaitBlocker) -> dict[str, Any]:
    status = str(blocker.status or "").upper()
    wire: dict[str, Any] = {"status": status.lower() if status else "blocked"}
    if blocker.correlation_id is not None:
        wire["correlation_id"] = blocker.correlation_id
    if blocker.project_id is not None:
        wire["project_id"] = blocker.project_id
    if blocker.task_id is not None:
        wire["task_id"] = blocker.task_id
    if blocker.error:
        wire["error"] = blocker.error
    elif blocker.summary:
        wire["error"] = blocker.summary
    if blocker.summary:
        wire["summary"] = blocker.summary
    if blocker.reason:
        wire["reason"] = blocker.reason
    return wire


def broker_event_from_wire(data: dict[str, Any]) -> BrokerEvent:
    """Restore a BrokerEvent from decoded JSON/wire data."""
    event_fields = dict(data)
    event_id = int(event_fields.pop("id", 0) or 0)
    event_time = str(event_fields.pop("time", "") or "")
    event_type = str(event_fields.pop("type", "") or "")
    preview = str(event_fields.pop("preview", "") or "")
    return BrokerEvent(id=event_id, time=event_time, type=event_type, preview=preview, fields=event_fields)


def broker_event_to_wire(event: BrokerEvent) -> dict[str, Any]:
    """Serialize an internal broker event record."""
    return {
        "id": event.id,
        "time": event.time,
        "type": event.type,
        "preview": event.preview,
        **dict(event.fields or {}),
    }


def worker_activity_from_wire(data: dict[str, Any] | WorkerActivityInput) -> WorkerActivityInput:
    """Build a typed worker-activity command from request data."""
    if isinstance(data, WorkerActivityInput):
        return data
    return WorkerActivityInput(
        project_id=str(data.get("project_id") or "default"),
        task_id=str(data["task_id"]) if data.get("task_id") is not None else None,
        conversation_id=str(data["conversation_id"]) if data.get("conversation_id") is not None else None,
        message_id=str(data["message_id"]) if data.get("message_id") is not None else None,
        agent_id=str(data["agent_id"]) if data.get("agent_id") is not None else None,
        session_lease_id=str(data["session_lease_id"]) if data.get("session_lease_id") is not None else None,
        activity_type=str(data.get("activity_type") or "activity"),
        phase=str(data["phase"]) if data.get("phase") is not None else None,
        tool_name=str(data["tool_name"]) if data.get("tool_name") is not None else None,
        detail=str(data.get("detail") or ""),
        status=str(data.get("status") or "RUNNING"),
        mode=str(data["mode"]) if data.get("mode") is not None else None,
    )


def activity_record_from_wire(data: dict[str, Any]) -> ActivityRecord:
    """Restore an ActivityRecord from decoded JSON/wire data."""
    return ActivityRecord(
        id=int(data.get("id") or 0),
        time=str(data.get("time") or ""),
        project_id=str(data.get("project_id") or "default"),
        task_id=data.get("task_id"),
        conversation_id=data.get("conversation_id"),
        message_id=data.get("message_id"),
        agent_id=data.get("agent_id"),
        session_lease_id=data.get("session_lease_id"),
        activity_type=str(data.get("activity_type") or "activity"),
        phase=data.get("phase"),
        tool_name=data.get("tool_name"),
        detail=str(data.get("detail") or ""),
        status=str(data.get("status") or "RUNNING"),
        mode=data.get("mode"),
    )


def activity_record_to_wire(activity: ActivityRecord) -> dict[str, Any]:
    """Serialize an internal worker activity record."""
    return {
        "id": activity.id,
        "time": activity.time,
        "project_id": activity.project_id,
        "task_id": activity.task_id,
        "conversation_id": activity.conversation_id,
        "message_id": activity.message_id,
        "agent_id": activity.agent_id,
        "session_lease_id": activity.session_lease_id,
        "activity_type": activity.activity_type,
        "phase": activity.phase,
        "tool_name": activity.tool_name,
        "detail": activity.detail,
        "status": activity.status,
        "mode": activity.mode,
    }


def message_input_to_wire(message: Any) -> dict[str, Any]:
    """Coerce a message boundary value into the broker wire dict shape."""
    from orchlink.core.envelope import MessageEnvelope

    if isinstance(message, StoredMessage):
        return stored_message_to_wire(message)
    if isinstance(message, MessageEnvelope):
        return envelope_to_dict(message)
    raise TypeError(
        f"Message input must be MessageEnvelope or StoredMessage; got {type(message).__name__}"
    )


def _stored_message_from_wire_data(data: dict[str, Any], now: str, *, preserve_status: bool) -> StoredMessage:
    envelope_fields = set(MessageEnvelope.model_fields.keys())
    envelope_payload = {key: value for key, value in data.items() if key in envelope_fields}
    stored = StoredMessage.from_envelope(MessageEnvelope.model_validate(envelope_payload), now=now)
    return replace(
        stored,
        status=str(data.get("status") or stored.status) if preserve_status else stored.status,
        created_at=data.get("created_at", stored.created_at),
        queued_at=data.get("queued_at", stored.queued_at),
        updated_at=data.get("updated_at", stored.updated_at),
    )


def message_input_to_stored(message: Any, now: str) -> StoredMessage:
    """Coerce a message boundary value into a StoredMessage domain object."""
    from orchlink.core.envelope import MessageEnvelope

    if isinstance(message, StoredMessage):
        if message.created_at is None and message.queued_at is None and message.updated_at is None:
            return replace(StoredMessage.from_envelope(message.envelope, now=now), status=message.status)
        return message
    if isinstance(message, MessageEnvelope):
        return StoredMessage.from_envelope(message, now=now)
    raise TypeError(
        f"Message input must be MessageEnvelope or StoredMessage; got {type(message).__name__}"
    )


def stored_message_from_wire(data: dict[str, Any]) -> StoredMessage:
    """Restore a StoredMessage from an existing broker/message wire dict."""
    now = str(data.get("updated_at") or data.get("queued_at") or data.get("created_at") or "")
    return _stored_message_from_wire_data(data, now=now, preserve_status=True)


def stored_message_to_wire(message: StoredMessage) -> dict[str, Any]:
    """Serialize a stored message record into the broker wire dict shape."""
    wire = envelope_to_dict(message.envelope)
    wire["status"] = message.status
    if message.created_at is not None:
        wire.setdefault("created_at", message.created_at)
    if message.queued_at is not None:
        wire["queued_at"] = message.queued_at
    if message.updated_at is not None:
        wire["updated_at"] = message.updated_at
    return wire


def reply_message_to_wire(message: StoredMessage) -> dict[str, Any]:
    """Serialize a typed reply message without adding broker queue metadata.

    Worker replies are validated as ``StoredMessage`` internally, but public
    reply outputs historically mirror the caller's reply envelope rather than
    active-message storage metadata. ``exclude_unset`` preserves that sparse
    boundary shape while logic can still use typed envelope fields.
    """
    return message.envelope.model_dump(mode="json", exclude_unset=True)


def conversation_from_wire(data: dict[str, Any]) -> Conversation:
    """Restore a Conversation domain object from decoded JSON/wire data."""
    participants_value = data.get("participants") or []
    if isinstance(participants_value, tuple):
        participants_value = list(participants_value)
    try:
        turn_value = int(data.get("turn") or 1)
    except (TypeError, ValueError):
        turn_value = 1
    try:
        max_turns_value = int(data.get("max_turns") or 6)
    except (TypeError, ValueError):
        max_turns_value = 6
    return Conversation(
        conversation_id=str(data.get("conversation_id") or ""),
        project_id=str(data.get("project_id") or "default"),
        participants=tuple(str(agent) for agent in participants_value if agent),
        status=str(data.get("status") or "OPEN"),
        turn=turn_value,
        max_turns=max_turns_value,
        from_agent=str(data["from_agent"]) if data.get("from_agent") is not None else None,
        to_agent=str(data["to_agent"]) if data.get("to_agent") is not None else None,
        message_type=str(data["message_type"]) if data.get("message_type") is not None else None,
        created_at=str(data["created_at"]) if data.get("created_at") is not None else None,
        updated_at=str(data["updated_at"]) if data.get("updated_at") is not None else None,
        last_message_preview=str(data.get("last_message_preview") or ""),
        preview=str(data.get("preview") or ""),
        last_activity_at=str(data["last_activity_at"]) if data.get("last_activity_at") is not None else None,
        last_activity_type=str(data["last_activity_type"]) if data.get("last_activity_type") is not None else None,
        last_activity_tool=str(data["last_activity_tool"]) if data.get("last_activity_tool") is not None else None,
        last_activity_preview=str(data["last_activity_preview"]) if data.get("last_activity_preview") is not None else None,
        worker_name=str(data["worker_name"]) if data.get("worker_name") is not None else None,
    )


def conversation_to_wire(conversation: Conversation) -> dict[str, Any]:
    """Serialize a conversation record into the broker conversation row."""
    return {
        "kind": "talk",
        "conversation_id": conversation.conversation_id,
        "project_id": conversation.project_id,
        "participants": list(conversation.participants),
        "mode": "TALK",
        "status": conversation.status,
        "turn": conversation.turn,
        "max_turns": conversation.max_turns,
        "from_agent": conversation.from_agent,
        "to_agent": conversation.to_agent,
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
        "last_message_preview": conversation.last_message_preview,
        "preview": conversation.preview,
        "message_type": conversation.message_type,
        "last_activity_at": conversation.last_activity_at,
        "last_activity_type": conversation.last_activity_type,
        "last_activity_tool": conversation.last_activity_tool,
        "last_activity_preview": conversation.last_activity_preview,
        "worker_name": conversation.worker_name,
    }


def lease_from_wire(lease: dict[str, Any] | JobLease | None) -> JobLease | None:
    """Restore a typed job lease from wire/snapshot data."""
    if lease is None:
        return None
    if isinstance(lease, JobLease):
        return lease
    if isinstance(lease, dict):
        return JobLease(
            holder=str(lease.get("holder") or ""),
            expires_at=str(lease.get("expires_at") or ""),
            epoch=int(lease.get("epoch") or 0),
            heartbeat_ms=max(int(lease.get("heartbeat_ms") or 15000), 1000),
        )
    raise TypeError(f"Job lease must be dict, JobLease, or None; got {type(lease).__name__}")


def lease_to_wire(lease: JobLease | dict[str, Any] | None) -> dict[str, Any] | None:
    """Serialize a job lease for API/CLI visibility (observability only)."""
    if lease is None:
        return None
    if isinstance(lease, dict):
        lease = lease_from_wire(lease)
    return {
        "holder": lease.holder,
        "expires_at": lease.expires_at,
        "epoch": lease.epoch,
        "heartbeat_ms": lease.heartbeat_ms,
    }


def task_job_to_wire(job: Job) -> dict[str, Any]:
    """Serialize a canonical task Job into the existing public task/job row."""
    payload = job_payload(job)
    return {
        "kind": "task",
        "project_id": job.project_id,
        "task_id": str(job.task_id or job.id),
        "conversation_id": job.conversation_id or payload.get("conversation_id"),
        "mode": payload.get("mode") or job.mode,
        "delivery": payload.get("delivery", "async"),
        "status": job.status,
        "from_agent": payload.get("from_agent") or job.route.from_agent,
        "to_agent": payload.get("to_agent") or job.route.to_agent,
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "preview": payload.get("preview", ""),
        "message_id": payload.get("message_id"),
        "correlation_id": payload.get("correlation_id"),
        "message_type": payload.get("message_type"),
        "last_activity_at": payload.get("last_activity_at"),
        "last_activity_type": payload.get("last_activity_type"),
        "last_activity_tool": payload.get("last_activity_tool"),
        "last_activity_preview": payload.get("last_activity_preview"),
        "lease": lease_to_wire(job.lease),
    }


def talk_job_to_wire(job: Job) -> dict[str, Any]:
    """Serialize a canonical talk Job into the existing public conversation row."""
    payload = job_payload(job)
    wire_status = payload.get("wire_status") or ("OPEN" if job.status == "RUNNING" else job.status)
    return {
        "kind": "talk",
        "conversation_id": str(job.conversation_id or job.id),
        "project_id": job.project_id,
        "participants": list(payload.get("participants") or []),
        "mode": "TALK",
        "status": wire_status,
        "turn": job.turn,
        "max_turns": job.max_turns,
        "from_agent": payload.get("from_agent") or job.route.from_agent,
        "to_agent": payload.get("to_agent") or job.route.to_agent,
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "last_message_preview": payload.get("last_message_preview", ""),
        "preview": payload.get("preview", ""),
        "message_type": payload.get("message_type"),
        "last_activity_at": payload.get("last_activity_at"),
        "last_activity_type": payload.get("last_activity_type"),
        "last_activity_tool": payload.get("last_activity_tool"),
        "last_activity_preview": payload.get("last_activity_preview"),
    }
