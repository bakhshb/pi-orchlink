"""Wire-view serializers for canonical Orchlink domain models.

The broker stores canonical OOP models internally, but the CLI/API still expose
stable dict shapes. Keep those conversions here so storage code does not hand-roll
public response rows in multiple places.
"""

from __future__ import annotations

from typing import Any

from orchlink.core.models import Job


def job_payload(job: Job) -> dict[str, Any]:
    return dict(job.payload or {})


def lease_to_wire(lease: dict[str, Any] | None) -> dict[str, Any] | None:
    """Serialize a job lease for API/CLI visibility (observability only)."""
    if not lease:
        return None
    return {
        "holder": lease.get("holder"),
        "expires_at": lease.get("expires_at"),
        "epoch": lease.get("epoch"),
        "heartbeat_ms": lease.get("heartbeat_ms"),
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
