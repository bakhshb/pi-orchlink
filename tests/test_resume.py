from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from orchlink.broker.checkpoint import Checkpoint, CheckpointLease, DriftedLease, record_lease
from orchlink.cli import main as cli_main
from orchlink.cli.resume import ActiveTaskSummary, ResumeState, render_resume_report
from orchlink.project.init import init_project


runner = CliRunner()


def test_resume_command_is_registered():
    result = runner.invoke(cli_main.app, ["--help"])

    assert result.exit_code == 0
    assert "resume" in result.output


def test_resume_cli_reports_status_checkpoint_drift_and_recommendation(monkeypatch, tmp_path: Path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    record_lease(tmp_path, "T900", epoch=1, holder="demo.work", status="in_flight")

    def fake_broker_get_sync(config, path):
        assert path.startswith("/v1/status")
        return {
            "jobs": [],
            "sessions": [
                {"agent_id": "demo.lead", "role": "lead", "status": "ACTIVE"},
                {"agent_id": "demo.work", "role": "work", "status": "ACTIVE"},
            ],
        }

    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)

    result = runner.invoke(cli_main.app, ["resume"])

    assert result.exit_code == 0, result.output
    assert "Active task or goal: (none)" in result.output
    assert "Lead/work sessions:" in result.output
    assert "Last broker checkpoint:" in result.output
    assert "Drifted leases since checkpoint:" in result.output
    assert "T900: missing_after_restart" in result.output
    assert "Recommended next: orch cancel T900" in result.output
    assert "Needs intervention." in result.output


def test_resume_states():
    checkpoint = Checkpoint(
        last_checkpoint_at="2026-07-01T00:00:00+00:00",
        leases=[
            CheckpointLease(
                task_id="T001",
                epoch=1,
                holder="demo.work",
                status="recently_settled",
                updated_at="2026-07-01T00:00:00+00:00",
            )
        ],
    )

    normal = render_resume_report(
        ResumeState(
            mode="normal",
            active=[ActiveTaskSummary(task_id="T002", kind="task", state="RUNNING", title="Inspect")],
            checkpoint=checkpoint,
        )
    )
    assert "T002 [task, RUNNING]: Inspect" in normal
    assert "Safe to continue." in normal
    assert "Recommended next: orch jobs --id T002" in normal

    idle = render_resume_report(ResumeState(mode="idle", checkpoint=Checkpoint(leases=[])))
    assert "Active task or goal: (none)" in idle
    assert "Recommended next: orch lead" in idle
    assert "Safe to continue." in idle

    stale = render_resume_report(
        ResumeState(
            mode="stale/interrupted",
            checkpoint=checkpoint,
            drifted_leases=[
                DriftedLease(
                    task_id="T003",
                    previous_epoch=1,
                    previous_holder="demo.work",
                    previous_updated_at="2026-07-01T00:00:00+00:00",
                    current_epoch=2,
                    current_holder="demo.work",
                    reason="epoch_changed",
                )
            ],
        )
    )
    assert "T003: epoch_changed" in stale
    assert "Recommended next: orch get T003" in stale
    assert "Needs intervention." in stale
