from types import SimpleNamespace

from typer.testing import CliRunner

from orchlink.cli import main as cli_main
from orchlink.project.config import load_project_config
from orchlink.project.init import init_project, load_skill_template


runner = CliRunner()


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

    result = runner.invoke(cli_main.app, ["send", "work", "-t", "R001", "-m", "Please review my changes."])

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
        ["send", "work", "-t", "R002", "-m", "Async review of unrelated docs.", "--allow-async-review"],
    )

    assert result.exit_code == 0
    assert "Sent R002" in result.output
    assert "verify the exact result" in result.output
    assert "orch wait R002" in result.output


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
        assert kwargs["max_turns"] == 12
        return {"status": "queued", "conversation_id": "C001"}

    monkeypatch.setattr(cli_main, "start_talk_sync", fake_start_talk_sync)

    result = runner.invoke(cli_main.app, ["talk", "work", "-m", "Memory or SQLite?", "-r", "6"])

    assert result.exit_code == 0
    assert "Started conversation C001" in result.output
    assert "Max rounds: 6 (12 turns)" in result.output
    assert "Reply will arrive" in result.output
    assert "polling needed" in result.output
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
        if path.startswith("/v1/tasks/C001"):
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
    monkeypatch.setattr(
        cli_main,
        "fetch_events_sync",
        lambda *args, **kwargs: {
            "events": [
                {
                    "type": "message_queued",
                    "conversation_id": "C001",
                    "message_type": "CHAT_START",
                    "from_agent": "demo.lead",
                    "turn": 1,
                    "max_turns": 6,
                    "payload": {"message": "Should we pick memory or SQLite?"},
                },
                {
                    "type": "reply_received",
                    "conversation_id": "C001",
                    "message_type": "CHAT_REPLY",
                    "from_agent": "demo.work",
                    "turn": 2,
                    "max_turns": 6,
                    "payload": {"summary": "SQLite is safer if restart recovery matters."},
                },
            ]
        },
    )

    result = runner.invoke(cli_main.app, ["get", "C001"])

    assert result.exit_code == 0
    assert "Conversation C001: OPEN" in result.output
    assert "Continue: orch say C001" in result.output
    assert "Conversation turns" in result.output
    assert "Should we pick memory or SQLite?" in result.output
    assert "SQLite is safer" in result.output


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
    assert called == {"path": "/v1/jobs/T010/cancel", "body": {"reason": "Wrong scope.", "project_id": "demo"}}
    assert "Cancelled T010" in result.output
    assert "asks Pi to abort" in result.output
    assert "already-running shell command" in result.output


