from pathlib import Path

import yaml
from typer.testing import CliRunner

from orchlink.bridge.ask import build_chat_envelope, build_task_envelope
from orchlink.cli.main import app
from orchlink.project.config import load_project_config
from orchlink.project.init import init_project


runner = CliRunner()


def test_init_project_creates_project_config_and_skills(tmp_path):
    paths = init_project(tmp_path, project_id="demo")

    assert paths["config"].is_file()
    assert paths["lead_skill"].is_file()
    assert paths["work_skill"].is_file()
    assert paths["run_dir"].is_dir()

    data = yaml.safe_load(paths["config"].read_text(encoding="utf-8"))
    assert data["project_id"] == "demo"
    assert data["lead"]["agent_id"] == "demo.lead"
    assert data["work"]["agent_id"] == "demo.work"
    assert data["broker"]["auto_start"] is True
    lead_skill = paths["lead_skill"].read_text(encoding="utf-8")
    work_skill = paths["work_skill"].read_text(encoding="utf-8")
    assert "not just delegate" in lead_skill
    assert "Task message checklist" in lead_skill
    assert "orch talk work" in lead_skill
    assert "orch ask work --wait" in lead_skill
    assert "orch send work" in lead_skill
    assert "orch cancel T002" in lead_skill
    assert "wait timeout does not cancel" in lead_skill
    assert "Do not use `orch send` for review gates" in lead_skill
    assert "orch idle" in lead_skill
    assert "Do not run dependent full tests" in lead_skill
    assert "single-flight" in lead_skill
    assert "steering interrupt" in lead_skill
    assert "think critically" in lead_skill
    assert "orch say C001" in lead_skill
    assert "orch close C001" in lead_skill
    assert "`C001` is a conversation ID" in lead_skill
    assert "Do not use `orch get C001`" in lead_skill
    assert "Talk Mode is a conversation" in lead_skill
    assert "Do not summarize after the first worker reply" in lead_skill
    assert "Stop conditions" in lead_skill
    assert "no new value" in lead_skill
    assert "do not do an exhaustive scan" in lead_skill
    assert "no TASK_ID" in lead_skill
    assert "MODE: DISCUSS | PLAN | DO | REVIEW" in lead_skill
    assert "## Modes" in work_skill
    assert "TALK: discuss" in work_skill
    assert "For TALK, behave like a collaborator" in work_skill
    assert "No big paragraph" in work_skill
    assert "Do not agree by default" in work_skill
    assert "too broad" in work_skill
    assert "ignore the command framing" in work_skill
    assert "read every file" in work_skill
    assert "Stop conditions for TALK" in work_skill
    assert "proceed, fix something first" in work_skill
    assert "TYPE: CHAT_REPLY" in work_skill


def test_refresh_skills_keeps_existing_project_config(tmp_path):
    paths = init_project(tmp_path, project_id="demo")
    paths["config"].write_text("project_id: custom\n", encoding="utf-8")
    paths["lead_skill"].write_text("old lead", encoding="utf-8")
    paths["work_skill"].write_text("old work", encoding="utf-8")

    refreshed = init_project(tmp_path, refresh_skills=True)

    assert refreshed["config"].read_text(encoding="utf-8") == "project_id: custom\n"
    assert "Task message checklist" in refreshed["lead_skill"].read_text(encoding="utf-8")
    assert "## Modes" in refreshed["work_skill"].read_text(encoding="utf-8")


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
