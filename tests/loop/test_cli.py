from __future__ import annotations

import shlex
from types import SimpleNamespace

from typer.testing import CliRunner

from orchlink.cli import main as cli_main
from orchlink.loop.adapters.state_repo import LoopStateRepo
from orchlink.loop.domain import LoopItemState, MakerResult, ReasonCode, Verdict, VerifierVerdict
from orchlink.loop.services import BrokerTaskStatus, ItemCandidate, LoopService
from orchlink.loop.services.verifier_service import VerifierHandle
from orchlink.project.init import init_project

runner = CliRunner()


def _init_loop_project(tmp_path, monkeypatch):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    from orchlink.loop import cli as loop_cli

    monkeypatch.setattr(loop_cli, "_build_broker_client", lambda config: None)
    return LoopService({}, LoopStateRepo(tmp_path))


def test_loop_tick_empty_loop_returns_summary(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)
    from orchlink.loop import cli as loop_cli

    monkeypatch.setattr(loop_cli, "_build_worker_gateway", lambda config: None)

    result = runner.invoke(cli_main.app, ["loop", "tick"])

    assert result.exit_code == 0
    assert "RunSummary" in result.output
    assert "dispatched=0" in result.output
    assert "done=0" in result.output


def test_loop_tick_run_checks_sets_engine_flag(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)
    from orchlink.loop import cli as loop_cli

    class FakeEngine:
        def __init__(self):
            self.config = {}
            self.calls = []
            self.worker_service = None
            self.verifier_service = None
            self.broker_client = None

        async def run(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                steps=1,
                ticks=1,
                items_dispatched=0,
                items_verified=0,
                items_blocked=0,
                items_done=0,
                errors=[],
                notes=[],
            )

    engine = FakeEngine()
    monkeypatch.setattr(loop_cli, "_build_services", lambda config: (None, None, None, engine, None))
    monkeypatch.setattr(loop_cli, "_build_worker_runtime", lambda config: (None, None))
    monkeypatch.setattr(loop_cli, "_build_broker_client", lambda config: None)

    result = runner.invoke(cli_main.app, ["loop", "tick", "--run-checks"])

    assert result.exit_code == 0
    assert engine.config["run_checks"] is True
    assert engine.calls == [{"max_steps": 1, "interval_seconds": 0, "allow_active_attempts": False}]


