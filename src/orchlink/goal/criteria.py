"""Goal Mode acceptance criteria selection and processing.

Reads ``acceptance.md``, selects the next core criterion for the work loop,
handles dependency checks, and processes noncore criteria. GoalRunner composes
this module instead of duplicating the logic.
"""

from __future__ import annotations

from typing import Any

from orchlink.goal.checks import (
    parse_acceptance_criteria,
    run_check,
)
from orchlink.goal.lifecycle import AcceptanceStatus
from orchlink.goal.models import AcceptanceCriterion
from orchlink.goal.store import GoalStore
from orchlink.project.config import project_root


class GoalCriteriaEngine:
    """Read, select, and process acceptance criteria for a goal."""

    def __init__(self, store: GoalStore, config: dict[str, Any] | None = None) -> None:
        self._store = store
        self._config = config

    def with_config(self, config: dict[str, Any]) -> "GoalCriteriaEngine":
        """Return a new engine bound to ``config`` for check execution."""
        return GoalCriteriaEngine(self._store, config=config)

    def criteria(self, goal_id: str) -> list[AcceptanceCriterion]:
        acceptance_path = self._store.goal_dir(goal_id) / "acceptance.md"
        if not acceptance_path.is_file():
            return []
        return parse_acceptance_criteria(acceptance_path.read_text(encoding="utf-8"))

    def status_for(self, goal_id: str, ac_id: str) -> str:
        """Return the authoritative acceptance-criterion status from goal.yaml."""
        return self._store.load(goal_id).ac_status.get(ac_id, AcceptanceStatus.PENDING.value)

    def selected(self, goal_id: str) -> AcceptanceCriterion | None:
        statuses = self._store.load(goal_id).ac_status
        for criterion in self.criteria(goal_id):
            if criterion.priority != "core":
                continue
            status = statuses.get(criterion.id, AcceptanceStatus.PENDING.value)
            if status in {"verified", "human-approved", "deferred"}:
                continue
            if criterion.type == "subjective" or not criterion.check:
                continue
            if self.dependencies_satisfied(goal_id, criterion):
                return criterion
        return None

    def pending_core_subjective(self, goal_id: str) -> list[AcceptanceCriterion]:
        statuses = self._store.load(goal_id).ac_status
        return [
            criterion
            for criterion in self.criteria(goal_id)
            if criterion.priority == "core"
            and statuses.get(criterion.id, AcceptanceStatus.PENDING.value) not in {"verified", "human-approved"}
            and (criterion.type == "subjective" or not criterion.check)
            and self.dependencies_satisfied(goal_id, criterion)
        ]

    def dependencies_satisfied(self, goal_id: str, criterion: AcceptanceCriterion) -> bool:
        statuses = self._store.load(goal_id).ac_status
        return all(statuses.get(dep) in {"verified", "human-approved"} for dep in criterion.depends_on)

    def all_required_criteria_satisfied(self, goal_id: str) -> bool:
        criteria = self.criteria(goal_id)
        if not criteria:
            return False
        statuses = self._store.load(goal_id).ac_status
        for criterion in criteria:
            status = statuses.get(criterion.id, AcceptanceStatus.PENDING.value)
            if criterion.priority == "core" and status not in {"verified", "human-approved"}:
                return False
        return True

    def process_noncore(self, goal_id: str, *, timeout_seconds: int = 1800) -> None:
        if self._config is None:
            raise GoalCriteriaConfigError("process_noncore requires an engine bound to config; call with_config().")
        statuses = self._store.load(goal_id).ac_status
        for criterion in self.criteria(goal_id):
            if criterion.priority == "core" or statuses.get(criterion.id, AcceptanceStatus.PENDING.value) in {"verified", "deferred"}:
                continue
            if not self.dependencies_satisfied(goal_id, criterion):
                continue
            if criterion.type == "subjective" or not criterion.check:
                self._store.defer_ac(goal_id, criterion.id, "Non-core subjective or manually verified criterion deferred.")
                continue
            result = run_check(criterion.check, cwd=project_root(self._config), timeout_seconds=timeout_seconds)
            self._store.record_evidence(
                goal_id,
                {
                    "type": "check",
                    "criterion_id": criterion.id,
                    "command": result.command,
                    "exit_code": result.exit_code,
                    "passed": result.passed,
                    "stdout": result.stdout[-4000:],
                    "stderr": result.stderr[-4000:],
                },
            )
            if result.passed:
                self._store.set_ac_status(goal_id, criterion.id, "verified")
            else:
                self._store.defer_ac(
                    goal_id,
                    criterion.id,
                    "Non-core objective check failed.",
                    {"command": result.command, "exit_code": result.exit_code},
                )

    @staticmethod
    def criterion_for_check(criteria: list[AcceptanceCriterion], command: str) -> str | None:
        for criterion in criteria:
            if criterion.check == command:
                return criterion.id
        return None


class GoalCriteriaConfigError(RuntimeError):
    """Raised when criteria operations are invoked without a config-bound engine."""


__all__ = [
    "GoalCriteriaConfigError",
    "GoalCriteriaEngine",
]