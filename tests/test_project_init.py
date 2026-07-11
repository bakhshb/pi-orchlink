import yaml
from typer.testing import CliRunner

from orchlink.client import build_chat_envelope, build_task_envelope
from orchlink.core.envelope import MessageEnvelope, envelope_to_dict
from orchlink.cli.main import app
from orchlink.core.prompt_policy import TaskPromptPolicy
from orchlink.connector.pi_connector import PiConnector
from orchlink.project.config import load_project_config
from orchlink.project.init import default_project_config, init_project, load_skill_reference_template


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
    assert "## Worker-use trigger" in lead_skill
    assert "worker input would tighten the loop" in lead_skill
    assert "## Task prompt shape" in lead_skill
    assert "## Non-negotiable safety rules" in lead_skill
    assert "orch send work --wait" in lead_skill
    assert "orch send" in lead_skill
    assert "orch jobs --wait" in lead_skill
    assert "orch jobs --result" in lead_skill
    assert "handle result notifications first-come, first-served" in lead_skill
    assert "orch jobs --idle" in lead_skill
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


def test_pi_connector_loads_ui_monitor_extension_for_lead_only(tmp_path):
    init_project(tmp_path, project_id="demo")
    config = load_project_config(tmp_path)

    lead_argv = PiConnector(config).lead_argv()
    work_argv = PiConnector(config).work_interactive_argv()

    ui_extension = str(tmp_path / ".orch" / "run" / "orchlink-pi-ui-extension.ts")
    assert ui_extension in lead_argv
    assert ui_extension not in work_argv
    assert (tmp_path / ".orch" / "run" / "orchlink-pi-ui-extension.ts").is_file()


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


def test_pi_connector_passes_worker_model_and_thinking_to_pi(tmp_path):
    init_project(tmp_path, project_id="demo")
    config = load_project_config(tmp_path)
    config["work"]["model"] = "openai/codex-max"
    config["work"]["thinking"] = "xhigh"

    connector = PiConnector(config)
    argv = connector.work_rpc_argv()
    env = connector._env("work")

    assert argv[argv.index("--model") + 1] == "openai/codex-max"
    assert argv[argv.index("--thinking") + 1] == "xhigh"
    assert env["ORCHLINK_WORKER_MODEL"] == "openai/codex-max"
    assert env["ORCHLINK_WORKER_THINKING"] == "xhigh"


def test_pi_connector_keeps_rpc_extension_discovery_for_ollama_provider(tmp_path):
    init_project(tmp_path, project_id="demo")
    config = load_project_config(tmp_path)
    config["work"]["model"] = "ollama-cloud/kimi-k2.7-code"

    argv = PiConnector(config).work_rpc_argv()

    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "ollama-cloud/kimi-k2.7-code"
    assert "--no-extensions" not in argv
    assert "--extension" in argv


def test_pi_connector_disables_rpc_extension_discovery_for_builtin_providers(tmp_path):
    init_project(tmp_path, project_id="demo")
    config = load_project_config(tmp_path)
    config["work"]["model"] = "openai-codex/gpt-5.5"

    argv = PiConnector(config).work_rpc_argv()

    assert "--no-extensions" in argv


