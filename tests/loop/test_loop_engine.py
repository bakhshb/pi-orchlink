from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from orchlink.loop.adapters.state_repo import LoopStateRepo
from orchlink.loop.domain import LoopItemState, MakerResult, ReasonCode, Verdict, VerifierVerdict
from orchlink.loop.services import ItemCandidate, LoopEngine, LoopService, MakerDispatchError, MakerTimeoutError, TriageService, VerifierService, WorkerService
from orchlink.loop.services.verifier_service import VerifierHandle


class FakeBrokerClient:
    def __init__(self, status=None):
        self.status = status

    def get_task_status(self, task_id):
        if callable(self.status):
            return self.status(task_id)
        if isinstance(self.status, dict):
            return self.status.get(task_id, self.status.get("*"))
        return self.status

    def get_session_active(self, lease_id):
        return True


class FakeGateway:
    def __init__(self, verdict: Verdict = Verdict.ACCEPTED, *, maker_timeout: bool = False):
        self.verdict = verdict
        self.maker_timeout = maker_timeout
        self.maker_dispatches = []

    async def dispatch_maker(self, maker_assignment, prompt):
        self.maker_dispatches.append((maker_assignment, prompt))
        return VerifierHandle(task_id=f"real-{maker_assignment.task_id}", worker_name=maker_assignment.worker_name)

    async def dispatch_verifier(self, verifier_assignment, prompt):
        return VerifierHandle(task_id="V-1", worker_name=verifier_assignment.worker_name)

    async def await_result(self, handle, timeout_seconds):
        if self.maker_timeout and handle.worker_name == "maker":
            raise TimeoutError("maker too slow")
        if handle.worker_name == "maker":
            return MakerResult("maker done")
        reason = ReasonCode.ACCEPTED.value if self.verdict is Verdict.ACCEPTED else ReasonCode.REVIEW_FAILED.value
        return MakerResult(
            "\n".join(
                [
                    f"VERDICT: {self.verdict.value.upper()}",
                    f"REASON: {reason}",
                    "DETAIL: checked",
                    "FIXES: none",
                    f"VERIFIER_WORKER: {handle.worker_name}",
                ]
            )
        )


class FakeMakerService:
    def __init__(self, *, dispatch_error=None, collect_error=None):
        self.dispatch_error = dispatch_error
        self.collect_error = collect_error
        self.started = []
        self.awaited = []

    async def start_maker(self, item, attempt, *, worktree=None):
        self.started.append((item.item_id, attempt.number, worktree))
        if self.dispatch_error:
            raise self.dispatch_error
        return VerifierHandle(task_id=f"real-{item.item_id}-{attempt.number}", worker_name=attempt.maker.worker_name)

    async def await_maker_result(self, handle, timeout_seconds=1800):
        self.awaited.append((handle, timeout_seconds))
        if self.collect_error:
            raise self.collect_error
        return MakerResult("maker done")


class RaisingTriageService:
    async def run_once(self):
        raise RuntimeError("triage boom")


class FakeGoalService:
    def __init__(self):
        self.calls = []

    def attach_evidence(self, *, goal_id, evidence):
        self.calls.append({"goal_id": goal_id, "evidence": evidence})


class RecordingVerifierService:
    def __init__(self):
        self.calls = []

    async def dispatch_and_collect(self, item, attempt, *, worktree=None, run_checks=False, check_service=None):
        self.calls.append({"run_checks": run_checks, "check_service": check_service})
        return VerifierVerdict(
            verdict=Verdict.ACCEPTED,
            reason_code=ReasonCode.ACCEPTED,
            detail="ok",
            required_fixes=(),
            verifier_worker=attempt.verifier.worker_name,
        )


def fake_clock():
    return datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_loop(tmp_path, *, broker=None):
    return LoopService({}, LoopStateRepo(tmp_path), broker=broker)


def make_verifier(verdict: Verdict = Verdict.ACCEPTED):
    return VerifierService({}, gateway=FakeGateway(verdict))


def make_worker_service():
    return FakeMakerService()


def add_ready(loop_service: LoopService, item_id="I-1"):
    loop_service.triage([ItemCandidate(item_id=item_id, title=item_id)])
    return loop_service.ready(item_id)


def add_reserved_dispatching(loop_service: LoopService, item_id="I-1"):
    add_ready(loop_service, item_id)
    loop_service.next_item(item_id, maker_worker="maker", worktree=None)
    return loop_service.get(item_id)


def add_dispatching(loop_service: LoopService, item_id="I-1"):
    add_ready(loop_service, item_id)
    reservation = loop_service.next_item(item_id, maker_worker="maker", worktree=None)
    loop_service.mark_dispatched(item_id, attempt_no=reservation.attempt.number, task_id=f"T-{item_id}")
    return loop_service.get(item_id)


