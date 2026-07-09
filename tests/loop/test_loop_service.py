from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from orchlink.loop.adapters.state_repo import LoopStateRepo
from orchlink.loop.domain import (
    IllegalTransition,
    LoopItemState,
    MakerResult,
    ReasonCode,
    Verdict,
    VerifierMismatch,
    VerifierVerdict,
    Worktree,
)
from orchlink.loop.domain.verdict import utc_now
from orchlink.loop.services import ItemCandidate, LoopService
from orchlink.loop.services.loop_service import DEFAULT_RESERVATION_GRACE


class FakeBroker:
    def __init__(self, *, tasks=None, sessions=None):
        self.tasks = tasks or {}
        self.sessions = sessions or {}

    def get_task_status(self, task_id):
        return self.tasks.get(task_id)

    def get_session_active(self, lease_id):
        return self.sessions.get(lease_id)


class FakeGoalService:
    def __init__(self):
        self.calls = []

    def attach_evidence(self, *, goal_id, evidence):
        self.calls.append({"goal_id": goal_id, "evidence": evidence})


@pytest.fixture
def repo(tmp_path):
    return LoopStateRepo(tmp_path)


@pytest.fixture
def service(repo):
    return LoopService({}, repo)


def verdict(kind=Verdict.ACCEPTED, worker="review", reason=ReasonCode.ACCEPTED, detail="ok"):
    return VerifierVerdict(
        verdict=kind,
        reason_code=reason,
        detail=detail,
        required_fixes=(),
        verifier_worker=worker,
    )


def add_ready(service, item_id="I-1"):
    service.triage([ItemCandidate(item_id=item_id, title="title")])
    return service.ready(item_id)


def add_running(service, item_id="I-1"):
    add_ready(service, item_id)
    reservation = service.next_item(item_id, maker_worker="maker", worktree=None)
    service.mark_dispatched(item_id, attempt_no=reservation.attempt.number, task_id="T-maker")
    return service.mark_running(item_id, attempt_no=reservation.attempt.number)


def add_awaiting(service, item_id="I-1"):
    running = add_running(service, item_id)
    return service.collect_maker_result(item_id, attempt_no=running.attempts[-1].number, result=MakerResult("done"))


def add_verifying(service, item_id="I-1", maker="maker", verifier="review"):
    add_ready(service, item_id)
    reservation = service.next_item(item_id, maker_worker=maker, worktree=None)
    service.mark_dispatched(item_id, attempt_no=reservation.attempt.number, task_id="T-maker")
    service.mark_running(item_id, attempt_no=reservation.attempt.number)
    service.collect_maker_result(item_id, attempt_no=reservation.attempt.number, result=MakerResult("done"))
    return service.reserve_verification(item_id, attempt_no=reservation.attempt.number, verifier_worker=verifier)


def add_verifying_with_goal(service, item_id="I-1", goal_id="G001"):
    service.triage([ItemCandidate(item_id=item_id, title="title", goal_id=goal_id)])
    service.ready(item_id)
    reservation = service.next_item(item_id, maker_worker="maker", worktree=None)
    service.mark_dispatched(item_id, attempt_no=reservation.attempt.number, task_id=f"T-{item_id}")
    service.mark_running(item_id, attempt_no=reservation.attempt.number)
    service.collect_maker_result(item_id, attempt_no=reservation.attempt.number, result=MakerResult("done"))
    return service.reserve_verification(item_id, attempt_no=reservation.attempt.number, verifier_worker="review")


def test_triage_creates_items_and_skips_duplicates(service):
    created = service.triage(
        [
            ItemCandidate(item_id="I-1", title="one", source_type="git", source_ref="abc"),
            ItemCandidate(item_id="I-2", title="two"),
        ]
    )
    again = service.triage([ItemCandidate(item_id="I-1", title="changed")])

    assert [item.item_id for item in created] == ["I-1", "I-2"]
    assert again == []
    assert service.get("I-1").title == "one"


