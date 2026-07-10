"""Pure Goal lifecycle policy.

This module is the single authority for mutating Goal lifecycle fields:
status, gates, active_task_id, evidence, blockers, deferred, and acceptance
status.  It validates every operation before touching state so the source Goal
instance remains unchanged on failure.

GoalStore coordinates transactions, files, history, and compatibility
projections; it never assigns lifecycle fields directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from orchlink.goal.lifecycle import (
    AcceptanceStatus,
    GateStatus,
    GoalEventType,
    GoalLifecycleError,
    GoalStatus,
    normalize_acceptance_status,
    normalize_gate_status,
    normalize_goal_status,
    require_goal_transition,
)
from orchlink.goal.models import Goal, GoalBlocker, GoalDeferral, GoalEvidence


class GoalPolicyError(GoalLifecycleError):
    """Raised when a goal lifecycle policy rule is violated."""


@dataclass
class PolicyOutcome:
    """Result of a policy mutation: a history event and optional projection update."""

    event: dict[str, Any] | None = None
    ac_id: str | None = None
    ac_status_value: str | None = None


class GoalPolicy:
    """Pure, stateless authority for Goal lifecycle transitions."""

    @staticmethod
    def approve_gate(goal: Goal, gate: str) -> PolicyOutcome:
        """Approve one gate and derive status from gate state."""
        _set_gate(goal, gate, GateStatus.APPROVED)
        _refresh_status_from_gates(goal)
        return PolicyOutcome(event={"type": GoalEventType.GATE_APPROVED.value, "gate": gate})

    @staticmethod
    def approve_combined_gate(goal: Goal) -> PolicyOutcome:
        """Approve both AC and plan gates and derive status."""
        _set_gate(goal, "ac", GateStatus.APPROVED)
        _set_gate(goal, "plan", GateStatus.APPROVED)
        _refresh_status_from_gates(goal)
        return PolicyOutcome(event={"type": GoalEventType.GATE_APPROVED.value, "gate": "combined"})

    @staticmethod
    def reject_combined_gate(goal: Goal, note: str = "") -> PolicyOutcome:
        """Reject both AC and plan gates and derive status."""
        _set_gate(goal, "ac", GateStatus.REJECTED)
        _set_gate(goal, "plan", GateStatus.REJECTED)
        _refresh_status_from_gates(goal)
        event: dict[str, Any] = {"type": GoalEventType.GATE_REJECTED.value, "gate": "combined"}
        if note:
            event["note"] = note
        return PolicyOutcome(event=event)

    @staticmethod
    def claim_task(
        goal: Goal,
        task_id: str,
        event_type: str = GoalEventType.TASK_DISPATCHED.value,
        detail: dict[str, Any] | None = None,
    ) -> PolicyOutcome:
        """Claim an active task if none is currently active."""
        if goal.active_task_id is not None:
            raise GoalPolicyError(f"Goal {goal.id} already has active task {goal.active_task_id}.")
        goal.active_task_id = task_id
        if event_type == GoalEventType.TASK_DISPATCHED.value and goal.status == GoalStatus.READY:
            goal.status = require_goal_transition(goal.status, GoalStatus.RUNNING)
        event: dict[str, Any] = {"type": event_type, "task_id": task_id}
        if detail:
            event.update(detail)
        return PolicyOutcome(event=event)

    @staticmethod
    def complete_task(goal: Goal, task_id: str) -> PolicyOutcome:
        """Release the active task only when task_id matches."""
        if goal.active_task_id != task_id:
            active = goal.active_task_id or "none"
            raise GoalPolicyError(f"Cannot complete task {task_id} for {goal.id}; active task is {active}.")
        goal.active_task_id = None
        return PolicyOutcome(event={"type": GoalEventType.TASK_RESULT.value, "task_id": task_id})

    @staticmethod
    def transition_status(
        goal: Goal,
        status: str,
        event_type: str,
        detail: dict[str, Any] | None = None,
    ) -> PolicyOutcome:
        """Apply a validated status transition and clear active task for terminals."""
        status_enum = require_goal_transition(goal.status, status)
        goal.status = status_enum
        if goal.status in {GoalStatus.BLOCKED, GoalStatus.DONE, GoalStatus.CANCELLED}:
            goal.active_task_id = None
        event: dict[str, Any] = {"type": event_type, "status": status_enum.value}
        if detail:
            event.update(detail)
        return PolicyOutcome(event=event)

    @staticmethod
    def cancel(goal: Goal, reason: str = "Cancelled by user.") -> PolicyOutcome:
        """Cancel the goal and clear any active task."""
        status_enum = require_goal_transition(goal.status, GoalStatus.CANCELLED)
        goal.status = status_enum
        goal.active_task_id = None
        return PolicyOutcome(event={"type": GoalEventType.CANCELLED.value, "reason": reason})

    @staticmethod
    def record_evidence(goal: Goal, evidence: dict[str, Any] | GoalEvidence) -> PolicyOutcome:
        """Attach typed evidence to the goal."""
        evidence_record = evidence if isinstance(evidence, GoalEvidence) else GoalEvidence.from_dict(evidence)
        goal.evidence.append(evidence_record)
        return PolicyOutcome(event={"type": GoalEventType.EVIDENCE.value, "evidence": evidence_record.to_dict()})

    @staticmethod
    def record_blocker(goal: Goal, blocker: dict[str, Any] | GoalBlocker) -> PolicyOutcome:
        """Attach a typed blocker to the goal."""
        blocker_record = blocker if isinstance(blocker, GoalBlocker) else GoalBlocker.from_dict(blocker)
        goal.blockers.append(blocker_record)
        return PolicyOutcome(event={"type": GoalEventType.BLOCKER.value, "blocker": blocker_record.to_dict()})

    @staticmethod
    def set_ac_status(goal: Goal, ac_id: str, status: str) -> PolicyOutcome:
        """Set an acceptance-criterion status (no history event; evidence records the check)."""
        status_enum = normalize_acceptance_status(status)
        goal.ac_status[ac_id] = status_enum.value
        return PolicyOutcome(ac_id=ac_id, ac_status_value=status_enum.value)

    @staticmethod
    def defer_ac(
        goal: Goal,
        ac_id: str,
        reason: str,
        detail: dict[str, Any] | None = None,
    ) -> PolicyOutcome:
        """Defer an acceptance criterion and record the deferral."""
        if not any(item.id == ac_id for item in goal.deferred):
            goal.deferred.append(GoalDeferral(id=ac_id, reason=reason, detail=detail))
        goal.ac_status[ac_id] = AcceptanceStatus.DEFERRED.value
        event: dict[str, Any] = {"type": GoalEventType.DEFERRED.value, "id": ac_id, "reason": reason}
        if detail:
            event["detail"] = detail
        return PolicyOutcome(event=event, ac_id=ac_id, ac_status_value=AcceptanceStatus.DEFERRED.value)


def _set_gate(goal: Goal, gate: str, status: GateStatus) -> None:
    gate_status = normalize_gate_status(status)
    if gate == "ac":
        goal.ac_gate = gate_status
        return
    if gate == "plan":
        goal.plan_gate = gate_status
        return
    raise GoalPolicyError("Gate must be 'ac' or 'plan'.")


def _refresh_status_from_gates(goal: Goal) -> None:
    """Derive draft/ready status from gate state without touching active goals."""
    current = normalize_goal_status(goal.status)
    if current == GoalStatus.CANCELLED:
        return
    gates_approved = goal.ac_gate == GateStatus.APPROVED and goal.plan_gate == GateStatus.APPROVED
    if gates_approved and current == GoalStatus.DRAFT:
        goal.status = require_goal_transition(goal.status, GoalStatus.READY)
    elif not gates_approved and current == GoalStatus.READY:
        goal.status = require_goal_transition(goal.status, GoalStatus.DRAFT)


__all__ = [
    "GoalPolicy",
    "GoalPolicyError",
    "PolicyOutcome",
]
