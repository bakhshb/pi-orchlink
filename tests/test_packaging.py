import tomllib
from pathlib import Path

from typer.testing import CliRunner


ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_declares_src_layout_and_console_script():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["name"] == "orchlink"
    assert data["project"]["scripts"] == {"orch": "orchlink.cli.main:app"}
    assert data["tool"]["setuptools"]["package-dir"] == {"": "src"}
    assert data["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]
    assert data["tool"]["setuptools"]["package-data"] == {"orchlink.project": ["templates/*.md", "templates/references/*.md"]}
    assert (ROOT / "src" / "orchlink").is_dir()


def test_windows_installer_exists_and_sets_up_command_shim():
    text = (ROOT / "install.ps1").read_text(encoding="utf-8")

    assert "https://github.com/bakhshb/pi-orchlink.git" in text
    assert "Set-StrictMode -Version Latest" in text
    assert "$env:LOCALAPPDATA" in text
    assert "LOCALAPPDATA is not set" in text
    assert "Scripts\\orch.exe" in text
    assert '"$OrchExe`" %*`r`n' in text
    assert 'exec `"$ShellOrchExe`" `"`$@`"' in text
    assert '@("orch", "orch.cmd", "orchlink.cmd")' in text
    assert "symbolic-ref -q --short HEAD" in text
    assert "git pull failed: $Ref" in text
    assert "git pull --ff-only origin $Ref 2>$null" not in text
    assert "orch.cmd" in text
    assert "SetEnvironmentVariable(\"Path\"" in text
    assert "Uninstall-Orchlink" in text
    assert "Close any running Orchlink/Pi terminals" in text
    assert "if ($Uninstall) {\n    Uninstall-Orchlink\n    exit 0\n}" in text
    assert "ORCHLINK_REPO_URL" in text
    assert "ORCHLINK_REF" in text
    assert "ORCHLINK_INSTALL_DIR" in text
    assert "ORCHLINK_BIN_DIR" in text
    assert "ORCHLINK_PYTHON" in text
    assert "ORCHLINK_SOURCE_DIR" in text


def test_readme_documents_windows_install_and_worker_background_option():
    text = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "install.ps1" in text
    assert "powershell -ExecutionPolicy Bypass -File .\\install.ps1" in text
    assert "-Uninstall" in text
    assert "ORCHLINK_INSTALL_DIR" in text
    assert "Close running `orch lead` / `orch work` / Pi terminals" in text
    assert "%LOCALAPPDATA%\\orchlink" in text
    assert "open a fresh terminal where `orch` is on PATH" in text
    assert "Start-Process orch" in text
    assert ".orch\\run\\orch-work.log" in text


def test_project_skill_templates_are_packaged_markdown_files():
    from orchlink.project.init import load_skill_reference_template, load_skill_template

    assert "# Lead Role" in load_skill_template("lead")
    assert "# Worker Role" in load_skill_template("work")
    assert "# Orchlink Goal Mode reference" in load_skill_reference_template("goal-mode.md")
    assert "LEAD_SKILL" not in (ROOT / "src" / "orchlink" / "project" / "init.py").read_text(encoding="utf-8")


def test_adapter_skills_share_prompt_policy_text():
    from orchlink.core.prompt_policy import TaskPromptPolicy

    policy = TaskPromptPolicy()
    for relative_path in ["skills/openclaw/orchlink", "skills/hermes/orchlink"]:
        skill_dir = ROOT / relative_path
        text = "\n".join(path.read_text(encoding="utf-8") for path in sorted(skill_dir.rglob("*.md")))
        assert policy.lead_task_prompt_guidance_markdown() in text
        assert policy.lead_reply_guidance_markdown() in text
        assert "references/review-gates.md" in text
        assert "Pi's native `/compact` command" in text
        assert "visible worker terminal" in text
        assert "nohup orch work --new" in text
        assert ".orch/run/orch-work.log" in text


def test_cli_imports_from_installable_package_and_exposes_required_commands():
    from orchlink.cli.main import app

    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "broker" in result.output
    assert "ask" in result.output
    assert "send" in result.output
    assert "talk" in result.output
    assert "say" in result.output
    assert "close" in result.output
    assert "cancel" in result.output
    assert "jobs" in result.output
    assert "idle" in result.output
    assert "peek" in result.output
    assert "status" in result.output
    assert "doctor" in result.output
    assert "update" in result.output


def test_pi_extension_uses_valid_record_type():
    from orchlink.connector.pi_extension import ORCHLINK_PI_EXTENSION

    assert "type OrchMessage = Record<string, any>;" in ORCHLINK_PI_EXTENSION
    assert "type OrchMessage = Record;" not in ORCHLINK_PI_EXTENSION
    assert "TYPE: CHAT_REPLY" not in ORCHLINK_PI_EXTENSION
    assert "MODE: TALK" not in ORCHLINK_PI_EXTENSION
    assert "[Orchlink Talk] ${speaker}" in ORCHLINK_PI_EXTENSION
    assert "value.startsWith(\"[Orchlink Talk]\")" in ORCHLINK_PI_EXTENSION
    assert "isOrchlinkWorkerPrompt(event.text)" in ORCHLINK_PI_EXTENSION
    assert "You are the worker coding agent in a Talk Mode conversation" not in ORCHLINK_PI_EXTENSION
    assert "CONVERSATION_ID:" not in ORCHLINK_PI_EXTENSION
    assert "TURN:" not in ORCHLINK_PI_EXTENSION
    assert "MESSAGE:" not in ORCHLINK_PI_EXTENSION
    assert "too broad" in ORCHLINK_PI_EXTENSION
    assert "stripChatReplyMarker" in ORCHLINK_PI_EXTENSION
    assert "pendingTask" in ORCHLINK_PI_EXTENSION
    assert "pi.on(\"input\"" in ORCHLINK_PI_EXTENSION
    assert "currentTask = pendingTask" in ORCHLINK_PI_EXTENSION
    assert "markMessageStatus" in ORCHLINK_PI_EXTENSION
    assert "RUNNING" in ORCHLINK_PI_EXTENSION
    assert "checkCurrentTaskCancellation" in ORCHLINK_PI_EXTENSION
    assert "Stop working now" in ORCHLINK_PI_EXTENSION
    assert "do not call more tools" in ORCHLINK_PI_EXTENSION
    assert "Stop working now. Do not make more edits, do not call more tools" in ORCHLINK_PI_EXTENSION
    assert "deliverAs: \"steer\"" in ORCHLINK_PI_EXTENSION
    assert "abortIfPossible" in ORCHLINK_PI_EXTENSION
    assert "ctx.abort" in ORCHLINK_PI_EXTENSION
    assert "Orchlink cancelled this work before the tool call started" in ORCHLINK_PI_EXTENSION
    assert "isRecoverableAssistantError" in ORCHLINK_PI_EXTENSION
    assert "ORCHLINK_RECOVERABLE_ERROR_GRACE_MS" in ORCHLINK_PI_EXTENSION
    assert "180000" in ORCHLINK_PI_EXTENSION
    assert "WebSocket error|provider_transport_failure|transport|Request timed out|timed out|timeout" in ORCHLINK_PI_EXTENSION
    assert "waiting for Pi recovery" in ORCHLINK_PI_EXTENSION
    assert "ORCHLINK_ACTIVITY_HEARTBEAT_MS" in ORCHLINK_PI_EXTENSION
    assert "postCurrentActivity" in ORCHLINK_PI_EXTENSION
    assert "x-orchlink-lease-epoch" in ORCHLINK_PI_EXTENSION
    assert "x-orchlink-lease-holder" in ORCHLINK_PI_EXTENSION
    assert "renewJobLease" in ORCHLINK_PI_EXTENSION
    assert "pi.registerCommand(\"orch\"" not in ORCHLINK_PI_EXTENSION
    assert "compact-phase" not in ORCHLINK_PI_EXTENSION
    assert "phaseCompactionInstructions" in ORCHLINK_PI_EXTENSION
    assert "ctx.compact" in ORCHLINK_PI_EXTENSION
    assert "setTimeout(() =>" in ORCHLINK_PI_EXTENSION
    assert "Orchlink ${role} polling resumed after compaction." in ORCHLINK_PI_EXTENSION
    assert "pi.on(\"session_compact\"" in ORCHLINK_PI_EXTENSION
    assert "ORCHLINK_AUTO_COMPACT_PHASES" in ORCHLINK_PI_EXTENSION
    assert "pendingReviewCompaction" in ORCHLINK_PI_EXTENSION
    assert "looksLikeReviewReconciliation" in ORCHLINK_PI_EXTENSION
    assert "Orchlink auto phase compaction started." in ORCHLINK_PI_EXTENSION
    assert "current goal ID" in ORCHLINK_PI_EXTENSION
    assert "pointers to durable .orch/ state files" in ORCHLINK_PI_EXTENSION
    assert "pi.on(\"tool_call\"" in ORCHLINK_PI_EXTENSION
    assert "pi.on(\"tool_result\"" in ORCHLINK_PI_EXTENSION
    assert "[Orchlink] ${message.from_agent" in ORCHLINK_PI_EXTENSION
    assert "Next: if worker asked a direct question" not in ORCHLINK_PI_EXTENSION
    assert "Worker says:" not in ORCHLINK_PI_EXTENSION
    assert "Conversation:" not in ORCHLINK_PI_EXTENSION
    assert "-m \"<your answer>\"" not in ORCHLINK_PI_EXTENSION
    assert "Talk Mode should stop only when" not in ORCHLINK_PI_EXTENSION
    assert "renderLeadPrompt(message), { deliverAs: \"steer\" }" in ORCHLINK_PI_EXTENSION
    assert "customType: \"orchlink\"" not in ORCHLINK_PI_EXTENSION
    assert "deliverAs: \"nextTurn\"" not in ORCHLINK_PI_EXTENSION
    assert "Recommended next step:" not in ORCHLINK_PI_EXTENSION
    assert "Stop any unrelated work now" not in ORCHLINK_PI_EXTENSION
    assert "Prefer starting task replies with: TYPE: PLAN | RESULT | BLOCKER" not in ORCHLINK_PI_EXTENSION
    assert "If no shape is requested, answer naturally and concisely" in ORCHLINK_PI_EXTENSION
    assert "expectedReply.length" in ORCHLINK_PI_EXTENSION
    assert "const expectedReply = formatList" not in ORCHLINK_PI_EXTENSION
    assert "summary, changed/inspected, tests" not in ORCHLINK_PI_EXTENSION
    assert "const firstLine = output.split" in ORCHLINK_PI_EXTENSION
    assert "if (!firstLine.startsWith(\"TYPE:\")) return \"RESULT\";" in ORCHLINK_PI_EXTENSION
    assert "for (const line of output.split" not in ORCHLINK_PI_EXTENSION


def test_pi_extension_has_session_before_compact_hook_with_state_pointer_summary():
    from orchlink.connector.pi_extension import ORCHLINK_PI_EXTENSION

    # The hook is registered and produces a custom Orchlink state-pointer summary
    # for normal Pi compaction and auto review-phase compaction.
    assert 'pi.on("session_before_compact"' in ORCHLINK_PI_EXTENSION
    assert "orchlinkCompactionSummary" in ORCHLINK_PI_EXTENSION
    assert "normalizeCompactionInstructions" in ORCHLINK_PI_EXTENSION
    assert "source: autoPhase ? \"auto-review\" : \"pi-compact\"" in ORCHLINK_PI_EXTENSION
    assert "compaction: {" in ORCHLINK_PI_EXTENSION
    assert "firstKeptEntryId" in ORCHLINK_PI_EXTENSION
    assert "## Orchlink state" in ORCHLINK_PI_EXTENSION
    assert "pi.on(\"session_compact\"" in ORCHLINK_PI_EXTENSION
    assert "schedule(0);" in ORCHLINK_PI_EXTENSION


def test_pi_extension_rekicks_polling_after_compaction_callbacks():
    from orchlink.connector.pi_extension import ORCHLINK_PI_EXTENSION

    assert """onComplete: () => {
            phaseCompactionRequested = false;
            phaseCompactionCustomInstructions = "";
            ctx.ui.notify("Orchlink auto phase compaction completed.", "info");
            schedule(0);
          }""" in ORCHLINK_PI_EXTENSION
    assert """onError: (error: any) => {
            phaseCompactionRequested = false;
            phaseCompactionCustomInstructions = "";
            ctx.ui.notify(`Orchlink phase compaction failed: ${error?.message || error}`, "error");
            schedule(0);
          }""" in ORCHLINK_PI_EXTENSION
    assert """pi.on("session_compact", async (_event: any, ctx: any) => {
    phaseCompactionRequested = false;
    phaseCompactionCustomInstructions = "";
    if (["lead", "work"].includes(role)) {
      ctx.ui.notify(`Orchlink ${role} polling resumed after compaction.`, "info");
      schedule(0);
    }
  });""" in ORCHLINK_PI_EXTENSION


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


def test_doctor_reports_project_local_state_and_global_cli_guidance():
    from orchlink.cli.main import app

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "Orchlink doctor" in result.output
    assert "Legacy config dir" not in result.output
    assert "~/.local/bin/orch" in result.output
    assert "~/.local/bin/orchlink" not in result.output