def test_jobs_rejects_stale_broker(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    # Patch the imported binding in client.sync because that module imports
    # the helper via ``from orchlink.client.process import broker_info``; rebinding
    # the source module's attribute would only take effect on a fresh import,
    # not for callers already holding a reference. The cli_main module's own
    # binding is a separate reference that ``jobs`` does not consult.
    monkeypatch.setattr(
        "orchlink.client.sync.broker_info",
        lambda url: {"status": "ok", "service": "orchlink", "version": "0.1.0", "capabilities": []},
    )

    result = runner.invoke(cli_main.app, ["jobs"])

    assert result.exit_code == 1
    assert "older incompatible Orchlink" in result.output
    assert "broker 0.1.0" in result.output
    assert "orch stop --all" in result.output


def test_get_rejects_cross_project_result(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="chatting")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(
        cli_main,
        "broker_get_sync",
        lambda config, path: {
            "status": "DONE",
            "project_id": "nexora",
            "task_id": "T001",
            "reply": {"project_id": "nexora", "type": "RESULT", "payload": {"summary": "old"}},
        },
    )

    result = runner.invoke(cli_main.app, ["get", "T001"])

    assert result.exit_code == 1
    assert "Refusing cross-project result" in result.output
    assert "nexora" in result.output
    assert "chatting" in result.output


def test_jobs_get_and_wait_commands(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    def fake_broker_get_sync(config, path):
        if path.startswith("/v1/jobs"):
            return {"jobs": [{"task_id": "T010", "mode": "PLAN", "status": "DONE", "from_agent": "demo.lead", "to_agent": "demo.work", "created_at": "now", "preview": "Inspect tests."}]}
        if path.startswith("/v1/tasks/T010/wait") or path.startswith("/v1/tasks/T010"):
            return {"status": "DONE", "project_id": "demo", "task_id": "T010", "reply": {"project_id": "demo", "type": "RESULT", "payload": {"summary": "Done."}}}
        raise AssertionError(path)

    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)

    jobs_result = runner.invoke(cli_main.app, ["jobs"])
    get_result = runner.invoke(cli_main.app, ["get", "T010"])
    wait_result = runner.invoke(cli_main.app, ["wait", "T010", "--timeout", "1"])

    assert jobs_result.exit_code == 0
    assert "ID" in jobs_result.output
    assert "KIND" in jobs_result.output
    assert "MODE" in jobs_result.output
    assert "T010" in jobs_result.output
    assert get_result.exit_code == 0
    assert "Done." in get_result.output
    assert wait_result.exit_code == 0
    assert "Done." in wait_result.output


def test_jobs_supports_filters_json_and_activity(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    seen_paths = []

    def fake_broker_get_sync(config, path):
        seen_paths.append(path)
        return {
            "project_id": "demo",
            "jobs": [
                {
                    "kind": "talk",
                    "conversation_id": "C001",
                    "mode": "TALK",
                    "status": "OPEN",
                    "from_agent": "demo.lead",
                    "to_agent": "demo.work",
                    "updated_at": "2026-06-23T04:42:00+00:00",
                    "preview": "Should we simplify?",
                    "last_activity_at": "2026-06-23T04:42:01+00:00",
                    "last_activity_type": "tool_call",
                    "last_activity_tool": "read",
                    "last_activity_preview": "src/foo.py",
                }
            ],
        }

    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)

    table = runner.invoke(cli_main.app, ["jobs", "--active", "--kind", "talk", "--status", "open", "--id", "C001"])
    json_result = runner.invoke(cli_main.app, ["jobs", "--json"])

    assert table.exit_code == 0
    assert seen_paths[0] == "/v1/jobs?limit=500&project_id=demo&active=true&status=OPEN&kind=talk&id=C001"
    assert "UPDATED" in table.output
    assert "C001" in table.output
    assert "talk" in table.output
    assert "last activity" in table.output
    assert "read:" in table.output
    assert json_result.exit_code == 0
    assert '"project_id": "demo"' in json_result.output


def test_jobs_status_json_fetches_wide_window_before_client_filter(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    seen_paths = []

    def fake_broker_get_sync(config, path):
        seen_paths.append(path)
        return {
            "project_id": "demo",
            "jobs": [
                {"task_id": "T001", "status": "RUNNING", "from_agent": "demo.lead", "to_agent": "demo.work"},
                {"task_id": "T002", "status": "DONE", "from_agent": "demo.lead", "to_agent": "demo.work"},
            ],
        }

    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)

    result = runner.invoke(cli_main.app, ["jobs", "--status", "DONE", "--json"])

    assert result.exit_code == 0
    assert seen_paths == ["/v1/jobs?limit=500&project_id=demo&status=DONE"]
    assert '"task_id": "T002"' in result.output
    assert '"task_id": "T001"' not in result.output


def test_jobs_rejects_unknown_kind(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_main.app, ["jobs", "--kind", "chat"])

    assert result.exit_code == 1
    assert "--kind must be 'task' or 'talk'" in result.output


def test_job_activity_line_hides_only_terminal_heartbeat():
    job = {
        "status": "RUNNING",
        "last_activity_at": "2026-06-23T04:42:00+00:00",
        "last_activity_type": "heartbeat",
        "last_activity_preview": "Worker still active.",
    }

    assert "Worker still active" in cli_main.job_activity_line(job)

    done_job = {**job, "status": "DONE"}
    assert cli_main.job_activity_line(done_job) == ""

    done_tool_job = {**job, "status": "DONE", "last_activity_type": "tool_call", "last_activity_tool": "read", "last_activity_preview": "src/foo.py"}
    assert "read: src/foo.py" in cli_main.job_activity_line(done_tool_job)


def test_root_help_explains_commands():
    result = runner.invoke(cli_main.app, ["--help"])

    assert result.exit_code == 0
    assert "Start or reopen the visible Pi lead session" in result.output
    assert "Send a task to work and wait" in result.output
    assert "Show recent tasks and Talk conversations" in result.output
    assert "Print raw broker status JSON" in result.output


def test_jobs_help_explains_options():
    result = runner.invoke(cli_main.app, ["jobs", "--help"])

    assert result.exit_code == 0
    assert "Show recent tasks and Talk conversations" in result.output
    assert "Maximum number of recent jobs to show" in result.output
    assert "--active" in result.output
    assert "--status" in result.output
    assert "--kind" in result.output
    assert "--id" in result.output
    assert "--json" in result.output


def test_sessions_lists_active_project_sessions(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    def fake_broker_get_sync(config, path):
        assert path == "/v1/sessions?active=true&project_id=demo"
        return {
            "project_id": "demo",
            "sessions": [
                {
                    "agent_id": "demo.work",
                    "role": "work",
                    "status": "ACTIVE",
                    "pid": 123,
                    "session_id": "work-1",
                    "last_heartbeat_at": "2026-06-23T04:42:00+00:00",
                }
            ],
        }

    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)

    result = runner.invoke(cli_main.app, ["sessions"])

    assert result.exit_code == 0
    assert "AGENT" in result.output
    assert "demo.work" in result.output
    assert "ACTIVE" in result.output
    assert "work-1" in result.output


def test_sessions_all_and_json(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    def fake_broker_get_sync(config, path):
        assert path == "/v1/sessions?active=false&project_id=demo"
        return {"project_id": "demo", "sessions": [{"agent_id": "demo.lead", "status": "RELEASED"}]}

    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)

    result = runner.invoke(cli_main.app, ["sessions", "--all", "--json"])

    assert result.exit_code == 0
    assert '"project_id": "demo"' in result.output
    assert '"status": "RELEASED"' in result.output


def test_wait_help_shows_timeout_flag_only():
    result = runner.invoke(cli_main.app, ["wait", "--help"])

    assert result.exit_code == 0
    assert result.output.count("--timeout") == 1


def test_wait_prints_worker_activity_during_progress(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    wait_calls = 0

    def fake_broker_get_sync(config, path):
        nonlocal wait_calls
        if path.startswith("/v1/tasks/T010/activity?"):
            return {
                "activity": [
                    {
                        "id": 1,
                        "time": "2026-06-23T04:42:00+00:00",
                        "activity_type": "tool_call",
                        "tool_name": "bash",
                        "detail": "rg users",
                    }
                ]
            }
        if path.startswith("/v1/tasks/T010/wait"):
            wait_calls += 1
            if wait_calls == 1:
                return {"status": "WAIT_TIMEOUT", "project_id": "demo", "task_id": "T010", "error": "still waiting"}
            return {"status": "DONE", "project_id": "demo", "task_id": "T010", "reply": {"project_id": "demo", "type": "RESULT", "payload": {"summary": "Done."}}}
        raise AssertionError(path)

    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)

    result = runner.invoke(cli_main.app, ["wait", "T010", "--timeout", "3", "--poll-seconds", "1"])

    assert result.exit_code == 0
    assert "Worker activity" in result.output
    assert "bash: rg users" in result.output
    assert "Done." in result.output


def test_wait_rejects_mismatched_task_result(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    def fake_broker_get_sync(config, path):
        if path.startswith("/v1/tasks/T013/wait"):
            return {"status": "DONE", "project_id": "other", "task_id": "T012", "reply": {"project_id": "other", "type": "RESULT", "payload": {"summary": "stale"}}}
        raise AssertionError(path)

    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)

    result = runner.invoke(cli_main.app, ["wait", "T013", "--timeout", "1"])

    assert result.exit_code == 1
    assert "waiting for T013" in result.output
    assert "T012" in result.output


def test_get_failed_task_prints_stderr(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(
        cli_main,
        "broker_get_sync",
        lambda config, path: {
            "status": "FAILED",
            "project_id": "demo",
            "task_id": "T010",
            "reply": {"project_id": "demo", "type": "BLOCKER", "payload": {"summary": "", "stderr": "WebSocket error"}},
        },
    )

    result = runner.invoke(cli_main.app, ["get", "T010"])

    assert result.exit_code == 0
    assert "Task T010: FAILED" in result.output
    assert "Type: BLOCKER" in result.output
    assert "Stderr" in result.output
    assert "WebSocket error" in result.output


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
                    "last_activity_at": "2026-06-23T04:42:00+00:00",
                    "last_activity_type": "tool_call",
                    "last_activity_tool": "read",
                    "last_activity_preview": "apps/api/app/api/users.py",
                }
            ]
        },
    )

    result = runner.invoke(cli_main.app, ["idle"])

    assert result.exit_code == 1
    assert "Worker is not idle" in result.output
    assert "R001" in result.output
    assert "last activity" in result.output
    assert "read:" in result.output
    assert "apps/api/app/api/users.py" in result.output
    assert "Do not run dependent full tests" in result.output


