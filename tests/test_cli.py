from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

from orchlink.cli import main as cli_main
from orchlink.cli.commands import jobs as jobs_commands
from orchlink.cli.commands import talk as talk_commands
from orchlink.cli.message_input import MessageInput, strip_editor_comments
from orchlink.loop.adapters.state_repo import LoopStateRepo
from orchlink.loop.domain import LoopItemState, MakerResult
from orchlink.loop.services import ItemCandidate, LoopService
from orchlink.loop.services.verifier_service import VerifierHandle
from orchlink.project.config import load_project_config
from orchlink.project.init import init_project, load_skill_template


runner = CliRunner()


def test_message_input_reads_inline_file_and_strips_editor_comments(tmp_path):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("File prompt\n", encoding="utf-8")

    assert MessageInput(text="Inline prompt").resolve(required=True) == "Inline prompt"
    assert MessageInput(file=prompt).resolve(required=True) == "File prompt\n"
    assert strip_editor_comments("# ignored\nBody\n  # also ignored\n") == "Body"


@pytest.mark.parametrize(
    "message_input",
    [
        MessageInput(text="inline", file=Path("prompt.md")),
        MessageInput(text="inline", edit=True),
        MessageInput(file=Path("prompt.md"), edit=True),
    ],
)
def test_message_input_rejects_multiple_sources(message_input):
    with pytest.raises(ValueError, match="Use only one"):
        message_input.resolve(required=False)


