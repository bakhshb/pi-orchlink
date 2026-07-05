from __future__ import annotations

from collections.abc import Callable
from typing import Any

from orchlink.goal.lifecycle import GateStatus, GoalEventType, GoalStatus, goal_event_type_or_none
from orchlink.project.config import broker_api_key, broker_url


# Goal history event type -> audit journal action.
# Only transition-relevant events are journaled; noisy events (evidence,
# trial_recorded, artifacts_written, task_result, gap_detected, gate_required,
# cancel_task_failed) are skipped to keep the audit log meaningful.
GOAL_EVENT_ACTION: dict[GoalEventType, tuple[str, str | None]] = {
    GoalEventType.CREATED: ("goal.started", GoalStatus.DRAFT.value),
    GoalEventType.GATE_APPROVED: ("goal.gated", GateStatus.APPROVED.value),
    GoalEventType.GATE_REJECTED: ("goal.gated", GateStatus.REJECTED.value),
    GoalEventType.TASK_DISPATCHED: ("goal.worked", GoalStatus.RUNNING.value),
    GoalEventType.DERIVATION_DISPATCHED: ("goal.worked", GoalStatus.RUNNING.value),
    GoalEventType.AUDIT_DISPATCHED: ("goal.worked", GoalStatus.RUNNING.value),
    GoalEventType.VERIFIED_DONE: ("goal.done", GoalStatus.DONE.value),
    GoalEventType.WORKER_BLOCKER: ("goal.blocked", GoalStatus.BLOCKED.value),
    GoalEventType.BROKER_BLOCKED: ("goal.blocked", GoalStatus.BLOCKED.value),
    GoalEventType.DERIVATION_BLOCKED: ("goal.blocked", GoalStatus.BLOCKED.value),
    GoalEventType.AUDIT_BLOCKED: ("goal.blocked", GoalStatus.BLOCKED.value),
    GoalEventType.CAP_REACHED: ("goal.blocked", GoalStatus.BLOCKED.value),
    GoalEventType.MANUAL_VERIFICATION_REQUIRED: ("goal.blocked", GoalStatus.BLOCKED.value),
    GoalEventType.SUBJECTIVE_SIGNOFF_REQUIRED: ("goal.blocked", GoalStatus.BLOCKED.value),
    GoalEventType.CANCELLED: ("goal.cancelled", GoalStatus.CANCELLED.value),
    GoalEventType.SUBJECTIVE_APPROVED: ("goal.signedoff", None),
}


def journal_goal_transition(
    config: dict[str, Any],
    goal_id: str,
    action: str,
    before: str | None,
    after: str | None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Best-effort POST of a goal transition to the broker audit journal.

    Observability-only: failures are swallowed so a journal/broker outage
    never blocks a goal operation. The goal store remains the source of truth.
    """
    try:
        import httpx

        project_id = str(config.get("project_id") or "default")
        body = {
            "project_id": project_id,
            "actor": "orchlink.goal",
            "action": action,
            "target_type": "goal",
            "target_id": goal_id,
            "before": before,
            "after": after,
            "meta": meta or {},
        }
        headers = {"X-API-Key": broker_api_key(config), "X-Orchlink-Project-ID": project_id}
        with httpx.Client(base_url=broker_url(config), timeout=2.0) as client:
            response = client.post("/v1/journal", headers=headers, json=body)
            response.raise_for_status()
    except Exception:
        # Observability-only: never propagate a journal failure.
        pass


class GoalJournal:
    """Best-effort broker audit journal adapter for goal history events."""

    def __init__(
        self,
        config: dict[str, Any],
        transition_func: Callable[[dict[str, Any], str, str, str | None, str | None, dict[str, Any] | None], None] = journal_goal_transition,
    ) -> None:
        self.config = config
        self._transition_func = transition_func

    def append_for_history_event(self, goal_id: str, record: dict[str, Any]) -> None:
        event_type = goal_event_type_or_none(record.get("type"))
        mapping = GOAL_EVENT_ACTION.get(event_type) if event_type is not None else None
        if mapping is None:
            return
        action, default_after = mapping
        try:
            self._transition_func(
                self.config,
                goal_id,
                action,
                before=None,
                after=record.get("status") or default_after,
                meta={"event_type": str(record.get("type") or ""), "source": "goal"},
            )
        except Exception:
            # Observability-only: never let a journal failure block a goal transition.
            pass


__all__ = ["GoalJournal", "GOAL_EVENT_ACTION", "journal_goal_transition"]
