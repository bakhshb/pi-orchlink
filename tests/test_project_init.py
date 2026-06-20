from pathlib import Path

import yaml
from typer.testing import CliRunner

from orchlink.bridge.ask import build_task_envelope
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
    assert "not only a task delegate" in lead_skill
    assert "do not do the same scope yourself" in lead_skill
    assert "split the scope explicitly" in lead_skill
    assert "workload split" in work_skill
    assert "WORKLOAD_SPLIT" in work_skill


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


def test_project_ask_envelope_resolves_work_alias(tmp_path):
    init_project(tmp_path, project_id="demo")
    config = load_project_config(tmp_path)

    envelope = build_task_envelope(config, "work", "T001", "Return PLAN only.", timeout_seconds=30)

    assert envelope["protocol"] == "orch-a2a-v1"
    assert envelope["project_id"] == "demo"
    assert envelope["from_agent"] == "demo.lead"
    assert envelope["to_agent"] == "demo.work"
    assert envelope["task_id"] == "T001"
    assert envelope["payload"]["scope"]["forbidden"] == [".git/**", ".orch/**", "node_modules/**", ".venv/**"]
