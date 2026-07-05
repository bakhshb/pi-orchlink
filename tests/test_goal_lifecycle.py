from __future__ import annotations

from pathlib import Path

import pytest

from orchlink.goal.lifecycle import (
    AcceptanceStatus,
    GateStatus,
    GoalEventType,
    GoalLifecycleError,
    GoalStatus,
    require_goal_transition,
    transition_goal,
)
from orchlink.goal.models import AcceptanceCriterion, Goal, GoalBlocker, GoalEvidence
from orchlink.goal.store import GoalStore, GoalStoreError


def test_goal_model_uses_typed_statuses_and_serializes_legacy_strings() -> None:
    goal = Goal.from_dict(
        {
            "id": "G001",
            "title": "Refactor goals",
            "source": "plan",
            "status": "ready",
            "ac_gate": "approved",
            "plan_gate": "approved",
        }
    )
    criterion = AcceptanceCriterion.from_dict({"id": "AC-1", "text": "Works", "status": "human-approved"})

    assert goal.status is GoalStatus.READY
    assert goal.ac_gate is GateStatus.APPROVED
    assert goal.plan_gate is GateStatus.APPROVED
    assert criterion.status is AcceptanceStatus.HUMAN_APPROVED
    assert goal.to_dict()["status"] == "ready"
    assert criterion.to_dict()["status"] == "human-approved"


def test_goal_lifecycle_allows_documented_forward_transitions() -> None:
    goal = Goal(id="G001", title="Lifecycle", source="text")

    transition_goal(goal, GoalStatus.READY)
    transition_goal(goal, GoalStatus.RUNNING)
    transition_goal(goal, GoalStatus.GATED)
    transition_goal(goal, GoalStatus.DONE)

    assert goal.status is GoalStatus.DONE


def test_goal_lifecycle_rejects_invalid_transition() -> None:
    goal = Goal(id="G001", title="Lifecycle", source="text")

    with pytest.raises(GoalLifecycleError, match="draft -> done"):
        transition_goal(goal, GoalStatus.DONE)

    with pytest.raises(GoalLifecycleError, match="cancelled -> running"):
        require_goal_transition("cancelled", "running")

    with pytest.raises(GoalLifecycleError, match="done -> cancelled"):
        require_goal_transition("done", "cancelled")


def test_goal_events_are_typed_constructs() -> None:
    assert GoalEventType.TASK_DISPATCHED.value == "task_dispatched"
    assert GoalEventType.VERIFIED_DONE.value == "verified_done"


def test_goal_state_uses_typed_evidence_blockers_and_legacy_yaml_shape(tmp_path: Path) -> None:
    store = GoalStore({"_project_root": str(tmp_path), "project_id": "demo"})
    goal = store.create_goal("Lifecycle", "text", "source")

    store.record_evidence(goal.id, {"type": "check", "criterion_id": "AC-1", "command": "pytest", "passed": True, "stdout": "ok"})
    store.record_blocker(goal.id, {"type": "decision", "message": "choose", "task_id": "T1", "extra": "kept"})
    store.defer_ac(goal.id, "AC-2", "non-core", {"exit_code": 1})

    loaded = store.load(goal.id)
    assert isinstance(loaded.evidence[0], GoalEvidence)
    assert loaded.evidence[0].detail["stdout"] == "ok"
    assert isinstance(loaded.blockers[0], GoalBlocker)
    assert loaded.blockers[0].detail["extra"] == "kept"
    assert loaded.deferred[0].detail == {"exit_code": 1}

    wire = loaded.to_dict()
    assert wire["evidence"][0]["passed"] is True
    assert wire["blockers"][0]["extra"] == "kept"
    assert wire["deferred"][0]["detail"] == {"exit_code": 1}


def test_goal_store_rejects_invalid_goal_status_transition(tmp_path: Path) -> None:
    store = GoalStore({"_project_root": str(tmp_path), "project_id": "demo"})
    goal = store.create_goal("Lifecycle", "text", "source")

    with pytest.raises(GoalStoreError, match="draft -> done"):
        store.set_status(goal.id, GoalStatus.DONE.value, GoalEventType.VERIFIED_DONE.value)

    assert store.load(goal.id).status is GoalStatus.DRAFT
