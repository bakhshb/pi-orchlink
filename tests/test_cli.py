from pathlib import Path

from typer.testing import CliRunner

from orchlink.cli import main as cli_main
from orchlink.project.init import init_project


runner = CliRunner()


def test_ask_command_prints_reply(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "orchestrator.yaml").write_text(
        "agent_id: orchestrator\n"
        "role: orchestrator\n"
        "display_name: Orchestrator\n"
        "broker_url: http://127.0.0.1:8787\n"
        "api_key: test-key\n",
        encoding="utf-8",
    )

    def fake_ask_worker_sync(**kwargs):
        assert kwargs["worker_id"] == "worker-backend"
        assert kwargs["task_id"] == "TEST-001"
        assert kwargs["message"] == "Return PLAN only."
        return {"status": "completed", "reply": {"type": "PLAN", "payload": {"summary": "done"}}}

    monkeypatch.setattr(cli_main, "ask_worker_sync", fake_ask_worker_sync)

    result = runner.invoke(
        cli_main.app,
        [
            "ask",
            "worker-backend",
            "--task-id",
            "TEST-001",
            "--message",
            "Return PLAN only.",
            "--config-dir",
            str(config_dir),
        ],
    )

    assert result.exit_code == 0
    assert '"status": "completed"' in result.output
    assert '"type": "PLAN"' in result.output


def test_project_ask_defaults_to_no_wait(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    def fake_project_ask_worker_sync(**kwargs):
        assert kwargs["worker"] == "work"
        assert kwargs["task_id"] == "T001"
        assert kwargs["wait"] is False
        return {"status": "queued", "message_id": "msg-1"}

    monkeypatch.setattr(cli_main, "project_ask_worker_sync", fake_project_ask_worker_sync)

    result = runner.invoke(cli_main.app, ["ask", "work", "-t", "T001", "-m", "Return PLAN only."])

    assert result.exit_code == 0
    assert '"status": "queued"' in result.output
    assert "Async mode" in result.output
    assert "pending" in result.output


def test_start_orchestrator_prints_guidance(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "orchestrator.yaml").write_text(
        "agent_id: orchestrator\n"
        "role: orchestrator\n"
        "display_name: Orchestrator\n"
        "broker_url: http://127.0.0.1:8787\n"
        "api_key: test-key\n"
        "workers:\n"
        "  - agent_id: worker-backend\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(cli_main, "register_agent_sync", lambda config: {"status": "registered"})

    result = runner.invoke(
        cli_main.app,
        ["start", "orchestrator", "--config-dir", str(config_dir)],
    )

    assert result.exit_code == 0
    assert "[Orchlink] Registered: orchestrator" in result.output
    assert "[Orchlink] Available worker: worker-backend" in result.output
    assert "orchlink ask worker-backend" in result.output


def test_work_command_starts_visible_pi(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    calls = []

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

        def run_work(self):
            calls.append("run_work")
            return 0

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: calls.append("ensure_broker"))
    monkeypatch.setattr(cli_main, "register_project_role_sync", lambda config, role: calls.append(f"register_{role}"))
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)

    result = runner.invoke(cli_main.app, ["work"])

    assert result.exit_code == 0
    assert calls == ["ensure_broker", "register_worker", "run_work"]
    assert "Starting Pi worker session" in result.output
    assert "posted directly into this Pi chat" in result.output


def test_work_new_uses_fresh_pi_session_id(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main.time, "strftime", lambda fmt: "20260621-010203")
    calls = []

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

        def run_work(self):
            calls.append(self.config["work"]["session_id"])
            return 0

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "register_project_role_sync", lambda config, role: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)

    result = runner.invoke(cli_main.app, ["work", "--new"])

    assert result.exit_code == 0
    assert calls == ["work-20260621-010203"]
    assert "New Pi worker session: work-20260621-010203" in result.output


def test_status_command_prints_status(monkeypatch):
    monkeypatch.setattr(
        cli_main,
        "fetch_status_sync",
        lambda broker_url, api_key: {"broker": "ok", "agent_count": 0},
    )

    result = runner.invoke(cli_main.app, ["status", "--broker-url", "http://broker", "--api-key", "test-key"])

    assert result.exit_code == 0
    assert '"broker": "ok"' in result.output