def test_peek_prints_worker_activity(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    def fake_broker_get_sync(config, path):
        assert path.startswith("/v1/tasks/T001/activity?")
        return {
            "activity": [
                {
                    "id": 1,
                    "time": "2026-06-23T04:42:00+00:00",
                    "task_id": "T001",
                    "activity_type": "tool_call",
                    "tool_name": "bash",
                    "detail": "rg organization_id",
                }
            ]
        }

    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)

    result = runner.invoke(cli_main.app, ["peek", "T001"])

    assert result.exit_code == 0
    assert "Recent worker activity" in result.output
    assert "tool_call" in result.output
    assert "bash: rg organization_id" in result.output


def test_task_command_reports_in_progress(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(
        cli_main,
        "fetch_status_sync",
        lambda broker_url, api_key, project_id=None, task_id=None, since=0, limit=20: {
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
        lambda broker_url, api_key, limit=500, project_id=None: {
            "events": [
                {
                    "task_id": "T001",
                    "type": "message_delivered",
                    "to_agent": "demo.work",
                },
                {
                    "task_id": "T001",
                    "type": "worker_activity",
                    "payload": {
                        "id": 1,
                        "time": "2026-06-23T04:42:00+00:00",
                        "activity_type": "tool_call",
                        "tool_name": "read",
                        "detail": "apps/api/app/api/users.py",
                    },
                }
            ]
        },
    )

    result = runner.invoke(cli_main.app, ["task", "T001"])

    assert result.exit_code == 0
    assert "Task T001: IN_PROGRESS" in result.output
    assert "Last worker activity" in result.output
    assert "read:" in result.output
    assert "apps/api/app/api/users.py" in result.output
    assert "Worker is still in progress" in result.output


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


def test_work_name_starts_visible_named_worker_without_project_yaml(monkeypatch, tmp_path):
    paths = init_project(tmp_path, project_id="demo")
    original_config = paths["config"].read_text(encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    calls = []

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

        def run_work(self):
            calls.append(
                (
                    "run_work",
                    self.config["work"]["agent_id"],
                    self.config["work"]["session_id"],
                    self.config["work"].get("name"),
                )
            )
            return 0

    def fake_register(config, role):
        calls.append(("register", role, config["work"]["agent_id"], config["work"]["session_id"]))

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "register_project_role_sync", fake_register)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)

    result = runner.invoke(cli_main.app, ["work", "--name", "review"])

    assert result.exit_code == 0
    assert calls == [
        ("register", "worker", "demo.review", "review"),
        ("run_work", "demo.review", "review", "review"),
    ]
    assert "Registered: demo.review" in result.output
    assert "Starting Pi worker 'review' session" in result.output
    assert paths["config"].read_text(encoding="utf-8") == original_config


def test_lead_auto_refreshes_stale_project_skills(monkeypatch, tmp_path):
    paths = init_project(tmp_path, project_id="demo")
    paths["lead_skill"].write_text("old lead", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def run_lead(self):
            return 0

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "register_project_role_sync", lambda config, role: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)

    result = runner.invoke(cli_main.app, ["lead"])

    assert result.exit_code == 0
    assert "Refreshed project skills from current templates: lead.md" in result.output
    assert paths["lead_skill"].read_text(encoding="utf-8") == load_skill_template("lead")
    assert paths["work_skill"].read_text(encoding="utf-8") == load_skill_template("work")


def test_work_auto_refreshes_missing_project_skills(monkeypatch, tmp_path):
    paths = init_project(tmp_path, project_id="demo")
    paths["work_skill"].unlink()
    monkeypatch.chdir(tmp_path)

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

        def run_work(self):
            return 0

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "register_project_role_sync", lambda config, role: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)

    result = runner.invoke(cli_main.app, ["work"])

    assert result.exit_code == 0
    assert "Refreshed project skills from current templates: work.md" in result.output
    assert paths["lead_skill"].read_text(encoding="utf-8") == load_skill_template("lead")
    assert paths["work_skill"].read_text(encoding="utf-8") == load_skill_template("work")


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
    assert load_project_config(tmp_path)["work"]["session_id"] == "work-20260621-010203"
    assert "New Pi worker session: work-20260621-010203" in result.output