def test_pi_connector_acquire_metadata_cannot_override_session_identity(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    config = load_project_config(tmp_path)
    connector = PiConnector(config)
    captured = {}

    def fake_post_broker(path, body):
        assert path == "/v1/sessions/acquire"
        captured.update(body)
        return {"session": {"lease_id": "lease-good"}}

    monkeypatch.setattr(connector, "_post_broker", fake_post_broker)

    lease_id = connector.acquire_session(
        "work",
        123,
        lease_id="lease-real",
        metadata={
            "project_id": "evil",
            "agent_id": "evil.worker",
            "role": "lead",
            "pid": 999,
            "session_id": "evil-session",
            "worker_name": "evil-worker",
            "lease_id": "lease-evil",
            "backend": "rpc-supervisor",
        },
    )

    assert lease_id == "lease-good"
    assert captured["project_id"] == "demo"
    assert captured["agent_id"] == "demo.work"
    assert captured["role"] == "work"
    assert captured["pid"] == 123
    assert captured["session_id"] == "work"
    assert captured["worker_name"] == "work"
    assert captured["lease_id"] == "lease-real"
    assert captured["backend"] == "rpc-supervisor"


def test_chat_envelope_summarizes_topic_without_duplicating_full_message(tmp_path):
    init_project(tmp_path, project_id="demo")
    config = load_project_config(tmp_path)
    long_message = "MODE: DISCUSS\nTASK_ID: SHOULD_NOT_BECOME_TOPIC\n" + ("x" * 180)

    envelope = build_chat_envelope(config, "work", "C001", long_message)
    wire = envelope_to_dict(envelope)

    assert isinstance(envelope, MessageEnvelope)
    assert wire["type"] == "CHAT_START"
    assert wire["delivery"] == "conversation"
    assert wire["payload"]["topic"] == "MODE: DISCUSS"
    assert wire["payload"]["message"] == long_message
    assert "Reply conversationally." in wire["payload"]["constraints"]
    assert wire["payload"]["expected_reply"] == []
    assert any("Do not read every file" in item for item in wire["payload"]["constraints"])


def test_task_envelope_resolves_named_worker_alias_without_yaml_registry(tmp_path):
    paths = init_project(tmp_path, project_id="demo")
    config = load_project_config(tmp_path)
    data = yaml.safe_load(paths["config"].read_text(encoding="utf-8"))

    envelope = build_task_envelope(config, "review", "R001", "Return PLAN only.", timeout_seconds=30)
    wire = envelope_to_dict(envelope)

    assert isinstance(envelope, MessageEnvelope)
    assert wire["project_id"] == "demo"
    assert wire["from_agent"] == "demo.lead"
    assert wire["to_agent"] == "demo.review"
    assert "workers" not in data


def test_task_envelope_resolves_work_alias(tmp_path):
    init_project(tmp_path, project_id="demo")
    config = load_project_config(tmp_path)

    envelope = build_task_envelope(config, "work", "T001", "Return PLAN only.", timeout_seconds=30)
    wire = envelope_to_dict(envelope)

    assert isinstance(envelope, MessageEnvelope)
    assert wire["protocol"] == "orch-a2a-v1"
    assert wire["project_id"] == "demo"
    assert wire["from_agent"] == "demo.lead"
    assert wire["to_agent"] == "demo.work"
    assert wire["task_id"] == "T001"
    assert wire["delivery"] == "async"
    assert wire["payload"]["mode"] == "PLAN"
    assert wire["payload"]["scope"]["forbidden"] == [".git/**", ".orch/**", "node_modules/**", ".venv/**"]
    assert wire["payload"]["expected_reply"] == []
    assert wire["payload"]["thinking"] == "xhigh"

    review_envelope = envelope_to_dict(build_task_envelope(config, "work", "T002", "Please inspect my changes.", timeout_seconds=30))
    no_edit_envelope = envelope_to_dict(build_task_envelope(config, "work", "T003", "Reply in one sentence. Do not inspect files or edit anything.", timeout_seconds=30))
    do_envelope = envelope_to_dict(build_task_envelope(config, "work", "T004", "Add one parser test. Do not edit docs.", timeout_seconds=30))
    explicit_envelope = envelope_to_dict(build_task_envelope(config, "work", "T005", "Please inspect my changes.", timeout_seconds=30, thinking="low"))

    assert review_envelope["payload"]["mode"] == "REVIEW"
    assert review_envelope["payload"]["thinking"] == "xhigh"
    assert no_edit_envelope["payload"]["mode"] == "PLAN"
    assert no_edit_envelope["payload"]["thinking"] == "xhigh"
    assert do_envelope["payload"]["mode"] == "DO"
    assert do_envelope["payload"]["thinking"] == "medium"
    assert explicit_envelope["payload"]["thinking"] == "low"


def test_default_project_config_generates_random_nondefault_api_key(tmp_path):
    config = default_project_config(tmp_path, project_id="demo")

    api_key = config["broker"]["api_key"]
    assert isinstance(api_key, str)
    assert api_key != "change-me"
    assert len(api_key) >= 32


def test_default_project_config_generates_unique_api_keys(tmp_path):
    first = default_project_config(tmp_path, project_id="demo")["broker"]["api_key"]
    second = default_project_config(tmp_path, project_id="demo")["broker"]["api_key"]

    assert first != second
    assert first != "change-me"
    assert second != "change-me"


def test_init_project_persists_generated_api_key(tmp_path):
    paths = init_project(tmp_path, project_id="demo")

    data = yaml.safe_load(paths["config"].read_text(encoding="utf-8"))
    persisted_key = data["broker"]["api_key"]
    assert persisted_key != "change-me"
    assert len(persisted_key) >= 32
