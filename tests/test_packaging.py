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
    assert "jobs" in result.output
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
    assert "Do not treat one worker reply as a final summary" in ORCHLINK_PI_EXTENSION


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


def test_doctor_reports_config_dir_and_global_cli_guidance(tmp_path):
    from orchlink.cli.main import app

    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "orchestrator.yaml").write_text("agent_id: orchestrator\n", encoding="utf-8")
    (config_dir / "worker-backend.yaml").write_text("agent_id: worker-backend\n", encoding="utf-8")

    result = CliRunner().invoke(app, ["doctor", "--config-dir", str(config_dir)])

    assert result.exit_code == 0
    assert "Orchlink doctor" in result.output
    assert str(config_dir) in result.output
    assert "orchestrator.yaml: found" in result.output
    assert "worker-backend.yaml: found" in result.output
    assert "~/.local/bin/orchlink" in result.output
