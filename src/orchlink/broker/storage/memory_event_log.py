"""Focused component: broker event log and activity event emission.

Lifted from ``memory.py``; preserves every method, body, and side effect.
Imports the shared state container instead of reaching through the facade.
"""

from __future__ import annotations

from typing import Any, Callable

from orchlink.broker.state import canonical_job_event_for_broker_event, is_talk_message_type
from orchlink.broker.storage.memory_state import InMemoryBrokerState, matches_project
from orchlink.core.models import ActivityRecord, BrokerEvent, BrokerEventContext, WorkerActivityInput
from orchlink.core.views import activity_record_to_wire, broker_event_to_wire


class MemoryEventLog:
    def __init__(
        self,
        state: InMemoryBrokerState,
        now: Callable[[], str],
    ) -> None:
        self._state = state
        self._now = now
        # Observability-only audit journal (M1). Late-bound via attach_journal
        # so the journal can be wired after the store is constructed.
        self._journal: Any = None

    @staticmethod
    def job_mode(message: dict[str, Any]) -> str:
        """Resolve the canonical job mode for a stored message dict.

        Mirrors the projection used by ``MemoryJobProjector.job_mode`` so the
        event log can surface a mode label without taking a callback.
        """
        payload = message.get("payload") or {}
        mode = payload.get("mode")
        if mode:
            return str(mode)
        if is_talk_message_type(message.get("type")):
            return "TALK"
        return "PLAN"

    @staticmethod
    def matches_project(item: dict[str, Any] | BrokerEvent | ActivityRecord, project_id: str | None) -> bool:
        if isinstance(item, BrokerEvent):
            item = broker_event_to_wire(item)
        elif isinstance(item, ActivityRecord):
            item = activity_record_to_wire(item)
        return matches_project(item, project_id)

    def payload_preview(self, payload: dict[str, Any]) -> str:
        for key in ("message", "intent", "topic", "summary", "stdout"):
            value = payload.get(key)
            if value:
                return str(value)
        return ""

    def message_preview(self, message: dict[str, Any]) -> str:
        return self.payload_preview(message.get("payload") or {})

    def append_event_locked(self, context: BrokerEventContext) -> dict[str, Any]:
        payload = context.fields["payload"] if "payload" in context.fields else {}
        preview = context.preview
        if preview is None and isinstance(payload, dict):
            preview = self.payload_preview(payload)
        event_fields = dict(context.fields)
        job_event = canonical_job_event_for_broker_event(context.event_type, event_fields)
        if job_event is not None:
            event_fields["job_event"] = job_event
        event = BrokerEvent(
            id=self._state.next_event_id,
            time=self._now(),
            type=context.event_type,
            preview=str(preview or "")[:300],
            fields=event_fields,
        )
        self._state.next_event_id += 1
        self._state.events.append(event)
        if len(self._state.events) > 1000:
            self._state.events = self._state.events[-1000:]
        event_wire = broker_event_to_wire(event)
        if self._journal is not None:
            try:
                self._journal.record_broker_event(event_wire)
            except Exception:
                # Observability-only: never fail a transition for the journal.
                pass
        return event_wire

    def event_fields(self, message: dict[str, Any], status: str | None = None) -> dict[str, Any]:
        payload = message.get("payload") or {}
        return {
            "project_id": message.get("project_id"),
            "task_id": message.get("task_id"),
            "conversation_id": message.get("conversation_id"),
            "message_id": message.get("message_id"),
            "correlation_id": message.get("correlation_id"),
            "from_agent": message.get("from_agent"),
            "to_agent": message.get("to_agent"),
            "message_type": message.get("type"),
            "mode": self.job_mode(message),
            "delivery": message.get("delivery"),
            "status": status or message.get("status"),
            "turn": message.get("turn"),
            "max_turns": message.get("max_turns"),
            "payload": payload,
        }

    def event_context(
        self,
        event_type: str,
        message: dict[str, Any],
        status: str | None = None,
        preview: str | None = None,
    ) -> BrokerEventContext:
        """Build a typed event context from a message wire view."""
        return BrokerEventContext.from_fields(
            event_type,
            **self.event_fields(message, status),
            preview=preview,
        )

    def activity_preview(self, activity: WorkerActivityInput | ActivityRecord) -> str:
        detail = str(activity.detail or activity.phase or activity.activity_type or "")
        tool_name = str(activity.tool_name or "")
        if tool_name and detail:
            return f"{tool_name}: {detail}"
        return tool_name or detail

    def record_activity_locked(
        self,
        activity: WorkerActivityInput,
        apply_activity_to_work: Callable[[ActivityRecord, str], None],
    ) -> dict[str, Any]:
        timestamp = self._now()
        stored = activity.to_record(self._state.next_activity_id, timestamp)
        stored_wire = activity_record_to_wire(stored)
        self._state.next_activity_id += 1
        self._state.activity.append(stored)
        if len(self._state.activity) > 1000:
            self._state.activity = self._state.activity[-1000:]
        apply_activity_to_work(stored, timestamp)
        self.append_event_locked(
            BrokerEventContext.from_fields(
                "worker_activity",
                project_id=stored.project_id,
                task_id=stored.task_id,
                conversation_id=stored.conversation_id,
                message_id=stored.message_id,
                from_agent=stored.agent_id,
                message_type="ACTIVITY",
                mode=stored.mode,
                status=stored.status,
                payload=stored_wire,
                preview=self.activity_preview(stored),
            )
        )
        return {"status": "recorded", "activity_id": stored.id}

    def list_activity_locked(
        self,
        item_id: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        selected = [activity_record_to_wire(item) for item in self._state.activity if self.matches_project(item, project_id)]
        if item_id:
            selected = [
                item
                for item in selected
                if str(item.get("task_id") or "") == item_id
                or str(item.get("conversation_id") or "") == item_id
                or str(item.get("message_id") or "") == item_id
            ]
        return selected[-limit:]

    def list_events_locked(self, since: int = 0, limit: int = 100, project_id: str | None = None) -> list[dict[str, Any]]:
        selected = [
            broker_event_to_wire(event)
            for event in self._state.events
            if event.id > since and self.matches_project(event, project_id)
        ]
        return selected[-limit:]


__all__ = ["MemoryEventLog"]