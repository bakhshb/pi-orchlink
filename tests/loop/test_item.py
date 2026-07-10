from datetime import datetime, timezone

import pytest

from orchlink.loop.domain import (
    BudgetExhausted,
    IllegalTransition,
    LoopAttempt,
    LoopItem,
    LoopItemState,
    LoopPolicy,
    MakerResult,
    ReasonCode,
    RetryPolicy,
    Verdict,
    VerifierMismatch,
    VerifierVerdict,
    WorkerAssignment,
)


def now():
    return datetime.now(timezone.utc)


def maker(name="maker", *, task_id=None, dispatched_at=None):
    return WorkerAssignment(
        worker_name=name,
        project_dir="/project",
        task_id=task_id,
        dispatched_at=dispatched_at,
    )


def verifier(name="review", *, task_id=None, dispatched_at=None):
    return WorkerAssignment(
        worker_name=name,
        project_dir="/project",
        task_id=task_id,
        dispatched_at=dispatched_at,
    )


def accepted(worker="review"):
    return VerifierVerdict(
        verdict=Verdict.ACCEPTED,
        reason_code=ReasonCode.ACCEPTED,
        detail="ok",
        required_fixes=(),
        verifier_worker=worker,
    )


def rejected(worker="review"):
    return VerifierVerdict(
        verdict=Verdict.REJECTED,
        reason_code=ReasonCode.REVIEW_FAILED,
        detail="fix it",
        required_fixes=("fix",),
        verifier_worker=worker,
    )


def blocker(worker="review"):
    return VerifierVerdict(
        verdict=Verdict.BLOCKER,
        reason_code=ReasonCode.BLOCKED,
        detail="blocked",
        required_fixes=(),
        verifier_worker=worker,
    )


def reserved_attempt(number=1):
    return LoopAttempt(number=number, maker=maker(task_id=f"reserved:{number}"))


def running_attempt(number=1):
    return LoopAttempt(number=number, maker=maker(task_id=f"T-{number}", dispatched_at=now()))


def awaiting_attempt(number=1):
    return LoopAttempt(
        number=number,
        maker=maker(task_id=f"T-{number}", dispatched_at=now()),
        maker_result=MakerResult("done"),
    )


def verifying_attempt(number=1, verifier_task="V-1"):
    return LoopAttempt(
        number=number,
        maker=maker(task_id=f"T-{number}", dispatched_at=now()),
        maker_result=MakerResult("done"),
        verifier=verifier(task_id=verifier_task),
    )


def completed_attempt(verdict, number=1):
    return LoopAttempt(
        number=number,
        maker=maker(task_id=f"T-{number}", dispatched_at=now()),
        maker_result=MakerResult("done"),
        verifier=verifier(task_id=f"V-{number}"),
        verdict=verdict,
        finished_at=now(),
    )


def drive_to_awaiting(item=None):
    item = item or LoopItem(item_id="I-1")
    return item.ready().dispatch(maker()).broker_sent(task_id="T-1").collect_result("done")


def test_legal_success_chain_reaches_done_only_after_accepted_verdict():
    item = LoopItem(item_id="I-1")
    assert item.state is LoopItemState.TRIAGED

    item = item.ready()
    assert item.state is LoopItemState.READY
    item = item.dispatch(maker())
    assert item.state is LoopItemState.DISPATCHING
    assert item.active_attempt is not None
    item = item.broker_sent(task_id="T-1")
    assert item.state is LoopItemState.RUNNING
    item = item.collect_result("result")
    assert item.state is LoopItemState.AWAITING_VERDICT
    item = item.start_verification(verifier())
    assert item.state is LoopItemState.VERIFYING
    item = item.apply_verdict(accepted())
    assert item.state is LoopItemState.DONE


def test_only_ready_items_can_dispatch():
    item = LoopItem(item_id="I-1")
    with pytest.raises(IllegalTransition) as exc:
        item.dispatch(maker())
    assert exc.value.state is LoopItemState.TRIAGED
    assert exc.value.method == "dispatch"


def test_dispatch_reservation_records_real_broker_task_then_confirms_running():
    item = LoopItem(item_id="I-1").ready().dispatch(maker())
    original = item

    item = item.record_broker_task("T-1")
    attempt = item.attempts[-1]

    assert item is not original
    assert item.state is LoopItemState.DISPATCHING
    assert attempt.maker.task_id == "T-1"
    assert attempt.maker.dispatched_at is not None

    running = item.confirm_dispatch_running()

    assert running is not item
    assert running.state is LoopItemState.RUNNING
    assert running.attempts[-1].maker.task_id == "T-1"
    assert running.attempts[-1].maker.dispatched_at is not None


def test_dispatch_reservation_rolls_back_to_ready():
    item = LoopItem(item_id="I-1").ready().dispatch(maker())
    rolled_back = item.rollback_dispatch()

    assert rolled_back is not item
    assert rolled_back.state is LoopItemState.READY
    assert rolled_back.attempts == ()


