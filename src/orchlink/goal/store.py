from __future__ import annotations

from pathlib import Path
from typing import Any

from orchlink.goal.lifecycle import (
    AcceptanceStatus,
    GateStatus,
    GoalEventType,
    GoalStatus,
    normalize_acceptance_status,
    refresh_goal_status_from_gates,
    set_goal_gate,
    transition_goal,
)
from orchlink.goal.files import GOAL_ID_RE, GoalFileStore
from orchlink.goal.journal import GoalJournal, journal_goal_transition
from orchlink.goal.models import Goal, GoalBlocker, GoalDeferral, GoalEvidence, SourceType
from orchlink.project.config import orch_dir


__all__ = ["GOAL_ID_RE", "GoalStore", "GoalStoreError", "journal_goal_transition"]


class GoalStoreError(RuntimeError):
    """Raised when Goal Mode state cannot be read or written."""


class GoalStore:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.root = orch_dir(config) / "goals"
        self.files = GoalFileStore(self.root, GoalStoreError)
        self.journal = GoalJournal(config, transition_func=journal_goal_transition)

    def goal_dir(self, goal_id: str) -> Path:
        return self.files.goal_dir(goal_id)

    def list_goals(self) -> list[Goal]:
        return [self.load(goal_id) for goal_id in self.files.list_goal_ids()]

    def next_goal_id(self) -> str:
        return self.files.next_goal_id()

    def create_goal(self, title: str, source_type: SourceType, source_text: str) -> Goal:
        goal_id = self.next_goal_id()
        self.files.create_goal_dir(goal_id)
        goal = Goal(id=goal_id, title=title, source=source_type)
        self.files.write_source(goal_id, source_text)
        self.files.write_acceptance(goal_id, self.default_acceptance(goal))
        self.files.write_plan(goal_id, self.default_plan(goal))
        self.save(goal)
        self.append_history(goal_id, {"type": GoalEventType.CREATED.value, "source": source_type, "title": title})
        return goal

    def load(self, goal_id: str) -> Goal:
        return self.files.load_goal(goal_id)

    def save(self, goal: Goal) -> None:
        self.files.save_goal(goal)

    def append_history(self, goal_id: str, event: dict[str, Any]) -> None:
        record = self.files.append_history(goal_id, event)
        self.journal.append_for_history_event(goal_id, record)

    def history(self, goal_id: str) -> list[dict[str, Any]]:
        return self.files.history(goal_id)

    def approve_gate(self, goal_id: str, gate: str) -> Goal:
        goal = self.load(goal_id)
        try:
            set_goal_gate(goal, gate, GateStatus.APPROVED)
            refresh_goal_status_from_gates(goal)
        except ValueError as exc:
            raise GoalStoreError(str(exc)) from exc
        self.save(goal)
        self.append_history(goal.id, {"type": GoalEventType.GATE_APPROVED.value, "gate": gate})
        return goal

    def approve_combined_gate(self, goal_id: str) -> Goal:
        goal = self.load(goal_id)
        try:
            set_goal_gate(goal, "ac", GateStatus.APPROVED)
            set_goal_gate(goal, "plan", GateStatus.APPROVED)
            refresh_goal_status_from_gates(goal)
        except ValueError as exc:
            raise GoalStoreError(str(exc)) from exc
        self.save(goal)
        self.append_history(goal.id, {"type": GoalEventType.GATE_APPROVED.value, "gate": "combined"})
        return goal

    def reject_combined_gate(self, goal_id: str, note: str = "") -> Goal:
        goal = self.load(goal_id)
        try:
            set_goal_gate(goal, "ac", GateStatus.REJECTED)
            set_goal_gate(goal, "plan", GateStatus.REJECTED)
            refresh_goal_status_from_gates(goal)
        except ValueError as exc:
            raise GoalStoreError(str(exc)) from exc
        self.save(goal)
        event: dict[str, Any] = {"type": GoalEventType.GATE_REJECTED.value, "gate": "combined"}
        if note:
            event["note"] = note
        self.append_history(goal.id, event)
        return goal

    def record_task(self, goal_id: str, task_id: str, event_type: str = GoalEventType.TASK_DISPATCHED.value, detail: dict[str, Any] | None = None) -> Goal:
        goal = self.load(goal_id)
        goal.active_task_id = task_id
        if event_type == GoalEventType.TASK_DISPATCHED.value and goal.status == GoalStatus.READY:
            transition_goal(goal, GoalStatus.RUNNING)
        self.save(goal)
        event: dict[str, Any] = {"type": event_type, "task_id": task_id}
        if detail:
            event.update(detail)
        self.append_history(goal.id, event)
        return goal

    def record_task_result(self, goal_id: str, task_id: str, result: dict[str, Any]) -> Goal:
        goal = self.load(goal_id)
        if goal.active_task_id == task_id:
            goal.active_task_id = None
        self.save(goal)
        self.append_history(goal.id, {"type": GoalEventType.TASK_RESULT.value, "task_id": task_id, "result": result})
        return goal

    def write_artifacts(self, goal_id: str, acceptance: str | None = None, plan: str | None = None, coverage: str | None = None) -> None:
        if acceptance is not None:
            self.files.write_acceptance(goal_id, acceptance)
        if plan is not None:
            self.files.write_plan(goal_id, plan)
        if coverage is not None:
            self.files.write_coverage(goal_id, coverage)
        self.append_history(
            goal_id,
            {
                "type": GoalEventType.ARTIFACTS_WRITTEN.value,
                "acceptance": acceptance is not None,
                "plan": plan is not None,
                "coverage": coverage is not None,
            },
        )

    def write_audit(self, goal_id: str, audit: str, task_id: str) -> Path:
        path = self.files.write_audit(goal_id, audit)
        self.append_history(goal_id, {"type": GoalEventType.AUDIT.value, "task_id": task_id})
        return path

    def append_trial(self, goal_id: str, trial: dict[str, Any]) -> Path:
        path, record = self.files.append_trial(goal_id, trial)
        self.append_history(goal_id, {"type": GoalEventType.TRIAL_RECORDED.value, "trial": record})
        return path

    def record_evidence(self, goal_id: str, evidence: dict[str, Any] | GoalEvidence) -> Goal:
        goal = self.load(goal_id)
        evidence_record = evidence if isinstance(evidence, GoalEvidence) else GoalEvidence.from_dict(evidence)
        evidence_wire = evidence_record.to_dict()
        goal.evidence.append(evidence_record)
        self.save(goal)
        self.append_history(goal.id, {"type": GoalEventType.EVIDENCE.value, "evidence": evidence_wire})
        return goal

    def record_blocker(self, goal_id: str, blocker: dict[str, Any] | GoalBlocker) -> Goal:
        goal = self.load(goal_id)
        blocker_record = blocker if isinstance(blocker, GoalBlocker) else GoalBlocker.from_dict(blocker)
        blocker_wire = blocker_record.to_dict()
        goal.blockers.append(blocker_record)
        self.save(goal)
        self.append_history(goal.id, {"type": GoalEventType.BLOCKER.value, "blocker": blocker_wire})
        return goal

    def set_ac_status(self, goal_id: str, ac_id: str, status: str) -> Goal:
        goal = self.load(goal_id)
        status_value = normalize_acceptance_status(status).value
        goal.ac_status[ac_id] = status_value
        self.files.update_acceptance_status(goal_id, ac_id, status_value)
        self.save(goal)
        return goal

    def defer_ac(self, goal_id: str, ac_id: str, reason: str, detail: dict[str, Any] | None = None) -> Goal:
        goal = self.load(goal_id)
        if not any(item.id == ac_id for item in goal.deferred):
            goal.deferred.append(GoalDeferral(id=ac_id, reason=reason, detail=detail))
        goal.ac_status[ac_id] = AcceptanceStatus.DEFERRED.value
        self.files.update_acceptance_status(goal_id, ac_id, AcceptanceStatus.DEFERRED.value)
        self.save(goal)
        event: dict[str, Any] = {"type": GoalEventType.DEFERRED.value, "id": ac_id, "reason": reason}
        if detail:
            event["detail"] = detail
        self.append_history(goal.id, event)
        return goal

    def set_status(self, goal_id: str, status: str, event_type: str, detail: dict[str, Any] | None = None) -> Goal:
        goal = self.load(goal_id)
        try:
            transition_goal(goal, status)
        except ValueError as exc:
            raise GoalStoreError(str(exc)) from exc
        status_value = GoalStatus(goal.status).value
        if goal.status in {GoalStatus.BLOCKED, GoalStatus.DONE, GoalStatus.CANCELLED}:
            goal.active_task_id = None
        self.save(goal)
        event: dict[str, Any] = {"type": event_type, "status": status_value}
        if detail:
            event.update(detail)
        self.append_history(goal.id, event)
        return goal

    def next_task_number(self, goal_id: str) -> int:
        highest = 0
        for event in self.history(goal_id):
            task_id = str(event.get("task_id") or "")
            prefix = f"{goal_id}-WORK-"
            if task_id.startswith(prefix):
                suffix = task_id[len(prefix):]
                if suffix.isdigit():
                    highest = max(highest, int(suffix))
        return highest + 1

    def cancel(self, goal_id: str, reason: str = "Cancelled by user.") -> Goal:
        goal = self.load(goal_id)
        try:
            transition_goal(goal, GoalStatus.CANCELLED)
        except ValueError as exc:
            raise GoalStoreError(str(exc)) from exc
        goal.active_task_id = None
        self.save(goal)
        self.append_history(goal.id, {"type": GoalEventType.CANCELLED.value, "reason": reason})
        return goal

    @staticmethod
    def default_acceptance(goal: Goal) -> str:
        return (
            f"# Acceptance criteria for {goal.id}: {goal.title}\n\n"
            "Status: draft\n\n"
            "Goal Mode has captured the source. Derive or edit acceptance criteria here before approval.\n"
        )

    @staticmethod
    def default_plan(goal: Goal) -> str:
        return (
            f"# Plan for {goal.id}: {goal.title}\n\n"
            "Status: draft\n\n"
            "Goal Mode has captured the source. Derive or edit the execution plan here before approval.\n"
        )
