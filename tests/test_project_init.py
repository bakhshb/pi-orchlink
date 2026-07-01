from pathlib import Path

import yaml
from typer.testing import CliRunner

from orchlink.bridge.ask import build_chat_envelope, build_task_envelope
from orchlink.cli.main import app
from orchlink.core.prompt_policy import TaskPromptPolicy
from orchlink.connector.pi_connector import PiConnector
from orchlink.project.config import load_project_config
from orchlink.project.init import init_project, load_skill_reference_template


runner = CliRunner()


def test_init_project_creates_project_config_and_skills(tmp_path):
    paths = init_project(tmp_path, project_id="demo")

    assert paths["config"].is_file()
    assert paths["lead_skill"].is_file()
    assert paths["work_skill"].is_file()
    assert paths["skill_references"].is_dir()
    assert (paths["skill_references"] / "goal-mode.md").is_file()
    assert paths["run_dir"].is_dir()

    data = yaml.safe_load(paths["config"].read_text(encoding="utf-8"))
    assert data["project_id"] == "demo"
    assert data["lead"]["agent_id"] == "demo.lead"
    assert data["work"]["agent_id"] == "demo.work"
    assert "timeout_seconds" not in data["work"]
    assert data["pi"]["session_dir"] == ".orch/run/pi-sessions"
    assert data["broker"]["auto_start"] is True
    assert data["broker"]["auto_stop"] is True
    assert data["broker"]["require_peer_sessions"] is True
    assert data["broker"]["store_backend"] == "memory"
    assert data["broker"]["store_path"] == ".orch/run/orchlink-journal.jsonl"
    assert data["broker"]["session_heartbeat_interval_seconds"] == 10
    assert data["broker"]["session_grace_seconds"] == 25
    lead_skill = paths["lead_skill"].read_text(encoding="utf-8")
    work_skill = paths["work_skill"].read_text(encoding="utf-8")
    assert "# Lead Role" in lead_skill
    assert "## Progressive reference files" in lead_skill
    assert "## Task prompt shape" in lead_skill
    assert "## Non-negotiable safety rules" in lead_skill
    assert "orch ask work --wait" in lead_skill
    assert "orch send" in lead_skill
    assert "orch wait" in lead_skill
    assert "orch get" in lead_skill
    assert "orch idle" in lead_skill
    assert "references/lead-commands.md" in lead_skill
    assert "references/goal-mode.md" in lead_skill
    policy = TaskPromptPolicy()
    assert policy.lead_task_prompt_guidance_markdown() in lead_skill
    assert policy.lead_reply_guidance_markdown() in lead_skill
    assert "{{" not in lead_skill
    assert "# Worker Role" in work_skill
    assert "## Task behavior" in work_skill
    assert "## TALK mode" in work_skill
    assert "## Task replies" in work_skill
    assert "Discuss or recommend" in work_skill
    assert "For TALK, behave like a collaborator" in work_skill
    assert "No template and no required labels" in work_skill
    assert "Do not agree by default" in work_skill
    assert "If implementation is not explicitly allowed" in work_skill
    assert policy.worker_reply_guidance_markdown() in work_skill
    assert "{{" not in work_skill
    assert "fixed summary/changed/tests template" in work_skill
    assert "summary:" not in work_skill
    assert "changed/inspected:" not in work_skill
    assert "TYPE: CHAT_REPLY" not in work_skill


def test_refresh_skills_keeps_existing_project_config(tmp_path):
    paths = init_project(tmp_path, project_id="demo")
    paths["config"].write_text("project_id: custom\n", encoding="utf-8")
    paths["lead_skill"].write_text("old lead", encoding="utf-8")
    paths["work_skill"].write_text("old work", encoding="utf-8")

    refreshed = init_project(tmp_path, refresh_skills=True)

    assert refreshed["config"].read_text(encoding="utf-8") == "project_id: custom\n"
    assert "# Lead Role" in refreshed["lead_skill"].read_text(encoding="utf-8")
    assert "## Task prompt shape" in refreshed["lead_skill"].read_text(encoding="utf-8")
    assert "## Task behavior" in refreshed["work_skill"].read_text(encoding="utf-8")
    assert refreshed["skill_references"].joinpath("goal-mode.md").read_text(encoding="utf-8") == load_skill_reference_template("goal-mode.md")