def test_ready_moves_allowed_states_and_refuses_active_or_done(service):
    item = service.triage([ItemCandidate(item_id="I-1")])[0]
    assert item.state is LoopItemState.TRIAGED
    assert service.ready("I-1").state is LoopItemState.READY

    reservation = service.next_item("I-1", maker_worker="maker", worktree=None)
    for state_name in ["I-1"]:
        with pytest.raises(IllegalTransition):
            service.ready(state_name)

    service.mark_dispatched("I-1", attempt_no=reservation.attempt.number, task_id="T-1")
    service.mark_running("I-1", attempt_no=reservation.attempt.number)
    with pytest.raises(IllegalTransition):
        service.ready("I-1")
    service.collect_maker_result("I-1", attempt_no=reservation.attempt.number, result=MakerResult("done"))
    service.reserve_verification("I-1", attempt_no=reservation.attempt.number, verifier_worker="review")
    with pytest.raises(IllegalTransition):
        service.ready("I-1")
    assert service.apply_verdict("I-1", attempt_no=reservation.attempt.number, verdict=verdict()).state is LoopItemState.DONE
    with pytest.raises(IllegalTransition):
        service.ready("I-1")

    service.triage([ItemCandidate(item_id="I-2"), ItemCandidate(item_id="I-3")])
    assert service.cancel("I-2", reason="nope").state is LoopItemState.CANCELLED
    with pytest.raises(IllegalTransition):
        service.ready("I-2")


def test_ready_moves_rejected_and_blocked_to_ready_when_budget_remains(service):
    verifying = add_verifying(service, "I-1")
    assert service.apply_verdict("I-1", attempt_no=verifying.attempt.number, verdict=verdict(Verdict.REJECTED, reason=ReasonCode.REVIEW_FAILED)).state is LoopItemState.REJECTED
    assert service.ready("I-1").state is LoopItemState.READY

    service.triage([ItemCandidate(item_id="I-2")])
    assert service.cancel("I-2", reason="skip").state is LoopItemState.CANCELLED

    service.triage([ItemCandidate(item_id="I-3")])
    item = service.get("I-3").block("blocked")
    with service.repo.transaction("test") as state:
        state.replace_item(item)
    assert service.ready("I-3").state is LoopItemState.READY


def test_next_item_reserves_attempt_and_second_call_raises(service):
    add_ready(service)
    reservation = service.next_item("I-1", maker_worker="maker", worktree=Worktree("/tmp/wt"))

    assert reservation.item.state is LoopItemState.DISPATCHING
    assert reservation.attempt.number == 1
    assert reservation.attempt.maker.worker_name == "maker"
    assert reservation.attempt.maker.task_id.startswith("reserved:")
    with pytest.raises(IllegalTransition):
        service.next_item("I-1", maker_worker="maker", worktree=None)


def test_mark_dispatched_and_rollback(service):
    add_ready(service)
    reservation = service.next_item("I-1", maker_worker="maker", worktree=None)
    dispatched = service.mark_dispatched("I-1", attempt_no=1, task_id="T-1")
    assert dispatched.state is LoopItemState.DISPATCHING
    assert dispatched.attempts[-1].maker.task_id == "T-1"
    assert dispatched.attempts[-1].maker.dispatched_at is not None
    with pytest.raises(ValueError):
        service.mark_dispatched("I-1", attempt_no=1, task_id="reserved:not-real")

    rolled_back = service.rollback_dispatch("I-1", 1)
    assert rolled_back.state is LoopItemState.READY
    assert rolled_back.attempts == ()


def test_mark_running_dispatching_to_running(service):
    add_ready(service)
    service.next_item("I-1", maker_worker="maker", worktree=None)
    dispatched = service.mark_dispatched("I-1", attempt_no=1, task_id="T-1")
    running = service.mark_running("I-1", attempt_no=1)
    assert running.state is LoopItemState.RUNNING
    assert running.attempts[-1].maker.dispatched_at == dispatched.attempts[-1].maker.dispatched_at


def test_collect_maker_result_only_from_running(service):
    add_ready(service)
    service.next_item("I-1", maker_worker="maker", worktree=None)
    with pytest.raises(IllegalTransition):
        service.collect_maker_result("I-1", attempt_no=1, result=MakerResult("too soon"))
    service.mark_dispatched("I-1", attempt_no=1, task_id="T-1")
    service.mark_running("I-1", attempt_no=1)
    awaiting = service.collect_maker_result("I-1", attempt_no=1, result=MakerResult("done"))
    assert awaiting.state is LoopItemState.AWAITING_VERDICT