def test_project_ask_defaults_to_wait(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    def fake_project_ask_worker_sync(**kwargs):
        assert kwargs["worker"] == "work"
        assert kwargs["task_id"] == "T001"
        assert kwargs["wait"] is True
        assert kwargs["thinking"] is None
        return {"status": "completed", "reply": {"type": "PLAN"}}

    monkeypatch.setattr(cli_main, "project_ask_worker_sync", fake_project_ask_worker_sync)

    result = runner.invoke(cli_main.app, ["ask", "work", "-t", "T001", "-m", "Return PLAN only."])

    assert result.exit_code == 0
    assert '"status": "completed"' in result.output
    assert "Async mode" not in result.output


def test_project_ask_reads_message_from_stdin(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    def fake_project_ask_worker_sync(**kwargs):
        assert kwargs["message"] == "Use `backticks` and $HOME literally.\n"
        return {"status": "completed", "reply": {"type": "PLAN"}}

    monkeypatch.setattr(cli_main, "project_ask_worker_sync", fake_project_ask_worker_sync)

    result = runner.invoke(
        cli_main.app,
        ["ask", "work", "-t", "TSTDIN", "-m", "-"],
        input="Use `backticks` and $HOME literally.\n",
    )

    assert result.exit_code == 0
    assert '"status": "completed"' in result.output


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
        assert kwargs["thinking"] == "medium"
        return {"status": "queued", "message_id": "msg-2"}

    monkeypatch.setattr(cli_main, "send_worker_sync", fake_send_worker_sync)

    result = runner.invoke(cli_main.app, ["send", "work", "-t", "T002", "-m", "Inspect tests.", "--thinking", "medium"])

    assert result.exit_code == 0
    assert "Sent T002" in result.output
    assert "Async mode" in result.output
    assert "continue only on non-conflicting" in result.output
    assert "lead-owned work" in result.output
    assert "Read result when ready: orch jobs --result T002" in result.output
    assert "Block only if this now gates you: orch jobs --wait T002" in result.output
    assert "Closeout: before a human-facing completion/decision" in result.output
    assert "read T002" in result.output
    assert "pending with blocking status and retrieval command" in result.output


def test_send_reads_message_from_stdin(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    def fake_send_worker_sync(**kwargs):
        assert kwargs["message"] == "Add one test with `quotes` and $PATH unchanged.\n"
        return {"status": "queued", "message_id": "msg-stdin"}

    monkeypatch.setattr(cli_main, "send_worker_sync", fake_send_worker_sync)

    result = runner.invoke(
        cli_main.app,
        ["send", "work", "-t", "TSTDIN2", "-m", "-"],
        input="Add one test with `quotes` and $PATH unchanged.\n",
    )

    assert result.exit_code == 0
    assert "Sent TSTDIN2" in result.output


def test_send_reads_message_from_file(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("File prompt with `literal` $VARS.\n", encoding="utf-8")

    def fake_send_worker_sync(**kwargs):
        assert kwargs["message"] == "File prompt with `literal` $VARS.\n"
        return {"status": "queued", "message_id": "msg-file"}

    monkeypatch.setattr(cli_main, "send_worker_sync", fake_send_worker_sync)

    result = runner.invoke(cli_main.app, ["send", "work", "-t", "TFILE", "--message-file", str(prompt)])

    assert result.exit_code == 0
    assert "Sent TFILE" in result.output


def test_message_source_options_are_mutually_exclusive(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    prompt = tmp_path / "prompt.md"
    prompt.write_text("file prompt\n", encoding="utf-8")

    result = runner.invoke(cli_main.app, ["ask", "work", "-t", "TCONFLICT", "-m", "inline", "-F", str(prompt)])

    assert result.exit_code == 1
    assert "Use only one of" in result.output


def test_ask_edit_opens_editor_and_sends_body(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    editor = tmp_path / "fake_editor.py"
    editor.write_text(
        "import pathlib, sys\n"
        "pathlib.Path(sys.argv[1]).write_text('# ignored scaffold\\nEdited prompt body.\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )

    def fake_project_ask_worker_sync(**kwargs):
        assert kwargs["message"] == "Edited prompt body."
        return {"status": "completed", "reply": {"type": "PLAN"}}

    monkeypatch.setattr(cli_main, "project_ask_worker_sync", fake_project_ask_worker_sync)

    result = runner.invoke(
        cli_main.app,
        ["ask", "work", "-t", "TEDIT", "--edit"],
        env={"VISUAL": f"python3 {editor}"},
    )

    assert result.exit_code == 0
    assert '"status": "completed"' in result.output


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
    assert "orch jobs --wait R002" in result.output


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


def test_talk_reads_message_from_stdin(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "next_conversation_id", lambda config: "CSTDIN")

    def fake_start_talk_sync(**kwargs):
        assert kwargs["message"] == "Discuss `risk` with $VARS literal.\n"
        return {"status": "queued", "conversation_id": "CSTDIN"}

    monkeypatch.setattr(cli_main, "start_talk_sync", fake_start_talk_sync)

    result = runner.invoke(
        cli_main.app,
        ["talk", "work", "-m", "-"],
        input="Discuss `risk` with $VARS literal.\n",
    )

    assert result.exit_code == 0
    assert "Started conversation CSTDIN" in result.output


def test_say_sends_chat_turn(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(
        talk_commands,
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


def test_say_reads_message_from_stdin(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(
        talk_commands,
        "conversation_state",
        lambda config, conversation_id: {"conversation_id": "C001", "status": "OPEN", "turn": 1, "max_turns": 6},
    )

    def fake_say_talk_sync(**kwargs):
        assert kwargs["message"] == "Answer with `literal` $HOME.\n"
        return {"status": "queued"}

    monkeypatch.setattr(cli_main, "say_talk_sync", fake_say_talk_sync)

    result = runner.invoke(
        cli_main.app,
        ["say", "C001", "-m", "-"],
        input="Answer with `literal` $HOME.\n",
    )

    assert result.exit_code == 0
    assert "Sent turn 2/6" in result.output


def test_close_sends_chat_close(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(
        talk_commands,
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


def test_close_reads_message_from_stdin(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(
        talk_commands,
        "conversation_state",
        lambda config, conversation_id: {"conversation_id": "C001", "status": "OPEN", "turn": 2, "max_turns": 6},
    )

    def fake_close_talk_sync(**kwargs):
        assert kwargs["message"] == "Decision: keep `$literal`.\n"
        return {"status": "queued"}

    monkeypatch.setattr(cli_main, "close_talk_sync", fake_close_talk_sync)

    result = runner.invoke(
        cli_main.app,
        ["close", "C001", "-m", "-"],
        input="Decision: keep `$literal`.\n",
    )

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
        jobs_commands,
        "conversation_state",
        lambda config, conversation_id: fake_broker_get_sync(config, "/v1/jobs")["jobs"][0],
    )
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

    result = runner.invoke(cli_main.app, ["jobs", "--result", "C001"])

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

    result = runner.invoke(cli_main.app, ["jobs", "--cancel", "T010", "-m", "Wrong scope."])

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

    result = runner.invoke(cli_main.app, ["jobs", "--result", "T001"])

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
    get_result = runner.invoke(cli_main.app, ["jobs", "--result", "T010"])
    wait_result = runner.invoke(cli_main.app, ["jobs", "--wait", "T010", "--timeout", "1"])

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
    assert "Updated" in table.output
    assert "C001" in table.output
    assert "talk" in table.output
    assert "Last activity" in table.output
    assert "read:" in table.output
    assert json_result.exit_code == 0
    assert '"project_id": "demo"' in json_result.output


def test_jobs_positional_id_matches_id_option(monkeypatch, tmp_path):
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
                    "task_id": "T123",
                    "kind": "task",
                    "mode": "DO",
                    "status": "RUNNING",
                    "from_agent": "demo.lead",
                    "to_agent": "demo.work",
                }
            ],
        }

    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)

    result = runner.invoke(cli_main.app, ["jobs", "T123"])

    assert result.exit_code == 0
    assert seen_paths == ["/v1/jobs?limit=500&project_id=demo&id=T123"]
    assert "Job T123" in result.output


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


def test_job_actions_are_not_duplicate_top_level_commands():
    for command in ["task", "peek", "get", "wait", "cancel", "idle", "status", "watch"]:
        result = runner.invoke(cli_main.app, [command, "--help"])
        assert result.exit_code != 0, command


def test_root_help_explains_commands():
    result = runner.invoke(cli_main.app, ["--help"])

    assert result.exit_code == 0
    assert "Start or reopen the visible Pi lead session" in result.output
    assert "Send a task to work and wait" in result.output
    assert "Inspect and control tracked work" in result.output
    assert "Run and manage the local Orchlink broker" in result.output


def test_jobs_help_explains_options():
    result = runner.invoke(cli_main.app, ["jobs", "--help"])

    assert result.exit_code == 0
    assert "Inspect and control tracked work" in result.output
    assert "recent jobs" in result.output
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


def test_jobs_help_shows_timeout_flag_once():
    result = runner.invoke(cli_main.app, ["jobs", "--help"])

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

    result = runner.invoke(cli_main.app, ["jobs", "--wait", "T010", "--timeout", "3", "--poll-seconds", "1"])

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

    result = runner.invoke(cli_main.app, ["jobs", "--wait", "T013", "--timeout", "1"])

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

    result = runner.invoke(cli_main.app, ["jobs", "--result", "T010"])

    assert result.exit_code == 0
    assert "Task T010: FAILED" in result.output
    assert "Type: BLOCKER" in result.output
    assert "Stderr" in result.output
    assert "WebSocket error" in result.output


def test_jobs_idle_reports_ready_when_no_pending_jobs(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "broker_get_sync", lambda config, path: {"jobs": []})

    result = runner.invoke(cli_main.app, ["jobs", "--idle"])

    assert result.exit_code == 0
    assert "Worker idle" in result.output


def test_jobs_idle_blocks_when_worker_has_pending_jobs(monkeypatch, tmp_path):
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

    result = runner.invoke(cli_main.app, ["jobs", "--idle"])

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

    result = runner.invoke(cli_main.app, ["jobs", "--live", "T001"])

    assert result.exit_code == 0
    assert "Recent worker activity" in result.output
    assert "tool_call" in result.output
    assert "bash: rg organization_id" in result.output


def test_jobs_id_reports_in_progress(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(
        cli_main,
        "broker_get_sync",
        lambda config, path: {
            "jobs": [
                {
                    "task_id": "T001",
                    "kind": "task",
                    "mode": "DO",
                    "status": "IN_PROGRESS",
                    "from_agent": "demo.lead",
                    "to_agent": "demo.work",
                    "last_activity_at": "2026-06-23T04:42:00+00:00",
                    "last_activity_type": "tool_call",
                    "last_activity_tool": "read",
                    "last_activity_preview": "apps/api/app/api/users.py",
                }
            ]
        },
    )

    result = runner.invoke(cli_main.app, ["jobs", "--id", "T001"])

    assert result.exit_code == 0
    assert "Job T001: task DO IN_PROGRESS" in result.output
    assert "Last activity" in result.output
    assert "read:" in result.output
    assert "apps/api/app/api/users.py" in result.output


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
    (tmp_path / ".orch" / "run" / "orch-work.pid").write_text("1111", encoding="utf-8")
    named_dir = tmp_path / ".orch" / "run" / "workers" / "review"
    named_dir.mkdir(parents=True)
    (named_dir / "orch-work.pid").write_text("2222", encoding="utf-8")
    calls = []

    def fake_stop_pid_file(path, label):
        calls.append((path, label))
        return label in {"worker 'work'", "worker 'review'"}

    monkeypatch.setattr(lead_command, "stop_pid_file", fake_stop_pid_file)
    monkeypatch.setattr(lead_command, "active_work_sessions", lambda config, worker_name=None: [])

    result = runner.invoke(cli_main.app, ["stop", "--all"])

    assert result.exit_code == 0
    assert calls == [
        (tmp_path / ".orch" / "run" / "orch-work.pid", "worker 'work'"),
        (tmp_path / ".orch" / "run" / "workers" / "review" / "orch-work.pid", "worker 'review'"),
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

        def resolved_pi_command(self):
            return "pi"

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
    monkeypatch.setattr(lead_command, "pi_list_models", lambda command, search=None: "provider model\nopenai-codex gpt-5.4")
    monkeypatch.setattr(lead_command.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(lead_command.time, "sleep", lambda seconds: None)

    result = runner.invoke(
        cli_main.app,
        ["work", "--background", "--timeout", "1", "--model", "openai/codex-max", "--thinking", "xhigh"],
    )

    assert result.exit_code == 0
    assert "Headless worker ready: work" in result.output
    assert (tmp_path / ".orch" / "run" / "orch-work.pid").read_text(encoding="utf-8") == "4321"
    assert calls["registered"] == []
    assert calls["popen"][0][0][:3] == [lead_command.sys.executable, "-m", "orchlink.worker.supervisor"]
    assert calls["popen"][0][0][calls["popen"][0][0].index("--model") + 1] == "openai/codex-max"
    assert calls["popen"][0][0][calls["popen"][0][0].index("--thinking") + 1] == "xhigh"
    assert calls["popen"][0][1]["cwd"] == tmp_path


def test_work_background_worktree_passes_project_dir_to_supervisor(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.chdir(tmp_path)
    calls = {"sessions": 0, "popen": []}

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
                    "session_id": "maker-1-20260621-010203",
                    "worker_name": "maker-1",
                    "ready": True,
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

    result = runner.invoke(
        cli_main.app,
        ["work", "--background", "--worktree", str(worktree), "--name", "maker-1", "--new", "--timeout", "1"],
    )

    command = calls["popen"][0][0]
    assert result.exit_code == 0
    assert command[command.index("--project-dir") + 1] == str(worktree.resolve())
    assert "Worktree:" in result.output
    assert str(worktree.resolve()) in result.output


def test_work_background_worktree_create_passes_project_dir_to_supervisor(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command
    from orchlink.loop.domain import Worktree

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    created_path = tmp_path.parent / "demo-maker-1"
    calls = {"sessions": 0, "popen": [], "created": []}

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

    class FakeWorktreeService:
        def __init__(self, project_root):
            self.project_root = project_root

        def create(self, name, base_ref="main", path=None):
            calls["created"].append({"name": name, "base_ref": base_ref, "path": path, "project_root": self.project_root})
            return Worktree(path=str(created_path), branch=f"loop/{name}", base_ref=base_ref)

    class FakeProcess:
        pid = 4321

    def fake_broker_get_sync(config, path):
        calls["sessions"] += 1
        if calls["sessions"] == 1:
            return {"sessions": []}
        return {
            "sessions": [
                {
                    "role": "work",
                    "status": "ACTIVE",
                    "session_id": "maker-1-20260621-010203",
                    "worker_name": "maker-1",
                    "ready": True,
                    "last_ready_heartbeat_at": "2999-01-01T00:00:00+00:00",
                }
            ]
        }

    monkeypatch.setattr(cli_main.time, "strftime", lambda fmt: "20260621-010203")
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)
    monkeypatch.setattr(lead_command, "WorktreeService", FakeWorktreeService)
    monkeypatch.setattr(lead_command.subprocess, "Popen", lambda command, **kwargs: calls["popen"].append((command, kwargs)) or FakeProcess())
    monkeypatch.setattr(lead_command.time, "sleep", lambda seconds: None)

    result = runner.invoke(
        cli_main.app,
        ["work", "--background", "--name", "maker-1", "--worktree-create", "--base", "main", "--new", "--timeout", "1"],
    )

    command = calls["popen"][0][0]
    assert result.exit_code == 0
    assert calls["created"][0]["name"] == "maker-1"
    assert calls["created"][0]["base_ref"] == "main"
    assert command[command.index("--project-dir") + 1] == str(created_path)
    assert str(created_path) in result.output
    assert "loop/maker-1" in result.output


def test_worktree_create_and_worktree_are_mutually_exclusive(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(lead_command.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not start")))

    result = runner.invoke(cli_main.app, ["work", "--background", "--worktree-create", "--worktree", str(worktree)])

    assert result.exit_code == 1
    assert "Use either --worktree-create or --worktree" in result.output


def test_worktree_create_without_base_defaults_to_main(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command
    from orchlink.loop.domain import Worktree

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    calls = {"sessions": 0, "base_refs": [], "popen": []}

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

    class FakeWorktreeService:
        def __init__(self, project_root):
            pass

        def create(self, name, base_ref="main", path=None):
            calls["base_refs"].append(base_ref)
            return Worktree(path=str(tmp_path.parent / "demo-maker-1"), branch=f"loop/{name}", base_ref=base_ref)

    class FakeProcess:
        pid = 4321

    def fake_broker_get_sync(config, path):
        calls["sessions"] += 1
        if calls["sessions"] == 1:
            return {"sessions": []}
        return {
            "sessions": [
                {
                    "role": "work",
                    "status": "ACTIVE",
                    "session_id": "maker-1",
                    "worker_name": "maker-1",
                    "ready": True,
                    "last_ready_heartbeat_at": "2999-01-01T00:00:00+00:00",
                }
            ]
        }

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)
    monkeypatch.setattr(lead_command, "WorktreeService", FakeWorktreeService)
    monkeypatch.setattr(lead_command.subprocess, "Popen", lambda command, **kwargs: calls["popen"].append((command, kwargs)) or FakeProcess())
    monkeypatch.setattr(lead_command.time, "sleep", lambda seconds: None)

    result = runner.invoke(cli_main.app, ["work", "--background", "--name", "maker-1", "--worktree-create", "--timeout", "1"])

    assert result.exit_code == 0
    assert calls["base_refs"] == ["main"]


def test_work_background_without_worktree_does_not_pass_project_dir(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    calls = {"sessions": 0, "popen": []}

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

    class FakeProcess:
        pid = 4321

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
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)
    monkeypatch.setattr(lead_command.subprocess, "Popen", lambda command, **kwargs: calls["popen"].append((command, kwargs)) or FakeProcess())
    monkeypatch.setattr(lead_command.time, "sleep", lambda seconds: None)

    result = runner.invoke(cli_main.app, ["work", "--background", "--timeout", "1"])

    assert result.exit_code == 0
    assert "--project-dir" not in calls["popen"][0][0]


def test_work_worktree_missing_path_exits_nonzero(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(lead_command.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not start")))

    result = runner.invoke(cli_main.app, ["work", "--background", "--worktree", str(tmp_path / "missing")])

    assert result.exit_code == 1
    assert "--worktree path does not exist" in result.output


def test_work_worktree_file_path_exits_nonzero(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(lead_command.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not start")))

    result = runner.invoke(cli_main.app, ["work", "--background", "--worktree", str(file_path)])

    assert result.exit_code == 1
    assert "--worktree path is not a directory" in result.output


def test_work_background_worktree_override_does_not_return_existing_ready(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    worktree = tmp_path / "wt"
    worktree.mkdir()
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
        lambda config, path: {
            "sessions": [
                {
                    "role": "work",
                    "status": "ACTIVE",
                    "session_id": "same-name",
                    "worker_name": "same-name",
                    "ready": True,
                    "runtime_mode": "rpc",
                    "backend": "rpc-supervisor",
                    "last_ready_heartbeat_at": "2999-01-01T00:00:00+00:00",
                }
            ]
        },
    )

    result = runner.invoke(cli_main.app, ["work", "--background", "--worktree", str(worktree), "--name", "same-name"])

    assert result.exit_code == 1
    assert "Background worker already ready" not in result.output
    assert "already active" in result.output


def test_work_background_worktree_replace_launches_new_supervisor_with_project_dir(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    monkeypatch.chdir(tmp_path)
    calls = {"sessions": 0, "popen": [], "released": []}

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

    class FakeProcess:
        pid = 4321

    def fake_broker_get_sync(config, path):
        calls["sessions"] += 1
        if calls["sessions"] == 1:
            return {
                "sessions": [
                    {
                        "role": "work",
                        "status": "ACTIVE",
                        "session_id": "same-name",
                        "worker_name": "same-name",
                        "lease_id": "lease-old",
                        "ready": True,
                        "runtime_mode": "rpc",
                        "backend": "rpc-supervisor",
                        "last_ready_heartbeat_at": "2999-01-01T00:00:00+00:00",
                    }
                ]
            }
        return {
            "sessions": [
                {
                    "role": "work",
                    "status": "ACTIVE",
                    "session_id": "same-name",
                    "worker_name": "same-name",
                    "ready": True,
                    "runtime_mode": "rpc",
                    "backend": "rpc-supervisor",
                    "last_ready_heartbeat_at": "2999-01-01T00:00:00+00:00",
                }
            ]
        }

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)
    monkeypatch.setattr(
        cli_main,
        "broker_post_sync",
        lambda config, path, body=None: calls["released"].append((path, body)) or {"status": "released"},
    )
    monkeypatch.setattr(lead_command, "stop_pid_file", lambda path, label: False)
    monkeypatch.setattr(lead_command.subprocess, "Popen", lambda command, **kwargs: calls["popen"].append((command, kwargs)) or FakeProcess())
    monkeypatch.setattr(lead_command.time, "sleep", lambda seconds: None)

    result = runner.invoke(
        cli_main.app,
        ["work", "--background", "--worktree", str(worktree), "--name", "same-name", "--replace", "--timeout", "1"],
    )

    command = calls["popen"][0][0]
    assert result.exit_code == 0
    assert command[command.index("--project-dir") + 1] == str(worktree.resolve())
    assert calls["released"] == [
        ("/v1/sessions/lease-old/release", {"project_id": "demo", "reason": "Replaced by orch work --name same-name --replace."})
    ]
    assert "Background worker already ready" not in result.output


def test_work_background_oneshot_passes_supervisor_flag(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    calls = {"sessions": 0, "popen": []}

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
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(cli_main, "broker_get_sync", fake_broker_get_sync)
    monkeypatch.setattr(lead_command.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(lead_command.time, "sleep", lambda seconds: None)

    result = runner.invoke(cli_main.app, ["work", "--background", "--oneshot", "--timeout", "1"])

    assert result.exit_code == 0
    assert "--oneshot" in calls["popen"][0][0]


def test_work_oneshot_rejects_visible_worker(tmp_path, monkeypatch):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_main.app, ["work", "--oneshot"])

    assert result.exit_code == 1
    assert "--oneshot is only supported with --background" in result.output


def test_model_lookup_pattern_strips_only_thinking_suffixes():
    from orchlink.cli.commands import lead as lead_command

    assert lead_command.model_lookup_pattern("sonnet:high") == "sonnet"
    assert lead_command.model_lookup_pattern("sonnet:HIGH") == "sonnet"
    assert lead_command.model_lookup_pattern("ollama/gpt-oss:20b") == "ollama/gpt-oss:20b"
    assert lead_command.model_lookup_pattern(":high") == ":high"


def test_pi_list_models_nonzero_exit_is_not_treated_as_match(monkeypatch):
    from orchlink.cli.commands import lead as lead_command
    from orchlink.connector.pi_connector import PiConnectorError

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=2, stdout="unknown option: --list-models")

    monkeypatch.setattr(lead_command.subprocess, "run", fake_run)

    with pytest.raises(PiConnectorError, match="pi --list-models"):
        lead_command.pi_list_models("pi", "sonnet")


def test_work_model_guard_lists_available_models_before_start(monkeypatch, tmp_path):
    from orchlink.cli.commands import lead as lead_command

    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def check_available(self):
            return True

        def resolved_pi_command(self):
            return "pi"

    def fake_pi_list_models(command, search=None):
        if search:
            return f'No models matching "{search}"\n'
        return "provider      model\nminimax       MiniMax-M3\nopenai-codex  gpt-5.4\n"

    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)
    monkeypatch.setattr(cli_main, "PiConnector", FakePiConnector)
    monkeypatch.setattr(lead_command, "pi_list_models", fake_pi_list_models)
    monkeypatch.setattr(lead_command.subprocess, "Popen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not start")))

    result = runner.invoke(cli_main.app, ["work", "--background", "--model", "sonnet"])
    visible_result = runner.invoke(cli_main.app, ["work", "--model", "sonnet"])

    assert result.exit_code == 1
    assert visible_result.exit_code == 1
    assert "Pi model is not registered or available: sonnet" in result.output
    assert "Pi model is not registered or available: sonnet" in visible_result.output
    assert "MiniMax-M3" in result.output
    assert "gpt-5.4" in result.output


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
    monkeypatch.setattr(cli_main, "broker_info", lambda url: None)

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


def test_doctor_reports_broker_exposure_and_key_state(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ORCHLINK_API_KEY", raising=False)
    monkeypatch.delenv("ORCHLINK_BROKER_URL", raising=False)
    monkeypatch.setattr(cli_main, "broker_info", lambda url: None)

    result = runner.invoke(cli_main.app, ["doctor"])

    assert result.exit_code == 0
    assert "Broker URL host: 127.0.0.1 (loopback)" in result.output
    assert "Broker bind config: 127.0.0.1:8787 (loopback only)" in result.output
    assert "API key default: no" in result.output


def test_doctor_reports_runtime_bind_when_broker_auth_succeeds(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ORCHLINK_API_KEY", raising=False)
    monkeypatch.delenv("ORCHLINK_BROKER_URL", raising=False)
    monkeypatch.setattr(
        cli_main,
        "broker_info",
        lambda url: {"status": "ok", "service": "orchlink", "version": "0.5.4", "capabilities": []},
    )
    monkeypatch.setattr(cli_main, "broker_compatible", lambda info: True)
    monkeypatch.setattr(cli_main, "broker_get_sync", lambda config, path: {"broker_host": "0.0.0.0", "broker_port": 8787})

    result = runner.invoke(cli_main.app, ["doctor"])

    assert result.exit_code == 0
    assert "Broker runtime bind: 0.0.0.0:8787 (network-exposed if firewall allows)" in result.output
    assert "Broker auth: project API key accepted" in result.output


def test_doctor_reports_project_key_rejected_by_running_broker(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ORCHLINK_API_KEY", raising=False)
    monkeypatch.delenv("ORCHLINK_BROKER_URL", raising=False)
    monkeypatch.setattr(
        cli_main,
        "broker_info",
        lambda url: {"status": "ok", "service": "orchlink", "version": "0.5.4", "capabilities": []},
    )
    monkeypatch.setattr(cli_main, "broker_compatible", lambda info: True)

    def reject_key(config, path):
        request = httpx.Request("GET", "http://127.0.0.1:8787/v1/status")
        response = httpx.Response(401, request=request)
        raise httpx.HTTPStatusError("unauthorized", request=request, response=response)

    monkeypatch.setattr(cli_main, "broker_get_sync", reject_key)

    result = runner.invoke(cli_main.app, ["doctor"])

    assert result.exit_code == 0
    assert "Broker auth: rejected project API key (different project/key?)" in result.output


def test_doctor_warns_for_default_key_and_non_loopback_bind(monkeypatch, tmp_path):
    paths = init_project(tmp_path, project_id="demo")
    config_text = paths["config"].read_text(encoding="utf-8")
    paths["config"].write_text(
        config_text
        .replace("api_key: ", "api_key: change-me # old: ", 1)
        .replace("host: 127.0.0.1", "host: 0.0.0.0")
        .replace("url: http://127.0.0.1:8787", "url: http://0.0.0.0:8787"),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ORCHLINK_API_KEY", raising=False)
    monkeypatch.delenv("ORCHLINK_BROKER_URL", raising=False)
    monkeypatch.setattr(cli_main, "broker_info", lambda url: None)

    result = runner.invoke(cli_main.app, ["doctor"])

    assert result.exit_code == 0
    assert "Broker URL host: 0.0.0.0 (non-loopback)" in result.output
    assert "Broker bind config: 0.0.0.0:8787 (network-exposed if broker runs with this host)" in result.output
    assert "API key default: yes (change-me)" in result.output


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("localhost", True),
        ("::1", True),
        ("[::1]", True),
        ("0.0.0.0", False),
        ("::", False),
        ("example.local", False),
        ("", False),
    ],
)
def test_is_loopback_host(host, expected):
    from orchlink.cli.commands._helpers import is_loopback_host

    assert is_loopback_host(host) is expected


def test_start_background_broker_passes_project_api_key(monkeypatch, tmp_path):
    from orchlink.client import process as process_helpers
    from orchlink.broker.main import BROKER_CAPABILITIES, VERSION

    init_project(tmp_path, project_id="demo")
    monkeypatch.delenv("ORCHLINK_API_KEY", raising=False)
    config = load_project_config(tmp_path)
    expected_key = config["broker"]["api_key"]
    captured = {}

    class FakeProcess:
        pid = 4321

        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(process_helpers.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        process_helpers,
        "broker_info",
        lambda url: {"status": "ok", "service": "orchlink", "version": VERSION, "capabilities": BROKER_CAPABILITIES},
    )

    process_helpers.start_background_broker(config)

    assert expected_key != "change-me"
    assert captured["env"]["ORCHLINK_API_KEY"] == expected_key


def test_status_command_prints_status(monkeypatch):
    called = {}

    def fake_fetch_status_sync(broker_url, api_key, project_id=None, task_id=None, since=0, limit=20):
        called.update({"broker_url": broker_url, "api_key": api_key, "project_id": project_id, "task_id": task_id, "since": since, "limit": limit})
        return {"broker": "ok", "agent_count": 0}

    monkeypatch.setattr(cli_main, "fetch_status_sync", fake_fetch_status_sync)

    result = runner.invoke(
        cli_main.app,
        ["broker", "status", "--broker-url", "http://broker", "--api-key", "test-key", "--task", "T010", "--since-id", "7", "--limit", "3"],
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
                    "model": "openai/codex-max",
                    "thinking": "xhigh",
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
    assert "MODEL" in result.output
    assert "THINKING" in result.output
    assert "review" in result.output
    assert "openai/codex-max" in result.output
    assert "xhigh" in result.output
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


# --- G004 AC-4: CLI-side wire parity for `send` and `wait` returning a dict ---


def test_stored_message_wire_shape_cli_send_keeps_dict_return(monkeypatch, tmp_path):
    """AC-4: the `orch send` CLI surface preserves the wire dict returned by
    the broker. This pins the shape that callers depend on.
    """
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_main, "ensure_broker_running", lambda config: None)

    captured: dict[str, object] = {}

    def fake_send_worker_sync(**kwargs):
        captured["kwargs"] = kwargs
        # Mirror the broker's `{status, message_id}` dict shape.
        return {"status": "queued", "message_id": "msg-cli-ac4"}

    monkeypatch.setattr(cli_main, "send_worker_sync", fake_send_worker_sync)

    result = runner.invoke(cli_main.app, ["send", "work", "-t", "T-CLI-AC4", "-m", "Wire-shape probe."])
    assert result.exit_code == 0

    # The CLI received the public wire-dict shape from the broker boundary.
    assert isinstance(captured.get("kwargs"), dict)
    # The handler invoked the underlying broker send with a dict-shaped message.
    assert isinstance(captured["kwargs"].get("message"), str)


# --- Loop CLI ---


def _init_loop_project(tmp_path, monkeypatch):
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    from orchlink.loop import cli as loop_cli

    monkeypatch.setattr(loop_cli, "_build_broker_client", lambda config: None)
    return LoopService({}, LoopStateRepo(tmp_path))


def _triaged_loop_item(service: LoopService, item_id: str = "L001"):
    return service.triage([ItemCandidate(item_id=item_id, title="Loop item")])[0]


def test_loop_ls_without_project_prints_clear_error(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(cli_main.app, ["loop", "ls"])

    assert result.exit_code == 1
    assert "No .orch/project.yaml found" in result.output


def test_loop_ls_with_initialized_project_prints_empty_table(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_main.app, ["loop", "ls"])

    assert result.exit_code == 0
    assert "Loop Items" in result.output
    assert "ID" in result.output
    assert "STATE" in result.output


def test_loop_next_missing_item_exits_nonzero(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_main.app, ["loop", "next", "L001", "--maker", "work"])

    assert result.exit_code == 1
    assert "Loop item not found: L001" in result.output


def test_loop_next_non_ready_item_exits_nonzero(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    _triaged_loop_item(service)

    result = runner.invoke(cli_main.app, ["loop", "next", "L001", "--maker", "work"])

    assert result.exit_code == 1
    assert "next requires ready" in result.output
    assert service.get("L001").state is LoopItemState.TRIAGED


def test_loop_ready_moves_triaged_item_to_ready(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    _triaged_loop_item(service)

    result = runner.invoke(cli_main.app, ["loop", "ready", "L001"])

    assert result.exit_code == 0
    assert "state=ready" in result.output
    assert service.get("L001").state is LoopItemState.READY


def test_loop_show_ready_item_prints_no_attempts(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    _triaged_loop_item(service)
    service.ready("L001")

    result = runner.invoke(cli_main.app, ["loop", "show", "L001"])

    assert result.exit_code == 0
    assert "L001" in result.output
    assert "ready" in result.output
    assert "No attempts" in result.output


def test_loop_cancel_ready_item(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    _triaged_loop_item(service)
    service.ready("L001")

    result = runner.invoke(cli_main.app, ["loop", "cancel", "L001", "--reason", "test"])

    assert result.exit_code == 0
    assert "state=cancelled" in result.output
    assert service.get("L001").state is LoopItemState.CANCELLED
    assert service.get("L001").cancellation_reason == "test"


def test_loop_watch_empty_loop_returns_summary(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_main.app, ["loop", "watch", "--max-steps", "1", "--interval", "0.01"])

    assert result.exit_code == 0
    assert "RunSummary" in result.output
    assert "dispatched=0" in result.output
    assert "done=0" in result.output


def test_loop_watch_ready_item_runs_one_tick_without_broker(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    _triaged_loop_item(service)
    service.ready("L001")

    result = runner.invoke(
        cli_main.app,
        ["loop", "watch", "--max-steps", "1", "--interval", "0.01", "--allow-active-attempts"],
    )

    assert result.exit_code == 0
    assert "RunSummary" in result.output
    assert service.get("L001").state is not LoopItemState.READY


def _awaiting_loop_item(service: LoopService, item_id: str = "L001"):
    _triaged_loop_item(service, item_id)
    service.ready(item_id)
    reservation = service.next_item(item_id, maker_worker="maker", worktree=None)
    service.mark_dispatched(item_id, attempt_no=reservation.attempt.number, task_id=f"T-{item_id}")
    service.mark_running(item_id, attempt_no=reservation.attempt.number)
    return service.collect_maker_result(item_id, attempt_no=reservation.attempt.number, result=MakerResult("done"))


class MalformedLoopGateway:
    async def dispatch_verifier(self, verifier_assignment, prompt):
        return VerifierHandle(task_id=verifier_assignment.task_id, worker_name=verifier_assignment.worker_name)

    async def await_result(self, handle, timeout_seconds):
        return MakerResult("not a structured verdict")


class TimeoutLoopGateway:
    async def dispatch_verifier(self, verifier_assignment, prompt):
        return VerifierHandle(task_id=verifier_assignment.task_id, worker_name=verifier_assignment.worker_name)

    async def await_result(self, handle, timeout_seconds):
        raise TimeoutError("too slow")


def test_loop_verify_without_gateway_does_not_mutate(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    _awaiting_loop_item(service)
    from orchlink.loop import cli as loop_cli

    monkeypatch.setattr(loop_cli, "_build_worker_gateway", lambda config: None)

    result = runner.invoke(cli_main.app, ["loop", "verify", "L001", "--verifier", "review"])

    assert result.exit_code == 1
    assert "no verifier worker gateway" in result.output
    assert service.get("L001").state is LoopItemState.AWAITING_VERDICT


def test_loop_verify_malformed_verdict_is_clean_error(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    _awaiting_loop_item(service)
    from orchlink.loop import cli as loop_cli

    monkeypatch.setattr(loop_cli, "_build_worker_gateway", lambda config: MalformedLoopGateway())

    result = runner.invoke(cli_main.app, ["loop", "verify", "L001", "--verifier", "review"])

    assert result.exit_code == 1
    assert "verifier produced an unparseable verdict" in result.output
    assert service.get("L001").state is not LoopItemState.DONE


def test_loop_verify_timeout_is_clean_error(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    _awaiting_loop_item(service)
    from orchlink.loop import cli as loop_cli

    monkeypatch.setattr(loop_cli, "_build_worker_gateway", lambda config: TimeoutLoopGateway())

    result = runner.invoke(cli_main.app, ["loop", "verify", "L001", "--verifier", "review"])

    assert result.exit_code == 1
    assert "verifier timed out" in result.output
    assert service.get("L001").state is not LoopItemState.DONE


def test_loop_collect_non_running_item_is_clean_error(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    _triaged_loop_item(service)
    service.ready("L001")

    result = runner.invoke(cli_main.app, ["loop", "collect", "L001", "--task-id", "T-L001"])

    assert result.exit_code == 1
    assert "collect requires running" in result.output
    assert service.get("L001").state is LoopItemState.READY


def test_loop_recover_empty_project_exits_zero(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_main.app, ["loop", "recover"])

    assert result.exit_code == 0
    assert "changed=0" in result.output


def test_loop_cancel_terminal_item_is_clean_error(monkeypatch, tmp_path):
    service = _init_loop_project(tmp_path, monkeypatch)
    _triaged_loop_item(service)
    service.cancel("L001", reason="already done")

    result = runner.invoke(cli_main.app, ["loop", "cancel", "L001", "--reason", "again"])

    assert result.exit_code == 1
    assert "cancel" in result.output.lower()
    assert service.get("L001").cancellation_reason == "already done"


def test_loop_show_missing_item_is_clean_error(monkeypatch, tmp_path):
    _init_loop_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_main.app, ["loop", "show", "L001"])

    assert result.exit_code == 1
    assert "Loop item not found: L001" in result.output
