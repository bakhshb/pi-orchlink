import tomllib
from pathlib import Path

from typer.testing import CliRunner


ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_declares_src_layout_and_console_script():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["name"] == "orchlink"
    assert data["project"]["scripts"]["orch"] == "orchlink.cli.main:app"
    assert data["project"]["scripts"]["orchlink"] == "orchlink.cli.main:app"
    assert data["tool"]["setuptools"]["package-dir"] == {"": "src"}
    assert data["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]
    assert (ROOT / "src" / "orchlink").is_dir()


def test_cli_imports_from_installable_package_and_exposes_required_commands():
    from orchlink.cli.main import app

    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "broker" in result.output
    assert "start" in result.output
    assert "ask" in result.output
    assert "send" in result.output
    assert "talk" in result.output
    assert "say" in result.output
    assert "close" in result.output
    assert "cancel" in result.output
    assert "jobs" in result.output
    assert "idle" in result.output
    assert "peek" in result.output
    assert "monitor" in result.output
    assert "status" in result.output
    assert "doctor" in result.output
    assert "update" in result.output


def test_pi_extension_uses_valid_record_type():
    from orchlink.connector.pi_extension import ORCHLINK_PI_EXTENSION

    assert "type OrchMessage = Record<string, any>;" in ORCHLINK_PI_EXTENSION
    assert "type OrchMessage = Record;" not in ORCHLINK_PI_EXTENSION
    assert "TYPE: CHAT_REPLY" in ORCHLINK_PI_EXTENSION
    assert "do not read every file" in ORCHLINK_PI_EXTENSION
    assert "no new value" in ORCHLINK_PI_EXTENSION
    assert "No big paragraph" in ORCHLINK_PI_EXTENSION
    assert "too broad" in ORCHLINK_PI_EXTENSION
    assert "stripChatReplyMarker" in ORCHLINK_PI_EXTENSION
    assert "pendingTask" in ORCHLINK_PI_EXTENSION
    assert "pi.on(\"input\"" in ORCHLINK_PI_EXTENSION
    assert "currentTask = pendingTask" in ORCHLINK_PI_EXTENSION
    assert "markMessageStatus" in ORCHLINK_PI_EXTENSION
    assert "RUNNING" in ORCHLINK_PI_EXTENSION
    assert "checkCurrentTaskCancellation" in ORCHLINK_PI_EXTENSION
    assert "Stop this work now" in ORCHLINK_PI_EXTENSION
    assert "isRecoverableAssistantError" in ORCHLINK_PI_EXTENSION
    assert "ORCHLINK_RECOVERABLE_ERROR_GRACE_MS" in ORCHLINK_PI_EXTENSION
    assert "180000" in ORCHLINK_PI_EXTENSION
    assert "WebSocket error|provider_transport_failure|transport" in ORCHLINK_PI_EXTENSION
    assert "waiting for Pi recovery" in ORCHLINK_PI_EXTENSION
    assert "ORCHLINK_ACTIVITY_HEARTBEAT_MS" in ORCHLINK_PI_EXTENSION
    assert "postCurrentActivity" in ORCHLINK_PI_EXTENSION
    assert "pi.on(\"tool_call\"" in ORCHLINK_PI_EXTENSION
    assert "pi.on(\"tool_result\"" in ORCHLINK_PI_EXTENSION
    assert "Next: if worker asked a direct question" in ORCHLINK_PI_EXTENSION
    assert "-m \"<your answer>\"" in ORCHLINK_PI_EXTENSION
    assert "Talk Mode should stop only when" not in ORCHLINK_PI_EXTENSION
    assert "renderLeadPrompt(message), { deliverAs: \"steer\" }" in ORCHLINK_PI_EXTENSION
    assert "deliverAs: \"nextTurn\"" not in ORCHLINK_PI_EXTENSION
    assert "Stop any unrelated work now" in ORCHLINK_PI_EXTENSION


def test_pi_extension_keeps_current_task_during_recoverable_transport_error():
    from orchlink.connector.pi_extension import ORCHLINK_PI_EXTENSION

    assert """if (isRecoverableAssistantError(event.message)) {
      void postCurrentActivity("recovering", "Provider transport error; waiting for Pi recovery.", { phase: "recovering" });
      deferRecoverableFailure(task, event.message, ctx);
      return;
    }

    clearRecoveryTimer();
    currentTask = undefined;
    clearCancelCheck();
    clearActivityHeartbeat();""" in ORCHLINK_PI_EXTENSION


def test_broker_run_command_is_registered_without_starting_server(monkeypatch):
    from orchlink.cli import main as cli_main

    called = {}

    def fake_run(app_path, host, port, reload):
        called.update({"app_path": app_path, "host": host, "port": port, "reload": reload})

    monkeypatch.setattr(cli_main.uvicorn, "run", fake_run)

    result = CliRunner().invoke(
        cli_main.app,
        ["broker", "run", "--host", "127.0.0.1", "--port", "8788"],
    )

    assert result.exit_code == 0
    assert called == {
        "app_path": "orchlink.broker.main:app",
        "host": "127.0.0.1",
        "port": 8788,
        "reload": False,
    }


def test_doctor_reports_project_local_state_and_global_cli_guidance(tmp_path):
    from orchlink.cli.main import app

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "orchestrator.yaml").write_text("agent_id: orchestrator\n", encoding="utf-8")
    (config_dir / "worker-backend.yaml").write_text("agent_id: worker-backend\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["doctor", "--config-dir", str(config_dir)])

    assert result.exit_code == 0
    assert "Orchlink doctor" in result.output
    assert "Legacy config dir" not in result.output
    assert "orchestrator.yaml" not in result.output
    assert "worker-backend.yaml" not in result.output
    assert "~/.local/bin/orchlink" in result.output
