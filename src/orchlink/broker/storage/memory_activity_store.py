"""Focused component: activity record storage and listing.

Lifted from ``memory.py``; preserves every method, body, and side effect.
Wraps the existing ``MemoryEventLog`` helpers so the event log remains the
source of truth for the audit journal, while this component owns the activity
lifecycle surface that the facade exposes (``record_activity``,
``list_activity``).
"""

from __future__ import annotations

from typing import Any, Callable

from orchlink.broker.storage.memory_event_log import MemoryEventLog
from orchlink.broker.storage.memory_state import InMemoryBrokerState
from orchlink.core.models import ActivityRecord, WorkerActivityInput


class MemoryActivityStore:
    """Focused component for activity records.

    Owns activity storage and listing. Wraps the existing `MemoryEventLog`
    helpers so the event log remains the source of truth for the audit
    journal, while this component owns the activity lifecycle surface that
    the facade exposes (`record_activity`, `list_activity`).

    Shares `InMemoryBrokerState`, the clock, and the `MemoryEventLog` with
    the facade. Holds no independent state copy.
    """

    def __init__(
        self,
        state: InMemoryBrokerState,
        event_log: MemoryEventLog,
        now: Callable[[], str],
        apply_activity_to_work: Callable[[ActivityRecord, str], None],
    ) -> None:
        self._state = state
        self._event_log = event_log
        self._now = now
        self._apply_activity_to_work = apply_activity_to_work

    def record_activity_locked(self, activity: WorkerActivityInput) -> dict[str, Any]:
        return self._event_log.record_activity_locked(
            activity,
            self._apply_activity_to_work,
        )

    def list_activity_locked(
        self,
        item_id: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self._event_log.list_activity_locked(
            item_id=item_id,
            limit=limit,
            project_id=project_id,
        )


__all__ = ["MemoryActivityStore"]