def test_loop_tick_max_steps_passes_to_engine(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)
    from orchlink.loop import cli as loop_cli

    class FakeEngine:
        def __init__(self):
            self.config = {}
            self.calls = []
            self.worker_service = None
            self.verifier_service = None
            self.broker_client = None

        async def run(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(
                steps=2,
                ticks=2,
                items_dispatched=0,
                items_verified=0,
                items_blocked=0,
                items_done=0,
                errors=[],
                notes=[],
            )

    engine = FakeEngine()
    monkeypatch.setattr(loop_cli, "_build_services", lambda config: (None, None, None, engine, None))
    monkeypatch.setattr(loop_cli, "_build_worker_runtime", lambda config: (None, None))
    monkeypatch.setattr(loop_cli, "_build_broker_client", lambda config: None)

    result = runner.invoke(cli_main.app, ["loop", "tick", "--max-steps", "2"])

    assert result.exit_code == 0
    assert engine.calls == [{"max_steps": 2, "interval_seconds": 0, "allow_active_attempts": False}]


def test_loop_tick_ready_item_without_broker_blocks_and_returns(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    service.triage([ItemCandidate(item_id="L001", title="Loop item")])
    service.ready("L001")
    from orchlink.loop import cli as loop_cli

    monkeypatch.setattr(loop_cli, "_build_worker_gateway", lambda config: None)

    result = runner.invoke(cli_main.app, ["loop", "tick"])

    assert result.exit_code == 0
    assert "RunSummary steps=1 ticks=1 dispatched=1" in result.output
    assert "blocked=1" in result.output
    assert "L001: maker_unavailable" in result.output
    assert service.get("L001").state is LoopItemState.BLOCKED
    assert service.get("L001").blocker == "maker_unavailable"


def test_loop_tick_idempotent_two_ticks_do_not_double_dispatch(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    service.triage([ItemCandidate(item_id="L001", title="Loop item")])
    service.ready("L001")
    from orchlink.loop import cli as loop_cli

    monkeypatch.setattr(loop_cli, "_build_worker_gateway", lambda config: None)

    first = runner.invoke(cli_main.app, ["loop", "tick"])
    second = runner.invoke(cli_main.app, ["loop", "tick"])
    item = service.get("L001")

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "dispatched=1" in first.output
    assert "dispatched=0" in second.output
    assert item.state is LoopItemState.BLOCKED
    assert len(item.attempts) == 1


def test_loop_recover_without_broker_reports_and_leaves_active_item_unchanged(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    service.triage([ItemCandidate(item_id="L001", title="Loop item")])
    service.ready("L001")
    service.next_item("L001", maker_worker="maker", worktree=None)

    result = runner.invoke(cli_main.app, ["loop", "recover"])
    item = service.get("L001")

    assert result.exit_code == 0
    assert "broker_unavailable" in result.output
    assert "left unchanged" in result.output
    assert "changed=0" in result.output
    assert item.state is LoopItemState.DISPATCHING
    assert item.blocker is None


def test_loop_recover_uses_broker_client_path_and_is_idempotent(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    service.triage([ItemCandidate(item_id="L001", title="Loop item")])
    service.ready("L001")
    reservation = service.next_item("L001", maker_worker="maker", worktree=None)
    service.mark_dispatched("L001", attempt_no=reservation.attempt.number, task_id="T-maker")
    from orchlink.loop import cli as loop_cli

    class FakeBroker:
        def get_task_status(self, task_id):
            assert task_id == "T-maker"
            return BrokerTaskStatus(status="running")

        def get_session_active(self, lease_id):
            return True

    calls = []
    monkeypatch.setattr(loop_cli, "_build_broker_client", lambda config: (calls.append(config), FakeBroker())[1])

    first = runner.invoke(cli_main.app, ["loop", "recover"])
    second = runner.invoke(cli_main.app, ["loop", "recover"])
    item = service.get("L001")

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert len(calls) == 2
    assert "changed=1" in first.output
    assert "changed=0" in second.output
    assert item.state is LoopItemState.RUNNING
    assert item.blocker is None


def test_http_loop_broker_client_fetches_result_bearing_task_snapshot(monkeypatch):
    from orchlink.loop import runtime as loop_runtime

    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "DONE",
                "project_id": "demo",
                "task_id": "T-maker",
                "reply": {"payload": {"summary": "real maker result"}},
            }

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, path, headers=None):
            calls.append({"path": path, "headers": headers, "kwargs": self.kwargs})
            return FakeResponse()

    monkeypatch.setattr(loop_runtime.httpx, "Client", FakeClient)
    monkeypatch.delenv("ORCHLINK_BROKER_URL", raising=False)
    monkeypatch.delenv("ORCHLINK_API_KEY", raising=False)
    client = loop_runtime.HttpLoopBrokerClient({"project_id": "demo", "broker": {"url": "http://broker", "api_key": "key"}})

    snapshot = client.get_task_status("T-maker")

    assert snapshot.status == "done"
    assert snapshot.result == "real maker result"
    assert calls == [
        {
            "path": "/v1/tasks/T-maker?project_id=demo",
            "headers": {"X-API-Key": "key"},
            "kwargs": {"base_url": "http://broker", "timeout": 1.0},
        }
    ]


def test_loop_schedule_every_30m_prints_crontab_tick(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_main.app, ["loop", "schedule", "--every", "30m"])

    assert result.exit_code == 0
    assert "*/30 * * * *" in result.output
    assert "orch loop tick --max-steps 1" in result.output
    assert "orch loop watch" not in result.output


def test_loop_schedule_every_1h_prints_hourly_crontab(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_main.app, ["loop", "schedule", "--every", "1h"])

    assert result.exit_code == 0
    assert "0 * * * *" in result.output


def test_loop_schedule_daily_prints_daily_crontab(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_main.app, ["loop", "schedule", "--every", "daily"])

    assert result.exit_code == 0
    assert "0 0 * * *" in result.output


def test_loop_schedule_systemd_prints_units(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_main.app, ["loop", "schedule", "--every", "30m", "--systemd"])

    assert result.exit_code == 0
    assert "[Service]" in result.output
    assert "Type=oneshot" in result.output
    assert "ExecStart=" in result.output
    assert "loop tick --max-steps" in result.output
    assert "[Timer]" in result.output
    assert "OnCalendar=*:0/30:00" in result.output
    assert "orch loop watch" not in result.output


def test_loop_schedule_print_modes_do_not_start_daemon_or_call_system_tools(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)
    from orchlink.loop import cli as loop_cli

    monkeypatch.setattr(loop_cli.subprocess, "run", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not call system tools")))

    cron = runner.invoke(cli_main.app, ["loop", "schedule", "--every", "30m"])
    systemd = runner.invoke(cli_main.app, ["loop", "schedule", "--every", "30m", "--systemd"])

    assert cron.exit_code == 0
    assert systemd.exit_code == 0
    assert "orch loop tick" in cron.output
    assert "loop tick --max-steps" in systemd.output
    assert "orch loop watch" not in cron.output + systemd.output


def test_loop_schedule_uses_resolved_quoted_paths_and_token_file_note(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)
    from orchlink.loop import cli as loop_cli

    project_dir = tmp_path / "project with spaces"
    executable = tmp_path / "bin dir" / "orch cmd"
    monkeypatch.setattr(loop_cli.shutil, "which", lambda name: str(executable))
    config = {"_project_root": str(project_dir)}

    cron = loop_cli._crontab_line(config, every="30m", max_steps=2, run_checks=True)
    service, _ = loop_cli._systemd_text(config, every="30m", max_steps=2, run_checks=True)

    assert f"cd {shlex.quote(str(project_dir.resolve()))}" in cron
    assert shlex.quote(str(executable.resolve())) in cron
    assert "loop tick --max-steps 2 --run-checks" in cron
    assert f'WorkingDirectory="{project_dir.resolve()}"' in service
    assert f'ExecStart="{executable.resolve()}" loop tick --max-steps 2 --run-checks' in service
    assert "external token files" in service


def test_loop_schedule_invalid_interval_exits_cleanly(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_main.app, ["loop", "schedule", "--every", "invalid"])

    assert result.exit_code == 1
    assert "invalid schedule interval" in result.output


def test_loop_schedule_install_show_remove_with_fake_crontab(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)
    from orchlink.loop import cli as loop_cli

    crontab_path = tmp_path / "crontab.txt"

    def fake_run(args, **kwargs):
        if args == ["crontab", "-l"]:
            return SimpleNamespace(returncode=0, stdout=crontab_path.read_text(encoding="utf-8") if crontab_path.exists() else "")
        if args == ["crontab", "-"]:
            crontab_path.write_text(kwargs.get("input", ""), encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(f"unexpected subprocess call: {args}")

    monkeypatch.setattr(loop_cli.subprocess, "run", fake_run)

    install = runner.invoke(cli_main.app, ["loop", "schedule", "--every", "30m", "--install"])
    installed_text = crontab_path.read_text(encoding="utf-8")
    show = runner.invoke(cli_main.app, ["loop", "schedule", "--show"])
    remove = runner.invoke(cli_main.app, ["loop", "schedule", "--remove"])
    show_after = runner.invoke(cli_main.app, ["loop", "schedule", "--show"])

    assert install.exit_code == 0
    assert "orch loop tick" in installed_text
    assert "orchlink-loop" in installed_text
    assert show.exit_code == 0
    assert "orch loop tick" in show.output
    assert remove.exit_code == 0
    assert "orch loop tick" not in crontab_path.read_text(encoding="utf-8")
    assert show_after.exit_code == 0
    assert "No schedule installed" in show_after.output


def test_loop_watch_recovers_before_refusing_remaining_active_attempts(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    service.triage([ItemCandidate(item_id="L001", title="done via recovery"), ItemCandidate(item_id="L002", title="still active")])
    service.ready("L001")
    service.ready("L002")
    item = service.next_item("L001", maker_worker="maker", worktree=None).item
    service.mark_dispatched("L001", attempt_no=item.attempts[-1].number, task_id="T-maker-1")
    service.mark_running("L001", attempt_no=item.attempts[-1].number)
    service.collect_maker_result("L001", attempt_no=item.attempts[-1].number, result=MakerResult("maker done"))
    service.reserve_verification("L001", attempt_no=item.attempts[-1].number, verifier_worker="review")
    item = service.next_item("L002", maker_worker="maker", worktree=None).item
    service.mark_dispatched("L002", attempt_no=item.attempts[-1].number, task_id="T-maker-2")
    service.mark_running("L002", attempt_no=item.attempts[-1].number)
    from orchlink.loop import cli as loop_cli

    class FakeBroker:
        def get_task_status(self, task_id):
            if task_id == "verify:L001:1":
                return BrokerTaskStatus(
                    status="done",
                    result=VerifierVerdict(
                        verdict=Verdict.ACCEPTED,
                        reason_code=ReasonCode.ACCEPTED,
                        detail="ok",
                        required_fixes=(),
                        verifier_worker="review",
                    ),
                )
            if task_id == "T-maker-2":
                return BrokerTaskStatus(status="running")
            return None

        def get_session_active(self, lease_id):
            return True

    monkeypatch.setattr(loop_cli, "_build_broker_client", lambda config: FakeBroker())
    monkeypatch.setattr(loop_cli, "_build_worker_runtime", lambda config: (None, None))

    result = runner.invoke(cli_main.app, ["loop", "watch", "--max-steps", "1", "--interval", "0.01"])

    assert result.exit_code == 0
    assert "active attempts present" in result.output
    assert service.get("L001").state is LoopItemState.DONE
    assert service.get("L002").state is LoopItemState.RUNNING


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
