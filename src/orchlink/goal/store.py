from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from orchlink.goal.files import GOAL_ID_RE, GoalFileStore
from orchlink.goal.journal import GoalJournal, journal_goal_transition
from orchlink.goal.lifecycle import GoalEventType, GoalLifecycleError
from orchlink.goal.models import Goal, GoalBlocker, GoalEvidence, SourceType
from orchlink.goal.policy import GoalPolicy, PolicyOutcome
from orchlink.goal.transaction import GoalTransactionManager
from orchlink.project.config import orch_dir


__all__ = ["GOAL_ID_RE", "GoalStore", "GoalStoreError", "journal_goal_transition"]

class GoalStoreError(RuntimeError):
    """Raised when Goal Mode state cannot be read or written."""


class GoalStore:
    """Coordinates per-goal transactions, files, history, and projections.

    Lifecycle decisions are delegated to :class:`orchlink.goal.policy.GoalPolicy`;
    this store never assigns Goal lifecycle fields directly.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.root = orch_dir(config) / "goals"
        self.files = GoalFileStore(self.root, GoalStoreError)
        self.journal = GoalJournal(config, transition_func=journal_goal_transition)
        self.transactions = GoalTransactionManager(self.files, GoalStoreError)

    def goal_dir(self, goal_id: str) -> Path:
        return self.files.goal_dir(goal_id)

    @contextmanager
    def transaction(self, goal_id: str) -> Iterator[None]:
        with self.transactions.transaction(goal_id):
            yield

    def _apply_outcome_locked(self, goal_id: str, goal: Goal, outcome: PolicyOutcome) -> Goal:
        # goal.yaml is the authoritative committed state. Save it first, then
        # record history, then update the acceptance.md projection. If the
        # projection fails, the authoritative state is still committed and will
        # be reconciled on the next load/mutation.
        self._save_locked(goal)
        if outcome.event is not None:
            self._append_history_locked(goal_id, outcome.event)
        self.files.repair_acceptance_projection(goal_id, goal.ac_status)
        return goal

    def _apply_policy_locked(self, goal_id: str, goal: Goal, policy_call: Callable[[], PolicyOutcome]) -> Goal:
        try:
            outcome = policy_call()
        except GoalLifecycleError as exc:
            raise GoalStoreError(str(exc)) from exc
        return self._apply_outcome_locked(goal_id, goal, outcome)

    def list_goals(self) -> list[Goal]:
        return [self.load(goal_id) for goal_id in self.files.list_goal_ids()]

    def next_goal_id(self) -> str:
        return self.files.next_goal_id()

    def create_goal(self, title: str, source_type: SourceType, source_text: str) -> Goal:
        goal_id = self.next_goal_id()
        self.files.create_goal_dir(goal_id)
        with self.transaction(goal_id):
            goal = Goal(id=goal_id, title=title, source=source_type)
            self.files.write_source(goal_id, source_text)
            self.files.write_acceptance(goal_id, self.default_acceptance(goal))
            self.files.write_plan(goal_id, self.default_plan(goal))
            self.files.save_goal(goal)
            self._append_history_locked(goal_id, {"type": GoalEventType.CREATED.value, "source": source_type, "title": title})
            return goal

    def load(self, goal_id: str) -> Goal:
        with self.transaction(goal_id):
            goal = self.files.load_goal(goal_id)
            self._reconcile_projection_locked(goal_id, goal)
            return goal

    def save(self, goal: Goal) -> None:
        with self.transaction(goal.id):
            self._save_locked(goal)

    def _save_locked(self, goal: Goal) -> None:
        current = self.files.load_goal(goal.id)
        if current.updated_at != goal.updated_at:
            raise GoalStoreError(f"Goal {goal.id} changed on disk; reload before saving.")
        self.files.save_goal(goal)

    def append_history(self, goal_id: str, event: dict[str, Any]) -> None:
        with self.transaction(goal_id):
            self._append_history_locked(goal_id, event)

    def _append_history_locked(self, goal_id: str, event: dict[str, Any]) -> None:
        record = self.files.append_history(goal_id, event)
        self.journal.append_for_history_event(goal_id, record)

    def _reconcile_projection_locked(self, goal_id: str, goal: Goal) -> None:
        """Repair acceptance.md so it reflects the authoritative ac_status.

        Projections are derivable from goal.yaml. A mismatch means the
        projection is stale, so rewrite it from the authoritative map where
        the acceptance block contains matching criteria IDs.
        """
        self.files.repair_acceptance_projection(goal_id, goal.ac_status)

    def history(self, goal_id: str) -> list[dict[str, Any]]:
        with self.transaction(goal_id):
            return self.files.history(goal_id)

    def approve_gate(self, goal_id: str, gate: str) -> Goal:
        with self.transaction(goal_id):
            goal = self.files.load_goal(goal_id)
            return self._apply_policy_locked(goal_id, goal, lambda: GoalPolicy.approve_gate(goal, gate))

    def approve_combined_gate(self, goal_id: str) -> Goal:
        with self.transaction(goal_id):
            goal = self.files.load_goal(goal_id)
            return self._apply_policy_locked(goal_id, goal, lambda: GoalPolicy.approve_combined_gate(goal))

    def reject_combined_gate(self, goal_id: str, note: str = "") -> Goal:
        with self.transaction(goal_id):
            goal = self.files.load_goal(goal_id)
            return self._apply_policy_locked(goal_id, goal, lambda: GoalPolicy.reject_combined_gate(goal, note))

    def claim_next_task(
        self,
        goal_id: str,
        kind: str,
        event_type: str = GoalEventType.TASK_DISPATCHED.value,
        detail: dict[str, Any] | None = None,
    ) -> tuple[Goal, str]:
        prefix = f"{goal_id}-{kind}-"
        with self.transaction(goal_id):
            task_id = f"{prefix}{self._next_number_for_prefix_locked(goal_id, prefix):03d}"
            goal = self.files.load_goal(goal_id)
            outcome = self._policy_outcome(lambda: GoalPolicy.claim_task(goal, task_id, event_type=event_type, detail=detail))
            return self._apply_outcome_locked(goal_id, goal, outcome), task_id

    def record_task(self, goal_id: str, task_id: str, event_type: str = GoalEventType.TASK_DISPATCHED.value, detail: dict[str, Any] | None = None) -> Goal:
        with self.transaction(goal_id):
            goal = self.files.load_goal(goal_id)
            return self._apply_policy_locked(goal_id, goal, lambda: GoalPolicy.claim_task(goal, task_id, event_type=event_type, detail=detail))

    def record_task_result(self, goal_id: str, task_id: str, result: dict[str, Any]) -> Goal:
        with self.transaction(goal_id):
            goal = self.files.load_goal(goal_id)
            outcome = self._policy_outcome(lambda: GoalPolicy.complete_task(goal, task_id))
            outcome.event["result"] = result
            return self._apply_outcome_locked(goal_id, goal, outcome)

    def write_artifacts(self, goal_id: str, acceptance: str | None = None, plan: str | None = None, coverage: str | None = None) -> None:
        with self.transaction(goal_id):
            if acceptance is not None:
                self.files.write_acceptance(goal_id, acceptance)
            if plan is not None:
                self.files.write_plan(goal_id, plan)
            if coverage is not None:
                self.files.write_coverage(goal_id, coverage)
            self._append_history_locked(
                goal_id,
                {
                    "type": GoalEventType.ARTIFACTS_WRITTEN.value,
                    "acceptance": acceptance is not None,
                    "plan": plan is not None,
                    "coverage": coverage is not None,
                },
            )

    def write_audit(self, goal_id: str, audit: str, task_id: str) -> Path:
        with self.transaction(goal_id):
            path = self.files.write_audit(goal_id, audit)
            self._append_history_locked(goal_id, {"type": GoalEventType.AUDIT.value, "task_id": task_id})
            return path

    def append_trial(self, goal_id: str, trial: dict[str, Any]) -> Path:
        with self.transaction(goal_id):
            path, record = self.files.append_trial(goal_id, trial)
            self._append_history_locked(goal_id, {"type": GoalEventType.TRIAL_RECORDED.value, "trial": record})
            return path

    def _policy_outcome(self, policy_call: Callable[[], PolicyOutcome]) -> PolicyOutcome:
        try:
            return policy_call()
        except GoalLifecycleError as exc:
            raise GoalStoreError(str(exc)) from exc

    def record_evidence(self, goal_id: str, evidence: dict[str, Any] | GoalEvidence) -> Goal:
        with self.transaction(goal_id):
            goal = self.files.load_goal(goal_id)
            return self._apply_policy_locked(goal_id, goal, lambda: GoalPolicy.record_evidence(goal, evidence))

    def record_blocker(self, goal_id: str, blocker: dict[str, Any] | GoalBlocker) -> Goal:
        with self.transaction(goal_id):
            goal = self.files.load_goal(goal_id)
            return self._apply_policy_locked(goal_id, goal, lambda: GoalPolicy.record_blocker(goal, blocker))

    def set_ac_status(self, goal_id: str, ac_id: str, status: str) -> Goal:
        with self.transaction(goal_id):
            goal = self.files.load_goal(goal_id)
            return self._apply_policy_locked(goal_id, goal, lambda: GoalPolicy.set_ac_status(goal, ac_id, status))

    def defer_ac(self, goal_id: str, ac_id: str, reason: str, detail: dict[str, Any] | None = None) -> Goal:
        with self.transaction(goal_id):
            goal = self.files.load_goal(goal_id)
            return self._apply_policy_locked(goal_id, goal, lambda: GoalPolicy.defer_ac(goal, ac_id, reason, detail))

    def set_status(self, goal_id: str, status: str, event_type: str, detail: dict[str, Any] | None = None) -> Goal:
        with self.transaction(goal_id):
            goal = self.files.load_goal(goal_id)
            return self._apply_policy_locked(goal_id, goal, lambda: GoalPolicy.transition_status(goal, status, event_type, detail))

    def _next_number_for_prefix_locked(self, goal_id: str, prefix: str) -> int:
        highest = 0
        for event in self.files.history(goal_id):
            task_id = str(event.get("task_id") or "")
            if task_id.startswith(prefix):
                suffix = task_id[len(prefix) :]
                if suffix.isdigit():
                    highest = max(highest, int(suffix))
        return highest + 1

    def cancel(self, goal_id: str, reason: str = "Cancelled by user.") -> Goal:
        with self.transaction(goal_id):
            goal = self.files.load_goal(goal_id)
            return self._apply_policy_locked(goal_id, goal, lambda: GoalPolicy.cancel(goal, reason))

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
