"""Shared broker state names and transition helpers.

Keep this module storage-agnostic. The in-memory store uses it today; a future
store can import the same constants without changing protocol strings.
"""

from typing import Any


# Target canonical Job lifecycle. CREATED is a domain lifecycle state, not a
# protocol status currently emitted by the broker.
JOB_STATUS_LIFECYCLE = (
    "CREATED",
    "QUEUED",
    "DELIVERED",
    "RUNNING",
    "DONE",
    "FAILED",
    "TIMEOUT",
    "CANCELLED",
    "CLOSED",
)

JOB_KIND_TASK = "task"
JOB_KIND_TALK = "talk"

FAILED_STATUSES = {"FAILED", "TIMEOUT", "CANCELLED"}
BUSY_MESSAGE_STATUSES = {"PENDING", "QUEUED", "DELIVERED", "RUNNING", "IN_PROGRESS"}
ACTIVE_ACTIVITY_STATUSES = {"DELIVERED", "RUNNING", "IN_PROGRESS"}
ACTIVE_JOB_STATUSES = BUSY_MESSAGE_STATUSES | {"OPEN"}
TERMINAL_MESSAGE_STATUSES = {"DONE", "COMPLETED", "FAILED", "TIMEOUT", "CANCELLED", "CLOSED"}
TALK_MESSAGE_TYPES = {"CHAT_START", "CHAT_TURN", "CHAT_REPLY", "CHAT_CLOSE"}
WORKER_BOUND_TYPES = {"TASK", "CHAT_START", "CHAT_TURN", "CHAT_CLOSE"}
SESSION_ACTIVE_STATUS = "ACTIVE"
SESSION_TERMINAL_STATUSES = {"RELEASED", "EXPIRED"}


def normalize_status(value: object) -> str:
    return str(value or "").upper()


def normalize_message_type(value: object) -> str:
    return str(value or "").upper()


def is_busy_status(value: object) -> bool:
    return normalize_status(value) in BUSY_MESSAGE_STATUSES


def is_terminal_status(value: object) -> bool:
    return normalize_status(value) in TERMINAL_MESSAGE_STATUSES


def is_active_job_status(value: object) -> bool:
    return normalize_status(value) in ACTIVE_JOB_STATUSES


def is_active_activity_status(value: object) -> bool:
    return normalize_status(value) in ACTIVE_ACTIVITY_STATUSES


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


def reply_job_status(message_type: object, reply_status: object = "DONE") -> str:
    if normalize_message_type(message_type) == "CHAT_CLOSE":
        return "CLOSED"
    if normalize_status(reply_status or "DONE") in FAILED_STATUSES:
        return "FAILED"
    return "DONE"