def test_cli_init_uses_current_folder_name_by_default(monkeypatch, tmp_path):
    project_dir = tmp_path / "sample-project"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)

    result = runner.invoke(app, ["init"])

    assert result.exit_code == 0
    config = yaml.safe_load((project_dir / ".orch" / "project.yaml").read_text(encoding="utf-8"))
    assert config["project_id"] == "sample-project"
    assert (project_dir / ".orch" / "skills" / "lead.md").is_file()
    assert (project_dir / ".orch" / "skills" / "work.md").is_file()
    assert (project_dir / ".orch" / "skills" / "references" / "goal-mode.md").is_file()


def test_pi_connector_defaults_to_project_local_session_dir(tmp_path):
    init_project(tmp_path, project_id="demo")
    config = load_project_config(tmp_path)
    config["pi"].pop("session_dir")

    argv = PiConnector(config).lead_argv()

    session_dir = tmp_path / ".orch" / "run" / "pi-sessions"
    assert "--session-dir" in argv
    assert argv[argv.index("--session-dir") + 1] == str(session_dir)
    assert session_dir.is_dir()


def test_pi_connector_launches_resolved_path_from_path_lookup(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    config = load_project_config(tmp_path)
    resolved = r"C:\Users\demo\AppData\Roaming\npm\pi.cmd"

    monkeypatch.setattr("orchlink.connector.pi_connector.shutil.which", lambda command: resolved if command == "pi" else None)

    connector = PiConnector(config)

    assert connector.pi_command() == "pi"
    assert connector.check_available()
    assert connector.lead_argv()[0] == resolved
    assert connector.work_interactive_argv()[0] == resolved


def test_pi_connector_adds_current_scripts_dir_to_pi_environment(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    config = load_project_config(tmp_path)
    scripts_dir = tmp_path / ".venv" / "Scripts"
    python_exe = scripts_dir / "python.exe"

    monkeypatch.setenv("PATH", "existing-path")
    monkeypatch.setattr("orchlink.connector.pi_connector.sys.executable", str(python_exe))

    env = PiConnector(config)._env("lead")

    assert env["PATH"].split(";" if ";" in env["PATH"] else ":")[0] == str(scripts_dir)
    assert env["Path"] == env["PATH"]


def test_chat_envelope_summarizes_topic_without_duplicating_full_message(tmp_path):
    init_project(tmp_path, project_id="demo")
    config = load_project_config(tmp_path)
    long_message = "MODE: DISCUSS\nTASK_ID: SHOULD_NOT_BECOME_TOPIC\n" + ("x" * 180)

    envelope = build_chat_envelope(config, "work", "C001", long_message)

    assert envelope["type"] == "CHAT_START"
    assert envelope["delivery"] == "conversation"
    assert envelope["payload"]["topic"] == "MODE: DISCUSS"
    assert envelope["payload"]["message"] == long_message
    assert "Reply conversationally." in envelope["payload"]["constraints"]
    assert envelope["payload"]["expected_reply"] == []
    assert any("Do not read every file" in item for item in envelope["payload"]["constraints"])


def test_project_ask_envelope_resolves_work_alias(tmp_path):
    init_project(tmp_path, project_id="demo")
    config = load_project_config(tmp_path)

    envelope = build_task_envelope(config, "work", "T001", "Return PLAN only.", timeout_seconds=30)

    assert envelope["protocol"] == "orch-a2a-v1"
    assert envelope["project_id"] == "demo"
    assert envelope["from_agent"] == "demo.lead"
    assert envelope["to_agent"] == "demo.work"
    assert envelope["task_id"] == "T001"
    assert envelope["delivery"] == "async"
    assert envelope["payload"]["mode"] == "PLAN"
    assert envelope["payload"]["scope"]["forbidden"] == [".git/**", ".orch/**", "node_modules/**", ".venv/**"]
    assert envelope["payload"]["expected_reply"] == []

    review_envelope = build_task_envelope(config, "work", "T002", "Please inspect my changes.", timeout_seconds=30)
    no_edit_envelope = build_task_envelope(config, "work", "T003", "Reply in one sentence. Do not inspect files or edit anything.", timeout_seconds=30)
    do_envelope = build_task_envelope(config, "work", "T004", "Add one parser test. Do not edit docs.", timeout_seconds=30)

    assert review_envelope["payload"]["mode"] == "REVIEW"
    assert no_edit_envelope["payload"]["mode"] == "PLAN"
    assert do_envelope["payload"]["mode"] == "DO"
