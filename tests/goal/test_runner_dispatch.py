from __future__ import annotations

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from orchlink.cli import main as cli_main
from orchlink.goal.runner import GoalEvidenceAdapter, GoalRunner
from orchlink.goal.store import GoalStore
from orchlink.project.config import load_project_config
from orchlink.project.init import init_project


runner = CliRunner()


def _init_goal_project(tmp_path: Path, monkeypatch) -> dict:
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    return load_project_config()


def _ready_goal(config: dict, tmp_path: Path) -> None:
    store = GoalStore(config)
    goal = store.create_goal("Build", "text", "Build thing")
    store.approve_combined_gate(goal.id)
    (tmp_path / "check_ok.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    acceptance = yaml.safe_dump(
        {
            "acceptance": [
                {
                    "id": "AC-1",
                    "text": "works",
                    "type": "objective",
                    "priority": "core",
                    "depends_on": [],
                    "check": "python3 check_ok.py",
                    "status": "pending",
                }
            ]
        },
        sort_keys=False,
    )
    (store.goal_dir(goal.id) / "acceptance.md").write_text(f"# Acceptance\n\n```yaml\n{acceptance}```\n", encoding="utf-8")


def _result(task_id: str) -> dict:
    return {"status": "DONE", "task_id": task_id, "reply": {"type": "RESULT", "payload": {"summary": "ok"}}}


def _history(tmp_path: Path, goal_id: str = "G001") -> list[dict]:
    path = tmp_path / ".orch" / "goals" / goal_id / "history.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_goal_runner_work_uses_custom_maker_worker(tmp_path, monkeypatch):
    config = _init_goal_project(tmp_path, monkeypatch)
    _ready_goal(config, tmp_path)
    calls = []

    def fake_send_worker_sync(**kwargs):
        calls.append(kwargs)
        return _result(kwargs["task_id"])

    monkeypatch.setattr("orchlink.goal.runner.send_worker_sync", fake_send_worker_sync)

    result = GoalRunner(config, maker_worker="builder", verifier_worker="reviewer").work("G001", max_steps=1)

    assert "done" in result.lower()
    assert calls[0]["worker"] == "builder"
    assert calls[0]["task_id"] == "G001-WORK-001"


def test_goal_runner_audit_uses_custom_verifier_worker(tmp_path, monkeypatch):
    config = _init_goal_project(tmp_path, monkeypatch)
    _ready_goal(config, tmp_path)
    calls = []

    def fake_send_worker_sync(**kwargs):
        calls.append(kwargs)
        return _result(kwargs["task_id"])

    monkeypatch.setattr("orchlink.goal.runner.send_worker_sync", fake_send_worker_sync)

    result = GoalRunner(config, maker_worker="builder", verifier_worker="reviewer").audit("G001")

    assert "audit recorded" in result.lower()
    assert calls == [calls[0]]
    assert calls[0]["worker"] == "reviewer"
    assert calls[0]["task_id"] == "G001-AUDIT-001"


def test_goal_work_cli_valid_worker_names_still_dispatch(tmp_path, monkeypatch):
    config = _init_goal_project(tmp_path, monkeypatch)
    _ready_goal(config, tmp_path)
    calls = []

    def fake_send_worker_sync(**kwargs):
        calls.append(kwargs)
        return _result(kwargs["task_id"])

    monkeypatch.setattr("orchlink.goal.runner.send_worker_sync", fake_send_worker_sync)

    result = runner.invoke(
        cli_main.app,
        ["goal", "work", "G001", "--max-steps", "1", "--maker-worker", "builder", "--verifier-worker", "reviewer"],
    )

    assert result.exit_code == 0
    assert calls[0]["worker"] == "builder"
    event = next(item for item in _history(tmp_path) if item.get("type") == "task_dispatched")
    assert event["worker"] == "builder"
    assert event["verifier_worker"] == "reviewer"


def test_goal_runner_records_model_metadata_for_dispatch(tmp_path, monkeypatch):
    config = _init_goal_project(tmp_path, monkeypatch)
    _ready_goal(config, tmp_path)

    def fake_send_worker_sync(**kwargs):
        return _result(kwargs["task_id"])

    monkeypatch.setattr("orchlink.goal.runner.send_worker_sync", fake_send_worker_sync)
    goal_runner = GoalRunner(config, maker_model="maker-model", verifier_model="verifier-model")

    goal_runner.work("G001", max_steps=1)

    event = next(item for item in _history(tmp_path) if item.get("type") == "task_dispatched")
    assert event["model"] == "maker-model"
    assert event["verifier_model"] == "verifier-model"
    assert goal_runner.dispatcher.last_dispatch_metadata["model"] == "maker-model"


def test_goal_evidence_adapter_wraps_record_evidence(tmp_path, monkeypatch):
    config = _init_goal_project(tmp_path, monkeypatch)
    store = GoalStore(config)
    goal = store.create_goal("Build", "text", "Build thing")
    adapter = GoalEvidenceAdapter(store)

    adapter.attach_evidence(goal_id=goal.id, evidence={"type": "loop_verdict", "passed": True})

    loaded = store.load(goal.id)
    assert len(loaded.evidence) == 1
    assert loaded.evidence[0].type == "loop_verdict"
    assert loaded.evidence[0].passed is True


def test_goal_runner_default_worker_names_remain_work(tmp_path, monkeypatch):
    config = _init_goal_project(tmp_path, monkeypatch)
    _ready_goal(config, tmp_path)
    calls = []

    def fake_send_worker_sync(**kwargs):
        calls.append(kwargs)
        return _result(kwargs["task_id"])

    monkeypatch.setattr("orchlink.goal.runner.send_worker_sync", fake_send_worker_sync)

    GoalRunner(config).work("G001", max_steps=1)

    assert calls[0]["worker"] == "work"


def test_goal_dispatcher_calls_send_worker_sync_with_wait_true(tmp_path, monkeypatch):
    config = _init_goal_project(tmp_path, monkeypatch)
    _ready_goal(config, tmp_path)
    calls = []

    def fake_send_worker_sync(**kwargs):
        calls.append(kwargs)
        return _result(kwargs["task_id"])

    monkeypatch.setattr("orchlink.goal.runner.send_worker_sync", fake_send_worker_sync)

    GoalRunner(config).work("G001", max_steps=1)

    assert calls[0]["wait"] is True
