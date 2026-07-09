from __future__ import annotations

import asyncio

import pytest

from orchlink.loop.domain import LoopAttempt, LoopItem, MakerResult, WorkerAssignment, Worktree
from orchlink.loop.services import MakerDispatchError, MakerTimeoutError, WorkerGatewayUnavailable, WorkerService
from orchlink.loop.services.verifier_service import VerifierHandle


def attempt() -> LoopAttempt:
    return LoopAttempt(number=1, maker=WorkerAssignment(worker_name="maker", task_id="T-maker"))


def item() -> LoopItem:
    return LoopItem(item_id="I-1", title="Implement real maker dispatch")


class FakeWorkerGateway:
    def __init__(self, *, dispatch_error=None, result_error=None):
        self.dispatch_error = dispatch_error
        self.result_error = result_error
        self.maker_dispatches = []
        self.verifier_dispatches = []
        self.awaited = []

    async def dispatch_maker(self, maker_assignment, prompt):
        if self.dispatch_error:
            raise self.dispatch_error
        self.maker_dispatches.append((maker_assignment, prompt))
        return VerifierHandle(task_id=maker_assignment.task_id or "T-maker", worker_name=maker_assignment.worker_name)

    async def dispatch_verifier(self, verifier_assignment, prompt):
        if self.dispatch_error:
            raise self.dispatch_error
        self.verifier_dispatches.append((verifier_assignment, prompt))
        return VerifierHandle(task_id=verifier_assignment.task_id or "T-verifier", worker_name=verifier_assignment.worker_name)

    async def await_result(self, handle, timeout_seconds):
        if self.result_error:
            raise self.result_error
        self.awaited.append((handle, timeout_seconds))
        return MakerResult("maker completed")


def test_start_maker_uses_shared_gateway_and_returns_handle():
    gateway = FakeWorkerGateway()
    service = WorkerService({}, gateway)
    loop_item = item()
    loop_attempt = attempt()
    worktree = Worktree("/tmp/wt")

    handle = asyncio.run(service.start_maker(loop_item, loop_attempt, worktree=worktree))

    assert handle == VerifierHandle(task_id="T-maker", worker_name="maker")
    assert gateway.maker_dispatches[0][0] == loop_attempt.maker
    assert "# Orchlink Loop Maker" in gateway.maker_dispatches[0][1]
    assert "ITEM_ID: I-1" in gateway.maker_dispatches[0][1]
    assert "WORKTREE: /tmp/wt" in gateway.maker_dispatches[0][1]


def test_dispatch_and_collect_maker_waits_for_result():
    gateway = FakeWorkerGateway()
    service = WorkerService({}, gateway)

    result = asyncio.run(service.dispatch_and_collect_maker(item(), attempt(), timeout_seconds=7))

    assert result.result == "maker completed"
    assert gateway.awaited == [(VerifierHandle(task_id="T-maker", worker_name="maker"), 7)]


def test_start_verifier_uses_same_gateway_boundary():
    gateway = FakeWorkerGateway()
    service = WorkerService({}, gateway)
    verifier = WorkerAssignment(worker_name="review", task_id="T-review")

    handle = asyncio.run(service.start_verifier(verifier, "verify this"))

    assert handle == VerifierHandle(task_id="T-review", worker_name="review")
    assert gateway.verifier_dispatches == [(verifier, "verify this")]


def test_worker_service_without_gateway_raises_unavailable():
    service = WorkerService({}, None)

    with pytest.raises(WorkerGatewayUnavailable):
        asyncio.run(service.start_maker(item(), attempt()))


def test_worker_service_dispatch_error_is_typed():
    service = WorkerService({}, FakeWorkerGateway(dispatch_error=RuntimeError("boom")))

    with pytest.raises(MakerDispatchError):
        asyncio.run(service.start_maker(item(), attempt()))


def test_worker_service_timeout_is_typed():
    service = WorkerService({}, FakeWorkerGateway(result_error=TimeoutError("too slow")))

    with pytest.raises(MakerTimeoutError):
        asyncio.run(service.dispatch_and_collect_maker(item(), attempt()))
