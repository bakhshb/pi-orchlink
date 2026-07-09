from __future__ import annotations

from typer.testing import CliRunner

from orchlink.cli import main as cli_main
from orchlink.loop.adapters.state_repo import LoopStateRepo
from orchlink.loop.domain import LoopItemState
from orchlink.loop.services import ItemCandidate, LoopService
from orchlink.loop.services.verifier_service import VerifierHandle
from orchlink.project.init import init_project

runner = CliRunner()


def _init_loop_project(tmp_path, monkeypatch):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    from orchlink.loop import cli as loop_cli

    monkeypatch.setattr(loop_cli, "_build_broker_client", lambda config: None)
    return LoopService({}, LoopStateRepo(tmp_path))


def test_loop_watch_empty_loop_with_worker_fallback_returns_summary(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)
    from orchlink.loop import cli as loop_cli

    monkeypatch.setattr(loop_cli, "_build_worker_gateway", lambda config: None)

    result = runner.invoke(
        cli_main.app,
        ["loop", "watch", "--max-steps", "1", "--interval", "0.01", "--allow-active-attempts"],
    )

    assert result.exit_code == 0
    assert "RunSummary" in result.output
    assert "dispatched=0" in result.output
    assert "blocked=0" in result.output
    assert "done=0" in result.output


class TimeoutMakerGateway:
    def __init__(self):
        self.dispatched = []

    async def dispatch_maker(self, maker_assignment, prompt):
        self.dispatched.append((maker_assignment, prompt))
        return VerifierHandle(task_id="real-maker-task", worker_name=maker_assignment.worker_name)

    async def dispatch_verifier(self, verifier_assignment, prompt):
        return VerifierHandle(task_id=verifier_assignment.task_id, worker_name=verifier_assignment.worker_name)

    async def await_result(self, handle, timeout_seconds):
        raise TimeoutError("maker timed out")


def test_loop_watch_real_worker_service_blocks_maker_timeout(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    service.triage([ItemCandidate(item_id="L001", title="Loop item")])
    service.ready("L001")
    gateway = TimeoutMakerGateway()
    from orchlink.loop import cli as loop_cli

    monkeypatch.setattr(loop_cli, "_build_worker_gateway", lambda config: gateway)

    result = runner.invoke(
        cli_main.app,
        ["loop", "watch", "--max-steps", "1", "--interval", "0.01", "--allow-active-attempts"],
    )

    assert result.exit_code == 0
    assert gateway.dispatched
    assert "L001: maker_timeout" in result.output
    assert service.get("L001").state is LoopItemState.BLOCKED
    assert service.get("L001").blocker == "maker_timeout"