@pytest.mark.parametrize(
    "method,args",
    [
        ("record_broker_task", ("T-1",)),
        ("rollback_dispatch", ()),
        ("confirm_dispatch_running", ()),
    ],
)
def test_dispatch_aggregate_methods_reject_illegal_source_states(method, args):
    item = LoopItem(item_id="I-1")

    with pytest.raises(IllegalTransition):
        getattr(item, method)(*args)


def test_record_broker_task_rejects_reserved_task_ids():
    item = LoopItem(item_id="I-1").ready().dispatch(maker())

    with pytest.raises(ValueError):
        item.record_broker_task("reserved:I-1:1")
    with pytest.raises(ValueError):
        item.record_broker_task("")


def test_confirm_dispatch_running_requires_real_broker_task():
    item = LoopItem(item_id="I-1").ready().dispatch(maker())

    with pytest.raises(IllegalTransition):
        item.confirm_dispatch_running()


def test_new_aggregate_transitions_do_not_mutate_source_items():
    dispatching = LoopItem(item_id="I-1").ready().dispatch(maker())
    original_attempts = dispatching.attempts

    recorded = dispatching.record_broker_task("T-1")
    rolled_back = dispatching.rollback_dispatch()
    running = recorded.confirm_dispatch_running()

    assert dispatching.state is LoopItemState.DISPATCHING
    assert dispatching.attempts == original_attempts
    assert dispatching.attempts[-1].maker.task_id.startswith("reserved:")
    assert recorded.state is LoopItemState.DISPATCHING
    assert rolled_back.state is LoopItemState.READY
    assert running.state is LoopItemState.RUNNING

    rejected_item = drive_to_awaiting(LoopItem(item_id="I-2", retry_policy=RetryPolicy(max_attempts=1))).start_verification(verifier()).apply_verdict(rejected())
    blocked = rejected_item.block_retry_exhausted()

    assert rejected_item.state is LoopItemState.REJECTED
    assert rejected_item.blocker is None
    assert blocked.state is LoopItemState.BLOCKED

    done = drive_to_awaiting(LoopItem(item_id="I-3")).start_verification(verifier()).apply_verdict(accepted())
    attached = done.attach_accepted_evidence_id("E-1")

    assert done.attached_evidence_ids == ()
    assert attached.attached_evidence_ids == ("E-1",)


def test_broker_send_requires_reserved_attempt_in_state_on_construction():
    with pytest.raises(IllegalTransition):
        LoopItem(
            item_id="I-1",
            _state=LoopItemState.DISPATCHING,
            attempts=(LoopAttempt(number=1, maker=maker()),),
        )


def test_no_result_collection_outside_running():
    item = LoopItem(item_id="I-1").ready().dispatch(maker())
    with pytest.raises(IllegalTransition):
        item.collect_result("too early")


def test_verify_only_from_awaiting_verdict():
    item = LoopItem(item_id="I-1").ready().dispatch(maker()).broker_sent()
    with pytest.raises(IllegalTransition):
        item.start_verification(verifier())


def test_done_unreachable_without_apply_verdict_accepted():
    item = drive_to_awaiting().start_verification(verifier())
    with pytest.raises(IllegalTransition):
        item.ready()
    item = item.apply_verdict(rejected())
    assert item.state is LoopItemState.REJECTED


def test_verifier_worker_must_differ_by_default():
    item = drive_to_awaiting()
    with pytest.raises(VerifierMismatch):
        item.start_verification(maker("maker"))
    item = item.start_verification(maker("maker"), allow_same_worker=True)
    assert item.state is LoopItemState.VERIFYING
    assert item.attempts[-1].same_worker_verifier_override is True


def test_same_worker_override_is_immutable_and_roundtrips():
    awaiting = drive_to_awaiting()
    verifying = awaiting.start_verification(maker("maker"), allow_same_worker=True)
    restored = LoopItem.from_dict(verifying.to_dict())

    assert awaiting.attempts[-1].same_worker_verifier_override is False
    assert verifying.attempts[-1].same_worker_verifier_override is True
    assert restored.attempts[-1].same_worker_verifier_override is True


def test_at_most_one_active_attempt_per_item():
    with pytest.raises(IllegalTransition):
        LoopItem(
            item_id="I-1",
            _state=LoopItemState.RUNNING,
            attempts=(reserved_attempt(1), running_attempt(2)),
        )


def test_retry_budget_exhaustion_is_explicit():
    item = LoopItem(item_id="I-1", retry_policy=RetryPolicy(max_attempts=1))
    item = drive_to_awaiting(item).start_verification(verifier()).apply_verdict(rejected())
    with pytest.raises(BudgetExhausted):
        item.ready()