def add_running(loop_service: LoopService, item_id="I-1"):
    item = add_dispatching(loop_service, item_id)
    loop_service.mark_running(item_id, attempt_no=item.attempts[-1].number)
    return loop_service.get(item_id)


def add_awaiting(loop_service: LoopService, item_id="I-1"):
    item = add_running(loop_service, item_id)
    loop_service.collect_maker_result(item_id, attempt_no=item.attempts[-1].number, result=MakerResult("done"))
    return loop_service.get(item_id)


def add_verifying(loop_service: LoopService, item_id="I-1"):
    item = add_awaiting(loop_service, item_id)
    loop_service.reserve_verification(item_id, attempt_no=item.attempts[-1].number, verifier_worker="review")
    return loop_service.get(item_id)


def test_tick_with_no_work_returns_zero_counters(tmp_path):
    loop_service = make_loop(tmp_path)
    engine = LoopEngine({}, loop_service, clock=fake_clock, sleeper=lambda seconds: None)

    result = engine.tick()

    assert result.items_dispatched == 0
    assert result.items_verified == 0
    assert result.items_blocked == 0
    assert result.items_done == 0
    assert result.errors == []


def test_tick_refuses_active_attempts_by_default(tmp_path):
    loop_service = make_loop(tmp_path)
    add_dispatching(loop_service)
    engine = LoopEngine({}, loop_service, broker_client=FakeBrokerClient("completed"), clock=fake_clock)

    result = engine.tick()

    assert result.items_dispatched == 0
    assert any("active attempts present" in note for note in result.notes)
    assert loop_service.get("I-1").state is LoopItemState.DISPATCHING


def test_tick_dispatches_ready_item_through_done(tmp_path):
    loop_service = make_loop(tmp_path)
    add_ready(loop_service)
    broker = FakeBrokerClient({"*": {"status": "completed", "result": MakerResult("maker done")}})
    engine = LoopEngine({}, loop_service, verifier_service=make_verifier(), worker_service=make_worker_service(), broker_client=broker, clock=fake_clock)

    result = engine.tick()

    assert result.items_dispatched == 1
    assert result.items_verified == 1
    assert result.items_done == 1
    assert result.errors == []
    assert loop_service.get("I-1").state is LoopItemState.DONE


def test_tick_attaches_accepted_loop_verdict_to_goal_service(tmp_path):
    loop_service = make_loop(tmp_path)
    loop_service.triage([ItemCandidate(item_id="I-1", title="I-1", goal_id="G123")])
    loop_service.ready("I-1")
    broker = FakeBrokerClient({"*": {"status": "completed", "result": MakerResult("maker done")}})
    goal_service = FakeGoalService()
    engine = LoopEngine(
        {},
        loop_service,
        verifier_service=make_verifier(),
        worker_service=make_worker_service(),
        broker_client=broker,
        goal_service=goal_service,
    )

    result = engine.tick()

    assert goal_service.calls[0]["goal_id"] == "G123"
    assert goal_service.calls[0]["evidence"]["type"] == "loop_verdict"
    assert "I-1: goal_evidence_attached" in result.notes


def test_tick_handles_rejected_verdict_and_retry_exhaustion(tmp_path):
    loop_service = make_loop(tmp_path)
    add_ready(loop_service)
    broker = FakeBrokerClient({"*": {"status": "completed", "result": MakerResult("maker done")}})
    engine = LoopEngine({}, loop_service, verifier_service=make_verifier(Verdict.REJECTED), worker_service=make_worker_service(), broker_client=broker)

    first = engine.tick()
    assert first.items_verified == 1
    assert loop_service.get("I-1").state is LoopItemState.REJECTED

    loop_service.ready("I-1")
    second = engine.tick()

    assert second.items_verified == 1
    assert second.items_blocked == 1
    assert loop_service.get("I-1").state is LoopItemState.BLOCKED
    assert loop_service.get("I-1").blocker == "retry_exhausted"


def test_tick_with_no_verifier_blocks_awaiting_verdict(tmp_path):
    loop_service = make_loop(tmp_path)
    add_awaiting(loop_service)
    engine = LoopEngine({}, loop_service, verifier_service=None, broker_client=FakeBrokerClient("running"))

    result = engine.tick(allow_active_attempts=True)

    assert result.items_blocked == 1
    assert loop_service.get("I-1").state is LoopItemState.BLOCKED
    assert loop_service.get("I-1").blocker == "verifier_unavailable"


