from pathlib import Path
from types import SimpleNamespace

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


def test_project_ask_defaults_to_wait(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    def fake_project_ask_worker_sync(**kwargs):
        assert kwargs["worker"] == "work"
        assert kwargs["task_id"] == "T001"
        assert kwargs["wait"] is True
        return {"status": "completed", "reply": {"type": "PLAN"}}

    monkeypatch.setattr(cli_main, "project_ask_worker_sync", fake_project_ask_worker_sync)

    result = runner.invoke(cli_main.app, ["ask", "work", "-t", "T001", "-m", "Return PLAN only."])

    assert result.exit_code == 0
    assert '"status": "completed"' in result.output
    assert "Async mode" not in result.output


def test_project_ask_wait_option_blocks(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    def fake_project_ask_worker_sync(**kwargs):
        assert kwargs["wait"] is True
        return {"status": "completed", "reply": {"type": "PLAN"}}

    monkeypatch.setattr(cli_main, "project_ask_worker_sync", fake_project_ask_worker_sync)

    result = runner.invoke(cli_main.app, ["ask", "work", "--wait", "-t", "T001", "-m", "Return PLAN only."])

    assert result.exit_code == 0
    assert '"status": "completed"' in result.output


def test_send_queues_async_task(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    def fake_send_worker_sync(**kwargs):
        assert kwargs["worker"] == "work"
        assert kwargs["task_id"] == "T002"
        assert kwargs["message"] == "Inspect tests."
        return {"status": "queued", "message_id": "msg-2"}

    monkeypatch.setattr(cli_main, "send_worker_sync", fake_send_worker_sync)

    result = runner.invoke(cli_main.app, ["send", "work", "-t", "T002", "-m", "Inspect tests."])

    assert result.exit_code == 0
    assert "Sent T002" in result.output
    assert "Async mode" in result.output
    assert "orch wait T002" in result.output


def test_send_rejects_review_gate_by_default(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)

    def fake_send_worker_sync(**kwargs):
        raise AssertionError("send should not be called for a review gate")

    monkeypatch.setattr(cli_main, "send_worker_sync", fake_send_worker_sync)

    result = runner.invoke(cli_main.app, ["send", "work", "-t", "R001", "-m", "MODE: REVIEW. Review my changes."])

    assert result.exit_code == 1
    assert "REVIEW is a gate" in result.output
    assert "orch ask work --wait" in result.output


def test_send_allows_async_review_when_explicit(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    def fake_send_worker_sync(**kwargs):
        assert kwargs["task_id"] == "R002"
        return {"status": "queued", "message_id": "msg-r2"}

    monkeypatch.setattr(cli_main, "send_worker_sync", fake_send_worker_sync)

    result = runner.invoke(
        cli_main.app,
        ["send", "work", "-t", "R002", "-m", "MODE: REVIEW. Async review of unrelated docs.", "--allow-async-review"],
    )

    assert result.exit_code == 0
    assert "Sent R002" in result.output


def test_talk_rejects_empty_message():
    result = runner.invoke(cli_main.app, ["talk", "work", "-m", "   "])

    assert result.exit_code == 1
    assert "Talk message cannot be empty" in result.output


def test_talk_creates_conversation(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "next_conversation_id", lambda config: "C001")

    def fake_start_talk_sync(**kwargs):
        assert kwargs["conversation_id"] == "C001"
        assert kwargs["worker"] == "work"
        assert kwargs["message"] == "Memory or SQLite?"
        assert kwargs["max_turns"] == 6
        return {"status": "queued", "conversation_id": "C001"}

    monkeypatch.setattr(cli_main, "start_talk_sync", fake_start_talk_sync)

    result = runner.invoke(cli_main.app, ["talk", "work", "-m", "Memory or SQLite?", "-r", "6"])

    assert result.exit_code == 0
    assert "Started conversation C001" in result.output
    assert "Waiting for worker reply" in result.output
    assert "not a final answer" in result.output


def test_say_rejects_empty_message():
    result = runner.invoke(cli_main.app, ["say", "C001", "-m", ""])

    assert result.exit_code == 1
    assert "Say message cannot be empty" in result.output


def test_say_sends_chat_turn(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(
        cli_main,
        "conversation_state",
        lambda config, conversation_id: {"conversation_id": "C001", "status": "OPEN", "turn": 2, "max_turns": 6},
    )

    def fake_say_talk_sync(**kwargs):
        assert kwargs["conversation_id"] == "C001"
        assert kwargs["turn"] == 3
        assert kwargs["max_turns"] == 6
        return {"status": "queued"}

    monkeypatch.setattr(cli_main, "say_talk_sync", fake_say_talk_sync)

    result = runner.invoke(cli_main.app, ["say", "C001", "-m", "Challenge restart risk."])

    assert result.exit_code == 0
    assert "Sent turn 3/6" in result.output
    assert "discussion is not resolved" in result.output


def test_close_sends_chat_close(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(
        cli_main,
        "conversation_state",
        lambda config, conversation_id: {"conversation_id": "C001", "status": "OPEN", "turn": 2, "max_turns": 6},
    )

    def fake_close_talk_sync(**kwargs):
        assert kwargs["conversation_id"] == "C001"
        assert kwargs["message"] == "Decision made."
        return {"status": "queued"}

    monkeypatch.setattr(cli_main, "close_talk_sync", fake_close_talk_sync)

    result = runner.invoke(cli_main.app, ["close", "C001", "-m", "Decision made."])

    assert result.exit_code == 0
    assert "Closed conversation C001" in result.output


def test_get_conversation_id_prints_conversation_guidance(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    def fake_broker_get_sync(config, path):
        if path == "/v1/tasks/C001":
            return {"status": "missing", "task_id": "C001", "error": "Task not found."}
        if path.startswith("/v1/jobs"):
            return {
                "jobs": [
                    {
                        "kind": "conversation",
                        "conversation_id": "C001",
                        "mode": "TALK",
                        "status": "OPEN",
                        "turn": 3,
                        "max_turns": 6,
                        "preview": "Discuss repo risks.",
                    }
                ]
            }
        raise AssertionError(path)

    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)

    result = runner.invoke(cli_main.app, ["get", "C001"])

    assert result.exit_code == 0
    assert "Conversation C001: OPEN" in result.output
    assert "Continue: orch say C001" in result.output


def test_cancel_command_posts_cancel(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    called = {}

    def fake_broker_post_sync(config, path, body=None):
        called.update({"path": path, "body": body})
        return {"status": "cancelled", "item_id": "T010", "cancelled": ["msg-010"]}

    monkeypatch.setattr(cli_main, "broker_post_sync", fake_broker_post_sync)

    result = runner.invoke(cli_main.app, ["cancel", "T010", "-m", "Wrong scope."])

    assert result.exit_code == 0
    assert called == {"path": "/v1/jobs/T010/cancel", "body": {"reason": "Wrong scope."}}
    assert "Cancelled T010" in result.output


def test_jobs_get_and_wait_commands(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    def fake_broker_get_sync(config, path):
        if path.startswith("/v1/jobs"):
            return {"jobs": [{"task_id": "T010", "mode": "PLAN", "status": "DONE", "from_agent": "demo.lead", "to_agent": "demo.work", "created_at": "now", "preview": "Inspect tests."}]}
        if path.startswith("/v1/tasks/T010/wait") or path == "/v1/tasks/T010":
            return {"status": "DONE", "task_id": "T010", "reply": {"type": "RESULT", "payload": {"summary": "Done."}}}
        raise AssertionError(path)

    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)

    jobs_result = runner.invoke(cli_main.app, ["jobs"])
    get_result = runner.invoke(cli_main.app, ["get", "T010"])
    wait_result = runner.invoke(cli_main.app, ["wait", "T010", "--timeout-seconds", "1"])

    assert jobs_result.exit_code == 0
    assert "ID" in jobs_result.output
    assert "KIND" in jobs_result.output
    assert "MODE" in jobs_result.output
    assert "T010" in jobs_result.output
    assert get_result.exit_code == 0
    assert "Done." in get_result.output
    assert wait_result.exit_code == 0
    assert "Done." in wait_result.output


def test_idle_reports_ready_when_no_pending_jobs(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "broker_get_sync", lambda config, path: {"jobs": []})

    result = runner.invoke(cli_main.app, ["idle"])

    assert result.exit_code == 0
    assert "Worker idle" in result.output


def test_idle_blocks_when_worker_has_pending_jobs(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(
        cli_main,
        "broker_get_sync",
        lambda config, path: {
            "jobs": [
                {
                    "kind": "task",
                    "task_id": "R001",
                    "mode": "REVIEW",
                    "status": "DELIVERED",
                    "preview": "Review before full tests.",
                }
            ]
        },
    )

    result = runner.invoke(cli_main.app, ["idle"])

    assert result.exit_code == 1
    assert "Worker is not idle" in result.output
    assert "R001" in result.output
    assert "Do not run dependent full tests" in result.output


def test_task_command_reports_in_progress(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(
        cli_main,
        "fetch_status_sync",
        lambda broker_url, api_key: {
            "active_messages": [
                {
                    "task_id": "T001",
                    "status": "IN_PROGRESS",
                    "from_agent": "demo.lead",
                    "to_agent": "demo.work",
                }
            ]
        },
    )
    monkeypatch.setattr(
        cli_main,
        "fetch_events_sync",
        lambda broker_url, api_key, limit=500: {
            "events": [
                {
                    "task_id": "T001",
                    "type": "message_delivered",
                    "to_agent": "demo.work",
                }
            ]
        },
    )

    result = runner.invoke(cli_main.app, ["task", "T001"])

    assert result.exit_code == 0
    assert "Task T001: IN_PROGRESS" in result.output
    assert "Worker is still in progress" in result.output


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


def test_update_runs_git_and_reinstalls_package(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli_main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli_main.subprocess, "run", fake_run)
    monkeypatch.setattr(cli_main.sys, "executable", "/venv/bin/python")

    result = runner.invoke(cli_main.app, ["update", "--ref", "main"])

    assert result.exit_code == 0
    assert ["git", "-C", str(tmp_path), "fetch", "--tags", "--prune", "origin"] in calls
    assert ["git", "-C", str(tmp_path), "checkout", "main"] in calls
    assert ["git", "-C", str(tmp_path), "pull", "--ff-only", "origin", "main"] in calls
    assert ["/venv/bin/python", "-m", "pip", "install", "-e", str(tmp_path)] in calls
    assert "Update complete" in result.output
    assert "orch init --refresh-skills" in result.output
    assert "orch lead --new" in result.output
    assert "orch work --new" in result.output


def test_doctor_reports_stale_project_skills(monkeypatch, tmp_path):
    paths = init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "broker_health", lambda url: False)

    current = runner.invoke(cli_main.app, ["doctor"])

    assert current.exit_code == 0
    assert "lead.md: current" in current.output
    assert "work.md: current" in current.output
    assert "Project .orch files: current" in current.output

    paths["lead_skill"].write_text("old lead\n", encoding="utf-8")

    stale = runner.invoke(cli_main.app, ["doctor"])

    assert stale.exit_code == 0
    assert "lead.md: stale" in stale.output
    assert "Project .orch files: stale" in stale.output
    assert "Run: orch init --refresh-skills" in stale.output


def test_status_command_prints_status(monkeypatch):
    monkeypatch.setattr(
        cli_main,
        "fetch_status_sync",
        lambda broker_url, api_key: {"broker": "ok", "agent_count": 0},
    )

    result = runner.invoke(cli_main.app, ["status", "--broker-url", "http://broker", "--api-key", "test-key"])

    assert result.exit_code == 0
    assert '"broker": "ok"' in result.output
