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
)
from orchlink.goal.models import AcceptanceCriterion, Goal, GoalBlocker, GoalEvidence
from orchlink.goal.policy import GoalPolicy, GoalPolicyError
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

    GoalPolicy.transition_status(goal, GoalStatus.READY.value, GoalEventType.GATE_APPROVED.value)
    GoalPolicy.transition_status(goal, GoalStatus.RUNNING.value, GoalEventType.TASK_DISPATCHED.value)
    GoalPolicy.transition_status(goal, GoalStatus.GATED.value, GoalEventType.SUBJECTIVE_SIGNOFF_REQUIRED.value)
    GoalPolicy.transition_status(goal, GoalStatus.DONE.value, GoalEventType.VERIFIED_DONE.value)

    assert goal.status is GoalStatus.DONE


def test_goal_lifecycle_rejects_invalid_transition() -> None:
    goal = Goal(id="G001", title="Lifecycle", source="text")

    with pytest.raises(GoalLifecycleError, match="draft -> done"):
        GoalPolicy.transition_status(goal, GoalStatus.DONE.value, GoalEventType.VERIFIED_DONE.value)

    with pytest.raises(GoalLifecycleError, match="cancelled -> running"):
        require_goal_transition("cancelled", "running")

    with pytest.raises(GoalLifecycleError, match="done -> cancelled"):
        require_goal_transition("done", "cancelled")


def test_goal_policy_rejects_illegal_terminal_transitions() -> None:
    for terminal in (GoalStatus.DONE, GoalStatus.CANCELLED):
        for target in GoalStatus:
            if target == terminal:
                continue
            goal = Goal(id="G001", title="Lifecycle", source="text", status=terminal)
            with pytest.raises(GoalLifecycleError):
                GoalPolicy.transition_status(goal, target.value, GoalEventType.VERIFIED_DONE.value)
            assert goal.status is terminal


def test_goal_policy_rejects_unknown_gate_name() -> None:
    goal = Goal(id="G001", title="Lifecycle", source="text")
    with pytest.raises(GoalPolicyError, match="Gate must be 'ac' or 'plan'"):
        GoalPolicy.approve_gate(goal, "bogus")
    assert goal.ac_gate is GateStatus.PENDING
    assert goal.plan_gate is GateStatus.PENDING


def test_goal_policy_rejects_claim_when_active_task_exists() -> None:
    goal = Goal(id="G001", title="Lifecycle", source="text", active_task_id="G001-WORK-001")
    with pytest.raises(GoalPolicyError, match="already has active task"):
        GoalPolicy.claim_task(goal, "G001-WORK-002")
    assert goal.active_task_id == "G001-WORK-001"


def test_goal_policy_rejects_completing_mismatched_active_task() -> None:
    goal = Goal(id="G001", title="Lifecycle", source="text", active_task_id="G001-WORK-001")
    with pytest.raises(GoalPolicyError, match="active task is"):
        GoalPolicy.complete_task(goal, "G001-WORK-002")
    assert goal.active_task_id == "G001-WORK-001"


def test_goal_policy_rejects_invalid_acceptance_status() -> None:
    goal = Goal(id="G001", title="Lifecycle", source="text")
    with pytest.raises(GoalLifecycleError, match="Unknown acceptance status"):
        GoalPolicy.set_ac_status(goal, "AC-1", "not-a-status")
    assert "AC-1" not in goal.ac_status


def test_goal_policy_does_not_mutate_source_instance_on_failure() -> None:
    """If a policy operation fails, the passed Goal instance must remain unchanged."""
    goal = Goal(id="G001", title="Lifecycle", source="text")
    original = goal.to_dict()

    with pytest.raises(GoalLifecycleError):
        GoalPolicy.transition_status(goal, GoalStatus.DONE.value, GoalEventType.VERIFIED_DONE.value)
    assert goal.to_dict() == original

    GoalPolicy.transition_status(goal, GoalStatus.READY.value, GoalEventType.GATE_APPROVED.value)

    GoalPolicy.claim_task(goal, "G001-WORK-001")
    after_claim = goal.to_dict()

    with pytest.raises(GoalPolicyError):
        GoalPolicy.claim_task(goal, "G001-WORK-002")
    assert goal.to_dict() == after_claim

    with pytest.raises(GoalPolicyError):
        GoalPolicy.complete_task(goal, "G001-WORK-999")
    assert goal.to_dict() == after_claim

    with pytest.raises(GoalPolicyError):
        GoalPolicy.approve_gate(goal, "bogus")
    assert goal.to_dict() == after_claim

    # Rejecting a status transition leaves status unchanged.
    with pytest.raises(GoalLifecycleError):
        GoalPolicy.transition_status(goal, GoalStatus.READY.value, GoalEventType.GATE_APPROVED.value)
    assert goal.to_dict() == after_claim

    # Cancelling a ready/running goal is legal and clears the active task.
    GoalPolicy.cancel(goal)
    assert goal.status is GoalStatus.CANCELLED
    assert goal.active_task_id is None

    # Once cancelled, no further transitions are allowed and state stays fixed.
    after_cancel = goal.to_dict()
    with pytest.raises(GoalLifecycleError):
        GoalPolicy.transition_status(goal, GoalStatus.RUNNING.value, GoalEventType.TASK_DISPATCHED.value)
    assert goal.to_dict() == after_cancel


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