def test_tick_dispatching_item_uses_worker_service_and_reaches_done(tmp_path):
    loop_service = make_loop(tmp_path)
    add_reserved_dispatching(loop_service)
    worker_service = make_worker_service()
    engine = LoopEngine(
        {},
        loop_service,
        verifier_service=make_verifier(),
        worker_service=worker_service,
        broker_client=FakeBrokerClient("completed"),
    )

    result = engine.tick(allow_active_attempts=True)

    assert worker_service.started == [("I-1", 1, None)]
    assert worker_service.awaited == [(VerifierHandle(task_id="real-I-1-1", worker_name="maker"), 1800)]
    assert result.items_verified == 1
    assert result.items_done == 1
    assert loop_service.get("I-1").state is LoopItemState.DONE


def test_tick_maker_dispatch_error_blocks_item(tmp_path):
    loop_service = make_loop(tmp_path)
    add_reserved_dispatching(loop_service)
    engine = LoopEngine(
        {},
        loop_service,
        worker_service=FakeMakerService(dispatch_error=MakerDispatchError("boom")),
        broker_client=FakeBrokerClient("completed"),
    )

    result = engine.tick(allow_active_attempts=True)

    assert result.items_blocked == 1
    assert "I-1: maker_dispatch_error" in result.notes
    assert loop_service.get("I-1").state is LoopItemState.BLOCKED
    assert loop_service.get("I-1").blocker == "maker_dispatch_error"


def test_tick_maker_result_timeout_blocks_item(tmp_path):
    loop_service = make_loop(tmp_path)
    add_dispatching(loop_service)
    engine = LoopEngine(
        {},
        loop_service,
        worker_service=FakeMakerService(collect_error=MakerTimeoutError("too slow")),
        broker_client=FakeBrokerClient("completed"),
    )

    result = engine.tick(allow_active_attempts=True)

    assert result.items_blocked == 1
    assert "I-1: maker_timeout" in result.notes
    assert loop_service.get("I-1").state is LoopItemState.BLOCKED
    assert loop_service.get("I-1").blocker == "maker_timeout"


def test_tick_without_worker_service_blocks_dispatching_items_once(tmp_path):
    loop_service = make_loop(tmp_path)
    add_reserved_dispatching(loop_service, "I-1")
    add_reserved_dispatching(loop_service, "I-2")
    engine = LoopEngine({}, loop_service, broker_client=None)

    result = engine.tick(allow_active_attempts=True)

    assert result.items_blocked == 2
    assert result.errors == []
    assert result.notes.count("I-1: maker_unavailable") == 1
    assert result.notes.count("I-2: maker_unavailable") == 1
    assert loop_service.get("I-1").blocker == "maker_unavailable"
    assert loop_service.get("I-2").blocker == "maker_unavailable"


def test_tick_without_worker_service_flags_dispatching_item_without_crash(tmp_path):
    loop_service = make_loop(tmp_path)
    add_reserved_dispatching(loop_service)
    engine = LoopEngine({}, loop_service, worker_service=None, broker_client=None)

    result = engine.tick(allow_active_attempts=True)

    assert result.items_blocked == 1
    assert result.errors == []
    assert "I-1: maker_unavailable" in result.notes
    assert loop_service.get("I-1").state is LoopItemState.BLOCKED
    assert loop_service.get("I-1").blocker == "maker_unavailable"


def test_tick_real_worker_service_collect_timeout_blocks_item(tmp_path):
    loop_service = make_loop(tmp_path)
    add_reserved_dispatching(loop_service)
    gateway = FakeGateway(maker_timeout=True)
    engine = LoopEngine(
        {},
        loop_service,
        worker_service=WorkerService({}, gateway),
        broker_client=FakeBrokerClient("completed"),
    )

    result = engine.tick(allow_active_attempts=True)

    assert gateway.maker_dispatches
    assert result.items_blocked == 1
    assert "I-1: maker_timeout" in result.notes
    assert loop_service.get("I-1").blocker == "maker_timeout"


def test_tick_passes_run_checks_to_verifier_path(tmp_path):
    loop_service = make_loop(tmp_path)
    add_verifying(loop_service)
    verifier = RecordingVerifierService()
    engine = LoopEngine({"run_checks": True}, loop_service, verifier_service=verifier, broker_client=FakeBrokerClient("completed"))

    result = engine.tick(allow_active_attempts=True)

    assert result.items_verified == 1
    assert verifier.calls[0]["run_checks"] is True
    assert verifier.calls[0]["check_service"] is not None
    assert loop_service.get("I-1").state is LoopItemState.DONE


def test_tick_with_no_broker_blocks_open_broker_states(tmp_path):
    loop_service = make_loop(tmp_path)
    add_dispatching(loop_service, "D-1")
    add_running(loop_service, "R-1")
    add_verifying(loop_service, "V-1")
    engine = LoopEngine({}, loop_service, verifier_service=make_verifier(), worker_service=make_worker_service(), broker_client=None)

    result = engine.tick(allow_active_attempts=True)

    assert result.items_blocked == 3
    assert loop_service.get("D-1").blocker == "broker_unavailable"
    assert loop_service.get("R-1").blocker == "broker_unavailable"
    assert loop_service.get("V-1").blocker == "broker_unavailable"