def test_reserve_verification_only_from_awaiting(service):
    add_running(service)
    with pytest.raises(IllegalTransition):
        service.reserve_verification("I-1", attempt_no=1, verifier_worker="review")
    service.collect_maker_result("I-1", attempt_no=1, result=MakerResult("done"))
    reserved = service.reserve_verification("I-1", attempt_no=1, verifier_worker="review")
    assert reserved.item.state is LoopItemState.VERIFYING
    assert reserved.attempt.verifier.worker_name == "review"


def test_apply_verdict_accepted_reaches_done_only_by_method(service):
    verifying = add_verifying(service)
    result = service.apply_verdict("I-1", attempt_no=verifying.attempt.number, verdict=verdict())
    assert result.state is LoopItemState.DONE
    assert result.item.attempts[-1].verdict.verdict is Verdict.ACCEPTED


def test_apply_verdict_rejected_then_exhausted_blocks(service):
    verifying = add_verifying(service)
    first = service.apply_verdict("I-1", attempt_no=1, verdict=verdict(Verdict.REJECTED, reason=ReasonCode.REVIEW_FAILED))
    assert first.state is LoopItemState.REJECTED
    service.ready("I-1")
    reservation = service.next_item("I-1", maker_worker="maker", worktree=None)
    service.mark_dispatched("I-1", attempt_no=reservation.attempt.number, task_id="T-2")
    service.mark_running("I-1", attempt_no=reservation.attempt.number)
    service.collect_maker_result("I-1", attempt_no=reservation.attempt.number, result=MakerResult("done again"))
    service.reserve_verification("I-1", attempt_no=reservation.attempt.number, verifier_worker="review")
    second = service.apply_verdict("I-1", attempt_no=reservation.attempt.number, verdict=verdict(Verdict.REJECTED, reason=ReasonCode.REVIEW_FAILED))
    assert second.state is LoopItemState.BLOCKED
    assert second.item.blocker == "retry_exhausted"


def test_apply_verdict_blocker_blocks_with_reason(service):
    verifying = add_verifying(service)
    result = service.apply_verdict(
        "I-1",
        attempt_no=verifying.attempt.number,
        verdict=verdict(Verdict.BLOCKER, reason=ReasonCode.BLOCKED, detail="needs human"),
    )
    assert result.state is LoopItemState.BLOCKED
    assert result.item.blocker == "needs human"


def test_attach_evidence_to_goal_returns_false_when_item_is_not_done(service):
    add_ready(service)
    goal_service = FakeGoalService()

    assert service.attach_evidence_to_goal("I-1", goal_service=goal_service) is False
    assert goal_service.calls == []


def test_attach_evidence_to_goal_returns_false_without_goal_id_or_service(service):
    verifying = add_verifying(service)
    service.apply_verdict("I-1", attempt_no=verifying.attempt.number, verdict=verdict())
    goal_service = FakeGoalService()

    assert service.attach_evidence_to_goal("I-1", goal_service=goal_service) is False
    assert service.attach_evidence_to_goal("I-1", goal_service=None) is False
    assert goal_service.calls == []


def test_attach_evidence_to_goal_returns_false_for_non_accepted_verdicts(service):
    rejected = add_verifying_with_goal(service, "I-1")
    service.apply_verdict("I-1", attempt_no=rejected.attempt.number, verdict=verdict(Verdict.REJECTED, reason=ReasonCode.REVIEW_FAILED))
    blocked = add_verifying_with_goal(service, "I-2")
    service.apply_verdict("I-2", attempt_no=blocked.attempt.number, verdict=verdict(Verdict.BLOCKER, reason=ReasonCode.BLOCKED, detail="blocked"))
    goal_service = FakeGoalService()

    assert service.attach_evidence_to_goal("I-1", goal_service=goal_service) is False
    assert service.attach_evidence_to_goal("I-2", goal_service=goal_service) is False
    assert goal_service.calls == []