def test_pi_connector_builds_headless_rpc_worker_argv(tmp_path):
    from orchlink.connector.pi_connector import PiConnector

    config = init_project(tmp_path, project_id="demo")
    loaded = load_project_config(tmp_path)
    argv = PiConnector(loaded).work_rpc_argv()

    assert "--mode" in argv
    assert "rpc" in argv
    assert "--no-extensions" in argv
    assert "--approve" in argv
    assert "--extension" in argv
    assert str(config["run_dir"] / "orchlink-pi-extension.ts") in argv
    assert "--append-system-prompt" in argv
    assert str(config["work_skill"]) in argv


def test_work_background_returns_when_existing_worker_ready(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "register_project_role_sync", lambda config, role: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(
        cli_main,
        "broker_get_sync",
        lambda config, path: {
            "sessions": [
                {
                    "role": "work",
                    "status": "ACTIVE",
                    "session_id": "work-active",
                    "ready": True,
                    "runtime_mode": "rpc",
                    "backend": "rpc-supervisor",
                    "last_ready_heartbeat_at": "2999-01-01T00:00:00+00:00",
                }
            ]
        },
    )

    result = runner.invoke(cli_main.app, ["work", "--background"])

    assert result.exit_code == 0
    assert "Background worker already ready: work-active" in result.output


def test_work_background_does_not_treat_active_not_ready_as_ready(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(
        cli_main,
        "broker_get_sync",
        lambda config, path: {"sessions": [{"role": "work", "status": "ACTIVE", "session_id": "work-active"}]},
    )

    result = runner.invoke(cli_main.app, ["work", "--background"])

    assert result.exit_code == 1
    assert "Worker 'work' is already active" in result.output
    assert "--name bg-test" in result.output
    assert "--new" in result.output


def test_work_background_new_stops_existing_pid_before_start(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    pid_path = tmp_path / ".orch" / "run" / "orch-work.pid"
    pid_path.write_text("1111", encoding="utf-8")
    calls = {"stopped": [], "released": [], "sessions": 0, "popen": []}

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

    class FakeProcess:
        pid = 4321

    def fake_stop_pid_file(path, label):
        calls["stopped"].append((path, label))
        path.unlink(missing_ok=True)
        return True

    def fake_popen(command, **kwargs):
        calls["popen"].append((command, kwargs))
        return FakeProcess()

    def fake_broker_get_sync(config, path):
        calls["sessions"] += 1
        if calls["sessions"] == 1:
            return {
                "sessions": [
                    {
                        "role": "work",
                        "status": "ACTIVE",
                        "session_id": "old-work",
                        "lease_id": "lease-old",
                        "backend": "rpc-supervisor",
                        "supervisor_pid": 1111,
                    }
                ]
            }
        return {
            "sessions": [
                {
                    "role": "work",
                    "status": "ACTIVE",
                    "session_id": "work-20260621-010203",
                    "ready": True,
                    "last_ready_heartbeat_at": "2999-01-01T00:00:00+00:00",
                }
            ]
        }

    def fake_broker_post_sync(config, path, body=None):
        calls["released"].append((path, body))
        return {"status": "released"}

    monkeypatch.setattr(cli_main.time, "strftime", lambda fmt: "20260621-010203")
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "register_project_role_sync", lambda config, role: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)
    monkeypatch.setattr(cli_main, "broker_post_sync", fake_broker_post_sync)
    monkeypatch.setattr(lead_command, "stop_pid_file", fake_stop_pid_file)
    monkeypatch.setattr(lead_command.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(lead_command.time, "sleep", lambda seconds: None)

    result = runner.invoke(cli_main.app, ["work", "--background", "--new", "--replace", "--timeout", "1"])

    assert result.exit_code == 0
    assert calls["stopped"] == [(pid_path, "worker 'work'")]
    assert calls["released"] == [
        ("/v1/sessions/lease-old/release", {"project_id": "demo", "reason": "Replaced by orch work --name work --replace."})
    ]
    assert pid_path.read_text(encoding="utf-8") == "4321"
    assert calls["popen"][0][0][:3] == [lead_command.sys.executable, "-m", "orchlink.worker.supervisor"]
    assert "New Pi worker session: work-20260621-010203" in result.output


def test_work_background_new_stops_existing_pid_without_active_session(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    pid_path = tmp_path / ".orch" / "run" / "orch-work.pid"
    pid_path.write_text("1111", encoding="utf-8")
    calls = {"stopped": [], "sessions": 0}

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

    class FakeProcess:
        pid = 4321

    def fake_stop_pid_file(path, label):
        calls["stopped"].append((path, label))
        path.unlink(missing_ok=True)
        return True

    def fake_broker_get_sync(config, path):
        calls["sessions"] += 1
        if calls["sessions"] == 1:
            return {"sessions": []}
        return {
            "sessions": [
                {
                    "role": "work",
                    "status": "ACTIVE",
                    "session_id": "work-20260621-010203",
                    "ready": True,
                    "last_ready_heartbeat_at": "2999-01-01T00:00:00+00:00",
                }
            ]
        }

    monkeypatch.setattr(cli_main.time, "strftime", lambda fmt: "20260621-010203")
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "register_project_role_sync", lambda config, role: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)
    monkeypatch.setattr(lead_command, "stop_pid_file", fake_stop_pid_file)
    monkeypatch.setattr(lead_command.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(lead_command.time, "sleep", lambda seconds: None)

    result = runner.invoke(cli_main.app, ["work", "--background", "--new", "--timeout", "1"])

    assert result.exit_code == 0
    assert calls["stopped"] == [(pid_path, "worker 'work'")]
    assert pid_path.read_text(encoding="utf-8") == "4321"


def test_work_background_new_does_not_release_visible_session_without_pid(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    calls = {"sessions": 0, "released": []}

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

    class FakeProcess:
        pid = 9876

    def fake_broker_get_sync(config, path):
        calls["sessions"] += 1
        if calls["sessions"] == 1:
            return {"sessions": [{"role": "work", "status": "ACTIVE", "session_id": "visible-work", "lease_id": "lease-visible"}]}
        return {
            "sessions": [
                {
                    "role": "work",
                    "status": "ACTIVE",
                    "session_id": "work-20260621-010203",
                    "ready": True,
                    "last_ready_heartbeat_at": "2999-01-01T00:00:00+00:00",
                }
            ]
        }

    def fake_broker_post_sync(config, path, body=None):
        calls["released"].append((path, body))
        return {"status": "released"}

    monkeypatch.setattr(cli_main.time, "strftime", lambda fmt: "20260621-010203")
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)
    monkeypatch.setattr(cli_main, "broker_post_sync", fake_broker_post_sync)
    monkeypatch.setattr(lead_command.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(lead_command.time, "sleep", lambda seconds: None)

    result = runner.invoke(cli_main.app, ["work", "--background", "--new", "--timeout", "1"])

    assert result.exit_code == 1
    assert calls["released"] == []
    assert "Worker 'work' is already active" in result.output


def test_work_background_replace_does_not_release_visible_session_without_pid(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    calls = {"released": []}

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

    def fake_broker_get_sync(config, path):
        return {
            "sessions": [
                {
                    "role": "work",
                    "status": "ACTIVE",
                    "session_id": "visible-work",
                    "lease_id": "lease-visible",
                    "backend": "interactive",
                }
            ]
        }

    def fake_broker_post_sync(config, path, body=None):
        calls["released"].append((path, body))
        return {"status": "released"}

    monkeypatch.setattr(cli_main.time, "strftime", lambda fmt: "20260621-010203")
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)
    monkeypatch.setattr(cli_main, "broker_post_sync", fake_broker_post_sync)
    monkeypatch.setattr(lead_command, "stop_pid_file", lambda path, label: False)

    result = runner.invoke(cli_main.app, ["work", "--background", "--new", "--replace", "--timeout", "1"])

    assert result.exit_code == 1
    assert calls["released"] == []
    assert "visible active session" in result.output
    assert "Stop it in that terminal with Ctrl-C" in result.output


def test_release_work_sessions_only_releases_active_sessions(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    config = load_project_config(tmp_path)
    calls = []

    def fake_broker_post_sync(config, path, body=None):
        calls.append((path, body))
        return {"status": "released"}

    monkeypatch.setattr(cli_main, "broker_post_sync", fake_broker_post_sync)

    lead_command.release_work_sessions(
        config,
        [
            {"lease_id": "lease-active", "status": "ACTIVE"},
            {"lease_id": "lease-released", "status": "RELEASED"},
            {"lease_id": "lease-missing-status"},
        ],
        "test release",
    )

    assert calls == [("/v1/sessions/lease-active/release", {"project_id": "demo", "reason": "test release"})]


def test_stop_defaults_to_worker_and_leaves_broker_running(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    pid_path = tmp_path / ".orch" / "run" / "orch-work.pid"
    pid_path.write_text("1111", encoding="utf-8")
    calls = {"stopped": [], "released": []}

    def fake_stop_pid_file(path, label):
        calls["stopped"].append((path, label))
        return True

    sessions = [
        {"lease_id": "lease-lead", "role": "lead", "backend": "interactive"},
        {"lease_id": "lease-visible", "role": "work", "backend": "interactive", "pid": 2222},
        {"lease_id": "lease-worker", "role": "work", "backend": "rpc-supervisor", "supervisor_pid": 1111},
    ]
    monkeypatch.setattr(lead_command, "stop_pid_file", fake_stop_pid_file)
    monkeypatch.setattr(lead_command, "active_work_sessions", lambda config, worker_name=None: sessions)
    monkeypatch.setattr(
        lead_command,
        "release_work_sessions",
        lambda config, sessions, reason: calls["released"].append((sessions, reason)),
    )

    result = runner.invoke(cli_main.app, ["stop"])

    assert result.exit_code == 0
    assert calls["stopped"] == [(pid_path, "worker 'work'")]
    assert calls["released"] == [([{"lease_id": "lease-worker", "role": "work", "backend": "rpc-supervisor", "supervisor_pid": 1111}], "Stopped by orch stop --name work.")]
    assert "Broker left running" in result.output


def test_stop_all_stops_worker_and_broker(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_stop_pid_file(path, label):
        calls.append((path, label))
        return label == "worker 'work'"

    monkeypatch.setattr(lead_command, "stop_pid_file", fake_stop_pid_file)
    monkeypatch.setattr(lead_command, "active_work_sessions", lambda config, worker_name=None: [])

    result = runner.invoke(cli_main.app, ["stop", "--all"])

    assert result.exit_code == 0
    assert calls == [
        (tmp_path / ".orch" / "run" / "orch-work.pid", "worker 'work'"),
        (tmp_path / ".orch" / "run" / "broker.pid", "broker"),
    ]


def test_stop_broker_only(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_stop_pid_file(path, label):
        calls.append((path, label))
        return True

    monkeypatch.setattr(lead_command, "stop_pid_file", fake_stop_pid_file)

    result = runner.invoke(cli_main.app, ["stop", "--broker"])

    assert result.exit_code == 0
    assert calls == [(tmp_path / ".orch" / "run" / "broker.pid", "broker")]


def test_stop_rejects_broker_and_all_together(tmp_path, monkeypatch):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_main.app, ["stop", "--broker", "--all"])

    assert result.exit_code == 1
    assert "Use either --broker or --all" in result.output


def test_work_background_starts_rpc_supervisor_and_waits_for_readiness(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    calls = {"sessions": 0, "popen": [], "registered": []}

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

    class FakeProcess:
        pid = 4321

    def fake_popen(command, **kwargs):
        calls["popen"].append((command, kwargs))
        return FakeProcess()

    def fake_broker_get_sync(config, path):
        calls["sessions"] += 1
        if calls["sessions"] == 1:
            return {"sessions": []}
        return {
            "sessions": [
                {
                    "role": "work",
                    "status": "ACTIVE",
                    "session_id": "work",
                    "ready": True,
                    "last_ready_heartbeat_at": "2999-01-01T00:00:00+00:00",
                }
            ]
        }

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "register_project_role_sync", lambda config, role: calls["registered"].append(role))
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)
    monkeypatch.setattr(lead_command.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(lead_command.time, "sleep", lambda seconds: None)

    result = runner.invoke(cli_main.app, ["work", "--background", "--timeout", "1"])

    assert result.exit_code == 0
    assert "Headless worker ready: work" in result.output
    assert (tmp_path / ".orch" / "run" / "orch-work.pid").read_text(encoding="utf-8") == "4321"
    assert calls["registered"] == []
    assert calls["popen"][0][0][:3] == [lead_command.sys.executable, "-m", "orchlink.worker.supervisor"]
    assert calls["popen"][0][1]["cwd"] == tmp_path


def test_work_background_named_timeout_suggests_named_visible_fallback(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

    class FakeProcess:
        pid = 4321

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(cli_main, "broker_get_sync", lambda config, path: {"sessions": []})
    monkeypatch.setattr(lead_command.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(lead_command.time, "sleep", lambda seconds: None)

    result = runner.invoke(cli_main.app, ["work", "--background", "--name", "review", "--timeout", "1"])

    assert result.exit_code == 1
    assert "Fallback: run `orch work --name review`" in result.output


def test_work_background_reports_readiness_timeout(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

    class FakeProcess:
        pid = 4321

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "register_project_role_sync", lambda config, role: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(cli_main, "broker_get_sync", lambda config, path: {"sessions": []})
    monkeypatch.setattr(lead_command.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(lead_command.time, "sleep", lambda seconds: None)

    result = runner.invoke(cli_main.app, ["work", "--background", "--timeout", "1"])

    assert result.exit_code == 1
    assert "did not become ready within 1s" in result.output
    assert "Fallback: run `orch work` in another terminal" in result.output


def test_work_background_has_no_fake_tty_fallback():
    from orchlink.cli.commands import lead as lead_command

    import inspect

    source = inspect.getsource(lead_command.launch_work_background)

    assert "t" + "mux" not in source
    assert "no" + "hup" not in source
    assert "DETACHED_PROCESS" not in source


def test_lead_new_persists_fresh_pi_session_id(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main.time, "strftime", lambda fmt: "20260621-010203")
    calls = []

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

        def run_lead(self):
            calls.append(self.config["lead"]["session_id"])
            return 0

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "register_project_role_sync", lambda config, role: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)

    result = runner.invoke(cli_main.app, ["lead", "--new"])

    assert result.exit_code == 0
    assert calls == ["lead-20260621-010203"]
    assert load_project_config(tmp_path)["lead"]["session_id"] == "lead-20260621-010203"
    assert "New Pi lead session: lead-20260621-010203" in result.output


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
    assert "orch stop --all" in result.output
    assert "orch lead --new" in result.output
    assert "orch work --new" in result.output


def test_update_defers_windows_reinstall_to_avoid_locked_launcher(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    calls = []
    popen_calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0)

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        return SimpleNamespace(pid=1234)

    monkeypatch.setattr(cli_main, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cli_main.subprocess, "run", fake_run)
    monkeypatch.setattr(cli_main.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cli_main.sys, "executable", r"C:\\orch\\.venv\\Scripts\\python.exe")
    monkeypatch.setattr(cli_main.sys, "platform", "win32")

    result = runner.invoke(cli_main.app, ["update", "--reinstall-only"])

    assert result.exit_code == 0
    assert not any(command[:4] == [r"C:\\orch\\.venv\\Scripts\\python.exe", "-m", "pip", "install"] for command in calls)
    assert popen_calls
    assert "Reinstall scheduled" in result.output
    assert "locked" in result.output


def test_doctor_reports_stale_project_skills(monkeypatch, tmp_path):
    paths = init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("orchlink.client.process.broker_health", lambda url: False)

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
    called = {}

    def fake_fetch_status_sync(broker_url, api_key, project_id=None, task_id=None, since=0, limit=20):
        called.update({"broker_url": broker_url, "api_key": api_key, "project_id": project_id, "task_id": task_id, "since": since, "limit": limit})
        return {"broker": "ok", "agent_count": 0}

    monkeypatch.setattr(cli_main, "fetch_status_sync", fake_fetch_status_sync)

    result = runner.invoke(
        cli_main.app,
        ["status", "--broker-url", "http://broker", "--api-key", "test-key", "--task", "T010", "--since-id", "7", "--limit", "3"],
    )

    assert result.exit_code == 0
    assert called["task_id"] == "T010"
    assert called["since"] == 7
    assert called["limit"] == 3
    assert '"broker": "ok"' in result.output


def test_work_background_test_uses_bg_test_named_worker(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    paths = init_project(tmp_path, project_id="demo")
    original_config = paths["config"].read_text(encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    calls = {"sessions": 0, "popen": []}

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

    class FakeProcess:
        pid = 5432

    def fake_popen(command, **kwargs):
        calls["popen"].append((command, kwargs))
        return FakeProcess()

    def fake_broker_get_sync(config, path):
        calls["sessions"] += 1
        if calls["sessions"] == 1:
            return {"sessions": []}
        return {
            "sessions": [
                {
                    "role": "work",
                    "worker_name": "bg-test",
                    "agent_id": "demo.bg-test",
                    "status": "ACTIVE",
                    "session_id": "bg-test-20260621-010203",
                    "ready": True,
                    "runtime_mode": "rpc",
                    "backend": "rpc-supervisor",
                    "last_ready_heartbeat_at": "2999-01-01T00:00:00+00:00",
                }
            ]
        }

    monkeypatch.setattr(cli_main.time, "strftime", lambda fmt: "20260621-010203")
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)
    monkeypatch.setattr(lead_command.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(lead_command.time, "sleep", lambda seconds: None)

    result = runner.invoke(cli_main.app, ["work", "--background", "--test", "--timeout", "1"])

    assert result.exit_code == 0
    assert "New Pi worker 'bg-test' session: bg-test-20260621-010203" in result.output
    assert "Headless worker ready: bg-test-20260621-010203" in result.output
    assert (tmp_path / ".orch" / "run" / "workers" / "bg-test" / "orch-work.pid").read_text(encoding="utf-8") == "5432"
    assert calls["popen"][0][0][-2:] == ["--worker-name", "bg-test"]
    assert paths["config"].read_text(encoding="utf-8") == original_config


def test_stop_name_releases_only_named_visible_session(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    calls = {"released": []}
    sessions = [
        {"lease_id": "lease-work", "role": "work", "worker_name": "work", "agent_id": "demo.work", "backend": "interactive"},
        {"lease_id": "lease-review", "role": "work", "worker_name": "review", "agent_id": "demo.review", "backend": "interactive"},
    ]

    monkeypatch.setattr(lead_command, "active_work_sessions", lambda config, worker_name=None: [s for s in sessions if s["worker_name"] == worker_name])
    monkeypatch.setattr(lead_command, "stop_pid_file", lambda path, label: False)
    monkeypatch.setattr(
        lead_command,
        "release_work_sessions",
        lambda config, sessions, reason: calls["released"].append((sessions, reason)),
    )

    result = runner.invoke(cli_main.app, ["stop", "--name", "review"])

    assert result.exit_code == 0
    assert calls["released"] == [([sessions[1]], "Stopped by orch stop --name review.")]
    assert "Fenced active worker 'review'" in result.output


def test_sessions_output_shows_worker_name_runtime_and_ready(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(
        cli_main,
        "broker_get_sync",
        lambda config, path: {
            "sessions": [
                {
                    "worker_name": "review",
                    "agent_id": "demo.review",
                    "role": "work",
                    "runtime_mode": "rpc",
                    "backend": "rpc-supervisor",
                    "status": "ACTIVE",
                    "pid": 123,
                    "session_id": "review-session",
                    "ready": True,
                    "last_heartbeat_at": "2999-01-01T00:00:00+00:00",
                }
            ]
        },
    )

    result = runner.invoke(cli_main.app, ["sessions", "--name", "review"])

    assert result.exit_code == 0
    assert "NAME" in result.output
    assert "review" in result.output
    assert "rpc-supervisor" in result.output
    assert "True" in result.output


def test_jobs_active_name_filter_shows_worker_column(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(
        cli_main,
        "broker_get_sync",
        lambda config, path: {
            "jobs": [
                {"task_id": "R001", "kind": "task", "mode": "REVIEW", "status": "RUNNING", "to_agent": "demo.review", "from_agent": "demo.lead", "preview": "review"},
                {"task_id": "W001", "kind": "task", "mode": "PLAN", "status": "RUNNING", "to_agent": "demo.work", "from_agent": "demo.lead", "preview": "work"},
            ]
        },
    )

    result = runner.invoke(cli_main.app, ["jobs", "--active", "--name", "review"])

    assert result.exit_code == 0
    assert "WORKER" in result.output
    assert "R001" in result.output
    assert "review" in result.output
    assert "W001" not in result.output