def test_rejected_item_blocks_after_retry_exhaustion():
    item = LoopItem(item_id="I-1", retry_policy=RetryPolicy(max_attempts=1))
    rejected_item = drive_to_awaiting(item).start_verification(verifier()).apply_verdict(rejected())

    blocked = rejected_item.block_retry_exhausted()

    assert blocked is not rejected_item
    assert blocked.state is LoopItemState.BLOCKED
    assert blocked.blocker == "retry_exhausted"


def test_block_retry_exhausted_rejects_illegal_or_non_exhausted_state():
    with pytest.raises(IllegalTransition):
        LoopItem(item_id="I-1").block_retry_exhausted()

    item = LoopItem(item_id="I-1", retry_policy=RetryPolicy(max_attempts=2))
    rejected_item = drive_to_awaiting(item).start_verification(verifier()).apply_verdict(rejected())
    with pytest.raises(ValueError):
        rejected_item.block_retry_exhausted()


def test_auto_merge_policy_is_rejected_at_construction():
    with pytest.raises(ValueError):
        LoopPolicy(auto_merge=True)


def test_max_concurrent_attempts_must_equal_one():
    with pytest.raises(ValueError):
        LoopPolicy(max_concurrent_attempts=2)


@pytest.mark.parametrize(
    "state,attempts,extra",
    [
        (LoopItemState.DONE, (), {}),
        (LoopItemState.DISPATCHING, (LoopAttempt(number=1, maker=maker()),), {}),
        (LoopItemState.RUNNING, (reserved_attempt(),), {}),
        (LoopItemState.AWAITING_VERDICT, (running_attempt(),), {}),
        (LoopItemState.VERIFYING, (awaiting_attempt(),), {}),
        (LoopItemState.REJECTED, (completed_attempt(accepted()),), {}),
        (LoopItemState.BLOCKED, (), {}),
        (LoopItemState.CANCELLED, (), {}),
        (LoopItemState.READY, (reserved_attempt(),), {}),
        (LoopItemState.TRIAGED, (reserved_attempt(),), {}),
        (LoopItemState.RUNNING, (running_attempt(2),), {}),
    ],
)
def test_illegal_construction_invariants_raise_illegal_transition(state, attempts, extra):
    with pytest.raises(IllegalTransition):
        LoopItem(item_id="I-1", _state=state, attempts=attempts, **extra)


def test_valid_terminal_and_active_constructions_are_allowed():
    assert LoopItem(
        item_id="I-1",
        _state=LoopItemState.DONE,
        attempts=(completed_attempt(accepted()),),
    ).state is LoopItemState.DONE
    assert LoopItem(
        item_id="I-2",
        _state=LoopItemState.BLOCKED,
        blocker="waiting",
    ).state is LoopItemState.BLOCKED
    assert LoopItem(
        item_id="I-3",
        _state=LoopItemState.CANCELLED,
        cancellation_reason="user",
    ).state is LoopItemState.CANCELLED
    assert LoopItem(
        item_id="I-4",
        _state=LoopItemState.VERIFYING,
        attempts=(verifying_attempt(),),
    ).state is LoopItemState.VERIFYING
    assert LoopItem(
        item_id="I-5",
        _state=LoopItemState.DISPATCHING,
        attempts=(LoopAttempt(number=1, maker=maker(task_id="T-1", dispatched_at=now())),),
    ).state is LoopItemState.DISPATCHING


def test_from_dict_uses_same_invariant_validation():
    with pytest.raises(IllegalTransition):
        LoopItem.from_dict({"item_id": "I-1", "state": "done", "attempts": []})


def test_accepted_evidence_id_attachment_is_idempotent():
    item = drive_to_awaiting().start_verification(verifier()).apply_verdict(accepted())

    attached = item.attach_accepted_evidence_id("E-1")
    attached_again = attached.attach_accepted_evidence_id("E-1")

    assert attached is not item
    assert attached.attached_evidence_ids == ("E-1",)
    assert attached_again.attached_evidence_ids == ("E-1",)


def test_accepted_evidence_id_attachment_rejects_illegal_state_and_empty_id():
    with pytest.raises(IllegalTransition):
        LoopItem(item_id="I-1").attach_accepted_evidence_id("E-1")

    item = drive_to_awaiting().start_verification(verifier()).apply_verdict(accepted())
    with pytest.raises(ValueError):
        item.attach_accepted_evidence_id("")


def test_valid_roundtrip_through_dict_and_from_dict():
    item = drive_to_awaiting().start_verification(verifier()).apply_verdict(accepted())
    restored = LoopItem.from_dict(item.to_dict())
    assert restored.state is LoopItemState.DONE
    assert restored.attempts[-1].verdict.verdict is Verdict.ACCEPTED


def test_legacy_attempt_without_same_worker_override_decodes_false():
    item = drive_to_awaiting().start_verification(verifier())
    data = item.to_dict()
    data["attempts"][-1].pop("same_worker_verifier_override")

    restored = LoopItem.from_dict(data)

    assert restored.attempts[-1].same_worker_verifier_override is False