def test_attach_evidence_to_goal_records_accepted_loop_verdict_once(service):
    verifying = add_verifying_with_goal(service, "I-1", goal_id="G123")
    service.apply_verdict("I-1", attempt_no=verifying.attempt.number, verdict=verdict())
    goal_service = FakeGoalService()

    assert service.attach_evidence_to_goal("I-1", goal_service=goal_service) is True

    assert len(goal_service.calls) == 1
    assert goal_service.calls[0]["goal_id"] == "G123"
    evidence = goal_service.calls[0]["evidence"]
    assert evidence["type"] == "loop_verdict"
    assert evidence["loop_item_id"] == "I-1"
    assert evidence["verdict"] == "accepted"
    assert evidence["passed"] is True
    assert service.get("I-1").attached_evidence_ids == (evidence["evidence_id"],)


def test_attach_evidence_to_goal_is_idempotent(service):
    verifying = add_verifying_with_goal(service, "I-1", goal_id="G123")
    service.apply_verdict("I-1", attempt_no=verifying.attempt.number, verdict=verdict())
    goal_service = FakeGoalService()

    assert service.attach_evidence_to_goal("I-1", goal_service=goal_service) is True
    assert service.attach_evidence_to_goal("I-1", goal_service=goal_service) is True

    assert len(goal_service.calls) == 1
    assert len(service.get("I-1").attached_evidence_ids) == 1


def test_attach_evidence_to_goal_missing_item_raises(service):
    with pytest.raises(KeyError):
        service.attach_evidence_to_goal("missing", goal_service=FakeGoalService())


def test_same_worker_verifier_requires_override_and_marks_lower_confidence(service):
    verifying = add_verifying(service, maker="same", verifier="same")
    with pytest.raises(VerifierMismatch):
        service.apply_verdict("I-1", attempt_no=verifying.attempt.number, verdict=verdict(worker="same"))
    result = service.apply_verdict("I-1", attempt_no=verifying.attempt.number, verdict=verdict(worker="same"), allow_same_worker=True)
    assert result.state is LoopItemState.DONE
    assert result.lower_confidence is True
    assert result.note == "same_worker_verifier_override"


def test_cancel_from_non_terminal(service):
    service.triage([ItemCandidate(item_id="I-1"), ItemCandidate(item_id="I-2")])
    assert service.cancel("I-1", reason="user").state is LoopItemState.CANCELLED
    add_ready(service, "I-2")
    assert service.cancel("I-2", reason="user").cancellation_reason == "user"


def test_block_public_method_transitions_and_preserves_terminal_invariants(service):
    add_ready(service, "I-1")
    blocked = service.block("I-1", reason="external stop")
    assert blocked.state is LoopItemState.BLOCKED
    assert blocked.blocker == "external stop"

    service.triage([ItemCandidate(item_id="I-2")])
    service.cancel("I-2", reason="done elsewhere")
    with pytest.raises(IllegalTransition):
        service.block("I-2", reason="too late")

    add_verifying(service, "I-3")
    service.apply_verdict("I-3", attempt_no=1, verdict=verdict())
    with pytest.raises(IllegalTransition):
        service.block("I-3", reason="too late")

    with pytest.raises(KeyError):
        service.block("missing", reason="missing")


def test_recover_accepts_explicit_broker_client(repo):
    service = LoopService({}, repo)
    add_ready(service)
    service.next_item("I-1", maker_worker="maker", worktree=None)
    service.mark_dispatched("I-1", attempt_no=1, task_id="T-maker")

    report = service.recover(broker_client=FakeBroker(tasks={"T-maker": "running"}))

    assert report.items_changed == 1
    assert report.items_resumed == 1
    assert service.get("I-1").state is LoopItemState.RUNNING


def test_recover_explicit_none_uses_broker_unavailable_reason(repo):
    service = LoopService({}, repo)
    add_running(service)

    report = service.recover(broker_client=None)

    assert report.items_changed == 1
    assert report.items_blocked == 1
    assert service.get("I-1").blocker == "broker_unavailable"


def test_recover_expired_dispatch_without_task_returns_ready(repo):
    service = LoopService({}, repo, broker=FakeBroker(tasks={}))
    add_ready(service)
    service.next_item("I-1", maker_worker="maker", worktree=None)
    item = service.get("I-1")
    expired = replace(item.attempts[-1], reserved_at=utc_now() - DEFAULT_RESERVATION_GRACE - timedelta(seconds=1))
    with repo.transaction("test") as state:
        state.replace_item(replace(item, attempts=(expired,)))

    report = service.recover()

    assert report.items_changed == 1
    assert service.get("I-1").state is LoopItemState.READY
    assert service.get("I-1").attempts == ()


