"""Session lifecycle primitives shared outside broker storage."""

from __future__ import annotations

from orchlink.core.models import SessionStatus


SESSION_ACTIVE_STATUS = SessionStatus.ACTIVE.value
SESSION_STATUSES = tuple(status.value for status in SessionStatus)


class SessionLifecycleError(ValueError):
    """Raised when a session lifecycle value is invalid."""


def normalize_session_status(value: object) -> SessionStatus:
    try:
        return SessionStatus(str(value or "").upper())
    except ValueError as exc:
        raise SessionLifecycleError(f"Unknown session status: {value!r}") from exc


def is_active_session_status(value: object) -> bool:
    try:
        return normalize_session_status(value) == SessionStatus.ACTIVE
    except SessionLifecycleError:
        return False


__all__ = [
    "SESSION_ACTIVE_STATUS",
    "SESSION_STATUSES",
    "SessionLifecycleError",
    "SessionStatus",
    "is_active_session_status",
    "normalize_session_status",
]