def test_ready_item_without_broker_is_blocked_once_and_notes_broker_unavailable(tmp_path):
    loop_service = make_loop(tmp_path)
    add_ready(loop_service)
    engine = LoopEngine({}, loop_service, verifier_service=make_verifier(), worker_service=make_worker_service(), broker_client=None)

    result = engine.tick()

    assert result.items_dispatched == 1
    assert result.items_blocked == 1
    assert result.notes.count("broker_unavailable") == 1
    assert loop_service.get("I-1").state is LoopItemState.BLOCKED
    assert loop_service.get("I-1").blocker == "broker_unavailable"


def test_tick_from_active_event_loop_reports_documented_sync_constraint(tmp_path):
    loop_service = make_loop(tmp_path)
    add_ready(loop_service)
    broker = FakeBrokerClient({"*": {"status": "completed", "result": MakerResult("maker done")}})
    engine = LoopEngine({}, loop_service, verifier_service=make_verifier(), worker_service=make_worker_service(), broker_client=broker)

    async def run_tick_inside_event_loop():
        return engine.tick()

    result = asyncio.run(run_tick_inside_event_loop())

    assert result.items_done == 0
    assert result.errors == [
        "advance failed: LoopEngine.tick() must be called from sync code; do not call it from an active event loop"
    ]


def test_loop_engine_does_not_mutate_loop_service_broker(tmp_path):
    loop_service = make_loop(tmp_path)
    add_ready(loop_service)
    broker = FakeBrokerClient({"*": {"status": "completed", "result": MakerResult("maker done")}})
    engine = LoopEngine({}, loop_service, verifier_service=make_verifier(), worker_service=make_worker_service(), broker_client=broker)

    engine.tick()

    assert loop_service.broker is None


def test_run_processes_max_steps(tmp_path):
    loop_service = make_loop(tmp_path)
    engine = LoopEngine({}, loop_service, clock=fake_clock, sleeper=lambda seconds: None)

    summary = engine.run(max_steps=3, interval_seconds=0)

    assert summary.steps == 3
    assert summary.ticks == 3
    assert summary.errors == []


def test_run_respects_stop_from_sleeper(tmp_path):
    loop_service = make_loop(tmp_path)
    engine = LoopEngine({}, loop_service, clock=fake_clock)

    def sleeper(seconds):
        engine.stop()

    engine.sleeper = sleeper

    summary = engine.run(max_steps=10, interval_seconds=0)

    assert summary.ticks == 1
    assert engine.stopped is True


def test_run_stops_on_first_tick_error(tmp_path):
    loop_service = make_loop(tmp_path)
    engine = LoopEngine(
        {},
        loop_service,
        triage_service=RaisingTriageService(),
        clock=fake_clock,
        sleeper=lambda seconds: (_ for _ in ()).throw(AssertionError("sleep should not run after an error")),
    )

    summary = engine.run(max_steps=5, interval_seconds=0)

    assert summary.steps == 1
    assert summary.ticks == 1
    assert summary.stopped is True
    assert summary.stop_reason == "error"
    assert summary.errors == ["triage failed: triage boom"]


def test_tick_records_triage_errors_and_continues(tmp_path):
    loop_service = make_loop(tmp_path)
    add_ready(loop_service)
    broker = FakeBrokerClient({"*": {"status": "completed", "result": MakerResult("maker done")}})
    engine = LoopEngine(
        {},
        loop_service,
        triage_service=RaisingTriageService(),
        verifier_service=make_verifier(),
        worker_service=make_worker_service(),
        broker_client=broker,
    )

    result = engine.tick()

    assert result.errors == ["triage failed: triage boom"]
    assert result.items_done == 1
    assert loop_service.get("I-1").state is LoopItemState.DONE


def test_tick_dispatches_multiple_ready_items_up_to_limit(tmp_path):
    loop_service = make_loop(tmp_path)
    add_ready(loop_service, "I-1")
    add_ready(loop_service, "I-2")
    add_ready(loop_service, "I-3")
    broker = FakeBrokerClient({"*": {"status": "completed", "result": MakerResult("maker done")}})
    engine = LoopEngine(
        {"per_tick_dispatch_limit": 2},
        loop_service,
        verifier_service=make_verifier(),
        worker_service=make_worker_service(),
        broker_client=broker,
    )

    result = engine.tick()

    assert result.items_dispatched == 2
    assert result.items_done == 2
    assert loop_service.get("I-1").state is LoopItemState.DONE
    assert loop_service.get("I-2").state is LoopItemState.DONE
    assert loop_service.get("I-3").state is LoopItemState.READY