def test_recover_dispatching_real_broker_task_found_moves_to_running(repo):
    service = LoopService({}, repo, broker=FakeBroker(tasks={"T-maker": "running"}))
    add_ready(service)
    service.next_item("I-1", maker_worker="maker", worktree=None)
    service.mark_dispatched("I-1", attempt_no=1, task_id="T-maker")

    report = service.recover()

    assert report.items_changed == 1
    assert report.items_resumed == 1
    assert service.get("I-1").state is LoopItemState.RUNNING


def test_recover_running_cancelled_task_blocks(repo):
    service = LoopService({}, repo, broker=FakeBroker(tasks={"T-maker": "cancelled"}))
    add_running(service)

    report = service.recover()

    assert report.items_changed == 1
    assert report.items_blocked == 1
    assert service.get("I-1").state is LoopItemState.BLOCKED
    assert service.get("I-1").blocker == "task_cancelled"


def test_recover_running_with_inactive_worker_after_grace_blocks(repo):
    service = LoopService({}, repo, broker=FakeBroker(tasks={}, sessions={"lease-maker": False}))
    running = add_running(service)
    attempt = running.attempts[-1]
    stale_maker = replace(
        attempt.maker,
        session_lease_id="lease-maker",
        dispatched_at=utc_now() - DEFAULT_RESERVATION_GRACE - timedelta(seconds=1),
    )
    with repo.transaction("test") as state:
        state.replace_item(replace(running, attempts=(replace(attempt, maker=stale_maker),)))

    report = service.recover()

    assert report.items_changed == 1
    assert service.get("I-1").state is LoopItemState.BLOCKED
    assert service.get("I-1").blocker == "worker_stale"


def test_recover_running_timeout_status_blocks(repo):
    service = LoopService({}, repo, broker=FakeBroker(tasks={"T-maker": "timeout"}))
    add_running(service)

    report = service.recover()

    assert report.items_changed == 1
    assert service.get("I-1").state is LoopItemState.BLOCKED
    assert service.get("I-1").blocker == "task_timeout"


@pytest.mark.parametrize(
    "verdict_value,expected_state,expected_blocker",
    [
        (verdict(Verdict.ACCEPTED, worker="review"), LoopItemState.DONE, None),
        (verdict(Verdict.REJECTED, worker="review", reason=ReasonCode.REVIEW_FAILED), LoopItemState.REJECTED, None),
        (verdict(Verdict.BLOCKER, worker="review", reason=ReasonCode.BLOCKED, detail="blocked"), LoopItemState.BLOCKED, "blocked"),
    ],
)
def test_recover_verifying_with_verifier_result_applies_verdict(repo, verdict_value, expected_state, expected_blocker):
    service = LoopService({}, repo, broker=FakeBroker(tasks={"verify:I-1:1": verdict_value}))
    add_verifying(service)

    report = service.recover()

    assert report.items_changed == 1
    assert service.get("I-1").state is expected_state
    assert service.get("I-1").blocker == expected_blocker


def test_recover_without_broker_blocks_active_items(repo):
    service = LoopService({}, repo)
    add_ready(service, "I-1")
    service.next_item("I-1", maker_worker="maker", worktree=None)
    add_running(service, "I-2")
    add_verifying(service, "I-3")

    report = service.recover()

    assert report.items_changed == 3
    assert report.items_blocked == 3
    assert {service.get(item_id).blocker for item_id in ["I-1", "I-2", "I-3"]} == {"stale_unrecoverable"}


def test_ls_get_and_find_by_source_ref(service):
    service.triage([ItemCandidate(item_id="I-1", source_type="git", source_ref="abc")])
    assert [item.item_id for item in service.ls()] == ["I-1"]
    assert service.get("I-1").item_id == "I-1"
    assert service.get("missing") is None
    assert service.find_by_source_ref("git", "abc").item_id == "I-1"
    assert service.find_by_source_ref("git", "missing") is None


def test_sequential_concurrent_next_item_only_one_wins(service):
    add_ready(service)
    service.next_item("I-1", maker_worker="maker-1", worktree=None)
    with pytest.raises(IllegalTransition):
        service.next_item("I-1", maker_worker="maker-2", worktree=None)
