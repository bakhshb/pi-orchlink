"""Shared broker state names and transition helpers.

Keep this module storage-agnostic. The in-memory store uses it today; a future
store can import the same constants without changing protocol strings.
"""

from typing import Any

from orchlink.core.models import JobEvent, JobEventType
from orchlink.core.states import (
    ACTIVE_ACTIVITY_STATUSES,
    ACTIVE_JOB_STATUSES,
    BUSY_MESSAGE_STATUSES,
    FAILED_STATUSES,
    JOB_STATUS_LIFECYCLE,
    TERMINAL_MESSAGE_STATUSES,
    is_active_job_status,
    is_busy_status,
    is_terminal_status,
    normalize_status,
    reply_job_status,
)


JOB_KIND_TASK = "task"
JOB_KIND_TALK = "talk"

TALK_MESSAGE_TYPES = {"CHAT_START", "CHAT_TURN", "CHAT_REPLY", "CHAT_CLOSE"}
WORKER_BOUND_TYPES = {"TASK", "CHAT_START", "CHAT_TURN", "CHAT_CLOSE"}
SESSION_ACTIVE_STATUS = "ACTIVE"
TASK_STATUS_JOB_EVENTS: dict[str, JobEventType] = {
    "PENDING": JobEventType.QUEUED,
    "QUEUED": JobEventType.QUEUED,
    "DELIVERED": JobEventType.DELIVERED,
    "RUNNING": JobEventType.STARTED,
    "IN_PROGRESS": JobEventType.STARTED,
    "DONE": JobEventType.REPLIED,
    "COMPLETED": JobEventType.REPLIED,
    "FAILED": JobEventType.FAILED,
    "TIMEOUT": JobEventType.TIMED_OUT,
    "CANCELLED": JobEventType.CANCELLED,
}


def normalize_message_type(value: object) -> str:
    return str(value or "").upper()


def is_active_session_status(value: object) -> bool:
    return normalize_status(value) == SESSION_ACTIVE_STATUS


def is_talk_message_type(value: object) -> bool:
    return normalize_message_type(value) in TALK_MESSAGE_TYPES


def job_kind_for(item: dict[str, Any]) -> str:
    if item.get("task_id"):
        return JOB_KIND_TASK
    if item.get("conversation_id"):
        return JOB_KIND_TALK
    return str(item.get("kind") or "-").lower()


def job_id_for(item: dict[str, Any]) -> str:
    return str(item.get("task_id") or item.get("conversation_id") or item.get("message_id") or "-")


def job_matches_id(item: dict[str, Any], item_id: str) -> bool:
    return (
        str(item.get("task_id") or "") == item_id
        or str(item.get("conversation_id") or "") == item_id
        or str(item.get("message_id") or "") == item_id
    )


def canonical_job_event_for_broker_event(event_type: str, fields: dict[str, Any]) -> dict[str, Any] | None:
    task_id = fields.get("task_id")
    if not task_id:
        return None
    job_event_type = TASK_STATUS_JOB_EVENTS.get(normalize_status(fields.get("status")))
    if job_event_type is None:
        return None
    project_id = str(fields.get("project_id") or "default")
    job_id = str(task_id)
    event = JobEvent(type=job_event_type, project_id=project_id, job_id=job_id)
    return {
        "type": event.type.value,
        "status": event.status,
        "kind": JOB_KIND_TASK,
        "job_id": event.job_id,
        "project_id": event.project_id,
        "source_type": event_type,
    }

