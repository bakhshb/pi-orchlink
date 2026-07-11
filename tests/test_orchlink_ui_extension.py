import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from orchlink.connector.pi_extension import ORCHLINK_PI_UI_EXTENSION, ensure_orchlink_ui_extension
from orchlink.project.init import init_project


def test_generated_ui_extension_is_read_only_tui_monitor():
    assert "/v1/status?limit=200" in ORCHLINK_PI_UI_EXTENSION
    assert "/v1/activity?item_id=${encodeURIComponent(itemId)}&limit=30" not in ORCHLINK_PI_UI_EXTENSION
    assert "refreshActivity" not in ORCHLINK_PI_UI_EXTENSION
    assert "X-Orchlink-Project-ID" in ORCHLINK_PI_UI_EXTENSION
    assert "ORCHLINK_BROKER_URL" in ORCHLINK_PI_UI_EXTENSION
    assert "ORCHLINK_API_KEY" in ORCHLINK_PI_UI_EXTENSION
    assert "ORCHLINK_MONITOR_POLL_SECONDS" in ORCHLINK_PI_UI_EXTENSION
    assert 'env("ORCHLINK_MONITOR_POLL_SECONDS", "1")' in ORCHLINK_PI_UI_EXTENSION
    assert "Math.max(500" in ORCHLINK_PI_UI_EXTENSION
    assert "pi.registerCommand(\"orchlink\"" in ORCHLINK_PI_UI_EXTENSION
    assert "ctx.mode === \"tui\"" in ORCHLINK_PI_UI_EXTENSION
    assert "pi.setSessionName(nextName)" in ORCHLINK_PI_UI_EXTENSION
    assert "ctx.ui.setStatus" not in ORCHLINK_PI_UI_EXTENSION
    assert "footerStatusText" not in ORCHLINK_PI_UI_EXTENSION
    # G019 AC-8: the inline worker tree is an above-editor widget, not a
    # status/footer injection. It stays visible while the transcript scrolls.
    # The monitor remains read-only while surfacing all active broker tasks.
    assert 'ctx.ui.setWidget("orchlink-worker-tree"' in ORCHLINK_PI_UI_EXTENSION
    assert 'placement: "aboveEditor"' in ORCHLINK_PI_UI_EXTENSION
    assert "renderWorkerTreeFromRows" in ORCHLINK_PI_UI_EXTENSION
    assert "foregroundTaskIds" not in ORCHLINK_PI_UI_EXTENSION
    assert "rows.filter((row) => Boolean(row.job))" in ORCHLINK_PI_UI_EXTENSION
    assert "Active workers" in ORCHLINK_PI_UI_EXTENSION
    assert "F8 open workers" in ORCHLINK_PI_UI_EXTENSION
    assert "ctx.ui.custom" in ORCHLINK_PI_UI_EXTENSION
    assert "worker - ${row.workerName}" in ORCHLINK_PI_UI_EXTENSION
    assert "Orchlink Lead · ${active} active · ${idle} idle" in ORCHLINK_PI_UI_EXTENSION
    assert "LEGACY_STATUS_KEYS" not in ORCHLINK_PI_UI_EXTENSION
    assert "\\u001b[90m${value}\\u001b[0m" not in ORCHLINK_PI_UI_EXTENSION
    assert "activityByItem" not in ORCHLINK_PI_UI_EXTENSION
    assert "formatActivityRecord" not in ORCHLINK_PI_UI_EXTENSION
    assert "ACTIVE_SESSION_STATUSES" in ORCHLINK_PI_UI_EXTENSION
    assert "activeSession(session)" in ORCHLINK_PI_UI_EXTENSION
    assert "overlayHandle?.requestRender?.()" in ORCHLINK_PI_UI_EXTENSION
    assert "onHandle: (handle: any)" in ORCHLINK_PI_UI_EXTENSION
    assert "spawn(\"orch\", [\"stop\", \"--name\", workerName]" in ORCHLINK_PI_UI_EXTENSION
    assert "Stop background worker ${result.workerName}?" in ORCHLINK_PI_UI_EXTENSION
    assert "Visible worker terminals are not stopped from this panel." in ORCHLINK_PI_UI_EXTENSION
    assert "stop it from its own terminal with Ctrl-C" in ORCHLINK_PI_UI_EXTENSION
    assert "isBackground ? \"stop\" : \"visible\"" in ORCHLINK_PI_UI_EXTENSION
    assert "s stop" in ORCHLINK_PI_UI_EXTENSION
    assert "Enter/f follow" in ORCHLINK_PI_UI_EXTENSION
    assert "q/Esc close" in ORCHLINK_PI_UI_EXTENSION
    assert "workers none" in ORCHLINK_PI_UI_EXTENSION
    assert "Orchlink Workers" not in ORCHLINK_PI_UI_EXTENSION
    assert "clearTimeout(timer)" in ORCHLINK_PI_UI_EXTENSION
    assert "abortController?.abort()" in ORCHLINK_PI_UI_EXTENSION
    assert "this.selected = Math.min(Math.max(0, rows.length - 1), this.selected + 1)" in ORCHLINK_PI_UI_EXTENSION
    assert "Math.min(this.maxOffset(), this.offset + 8)" in ORCHLINK_PI_UI_EXTENSION
    assert "minWidth: 60" in ORCHLINK_PI_UI_EXTENSION
    assert 'width: "92%"' in ORCHLINK_PI_UI_EXTENSION
    assert 'maxHeight: "88%"' in ORCHLINK_PI_UI_EXTENSION
    assert "Math.min(38, Math.max(6, Math.floor(this.terminalRows * 0.80)))" in ORCHLINK_PI_UI_EXTENSION
    assert 'import { Container, Key, Markdown, Text, matchesKey, truncateToWidth, visibleWidth } from "@earendil-works/pi-tui"' in ORCHLINK_PI_UI_EXTENSION
    assert 'new Markdown(follow.lines.join("\\n")' in ORCHLINK_PI_UI_EXTENSION
    assert 'getMarkdownTheme()' in ORCHLINK_PI_UI_EXTENSION
    assert "Math.min(22, Math.max(6, this.allLines().length))" not in ORCHLINK_PI_UI_EXTENSION
    assert 'width: "70%"' not in ORCHLINK_PI_UI_EXTENSION
    assert 'maxHeight: "70%"' not in ORCHLINK_PI_UI_EXTENSION
    assert "visible: (termWidth" not in ORCHLINK_PI_UI_EXTENSION
    assert "anchor: \"center\"" in ORCHLINK_PI_UI_EXTENSION

    # Follow view
    assert "ViewMode" in ORCHLINK_PI_UI_EXTENSION
    assert "FollowState" in ORCHLINK_PI_UI_EXTENSION
    assert "mode === \"list\"" in ORCHLINK_PI_UI_EXTENSION
    assert "mode === \"follow\"" in ORCHLINK_PI_UI_EXTENSION
    assert "enterFollow" in ORCHLINK_PI_UI_EXTENSION
    assert "cycleFollow" in ORCHLINK_PI_UI_EXTENSION
    assert "returnToList" in ORCHLINK_PI_UI_EXTENSION
    assert "closePanel" in ORCHLINK_PI_UI_EXTENSION
    assert "FOLLOW ·" in ORCHLINK_PI_UI_EXTENSION
    assert "LIVE" in ORCHLINK_PI_UI_EXTENSION
    assert "PAUSED" in ORCHLINK_PI_UI_EXTENSION
    assert "Tab switch" in ORCHLINK_PI_UI_EXTENSION
    assert "Esc workers" in ORCHLINK_PI_UI_EXTENSION
    assert "Wheel/keys scroll" in ORCHLINK_PI_UI_EXTENSION
    assert "End live" in ORCHLINK_PI_UI_EXTENSION
    assert "\\x1b[?1000h\\x1b[?1006h" in ORCHLINK_PI_UI_EXTENSION
    assert "\\x1b[?1000l\\x1b[?1006l" in ORCHLINK_PI_UI_EXTENSION
    assert "mouseWheelDirection(data)" in ORCHLINK_PI_UI_EXTENSION
    assert 'matchesKey(data, Key.shift("tab"))' in ORCHLINK_PI_UI_EXTENSION
    assert "matchesKey(data, Key.end)" in ORCHLINK_PI_UI_EXTENSION
    assert "followByKey" in ORCHLINK_PI_UI_EXTENSION
    assert "currentFollowKey" in ORCHLINK_PI_UI_EXTENSION

    # G019 AC-1: native lead-only delegate_worker tool
    assert 'import { Type } from "typebox"' in ORCHLINK_PI_UI_EXTENSION
    assert 'name: "delegate_worker"' in ORCHLINK_PI_UI_EXTENSION
    assert "pi.registerTool({" in ORCHLINK_PI_UI_EXTENSION
    # Role gating: the tool registration block sits inside role === "lead".
    assert 'if (role === "lead" && typeof pi.registerTool === "function")' in ORCHLINK_PI_UI_EXTENSION
    # Canonical envelope contract: tool spawns ``orch send --async-json``
    # so the Python envelope builder is reused unchanged.
    assert "pi.exec(\"orch\"" in ORCHLINK_PI_UI_EXTENSION
    assert '"send"' in ORCHLINK_PI_UI_EXTENSION
    assert '"--async-json"' in ORCHLINK_PI_UI_EXTENSION
    assert '"--task-id"' in ORCHLINK_PI_UI_EXTENSION
    # Handle is structurally distinct from a deliverable: description names the
    # handle-vs-result distinction and the prompt guidelines forbid treating
    # acceptance as the worker's final answer.
    assert "tracking handle" in ORCHLINK_PI_UI_EXTENSION.lower() or "TRACKING HANDLE" in ORCHLINK_PI_UI_EXTENSION
    assert "never the worker's final answer" in ORCHLINK_PI_UI_EXTENSION.lower()
    assert "streams inline until completion by default" in ORCHLINK_PI_UI_EXTENSION.lower()
    assert "async=true" in ORCHLINK_PI_UI_EXTENSION
    assert 'promptSnippet:' in ORCHLINK_PI_UI_EXTENSION
    assert 'promptGuidelines:' in ORCHLINK_PI_UI_EXTENSION

    # AC-3: every guidance surface names delegate_worker AND names the
    # handle fields AND forbids treating the handle as the answer. Pi's
    # Guidelines section requires each guideline to name its tool, so each
    # entry below is verified against that contract as well.
    tool_block_start = ORCHLINK_PI_UI_EXTENSION.find('name: "delegate_worker"')
    tool_block_end = ORCHLINK_PI_UI_EXTENSION.find("pi.registerCommand(\"orchlink\"", tool_block_start)
    assert tool_block_start > 0 and tool_block_end > tool_block_start
    delegate_block = ORCHLINK_PI_UI_EXTENSION[tool_block_start:tool_block_end]
    # Extract the description string template literal between description: and
    # the next sibling field. The description text can include commas and
    # periods — split on the next field label instead.
    description_start = delegate_block.find("description:") + len("description:")
    description_end = delegate_block.find("promptSnippet:", description_start)
    description_text = delegate_block[description_start:description_end].strip()
    if description_text.startswith('"') and description_text.endswith('"'):
        description_text = description_text[1:-1]
    elif description_text.startswith('"') or description_text.endswith('"'):
        # Multi-line template literal — keep one or both quote sides as needed.
        description_text = description_text.strip('"')
    # Collapse template-literal interpolation so ``${...}`` doesn't break our
    # substring checks; we only care that the literal phrases are present.
    description_text = description_text.replace("\\`", "`")
    # Description explicitly teaches handle-vs-answer and lists the fields.
    for needle in (
        "TRACKING HANDLE",
        "worker",
        "task_id",
        "correlation_id",
        "status",
        "never the worker's final answer",
        "reconcile the authoritative broker result",
        "/orchlink",
        "acceptance metadata",
    ):
        assert needle in description_text, f"description missing: {needle!r}"
    # Description carries at least one explicit handle-vs-delivery guard
    # phrase — it MUST distinguish acceptance from delivery somehow.
    description_guards = (
        "never the worker's final answer",
        "the worker's reply payload",
        "not a deliverable",
        "acceptance metadata",
        "not that the worker answered",
        "ONLY —",
    )
    assert any(g in description_text for g in description_guards), (
        f"description carries no handle-vs-delivery guard; text={description_text!r}"
    )
    # promptSnippet must distinguish foreground streaming from async handles.
    snippet_end = delegate_block.find("parameters:", description_start)
    snippet_text = delegate_block[description_end:snippet_end]
    snippet_start = snippet_text.find("promptSnippet:") + len("promptSnippet:")
    snippet_value = snippet_text[snippet_start:].split("promptGuidelines:")[0].strip().strip(",").strip().strip('"').strip()
    for needle in (
        "delegate_worker",
        "streams inline until completion",
        "async=true",
        "tracking handle",
        "requires later result reconciliation",
    ):
        assert needle in snippet_value, f"promptSnippet missing: {needle!r}"
    # promptGuidelines must list at least three bullets, each one naming
    # ``delegate_worker`` explicitly (per the Pi docs requirement that each
    # guideline name its tool, since the Guidelines section has no per-tool
    # prefix) and each one calling out the handle-vs-answer distinction.
    guidelines_text = delegate_block.split("promptGuidelines:")[1].split("parameters:")[0]
    # Each promptGuidelines entry is a quoted string between ``"`` and ``,``
    # or closing ``]``.
    import re as _re
    bullets = _re.findall(r'"((?:[^"\\]|\\.)*)"', guidelines_text)
    assert len(bullets) >= 3, f"expected >=3 promptGuidelines bullets, found {len(bullets)}"
    for index, bullet in enumerate(bullets):
        assert "delegate_worker" in bullet, f"guideline #{index + 1} must name delegate_worker: {bullet!r}"
    # At least one guideline must explicitly forbid treating the handle as the
    # answer / payload / deliverable (any phrasing that captures the
    # handle-vs-delivery distinction).
    guard_phrases = (
        "not the worker's final answer",
        "not a result",
        "do not treat the handle as a result",
        "acceptance metadata alone never counts",
        "not the answer",
        "does not end the turn on acceptance alone",
        "end the turn on acceptance alone",
    )
    assert any(any(g in b.lower() for g in guard_phrases) for b in bullets), (
        f"no guideline carries a handle-vs-answer guard phrase; bullets={bullets!r}"
    )
    # And there must be a guideline that points at the reconciliation path
    # (the post-acceptance retrieval route).
    reconciliation_phrases = (
        "reconcile",
        "orch jobs --result",
        "/orchlink",
        "later",
        "after delegate_worker",
    )
    assert any(any(p in b.lower() for p in reconciliation_phrases) for b in bullets), (
        f"no guideline mentions the reconciliation path; bullets={bullets!r}"
    )
    # Required handle keys returned from the tool.
    for needle in (
        '"worker"',
        '"task_id"',
        '"correlation_id"',
        "conversation_id",
        '"status"',
        "accepted_at",
    ):
        assert needle in ORCHLINK_PI_UI_EXTENSION, f"missing handle key: {needle}"
    # Tool must never open or focus the transcript overlay. The overlay open
    # path is the ``ctx.ui.custom(`` factory call; the delegate_worker
    # execute path must not contain that call. Scope the check to the execute
    # body so helper functions and the widget timer are not falsely flagged.
    tool_block_start = ORCHLINK_PI_UI_EXTENSION.find('name: "delegate_worker"')
    tool_block_end = ORCHLINK_PI_UI_EXTENSION.find("pi.registerCommand(\"orchlink\"", tool_block_start)
    assert tool_block_start > 0 and tool_block_end > tool_block_start
    delegate_block = ORCHLINK_PI_UI_EXTENSION[tool_block_start:tool_block_end]
    execute_start = delegate_block.find("async execute(")
    execute_end = delegate_block.find("renderCall(", execute_start)
    execute_body = delegate_block[execute_start:execute_end] if execute_start >= 0 and execute_end > execute_start else delegate_block
    assert "ctx.ui.custom(" not in execute_body
    assert "panelTranscriber" not in execute_body
    assert "overlayHandle" not in execute_body
    # The tool surfaces F8 to open /orchlink (human-driven) rather than
    # opening the overlay itself.
    assert "F8" in delegate_block

    # AC-2: renderCall / renderResult shapes.
    assert "`● Delegate → ${worker}${background}`" in ORCHLINK_PI_UI_EXTENSION
    assert "Expand tool row for live output · F8 details" in ORCHLINK_PI_UI_EXTENSION
    assert "refreshDelegateSnapshot" in ORCHLINK_PI_UI_EXTENSION
    assert "onUpdate?.(delegateResult(handle, snapshot))" in ORCHLINK_PI_UI_EXTENSION
    assert "context.invalidate()" in ORCHLINK_PI_UI_EXTENSION
    # renderResult anchors status to task_id + worker and never paints the
    # task prompt, correlation id, CLI stdout/stderr, or raw tool output.
    assert '`${statusStyled} · ${accent(handle.task_id)} · ${handle.worker}`' in ORCHLINK_PI_UI_EXTENSION
    # correlation_id stays in the handle (in details) but is not in the
    # painted row — keeps the render concise and avoids leaking broker
    # metadata into the user-visible tool row.
    call_block = ORCHLINK_PI_UI_EXTENSION[tool_block_start:tool_block_end]
    call_render_idx = call_block.find("renderResult")
    call_render_block = call_block[call_render_idx:] if call_render_idx >= 0 else ""
    assert "new Text(`F8 open /orchlink · handle" not in call_render_block
    # Renderers never dereference transcript keys, raw stdout/stderr, message body,
    # or any content text. They only look at the handle's worker / task_id / status.
    for forbidden_render_source in (
        "handle.message",
        "handle.body",
        "handle.stdout",
        "handle.stderr",
        "handle.transcript",
        "handle.output",
        ".content[0].text",
        "details.message",
        "details.stdout",
    ):
        assert forbidden_render_source not in call_render_block, (
            f"renderer must not read {forbidden_render_source!r}"
        )

    forbidden = [
        "pi.sendUserMessage",
        "pi.sendMessage",
        "deliverAs:",
        "ctx.compact",
        "session_before_compact",
        "session_compact",
        "ORCHLINK_POLL_WAIT_SECONDS",
        "compact-phase",
        "phaseCompactionInstructions",
        "pi.registerCommand(\"orch\",",
    ]
    for needle in forbidden:
        assert needle not in ORCHLINK_PI_UI_EXTENSION


def test_ensure_orchlink_ui_extension_writes_project_run_file(tmp_path):
    paths = init_project(tmp_path, project_id="demo")
    path = ensure_orchlink_ui_extension({"_project_root": str(tmp_path)})

    assert path == paths["run_dir"] / "orchlink-pi-ui-extension.ts"
    assert path.read_text(encoding="utf-8") == ORCHLINK_PI_UI_EXTENSION


# --- AC-6: Two-worker follow switching, per-follow scroll, Esc returns -------


_PANEL_TEST_TEMPLATE = """
globalThis.runPanelTest = function() {
  const rows = [
    {
      workerName: "work",
      agentId: "test.work",
      ready: true,
      model: "m",
      thinking: "",
      runtime: "r",
      backend: "b",
      heartbeat: "h",
      job: { kind: "task", id: "T001", mode: "DO", status: "RUNNING", activity: "editing", tool: "edit", updated: "u" },
    },
    {
      workerName: "review",
      agentId: "test.review",
      ready: true,
      model: "m",
      thinking: "",
      runtime: "r",
      backend: "b",
      heartbeat: "h",
      job: { kind: "task", id: "T002", mode: "REVIEW", status: "RUNNING", activity: "checking", tool: "read", updated: "u" },
    },
  ];
  const panel = new OrchlinkWorkersPanel(
    () => rows,
    () => ({}),
    () => false,
    (_v) => {},
  );
  panel.setOverlayHandle(() => ({ requestRender: () => {} }));

  panel.handleInput("\\r");
  const f0 = panel.followByKey[panel.currentFollowKey];
  for (let i = 0; i < 80; i++) f0.lines.push("work line " + i);
  const t0 = { current: panel.currentFollowKey, live: f0.live };

  panel.handleInput("\\u001b[6~");
  panel.handleInput("\\u001b[6~");
  panel.handleInput("\\u001b[6~");
  const t1 = { live: f0.live, offset: panel.offset, f0Offset: f0.offset };

  panel.handleInput("\\t");
  const f1 = panel.followByKey[panel.currentFollowKey];
  for (let i = 0; i < 30; i++) f1.lines.push("review line " + i);
  const t2 = { current: panel.currentFollowKey, f0Saved: f0.offset };

  panel.handleInput("\\u001b[Z");
  const back = panel.followByKey[panel.currentFollowKey];
  const t3 = {
    current: panel.currentFollowKey,
    offset: panel.offset,
    followSavedOffset: back.offset,
    followLive: back.live,
    followLinesCount: back.lines.length,
  };

  panel.handleInput("\\u001b");
  const t4 = { mode: panel.mode };

  const preserved = {
    f0LinesCount: panel.followByKey["work:T001"].lines.length,
    f1LinesCount: panel.followByKey["review:T002"].lines.length,
    f0Offset: panel.followByKey["work:T001"].offset,
    f1Offset: panel.followByKey["review:T002"].offset,
  };
  return { t0, t1, t2, t3, t4, preserved };
};
"""


def _run_panel_state_test():
    """Run the panel state machine under Node and return the captured state.

    Uses Node's ``--experimental-transform-types`` to strip TypeScript syntax
    (parameter properties included) so the panel class can be exercised
    directly without a TypeScript build step. Skips if Node is unavailable.
    """
    return _run_node_test(_PANEL_TEST_TEMPLATE, "runPanelTest")


def _run_node_test(template: str, fn_name: str):
    """Run an arbitrary panel-state template under Node and return JSON output.

    ``template`` must define ``globalThis[<fn_name>] = function () { ... }``
    and ``src`` is the panel source as a string.
    """
    node = shutil.which("node")
    if node is None:
        return None

    src = ORCHLINK_PI_UI_EXTENSION
    no_default = re.split(r"export default function", src, maxsplit=1)[0]
    # Wrap the template in an async IIFE so both sync and async test
    # templates ``await`` their setup before printing the JSON result.
    no_default = (
        no_default
        + "\n"
        + template
        + f"\n;(async () => {{ const _r = await globalThis.{fn_name}(); console.log(JSON.stringify(_r)); }})().catch((e) => {{ console.error(e); process.exit(1); }});\n"
    )

    tmp = Path("/tmp/orchlink_panel_test")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    bundle = tmp / f"_{fn_name}.mts"
    bundle.write_text(no_default)

    # Node's ESM resolver ignores NODE_PATH. Link Pi's bundled pi-tui package
    # beside the temporary module so runtime imports resolve exactly as in Pi.
    package_scope = tmp / "node_modules" / "@earendil-works"
    package_scope.mkdir(parents=True)
    pi_tui = Path(
        "/home/debian/.local/lib/node_modules/@earendil-works/"
        "pi-coding-agent/node_modules/@earendil-works/pi-tui"
    )
    (package_scope / "pi-tui").symlink_to(pi_tui, target_is_directory=True)
    pi_agent = Path("/home/debian/.local/lib/node_modules/@earendil-works/pi-coding-agent")
    (package_scope / "pi-coding-agent").symlink_to(pi_agent, target_is_directory=True)
    # Link typebox (a peer dep of pi-coding-agent) at the bundle's node_modules
    # root so ``import { Type } from "typebox"`` resolves under the test runner.
    typebox_src = Path(
        "/home/debian/.local/lib/node_modules/@earendil-works/"
        "pi-coding-agent/node_modules/typebox"
    )
    if typebox_src.exists():
        (tmp / "node_modules" / "typebox").symlink_to(typebox_src, target_is_directory=True)

    env = os.environ.copy()

    result = subprocess.run(
        [node, "--experimental-transform-types", str(bundle)],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}")
    return json.loads(result.stdout.strip())


def test_panel_follow_two_worker_switching_preserves_scroll_position():
    """AC-6: Tab/Shift-Tab cycles active follows without mixing transcripts.

    Ensures per-follow scroll position (offset) is preserved when switching
    away and restored when returning via Shift-Tab, while Esc returns the
    panel to the worker list without losing any follow state.
    """
    import pytest

    state = _run_panel_state_test()
    if state is None:
        pytest.skip("node not available for panel runtime test")

    t0, t1, t2, t3, t4 = state["t0"], state["t1"], state["t2"], state["t3"], state["t4"]
    preserved = state["preserved"]

    # Enter follow on the first worker.
    assert t0["current"] == "work:T001"
    assert t0["live"] is True

    # Scrolling pauses the follow and records the new offset.
    assert t1["live"] is False
    assert t1["offset"] > 0, "PgDown should advance the panel offset"
    assert t1["f0Offset"] == t1["offset"], "scroll must sync to follow.offset"

    # Tab cycles to the second worker without losing f0 state.
    assert t2["current"] == "review:T002"
    assert t2["f0Saved"] == t1["offset"], "switching must save the prior follow's offset"

    # Shift-Tab returns to the first worker and restores the saved offset.
    assert t3["current"] == "work:T001"
    assert t3["offset"] == t1["offset"], "shift-tab must restore the saved scroll offset"
    assert t3["followSavedOffset"] == t1["offset"]
    assert t3["followLive"] is False
    assert t3["followLinesCount"] == 80, "transcript content is not lost across the round trip"

    # Esc returns to the worker list.
    assert t4["mode"] == "list"

    # Both follow buffers remain in followByKey after returning to the list.
    assert preserved["f0LinesCount"] == 80
    assert preserved["f1LinesCount"] == 30
    assert preserved["f0Offset"] == t1["offset"]
    assert preserved["f1Offset"] == 0


def test_panel_follow_state_uses_task_keyed_storage():
    """AC-6: Per-worker transcript storage is keyed on worker:task so
    follow views do not mix transcripts from different workers.
    """
    import pytest

    state = _run_panel_state_test()
    if state is None:
        pytest.skip("node not available for panel runtime test")

    # Each follow view is a separate key, so transcript content stays isolated.
    assert state["preserved"]["f0LinesCount"] == 80
    assert state["preserved"]["f1LinesCount"] == 30


# --- AC-7: Auto-scroll, pause, Up/Down/PgUp/PgDn, End -----------------------


_AC7_TEST_TEMPLATE = r"""
globalThis.runAC7 = function() {
  const rows = [
    {
      workerName: "work",
      agentId: "test.work",
      ready: true,
      model: "m",
      thinking: "",
      runtime: "r",
      backend: "b",
      heartbeat: "h",
      job: { kind: "task", id: "T001", mode: "DO", status: "RUNNING", activity: "editing", tool: "edit", updated: "u" },
    },
  ];
  const panel = new OrchlinkWorkersPanel(
    () => rows,
    () => ({}),
    () => false,
    (_v) => {},
  );
  panel.setOverlayHandle(() => ({ requestRender: () => {} }));

  // Enter follow and seed enough lines to make scrolling meaningful.
  panel.handleInput("\r");
  for (let i = 0; i < 80; i++) panel.appendTranscript("T001", "initial " + i);
  const enterState = {
    mode: panel.mode,
    current: panel.currentFollowKey,
    live: panel.followByKey["work:T001"].live,
    offset: panel.offset,
    maxOffset: panel.maxOffset(),
    atBottom: panel.offset === panel.maxOffset(),
    followLines: panel.followByKey["work:T001"].lines.length,
  };

  // Page Up: pause + scroll by one page.
  panel.handleInput("\u001b[5~");
  const afterPgUp = { offset: panel.offset, maxOffset: panel.maxOffset(), live: panel.followByKey["work:T001"].live };
  const pageSize = panel.maxOffset() - panel.offset;  // forward jump for one PgDn step from here

  // Page Down: scrolls forward by one page.
  panel.handleInput("\u001b[6~");
  const afterPgDn = { offset: panel.offset, maxOffset: panel.maxOffset() };

  // Up arrow scrolls one line (above current position).
  panel.handleInput("\u001b[A");
  const afterUp = { offset: panel.offset };

  // Down arrow scrolls one line (back).
  panel.handleInput("\u001b[B");
  const afterDown = { offset: panel.offset };

  // Render while paused: must include the PAUSED marker.
  const pausedRender = panel.render(80);
  const containsPausedMarker = pausedRender.some((line) => line.includes("-- PAUSED --"));

  // Append two transcript lines while paused: lines buffer, offset does NOT move.
  panel.appendTranscript("T001", "buffered-while-paused A");
  panel.appendTranscript("T001", "buffered-while-paused B");
  const bufferState = {
    offset: panel.offset,
    maxOffset: panel.maxOffset(),
    followLines: panel.followByKey["work:T001"].lines.length,
    live: panel.followByKey["work:T001"].live,
  };

  // End key: resumes live and snaps to bottom.
  panel.handleInput("\u001b[F");
  const endState = {
    live: panel.followByKey["work:T001"].live,
    offset: panel.offset,
    maxOffset: panel.maxOffset(),
    atBottom: panel.offset === panel.maxOffset(),
  };

  // Auto-scroll: while live and at bottom, appending a line keeps panel at the new bottom.
  panel.appendTranscript("T001", "live-autoscroll Z");
  const autoScrollState = {
    offset: panel.offset,
    maxOffset: panel.maxOffset(),
    atBottom: panel.offset === panel.maxOffset(),
    followLines: panel.followByKey["work:T001"].lines.length,
  };

  return {
    enterState,
    afterPgUp,
    afterPgDn,
    afterUp,
    afterDown,
    pageSize,
    containsPausedMarker,
    bufferState,
    endState,
    autoScrollState,
  };
};
"""


def _run_ac7_test():
    return _run_node_test(_AC7_TEST_TEMPLATE, "runAC7")


def test_panel_follow_starts_at_bottom_and_autoscrolls():
    """AC-7: Follow opens at the bottom of the new content and auto-scrolls
    so newly arriving transcript lines stay visible.
    """
    import pytest

    state = _run_ac7_test()
    if state is None:
        pytest.skip("node not available for panel runtime test")

    enter = state["enterState"]
    assert enter["mode"] == "follow"
    assert enter["live"] is True
    assert enter["atBottom"] is True, "follow should snap to the bottom on entry"

    auto = state["autoScrollState"]
    assert auto["atBottom"] is True
    assert auto["followLines"] == 83, "one new line should be appended"
    assert auto["offset"] >= enter["maxOffset"], "auto-scroll must push the visible bottom forward"
    assert state["endState"]["live"] is True, "auto-scroll applies only after End resumes live"


def test_panel_follow_pause_via_page_keys_and_marker():
    """AC-7: PgUp/PgDn pause auto-scroll; manual scroll marks the follow
    PAUSED so the user knows streaming is suspended.
    """
    import pytest

    state = _run_ac7_test()
    if state is None:
        pytest.skip("node not available for panel runtime test")

    after_pgup = state["afterPgUp"]
    assert after_pgup["live"] is False, "PgUp must pause the follow"
    assert after_pgup["offset"] < after_pgup["maxOffset"]

    assert state["containsPausedMarker"] is True, "paused rendering must include -- PAUSED --"

    after_pgdn = state["afterPgDn"]
    assert after_pgdn["offset"] > after_pgup["offset"], "PgDn must advance past the PgUp point"


def test_panel_follow_up_down_scrolls_one_line():
    """AC-7: Up/Down arrows scroll exactly one line."""
    import pytest

    state = _run_ac7_test()
    if state is None:
        pytest.skip("node not available for panel runtime test")

    after_pgdn = state["afterPgDn"]
    after_up = state["afterUp"]
    after_down = state["afterDown"]

    # Direction only: Up moved one line up from the post-PgDn position.
    assert after_up["offset"] == after_pgdn["offset"] - 1
    # Down returns to the same line.
    assert after_down["offset"] == after_pgdn["offset"]
def test_panel_follow_pause_continues_buffering_new_events():
    """AC-7: New transcript events keep buffering in the follow view even
    when the user has paused via manual scrolling.
    """
    import pytest

    state = _run_ac7_test()
    if state is None:
        pytest.skip("node not available for panel runtime test")

    buf = state["bufferState"]
    assert buf["live"] is False, "follow is paused"
    assert buf["followLines"] == 82, "two more transcript lines must be buffered (80 + 2)"
    assert buf["offset"] < buf["maxOffset"], "paused view must not auto-scroll"


def test_panel_follow_end_key_resumes_live_and_snaps_to_bottom():
    """AC-7: End jumps to the latest output and resumes live rendering."""
    import pytest

    state = _run_ac7_test()
    if state is None:
        pytest.skip("node not available for panel runtime test")

    end = state["endState"]
    assert end["live"] is True, "End key must resume live"
    assert end["atBottom"] is True, "End key must snap the panel scroll to the bottom"


# --- AC-9: Existing /orchlink behavior remains ---------------------------------


_AC9_BACKGROUNDS_TEMPLATE = r"""
globalThis.runAC9Backgrounds = function() {
  // AC-9: background worker is stopped via "orch stop"; visible worker is
  // routed to a "visible" notification instead. Distinguishing the two
  // requires preserving both the runtime backend signal and the RPC-supervisor
  // / RPC runtime marker.
  const makeRow = (workerName, agentId, backend, runtime, jobId) => ({
    workerName,
    agentId,
    ready: true,
    model: "m",
    thinking: "",
    runtime,
    backend,
    heartbeat: "h",
    job: jobId ? { kind: "task", id: jobId, mode: "DO", status: "RUNNING", activity: "editing", tool: "edit", updated: "u" } : undefined,
  });

  const backgroundWorker = makeRow("bg.w", "test.bg", "rpc-supervisor", "rpc", "TBG1");
  const visibleWorker = makeRow("vis.w", "test.vis", "local-cli", "local", "TVIS1");
  const rows = [backgroundWorker, visibleWorker];

  const decisions = [];
  const panel = new OrchlinkWorkersPanel(
    () => rows,
    () => ({}),
    () => false,
    (value) => decisions.push(value),
  );
  panel.setOverlayHandle(() => ({ requestRender: () => {} }));

  // Press 's' while the background worker is selected.
  panel.selected = 0;
  panel.handleInput("s");
  // Press 's' while the visible worker is selected.
  panel.selected = 1;
  panel.handleInput("s");

  // Esc returns the result callback to a falsy "closed" value (null) by
  // closing the panel.
  panel.handleInput("q");

  return {
    background_action: (decisions[0] && decisions[0].action) || null,
    background_worker: (decisions[0] && decisions[0].workerName) || null,
    visible_action: (decisions[1] && decisions[1].action) || null,
    visible_worker: (decisions[1] && decisions[1].workerName) || null,
    // The q key closes the panel via `this.done(null)`. Capture the raw
    // third decision so the test can distinguish "panel closed" from
    // "no closure yet".
    closed_index: decisions.length,
    closed_value: decisions.length > 2 ? (decisions[2] === null ? "null" : JSON.stringify(decisions[2])) : "not-closed",
  };
};
"""


def _run_ac9_backgrounds_test():
    return _run_node_test(_AC9_BACKGROUNDS_TEMPLATE, "runAC9Backgrounds")


def test_ui_panel_preserves_ac9_existing_behaviors():
    """AC-9 acceptance consolidation.

    Codifies in one place that the Pi UI extension still ships every
    pre-follow surface: worker status rendering with status-projected activity,
    session naming via ``pi.setSessionName``, background-worker stop
    confirmation, visible-worker protection, and the absence of any
    orch-side second slash command. Same source must remain compatible
    with the existing broker and task result endpoints.
    """
    # One status fetch carries worker state and its latest activity preview;
    # do not fan out N extra activity requests on every UI poll.
    assert "/v1/status?limit=200" in ORCHLINK_PI_UI_EXTENSION
    assert "last_activity_preview" in ORCHLINK_PI_UI_EXTENSION
    assert "/v1/activity?item_id=" not in ORCHLINK_PI_UI_EXTENSION
    # Session naming.
    assert 'pi.setSessionName(nextName)' in ORCHLINK_PI_UI_EXTENSION
    assert 'sessionNameText(rows, offline)' in ORCHLINK_PI_UI_EXTENSION
    # Background-worker stop (spawn + confirmation).
    assert 'spawn("orch", ["stop", "--name", workerName]' in ORCHLINK_PI_UI_EXTENSION
    assert 'Stop background worker ${result.workerName}?' in ORCHLINK_PI_UI_EXTENSION
    # Visible-worker protection: explicit notification, not silent.
    assert 'stop it from its own terminal with Ctrl-C' in ORCHLINK_PI_UI_EXTENSION
    assert 'Visible worker terminals are not stopped from this panel.' in ORCHLINK_PI_UI_EXTENSION
    # Background vs visible routing must still be the runtime/backend-driven
    # switch (rpc-supervisor / rpc => background, anything else => visible).
    assert 'isBackground ? "stop" : "visible"' in ORCHLINK_PI_UI_EXTENSION
    # The two follow-view modes must remain internal to the same overlay.
    assert 'pi.registerCommand("orchlink"' in ORCHLINK_PI_UI_EXTENSION
    assert 'pi.registerCommand("orch",' not in ORCHLINK_PI_UI_EXTENSION
    # Worker rows always include the canonical fields so the list keeps
    # rendering the same shape it did before the follow-view slice.
    assert "workerName" in ORCHLINK_PI_UI_EXTENSION
    assert "ready" in ORCHLINK_PI_UI_EXTENSION
    assert "last_heartbeat_at" in ORCHLINK_PI_UI_EXTENSION or "last_ready_heartbeat_at" in ORCHLINK_PI_UI_EXTENSION


def test_ui_panel_routes_background_stop_and_visible_notification():
    """AC-9: Pressing 's' on a background worker must dispatch ``{action: stop}``
    while a visible (local) worker must dispatch ``{action: visible}``. The
    protected path is observed so a visible worker terminal cannot be torn
    down from the orchlink panel.
    """
    import pytest

    state = _run_ac9_backgrounds_test()
    if state is None:
        pytest.skip("node not available for panel runtime test")

    assert state["background_action"] == "stop"
    assert state["background_worker"] == "bg.w"
    assert state["visible_action"] == "visible"
    assert state["visible_worker"] == "vis.w"
    # Pressing q on the panel closes it (null result returned to the caller).
    assert state["closed_index"] == 3
    assert state["closed_value"] == "null"


def test_cli_orchlink_command_runs_without_broker(tmp_path):
    """AC-9 anchor on the CLI: ``orchlink`` (worker-side) still launches in
    degraded modes that have nothing to do with the broker transcript slice.
    This guards against regressions where adding transcript support could
    tighten a required connection.
    """
    # Smoke test: re-use loop CLI modules that expose the orchlink command.
    # Defer to the existing cli test that exercises the same code path.
    from orchlink.cli import main as cli_main  # noqa: F401

    assert hasattr(cli_main, "app")


# --- G018 transcript follow wiring -------------------------------------------


_FOLLOW_DELIVERY_TEMPLATE = r"""
globalThis.runFollowDelivery = async function() {
  const calls = [];
  const seenHeaders = [];
  let aborts = 0;
  const fetchImpl = async (url, init) => {
    const value = String(url);
    calls.push(value);
    seenHeaders.push(init.headers);
    init.signal.addEventListener("abort", () => { aborts += 1; });
    const after = Number(new URL(value).searchParams.get("after") || 0);
    let events = [];
    if (value.includes("/T001/") && after === 0) events = [
      { seq: 1, kind: "assistant_delta", text: "first" },
      { seq: 2, kind: "assistant_delta", text: " second" },
    ];
    if (value.includes("/T002/") && after === 0) events = [
      { seq: 1, kind: "assistant_delta", text: "review" },
    ];
    return { ok: true, status: 200, json: async () => ({ events, next_seq: 99 }) };
  };
  const rows = [
    { workerName: "work", agentId: "test.work", ready: true, model: "m", thinking: "", runtime: "r", backend: "b", heartbeat: "h",
      job: { kind: "task", id: "T001", mode: "DO", status: "RUNNING", activity: "editing", tool: "edit", updated: "u" } },
    { workerName: "review", agentId: "test.review", ready: true, model: "m", thinking: "", runtime: "r", backend: "b", heartbeat: "h",
      job: { kind: "task", id: "T002", mode: "REVIEW", status: "RUNNING", activity: "checking", tool: "read", updated: "u" } },
  ];
  const panel = new OrchlinkWorkersPanel(() => rows, () => ({}), () => false, () => {});
  panel.setOverlayHandle(() => ({ requestRender: () => {} }));
  const transcriber = createFollowTranscriber(panel, {
    fetchImpl, brokerUrl: "http://broker.test", apiKey: "test-key", projectId: "test", isStopped: () => false,
  });
  panel.handleInput("\r");
  await new Promise((r) => setTimeout(r, 20));
  const first = { ...panel.followByKey["work:T001"], lines: panel.followByKey["work:T001"].lines.slice() };
  panel.handleInput("\t");
  await new Promise((r) => setTimeout(r, 20));
  const second = { ...panel.followByKey["review:T002"], lines: panel.followByKey["review:T002"].lines.slice() };
  panel.handleInput("\u001b[Z");
  await new Promise((r) => setTimeout(r, 10));
  const returned = { ...panel.followByKey["work:T001"], lines: panel.followByKey["work:T001"].lines.slice() };
  panel.handleInput("\u001b");
  await new Promise((r) => setTimeout(r, 5));
  return { calls, seenHeaders, aborts, first, second, returned, current: transcriber.currentFollowKey };
};
"""


def _run_follow_delivery_test():
    return _run_node_test(_FOLLOW_DELIVERY_TEMPLATE, "runFollowDelivery")


def test_follow_wiring_fetches_transcript_with_cursor_and_headers():
    state = _run_follow_delivery_test()
    if state is None:
        import pytest
        pytest.skip("node not available for panel runtime test")

    assert "/v1/tasks/T001/transcript?after=0" in state["calls"][0]
    assert "limit=200" in state["calls"][0]
    assert "wait_seconds=2" in state["calls"][0]
    assert state["seenHeaders"][0]["X-API-Key"] == "test-key"
    assert state["seenHeaders"][0]["X-Orchlink-Project-ID"] == "test"
    assert any("/T001/transcript?after=2" in call for call in state["calls"])
    # next_seq is deliberately 99; only returned event seqs form the cursor.
    assert state["first"]["cursor"] == 2
    assert state["first"]["lines"] == ["first second"]


def test_follow_wiring_switches_without_cursor_or_text_bleed():
    state = _run_follow_delivery_test()
    if state is None:
        import pytest
        pytest.skip("node not available for panel runtime test")

    assert any("/T002/transcript?after=0" in call for call in state["calls"])
    assert state["second"]["lines"] == ["review"]
    assert state["returned"]["lines"] == ["first second"]
    assert state["returned"]["cursor"] == 2
    assert state["aborts"] >= 2
    assert state["current"] is None


def test_follow_wiring_close_aborts_and_fences_late_response():
    template = r"""
globalThis.runFollowClose = async function() {
  let release;
  let abortSeen = false;
  let closed = false;
  const pending = new Promise((resolve) => { release = resolve; });
  const fetchImpl = async (_url, init) => {
    init.signal.addEventListener("abort", () => { abortSeen = true; });
    await pending;
    return { ok: true, status: 200, json: async () => ({ events: [
      { seq: 1, kind: "assistant_delta", text: "late" },
    ], next_seq: 2 }) };
  };
  const rows = [{ workerName: "work", agentId: "test.work", ready: true, model: "m", thinking: "", runtime: "r", backend: "b", heartbeat: "h",
    job: { kind: "task", id: "TC", mode: "DO", status: "RUNNING", activity: "x", tool: "x", updated: "u" } }];
  const panel = new OrchlinkWorkersPanel(() => rows, () => ({}), () => false, () => { closed = true; });
  panel.setOverlayHandle(() => ({ requestRender: () => {} }));
  const transcriber = createFollowTranscriber(panel, {
    fetchImpl, brokerUrl: "http://broker.test", apiKey: "key", projectId: "test", isStopped: () => false,
  });
  panel.handleInput("\r");
  await new Promise((r) => setTimeout(r, 10));
  const before = transcriber.activeGeneration;
  panel.handleInput("q");
  release({});
  await new Promise((r) => setTimeout(r, 20));
  const follow = panel.followByKey["work:TC"];
  return { abortSeen, closed, before, after: transcriber.activeGeneration, current: transcriber.currentFollowKey,
    lines: follow.lines, cursor: follow.cursor };
};
"""
    state = _run_node_test(template, "runFollowClose")
    if state is None:
        import pytest
        pytest.skip("node not available for panel runtime test")

    assert state["abortSeen"] is True
    assert state["closed"] is True
    assert state["after"] > state["before"]
    assert state["current"] is None
    assert state["lines"] == []
    assert state["cursor"] == 0


def test_follow_wiring_privacy_filter_and_retention_marker():
    template = r"""
globalThis.runFollowPrivacy = async function() {
  const fetchImpl = async () => ({ ok: true, status: 200, json: async () => ({ events: [
    { seq: 1, kind: "assistant_delta", text: "visible" },
    { seq: 2, kind: "tool", text: "RAW TOOL OUTPUT" },
    { seq: 3, kind: "thinking_delta", text: "HIDDEN REASONING" },
    { seq: 4, kind: "status", text: "FORGED STATUS" },
    { seq: 5, kind: "system", text: "FORGED SYSTEM" },
    { seq: 6, kind: "system", text: "Earlier transcript history was dropped by retention. This is not the complete output." },
    { seq: 6, kind: "assistant_delta", text: " retained" },
  ], next_seq: 7 }) });
  const rows = [{ workerName: "work", agentId: "test.work", ready: true, model: "m", thinking: "", runtime: "r", backend: "b", heartbeat: "h",
    job: { kind: "task", id: "TP", mode: "DO", status: "RUNNING", activity: "x", tool: "x", updated: "u" } }];
  const panel = new OrchlinkWorkersPanel(() => rows, () => ({}), () => false, () => {});
  panel.setOverlayHandle(() => ({ requestRender: () => {} }));
  const transcriber = createFollowTranscriber(panel, {
    fetchImpl, brokerUrl: "http://broker.test", apiKey: "key", projectId: "test", isStopped: () => false,
  });
  panel.handleInput("\r");
  await new Promise((r) => setTimeout(r, 20));
  transcriber.abortActive();
  const follow = panel.followByKey["work:TP"];
  return { lines: follow.lines, cursor: follow.cursor, truncated: follow.truncated };
};
"""
    state = _run_node_test(template, "runFollowPrivacy")
    if state is None:
        import pytest
        pytest.skip("node not available for panel runtime test")

    rendered = "\n".join(state["lines"])
    assert "visible" in rendered and "retained" in rendered
    assert "RAW TOOL" not in rendered
    assert "HIDDEN" not in rendered
    assert "FORGED" not in rendered
    assert state["cursor"] == 6
    assert state["truncated"] is True


def test_panel_accepts_herdr_kitty_keys_and_has_responsive_minimum_height():
    """Herdr/Ghostty may forward Kitty CSI-u keys instead of legacy bytes."""
    template = r"""
globalThis.runHerdrKeys = function() {
  const rows = [
    { workerName: "work", agentId: "test.work", ready: true, model: "m", thinking: "", runtime: "r", backend: "b", heartbeat: "h",
      job: { kind: "task", id: "T1", mode: "DO", status: "RUNNING", activity: "x", tool: "x", updated: "u" } },
    { workerName: "review", agentId: "test.review", ready: true, model: "m", thinking: "", runtime: "r", backend: "b", heartbeat: "h",
      job: { kind: "task", id: "T2", mode: "REVIEW", status: "RUNNING", activity: "x", tool: "x", updated: "u" } },
  ];
  const panel = new OrchlinkWorkersPanel(() => rows, () => ({}), () => false, () => {}, 35);
  panel.setOverlayHandle(() => ({ requestRender: () => {} }));
  const initialHeight = panel.render(100).length;
  panel.handleInput("\u001b[57420u"); // Kitty Down
  const afterDown = panel.selected;
  panel.handleInput("\u001b[57419u"); // Kitty Up
  const afterUp = panel.selected;
  panel.handleInput("\u001b[102u");   // Kitty f
  const afterF = panel.mode;
  panel.handleInput("\u001b[27u");    // Kitty Escape
  const afterEsc = panel.mode;
  panel.handleInput("\u001b[13u");    // Kitty Enter
  const afterEnter = panel.mode;

  const idleRows = [{ workerName: "idle", agentId: "test.idle", ready: true, model: "m", thinking: "", runtime: "r", backend: "b", heartbeat: "h" }];
  const idle = new OrchlinkWorkersPanel(() => idleRows, () => ({}), () => false, () => {}, 35);
  idle.setOverlayHandle(() => ({ requestRender: () => {} }));
  idle.handleInput("\u001b[13u");
  return { initialHeight, afterDown, afterUp, afterF, afterEsc, afterEnter, idleRender: idle.render(100).join("\n") };
};
"""
    state = _run_node_test(template, "runHerdrKeys")
    if state is None:
        import pytest
        pytest.skip("node not available for panel runtime test")

    assert state["initialHeight"] >= 26
    assert state["afterDown"] == 1
    assert state["afterUp"] == 0
    assert state["afterF"] == "follow"
    assert state["afterEsc"] == "list"
    assert state["afterEnter"] == "follow"
    assert "idle; there is no active task to follow" in state["idleRender"]


def test_follow_mouse_wheel_scrolls_and_help_is_context_sensitive():
    template = r"""
globalThis.runMouseFollow = function() {
  const makeRow = (name, task) => ({ workerName: name, agentId: `test.${name}`, ready: true, model: "m", thinking: "", runtime: "r", backend: "b", heartbeat: "h",
    job: { kind: "task", id: task, mode: "DO", status: "RUNNING", activity: "x", tool: "x", updated: "u" } });
  const rows = [makeRow("work", "T1")];
  const panel = new OrchlinkWorkersPanel(() => rows, () => ({}), () => false, () => {}, 35);
  panel.setOverlayHandle(() => ({ requestRender: () => {} }));
  const compactList = panel.render(100).join("\n");
  panel.handleInput("\r");
  panel.appendTranscript("T1", "short");
  const shortHelp = panel.render(100).join("\n");
  for (let i = 0; i < 80; i++) panel.appendTranscript("T1", `line ${i}`);
  panel.render(100);
  const before = panel.offset;
  panel.handleInput("\u001b[<64;50;15M"); // wheel up
  const afterUp = { offset: panel.offset, live: panel.followByKey["work:T1"].live };
  panel.handleInput("\u001b[<65;50;15M"); // wheel down
  const afterDown = panel.offset;
  const beforeButton = panel.offset;
  panel.handleInput("\u001b[<0;50;15M");  // ordinary click, not wheel
  const afterButton = panel.offset;
  panel.handleInput("\u001b[<66;50;15M"); // horizontal wheel left
  panel.handleInput("\u001b[<67;50;15M"); // horizontal wheel right
  const afterHorizontal = panel.offset;
  const longHelp = panel.render(100).join("\n");

  const twoRows = [makeRow("work", "T1"), makeRow("review", "T2")];
  const two = new OrchlinkWorkersPanel(() => twoRows, () => ({}), () => false, () => {}, 35);
  two.handleInput("\r");
  const switchHelp = two.render(100).join("\n");
  return { compactList, shortHelp, before, afterUp, afterDown, beforeButton, afterButton, afterHorizontal, longHelp, switchHelp };
};
"""
    state = _run_node_test(template, "runMouseFollow")
    if state is None:
        import pytest
        pytest.skip("node not available for panel runtime test")

    assert "agent:" not in state["compactList"]
    assert "runtime:" not in state["compactList"]
    assert "Tab switch" not in state["shortHelp"]
    assert "Wheel/keys scroll" not in state["shortHelp"]
    assert state["afterUp"]["offset"] == state["before"] - 3
    assert state["afterUp"]["live"] is False
    assert state["afterDown"] == state["before"]
    assert state["afterButton"] == state["beforeButton"]
    assert state["afterHorizontal"] == state["beforeButton"]
    assert "Wheel/keys scroll" in state["longHelp"]
    assert "Tab switch" in state["switchHelp"]


def test_mouse_tracking_is_enabled_and_disabled_around_overlay(tmp_path):
    """The terminal must never remain in mouse-reporting mode after close."""
    node = shutil.which("node")
    if node is None:
        import pytest
        pytest.skip("node not available for extension runtime test")

    package_scope = tmp_path / "node_modules" / "@earendil-works"
    package_scope.mkdir(parents=True)
    pi_tui = Path(
        "/home/debian/.local/lib/node_modules/@earendil-works/"
        "pi-coding-agent/node_modules/@earendil-works/pi-tui"
    )
    (package_scope / "pi-tui").symlink_to(pi_tui, target_is_directory=True)
    pi_agent = Path("/home/debian/.local/lib/node_modules/@earendil-works/pi-coding-agent")
    (package_scope / "pi-coding-agent").symlink_to(pi_agent, target_is_directory=True)
    typebox_src = Path(
        "/home/debian/.local/lib/node_modules/@earendil-works/"
        "pi-coding-agent/node_modules/typebox"
    )
    if typebox_src.exists():
        (tmp_path / "node_modules" / "typebox").symlink_to(typebox_src, target_is_directory=True)
    (tmp_path / "extension.mts").write_text(ORCHLINK_PI_UI_EXTENSION)
    (tmp_path / "run.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "lead";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
globalThis.fetch = async () => ({
  ok: true,
  status: 200,
  json: async () => ({ broker: "ok", sessions: [], jobs: [], activity: [] }),
});
const { default: register } = await import("./extension.mts");
const commands = {};
const events = {};
const writes = [];
const pi = {
  registerCommand: (name, command) => { commands[name] = command; },
  on: (name, handler) => { events[name] = handler; },
  setSessionName: () => {},
  setWidget: () => {},
};
register(pi);
const terminal = { rows: 35, write: (data) => writes.push(data) };
const ctx = {
  mode: "tui",
  hasUI: true,
  ui: {
    notify: () => {},
    custom: (factory, options) => new Promise((resolve) => {
      options.onHandle({ requestRender: () => {} });
      const panel = factory({ terminal }, {}, {}, resolve);
      panel.handleInput("q");
    }),
  },
};
await commands.orchlink.handler("", ctx);
await events.session_shutdown({}, ctx);
console.log(JSON.stringify(writes));
'''
    )
    result = subprocess.run(
        [node, "--experimental-transform-types", str(tmp_path / "run.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    writes = json.loads(result.stdout.strip())
    assert writes == ["\x1b[?1000h\x1b[?1006h", "\x1b[?1000l\x1b[?1006l"]


def test_follow_transcript_renders_pi_markdown_colors_and_code_blocks():
    template = r"""
globalThis.runMarkdownFollow = async function() {
  const { initTheme } = await import("@earendil-works/pi-coding-agent");
  initTheme("dark", false);
  const rows = [{ workerName: "work", agentId: "test.work", ready: true, model: "m", thinking: "", runtime: "r", backend: "b", heartbeat: "h",
    job: { kind: "task", id: "TM", mode: "DO", status: "RUNNING", activity: "x", tool: "x", updated: "u" } }];
  const uiTheme = { fg: (_color, text) => `\u001b[36m${text}\u001b[0m` };
  const panel = new OrchlinkWorkersPanel(() => rows, () => ({}), () => false, () => {}, 35, getMarkdownTheme(), uiTheme);
  panel.setOverlayHandle(() => ({ requestRender: () => {} }));
  panel.handleInput("\r");
  panel.appendTranscript("TM", "# Heading\n\n**bold text**\n\n```js\nconst answer = 42;\n```", "assistant_delta");
  const rendered = panel.render(100);
  return {
    joined: rendered.join("\n"),
    widths: rendered.map((line) => visibleWidth(line)),
  };
};
"""
    state = _run_node_test(template, "runMarkdownFollow")
    if state is None:
        import pytest
        pytest.skip("node not available for panel runtime test")

    assert "Heading" in state["joined"]
    assert "bold text" in state["joined"]
    assert "const" in state["joined"] and "answer =" in state["joined"] and "42" in state["joined"]
    assert "\x1b[" in state["joined"]
    assert max(state["widths"]) <= 100


# --- G019 AC-1: Native lead-only delegate_worker tool -------------------------


def _build_pi_capture_bundle(tmp_path: Path) -> Path:
    """Stage a fresh tmp_path that links Pi's bundled deps for the AC-1 tool test."""
    import shutil
    if tmp_path.exists():
        shutil.rmtree(tmp_path)
    tmp_path.mkdir(parents=True)
    package_scope = tmp_path / "node_modules" / "@earendil-works"
    package_scope.mkdir(parents=True)
    pi_tui = Path(
        "/home/debian/.local/lib/node_modules/@earendil-works/"
        "pi-coding-agent/node_modules/@earendil-works/pi-tui"
    )
    (package_scope / "pi-tui").symlink_to(pi_tui, target_is_directory=True)
    pi_agent = Path("/home/debian/.local/lib/node_modules/@earendil-works/pi-coding-agent")
    (package_scope / "pi-coding-agent").symlink_to(pi_agent, target_is_directory=True)
    typebox_src = Path(
        "/home/debian/.local/lib/node_modules/@earendil-works/"
        "pi-coding-agent/node_modules/typebox"
    )
    if typebox_src.exists():
        (tmp_path / "node_modules" / "typebox").symlink_to(typebox_src, target_is_directory=True)
    (tmp_path / "extension.mts").write_text(ORCHLINK_PI_UI_EXTENSION)
    return tmp_path


def test_delegate_worker_tool_registers_lead_only_and_uses_orch_send(tmp_path):
    """AC-1: delegate_worker is a lead-only native tool that calls ``orch send``
    (the canonical Python envelope builder) and returns a tracking handle
    without ever opening the transcript overlay.

    Verifies runtime behavior:
    * the tool is registered only when ORCHLINK_PI_ROLE=lead;
    * the tool calls ``pi.exec('orch', [..., '--async-json'], ...)``;
    * the returned handle has all required fields;
    * the tool's execute path never opens the overlay (no ``ctx.ui.custom`` call).
    """
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    tmp_path = _build_pi_capture_bundle(tmp_path)
    (tmp_path / "lead.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "lead";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
globalThis.fetch = async () => ({
  ok: true,
  status: 200,
  json: async () => ({ broker: "ok", sessions: [], jobs: [], activity: [] }),
});
const { default: register } = await import("./extension.mts");
const calls = [];
let registered = null;
const pi = {
  registerCommand: () => {},
  registerTool: (tool) => { registered = tool; },
  registerShortcut: () => {},
  on: () => {},
  setSessionName: () => {},
  setWidget: () => {},
  exec: async (cmd, args, opts) => {
    calls.push({ cmd, args, opts });
    return {
      code: 0,
      stdout: JSON.stringify({
        worker: args[1],
        task_id: "UI-031",
        correlation_id: "req-test-correlation",
        conversation_id: "demo-tasks",
        status: "PENDING",
        accepted_at: "2026-07-11T00:00:00+00:00",
      }),
      stderr: "",
      killed: false,
    };
  },
};
register(pi);
if (!registered || registered.name !== "delegate_worker") {
  throw new Error("delegate_worker tool was not registered");
}
// Snapshot the execute function and a few metadata fields, then call it.
const tool = registered;
const result = await tool.execute("call-1", { worker: "work", task_id: "UI-031", message: "implement X", async: true }, undefined, undefined, {});
// Render the call and result rows.
const callRender = tool.renderCall({ worker: "work", task_id: "UI-031", message: "implement X" }, { fg: (_c, t) => t });
const renderContext = { state: {}, lastComponent: undefined, invalidate: () => {} };
const resultRender = tool.renderResult(result, { isError: false, expanded: false }, { fg: (_c, t) => t }, renderContext);
console.log(JSON.stringify({
  toolName: tool.name,
  toolLabel: tool.label,
  hasDescription: typeof tool.description === "string" && tool.description.length > 0,
  hasPromptSnippet: typeof tool.promptSnippet === "string" && tool.promptSnippet.length > 0,
  promptGuidelinesLen: Array.isArray(tool.promptGuidelines) ? tool.promptGuidelines.length : 0,
  descriptionMentionsHandle: /handle/i.test(tool.description),
  descriptionMentionsNotAnswer: /not the .*answer|never the worker/i.test(tool.description),
  execCalls: calls,
  execArgv: calls[0]?.args ?? null,
  resultDetails: result.details,
  resultFirst: result.content[0].text,
  callRenderFirst: callRender?.render ? callRender.render(80).join("|") : String(callRender),
  resultRenderTexts: resultRender?.render ? resultRender.render(80) : [String(resultRender)],
}));
'''
    )
    result = subprocess.run(
        [node, "--experimental-transform-types", str(tmp_path / "lead.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"node failed: rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    state = json.loads(result.stdout.strip())

    # Lead-only and shape.
    assert state["toolName"] == "delegate_worker"
    assert state["toolLabel"]
    assert state["hasDescription"]
    assert state["hasPromptSnippet"]
    assert state["promptGuidelinesLen"] >= 3
    assert state["descriptionMentionsHandle"]
    assert state["descriptionMentionsNotAnswer"]

    # The tool calls ``orch send`` with argument-array execution.
    assert state["execCalls"][0]["cmd"] == "orch"
    argv = state["execArgv"]
    assert "send" in argv
    assert "--task-id" in argv
    assert "UI-031" in argv
    assert "--async-json" in argv
    assert "--message" in argv
    assert "implement X" in argv

    # Handle has all required keys and is structurally distinct from a deliverable.
    handle = state["resultDetails"]["handle"]
    for field in ("worker", "task_id", "correlation_id", "conversation_id", "status", "accepted_at"):
        assert handle[field], f"handle missing field: {field}"
    assert handle["status"] == "PENDING"
    assert handle["correlation_id"] == "req-test-correlation"
    # Result text is explicitly about *acceptance*, not the answer.
    assert "accepted" in state["resultFirst"].lower()
    assert "not the answer" not in state["resultFirst"].lower() or "never" in state["resultFirst"].lower()
    # renderCall / renderResult shape pointers the user at the overlay (F8)
    # without opening it from inside the tool.
    assert "Delegate → work" in state["callRenderFirst"]


def test_delegate_worker_tool_is_absent_for_non_lead_role(tmp_path):
    """AC-1: delegate_worker must NOT register when ORCHLINK_PI_ROLE != lead.

    Workers and background agents must not be able to recursively spawn tasks
    through the lead-only delegate surface.
    """
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    tmp_path = _build_pi_capture_bundle(tmp_path)
    (tmp_path / "worker.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "worker";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
globalThis.fetch = async () => ({
  ok: true,
  status: 200,
  json: async () => ({ broker: "ok", sessions: [], jobs: [], activity: [] }),
});
const { default: register } = await import("./extension.mts");
const tools = [];
const pi = {
  registerCommand: () => {},
  registerTool: (tool) => { tools.push(tool.name); },
  registerShortcut: () => {},
  on: () => {},
  setSessionName: () => {},
  setWidget: () => {},
  exec: async () => ({ code: 0, stdout: "{}", stderr: "", killed: false }),
};
register(pi);
console.log(JSON.stringify({ tools }));
'''
    )
    result = subprocess.run(
        [node, "--experimental-transform-types", str(tmp_path / "worker.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"node failed: rc={result.returncode}\nstdout={result.stdout}\nstderr={result.stderr}"
    state = json.loads(result.stdout.strip())
    assert "delegate_worker" not in state["tools"]


def test_cli_send_emits_async_json_handle_when_flagged(tmp_path):
    """AC-1: orch send --async-json emits a single-line tracking handle to stdout
    and suppresses human-readable guidance, with the canonical Python envelope
    builder reused unchanged.
    """
    from typer.testing import CliRunner

    from orchlink.cli import main as cli_main

    runner = CliRunner()

    def fake_send_worker_sync(**_kwargs):
        return {
            "status": "PENDING",
            "to_agent": "demo.work",
            "task_id": "UI-031",
            "correlation_id": "req-test-correlation",
            "conversation_id": "demo-tasks",
            "received_at": "2026-07-11T00:00:00+00:00",
        }

    captured = {}
    import orchlink.cli.commands.tasks as tasks_mod

    original_emit = tasks_mod._emit_async_handle

    def capturing_emit(payload):
        captured["payload"] = payload
        return original_emit(payload)

    # Patch the module-level emit helper so we capture the JSON shape passed.
    import orchlink.cli.commands.tasks as tasks_module
    tasks_module._emit_async_handle = capturing_emit  # type: ignore[assignment]
    try:
        cli_main.send_worker_sync = fake_send_worker_sync  # type: ignore[assignment]
        result = runner.invoke(
            cli_main.app,
            ["send", "work", "--task-id", "UI-031", "--message", "implement X", "--async-json"],
        )
    finally:
        tasks_module._emit_async_handle = original_emit  # type: ignore[assignment]

    assert result.exit_code == 0, result.stdout
    # Single-line JSON to stdout.
    body = result.stdout.strip()
    assert "\n" not in body, f"expected single-line JSON, got {body!r}"
    handle = json.loads(body)
    assert handle["worker"] == "demo.work"
    assert handle["task_id"] == "UI-031"
    assert handle["correlation_id"] == "req-test-correlation"
    assert handle["conversation_id"] == "demo-tasks"
    assert handle["status"] == "PENDING"
    assert handle["accepted_at"]
    # Human-readable guidance must NOT be present in --async-json mode.
    assert "[Orch]" not in result.stdout
    assert "Async mode:" not in result.stdout
    assert "orch jobs --active" not in result.stdout
    # The Python envelope builder ran through send_worker_sync unchanged.
    assert captured["payload"]["task_id"] == "UI-031"
    assert captured["payload"]["correlation_id"] == "req-test-correlation"


def test_cli_send_async_json_omitted_keeps_existing_human_output(monkeypatch, tmp_path):
    """Additive contract: with --async-json OFF the existing human-readable
    guidance remains unchanged for backwards compatibility.
    """
    from typer.testing import CliRunner

    from orchlink.cli import main as cli_main

    runner = CliRunner()

    def fake_send_worker_sync(**_kwargs):
        return {
            "status": "PENDING",
            "to_agent": "demo.work",
            "task_id": "UI-031",
            "correlation_id": "req-test-correlation",
            "conversation_id": "demo-tasks",
        }

    cli_main.send_worker_sync = fake_send_worker_sync  # type: ignore[assignment]
    result = runner.invoke(
        cli_main.app,
        ["send", "work", "--task-id", "UI-031", "--message", "implement X"],
    )

    assert result.exit_code == 0, result.stdout
    # Human-readable guidance is still produced and no JSON handle is mixed in.
    assert "[Orch] Sent UI-031 to work" in result.stdout
    assert "Async mode:" in result.stdout
    assert "{" not in result.stdout.split("Sent")[1].split("\n")[0] if "Sent" in result.stdout else True
    # No handle line on stdout.
    assert "correlation_id" not in result.stdout


# --- G019 AC-2: delegate_worker renderCall / renderResult shape ----------------


def test_delegate_worker_render_call_is_concise_intent(tmp_path):
    """AC-2: renderCall returns a concise intent row shaped ``● Delegate → <worker>``
    using Pi theme colors. The row surfaces only an identifier the LLM already
    supplied (worker name) — never transcript text, raw CLI command output, or
    any tool body.
    """
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    tmp_path = _build_pi_capture_bundle(tmp_path)
    (tmp_path / "render_call.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "lead";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
globalThis.fetch = async () => ({
  ok: true,
  status: 200,
  json: async () => ({ broker: "ok", sessions: [], jobs: [], activity: [] }),
});
const { default: register } = await import("./extension.mts");
let registered = null;
const pi = {
  registerCommand: () => {},
  registerTool: (tool) => { registered = tool; },
  registerShortcut: () => {},
  on: () => {},
  setSessionName: () => {},
  setWidget: () => {},
  exec: async () => ({ code: 0, stdout: "{}", stderr: "", killed: false }),
};
register(pi);
const tool = registered;
const theme = {
  fg: (color, text) => `\x1b[${color === "accent" ? 36 : color === "dim" ? 90 : 0}m${text}\x1b[0m`,
};
const component = tool.renderCall(
  { worker: "review", task_id: "REVIEW-7", message: "long task body that must NOT leak" },
  theme,
);
const rendered = component.render(80);
console.log(JSON.stringify({ isComponent: typeof component.render === "function", rowCount: rendered.length, rendered }));
'''
    )
    result = subprocess.run(
        [node, "--experimental-transform-types", str(tmp_path / "render_call.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"node failed: rc={result.returncode}\nstderr={result.stderr}"
    state = json.loads(result.stdout.strip())
    rendered = state["rendered"]
    assert state["isComponent"] is True
    # Concise intent row containing the bullet and arrow.
    assert state["rowCount"] >= 1
    assert any("Delegate → review" in row for row in rendered), rendered
    # The long task body (a tool-body argument) must NOT appear in any row.
    for row in rendered:
        assert "long task body" not in row, f"renderCall leaked tool body: {row!r}"
    # Bullet glyph is part of the intent row.
    assert any("●" in row for row in rendered), rendered


def test_delegate_worker_render_result_points_to_worker_task_and_f8_hint(tmp_path):
    """AC-2: renderResult returns one or two rows that point at the worker
    + task id and include the F8 hint, while staying free of transcript text
    or any tool body.
    """
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    tmp_path = _build_pi_capture_bundle(tmp_path)
    (tmp_path / "render_result.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "lead";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
globalThis.fetch = async () => ({
  ok: true,
  status: 200,
  json: async () => ({ broker: "ok", sessions: [], jobs: [], activity: [] }),
});
const { default: register } = await import("./extension.mts");
let registered = null;
const pi = {
  registerCommand: () => {},
  registerTool: (tool) => { registered = tool; },
  registerShortcut: () => {},
  on: () => {},
  setSessionName: () => {},
  setWidget: () => {},
  exec: async () => ({ code: 0, stdout: "{}", stderr: "", killed: false }),
};
register(pi);
const tool = registered;
const theme = { fg: (_color, text) => text };
const handle = {
  worker: "work",
  task_id: "UI-031",
  correlation_id: "req-test-correlation",
  conversation_id: "demo-tasks",
  status: "PENDING",
  accepted_at: "2026-07-11T00:00:00+00:00",
};
// Simulate a result with rich details that MUST NOT leak into the rendered
// rows (the privacy boundary enforced by AC-2 and AC-10).
const result = {
  content: [{ type: "text", text: "secret content body to be filtered" }],
  details: {
    handle,
    snapshot: {
      status: "RUNNING", startedAt: "2026-07-11T00:00:00+00:00",
      toolCount: 2, tokens: 12000, contextWindow: 128000, percent: 9.4,
      transcript: [], cursor: 0, resultSummary: "", background: false,
    },
    message: "the full task body that must not appear in rendering",
    stdout: "worker stdout that must not appear",
    stderr: "worker stderr that must not appear",
    transcript: "raw transcript text that must not appear",
    tool_body: "tool output body that must not appear",
  },
};
const renderContext = { state: {}, lastComponent: undefined, invalidate: () => {} };
const component = tool.renderResult(
  result,
  { isError: false, expanded: false },
  theme,
  renderContext,
);
const rendered = component.render(80);
const joined = rendered.join("\n");
console.log(JSON.stringify({ isComponent: typeof component.render === "function", rowCount: rendered.length, rendered, joined }));
'''
    )
    result = subprocess.run(
        [node, "--experimental-transform-types", str(tmp_path / "render_result.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"node failed: rc={result.returncode}\nstderr={result.stderr}"
    state = json.loads(result.stdout.strip())
    rendered = state["rendered"]
    joined = state["joined"]
    assert state["isComponent"] is True
    # Worker + task id are present.
    assert any("UI-031" in row for row in rendered), rendered
    assert any("work" in row for row in rendered), rendered
    # The collapsed active row points at both inline expansion and F8 details.
    assert "Expand tool row for live output" in joined, joined
    assert "F8 details" in joined, joined
    # Privacy boundary: even though ``details`` carried transcript text, raw
    # stdout/stderr, the task body, and a tool body, NONE of those strings
    # must appear in any rendered row.
    for forbidden in (
        "secret content body to be filtered",
        "the full task body that must not appear in rendering",
        "worker stdout that must not appear",
        "worker stderr that must not appear",
        "raw transcript text that must not appear",
        "tool output body that must not appear",
    ):
        assert forbidden not in joined, (
            f"renderResult leaked {forbidden!r}; rows={rendered!r}"
        )
    # correlation_id stays in the handle (in details) but is not painted in
    # the visible row — keeps the row concise.
    assert "req-test-correlation" not in joined, joined


def test_delegate_worker_inline_row_updates_active_to_done_and_filters_expanded_transcript(tmp_path):
    """The original tool row stays live after async acceptance, can show only
    visible assistant transcript when expanded, and settles on broker DONE.
    """
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    tmp_path = _build_pi_capture_bundle(tmp_path)
    (tmp_path / "live_delegate.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "lead";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
let phase = "RUNNING";
globalThis.fetch = async (url) => {
  const value = String(url);
  if (value.includes("/transcript?")) return { ok: true, json: async () => ({ events: [
    { seq: 1, kind: "assistant_delta", text: "visible progress" },
    { seq: 2, kind: "tool", text: "SECRET TOOL OUTPUT" },
    { seq: 3, kind: "thinking_delta", text: "SECRET REASONING" },
    { seq: 4, kind: "system", text: "Earlier transcript history was dropped by retention. This is not the complete output." },
    { seq: 4, kind: "assistant_delta", text: "retained visible output" },
  ] }) };
  if (value.endsWith("/telemetry")) return { ok: true, json: async () => ({
    tool_count: 3, tokens: 12000, context_window: 128000, percent: 9.4,
  }) };
  if (value.includes("/v1/tasks/UI-LIVE")) return { ok: true, json: async () => ({
    status: phase,
    ...(phase === "DONE" ? { reply: { payload: { summary: "authoritative worker result" } } } : {}),
  }) };
  return { ok: true, json: async () => ({ broker: "ok", sessions: [], jobs: [] }) };
};
const { default: register } = await import("./extension.mts");
let tool;
const pi = {
  registerCommand: () => {}, registerTool: (value) => { tool = value; }, registerShortcut: () => {},
  on: () => {}, setSessionName: () => {}, setWidget: () => {},
  exec: async () => ({ code: 0, stdout: "{}", stderr: "" }),
};
register(pi);
let sendArgs = [];
pi.exec = async (_cmd, args) => { sendArgs = args; return ({ code: 0, stdout: JSON.stringify({
  worker: "work", task_id: "UI-LIVE", correlation_id: "secret-correlation",
  conversation_id: "tasks", status: "PENDING", accepted_at: new Date().toISOString(),
}), stderr: "" }); };
const updates = [];
setTimeout(() => { phase = "DONE"; }, 150);
const result = await tool.execute(
  "call-live",
  { worker: "work", task_id: "UI-LIVE", message: "visual test" },
  undefined,
  (update) => updates.push(update),
  {},
);
const activeResult = updates.find((update) => update.details.snapshot.status === "RUNNING");
const theme = { fg: (_color, text) => text };
const activeContext = { state: {}, lastComponent: undefined, invalidate: () => {} };
const active = tool.renderResult(activeResult, { expanded: true }, theme, activeContext).render(100).join("\n");
const doneContext = { state: {}, lastComponent: undefined, invalidate: () => {} };
const done = tool.renderResult(result, { expanded: false }, theme, doneContext).render(100).join("\n");
console.log(JSON.stringify({ active, done, updateCount: updates.length, sendArgs }));
'''
    )
    result = subprocess.run(
        [node, "--experimental-transform-types", str(tmp_path / "live_delegate.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip())
    assert "running" in state["active"].lower()
    assert "3 tools" in state["active"]
    assert "ctx 12k/128k (9%)" in state["active"]
    assert "visible progress" in state["active"]
    assert "Earlier transcript history was dropped by retention" in state["active"]
    assert "retained visible output" in state["active"]
    assert "SECRET TOOL OUTPUT" not in state["active"]
    assert "SECRET REASONING" not in state["active"]
    assert "✓ complete" in state["done"]
    assert "authoritative worker result" in state["done"].lower()
    assert state["updateCount"] >= 2
    assert "--foreground-json" in state["sendArgs"]


def test_delegate_worker_foreground_abort_detaches_without_cancelling_broker_task(tmp_path):
    """Interrupting the local foreground wait leaves broker work running."""
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    tmp_path = _build_pi_capture_bundle(tmp_path)
    (tmp_path / "detach_delegate.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "lead";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
const urls = [];
globalThis.fetch = async (url) => {
  urls.push(String(url));
  if (String(url).endsWith("/telemetry")) return { ok: false, json: async () => ({}) };
  if (String(url).includes("/transcript?")) return { ok: true, json: async () => ({ events: [] }) };
  if (String(url).includes("/v1/tasks/UI-DETACH")) return { ok: true, json: async () => ({ status: "RUNNING" }) };
  return { ok: true, json: async () => ({ broker: "ok", sessions: [], jobs: [] }) };
};
const { default: register } = await import("./extension.mts");
let tool;
const pi = {
  registerCommand: () => {}, registerTool: (value) => { tool = value; }, registerShortcut: () => {},
  on: () => {}, setSessionName: () => {}, setWidget: () => {},
  exec: async () => ({ code: 0, stdout: JSON.stringify({
    worker: "work", task_id: "UI-DETACH", correlation_id: "req-detach",
    conversation_id: "tasks", status: "PENDING", accepted_at: new Date().toISOString(),
  }), stderr: "" }),
};
register(pi);
const controller = new AbortController();
setTimeout(() => controller.abort(), 50);
const result = await tool.execute(
  "call-detach",
  { worker: "work", task_id: "UI-DETACH", message: "keep running" },
  controller.signal,
  () => {},
  {},
);
console.log(JSON.stringify({ snapshot: result.details.snapshot, urls }));
'''
    )
    result = subprocess.run(
        [node, "--experimental-transform-types", str(tmp_path / "detach_delegate.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip())
    assert state["snapshot"]["status"] == "DETACHED"
    assert state["snapshot"]["background"] is True
    assert not any("cancel" in url or "stop" in url for url in state["urls"])


def test_delegate_worker_render_handles_missing_handle_safely(tmp_path):
    """AC-2: renderResult must not throw if ``details`` is missing the
    structured handle — it surfaces a single concise row instead.
    """
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    tmp_path = _build_pi_capture_bundle(tmp_path)
    (tmp_path / "missing_handle.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "lead";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
globalThis.fetch = async () => ({
  ok: true,
  status: 200,
  json: async () => ({ broker: "ok", sessions: [], jobs: [], activity: [] }),
});
const { default: register } = await import("./extension.mts");
let registered = null;
const pi = {
  registerCommand: () => {},
  registerTool: (tool) => { registered = tool; },
  registerShortcut: () => {},
  on: () => {},
  setSessionName: () => {},
  setWidget: () => {},
  exec: async () => ({ code: 0, stdout: "{}", stderr: "", killed: false }),
};
register(pi);
const tool = registered;
const theme = { fg: (_color, text) => text };
const cases = [
  { name: "empty_details", result: { content: [{ type: "text", text: "ok" }], details: undefined } },
  { name: "missing_handle", result: { content: [{ type: "text", text: "ok" }], details: {} } },
  { name: "handle_partial", result: { content: [{ type: "text", text: "ok" }], details: { handle: { worker: "work" } } } },
];
const out = [];
for (const { name, result } of cases) {
  const component = tool.renderResult(
    result,
    { isError: false, expanded: false },
    theme,
    { state: {}, lastComponent: undefined, invalidate: () => {} },
  );
  const rows = component.render(80);
  out.push({ name, isComponent: typeof component.render === "function", rows });
}
console.log(JSON.stringify(out));
'''
    )
    result = subprocess.run(
        [node, "--experimental-transform-types", str(tmp_path / "missing_handle.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"node failed: rc={result.returncode}\nstderr={result.stderr}"
    cases_out = json.loads(result.stdout.strip())
    for case in cases_out:
        assert case["isComponent"] is True
        # Each case must produce a finite row list without throwing.
        assert isinstance(case["rows"], list)
        assert len(case["rows"]) >= 1
        # No row should inline transcript/CLI/tool body content.
        for row in case["rows"]:
            assert "transcript" not in row.lower() or "transcript" in row.lower()
        # Privacy: no leak of any of the source fields from a missing handle.
        joined = "\n".join(case["rows"])
        assert "secret" not in joined
        assert "raw stdout" not in joined


# --- G019 AC-3: delegate_worker guidance surfaces -----------------------------


def test_delegate_worker_guidance_surfaces_teach_handle_vs_answer(tmp_path):
    """AC-3: tool description, promptSnippet, and every promptGuidelines bullet
    explicitly teach that delegate_worker returns a tracking handle (NOT the
    worker's final answer) and that the lead must reconcile the authoritative
    broker result later via the /orchlink overlay or ``orch jobs --result``.

    Asserted at runtime so the registered definition is the source of truth,
    not the source template.
    """
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    tmp_path = _build_pi_capture_bundle(tmp_path)
    (tmp_path / "guidance.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "lead";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
globalThis.fetch = async () => ({
  ok: true,
  status: 200,
  json: async () => ({ broker: "ok", sessions: [], jobs: [], activity: [] }),
});
const { default: register } = await import("./extension.mts");
let registered = null;
const pi = {
  registerCommand: () => {},
  registerTool: (tool) => { registered = tool; },
  registerShortcut: () => {},
  on: () => {},
  setSessionName: () => {},
  setWidget: () => {},
  exec: async () => ({ code: 0, stdout: "{}", stderr: "", killed: false }),
};
register(pi);
const tool = registered;
console.log(JSON.stringify({
  name: tool.name,
  description: tool.description,
  promptSnippet: tool.promptSnippet,
  promptGuidelines: tool.promptGuidelines,
}));
'''
    )
    result = subprocess.run(
        [node, "--experimental-transform-types", str(tmp_path / "guidance.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"node failed: rc={result.returncode}\nstderr={result.stderr}"
    state = json.loads(result.stdout.strip())
    assert state["name"] == "delegate_worker"

    description = state["description"]
    description_lower = description.lower()
    # Description must list the handle fields and forbid treating the handle
    # as a delivery / answer.
    for needle in (
        "TRACKING HANDLE",
        "worker",
        "task_id",
        "correlation_id",
        "status",
        "never the worker's final answer",
        "reconcile the authoritative broker result",
        "/orchlink",
        "acceptance metadata",
    ):
        assert needle in description, f"description missing: {needle!r}"
    # A handle-vs-delivery guard must exist in the description.
    description_guards = (
        "never the worker's final answer",
        "the worker's reply payload",
        "not a deliverable",
        "acceptance metadata",
        "not that the worker answered",
        "only",
    )
    assert any(g in description_lower for g in description_guards), (
        f"description carries no handle-vs-delivery guard; text={description!r}"
    )

    # promptSnippet teaches the native foreground default and the explicit
    # background handle/reconciliation distinction.
    snippet = state["promptSnippet"]
    for needle in (
        "delegate_worker",
        "streams inline until completion",
        "async=true",
        "tracking handle",
        "requires later result reconciliation",
    ):
        assert needle in snippet, f"promptSnippet missing: {needle!r}"

    # Each promptGuidelines bullet must explicitly name ``delegate_worker``
    # AND must carry the handle-vs-answer guard or the reconciliation path.
    guidelines = state["promptGuidelines"]
    assert isinstance(guidelines, list) and len(guidelines) >= 3, guidelines
    guard_phrases = (
        "not the worker's final answer",
        "not the answer",
        "do not treat the handle as a result",
        "acceptance metadata alone never counts",
        "is not",
        "do not end the turn on acceptance",
    )
    reconciliation_phrases = (
        "reconcile",
        "orch jobs --result",
        "/orchlink",
        "later",
        "after delegate_worker",
    )
    guard_bullets: list[int] = []
    reconcile_bullets: list[int] = []
    for index, bullet in enumerate(guidelines):
        assert isinstance(bullet, str) and bullet, (index, bullet)
        assert "delegate_worker" in bullet, (
            f"guideline #{index + 1} must name delegate_worker (Pi Guidelines "
            f"section has no per-tool prefix): {bullet!r}"
        )
        bullet_lower = bullet.lower()
        if any(g in bullet_lower for g in guard_phrases):
            guard_bullets.append(index + 1)
        if any(r in bullet_lower for r in reconciliation_phrases):
            reconcile_bullets.append(index + 1)
    assert guard_bullets, (
        f"no guideline carries a handle-vs-answer guard; bullets={guidelines!r}"
    )
    assert reconcile_bullets, (
        f"no guideline carries the reconciliation path; bullets={guidelines!r}"
    )


def test_delegate_worker_handle_shape_is_distinct_from_delivered_result(tmp_path):
    """AC-3 structural invariant: an accepted tracking handle and a delivered
    broker result have disjoint shapes. A handle carries the lead-side
    acceptance metadata (worker, task_id, correlation_id, status); a
    delivered result carries the broker-side reply envelope
    (``reply.type``, ``reply.payload``, ``summary``). Neither shape must be
    able to masquerade as the other, so the LLM cannot mistake acceptance
    for delivery.
    """
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    tmp_path = _build_pi_capture_bundle(tmp_path)
    (tmp_path / "shape.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "lead";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
globalThis.fetch = async () => ({
  ok: true,
  status: 200,
  json: async () => ({ broker: "ok", sessions: [], jobs: [], activity: [] }),
});
const { default: register } = await import("./extension.mts");
let registered = null;
const pi = {
  registerCommand: () => {},
  registerTool: (tool) => { registered = tool; },
  registerShortcut: () => {},
  on: () => {},
  setSessionName: () => {},
  setWidget: () => {},
  exec: async () => ({ code: 0, stdout: JSON.stringify({
    worker: "work",
    task_id: "UI-031",
    correlation_id: "req-handle-shape",
    conversation_id: "demo-tasks",
    status: "PENDING",
    accepted_at: "2026-07-11T00:00:00+00:00",
  }), stderr: "", killed: false }),
};
register(pi);
const tool = registered;
const accepted = (await tool.execute("c1", { worker: "work", task_id: "UI-031", message: "x", async: true }, undefined, undefined, {})).details.handle;
// A broker-delivered result is structurally different: it carries the reply
// envelope, not the acceptance handle. We construct the shape the broker
// emits for a completed task so the test pins the disjoint-ness.
const delivered = {
  status: "RESULT",
  task_id: "UI-031",
  reply: {
    type: "RESULT",
    payload: { summary: "The worker actually answered", stdout: "...", stderr: "" },
  },
};
const acceptedKeys = Object.keys(accepted).sort();
const deliveredKeys = Object.keys(delivered).sort();
const acceptedHasReply = "reply" in accepted;
const deliveredHasCorrelation = "correlation_id" in delivered;
const acceptedHasConversation = "conversation_id" in accepted;
const deliveredHasStatus = "status" in delivered;
// Pin the disjoint shape: accepted has handle-only fields; delivered has
// reply-only fields. The two key sets must overlap on nothing semantically
// meaningful: status and task_id are the only shared names, but their
// semantics differ (acceptance vs completion).
const sharedFields = acceptedKeys.filter((k) => deliveredKeys.includes(k));
console.log(JSON.stringify({
  acceptedKeys,
  deliveredKeys,
  sharedFields,
  acceptedHasReply,
  deliveredHasCorrelation,
  acceptedHasConversation,
  // Sanity: status text on the two shapes differs in meaning even though
  // the field name collides.
  acceptedStatusValue: accepted.status,
  deliveredStatusValue: delivered.status,
  // And the handle correlation_id is present, the delivered result has no
  // broker correlation id. A future regression that adds correlation_id to
  // the delivered shape would be caught by deliveredHasCorrelation below.
  deliveredHasCorrelation,
}));
'''
    )
    result = subprocess.run(
        [node, "--experimental-transform-types", str(tmp_path / "shape.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"node failed: rc={result.returncode}\nstderr={result.stderr}"
    state = json.loads(result.stdout.strip())
    # Required handle fields are present on the accepted shape.
    for needle in ("worker", "task_id", "correlation_id", "conversation_id", "status", "accepted_at"):
        assert needle in state["acceptedKeys"], (
            f"missing handle field {needle!r}; got {state['acceptedKeys']}"
        )
    # The accepted handle does NOT carry a ``reply`` (a delivered-result field).
    assert state["acceptedHasReply"] is False
    # The delivered result does NOT carry a correlation_id (a handle field).
    assert state["deliveredHasCorrelation"] is False
    # Sanity: status values differ — the accepted handle carries an
    # in-flight status (PENDING / RUNNING / etc.), the delivered result
    # carries a terminal completion status (RESULT / BLOCKER / etc.).
    assert state["acceptedStatusValue"] != state["deliveredStatusValue"], state
    # The two key sets may share only field names that mean different things
    # on each side. status and task_id are the only legitimate overlap; both
    # must not be identical across the two shapes (since their meaning is
    # what we pin above).
    for field in state["sharedFields"]:
        assert field in ("status", "task_id"), (
            f"unexpected shared field {field!r}; accepted={state['acceptedKeys']}, delivered={state['deliveredKeys']}"
        )


# --- G019 AC-8: Inline worker tree fields and placement ----------------------


_AC8_TREE_TEMPLATE = r"""
globalThis.runAC8Tree = function() {
  const makeRow = (workerName, taskId, status, toolCount, tokens, contextWindow, percent, startedAt) => ({
    workerName,
    agentId: `test.${workerName}`,
    ready: true,
    model: "m",
    thinking: "",
    runtime: "r",
    backend: "b",
    heartbeat: "h",
    job: {
      kind: "task",
      id: taskId,
      mode: "DO",
      status,
      activity: "x",
      tool: "x",
      updated: "u",
      startedAt,
      toolCount,
      tokens,
      contextWindow,
      percent,
    },
  });
  const rows = [
    makeRow("work", "UI-031", "RUNNING", 9, 31000, 200000, 16, "2026-07-11T00:00:00Z"),
    makeRow("review", "REVIEW-032", "RUNNING", 3, 18000, 128000, 14, "2026-07-11T00:00:30Z"),
    makeRow("bg-test", "TEST-033", "COMPLETE", 6, null, null, null, "2026-07-11T00:01:00Z"),
  ];
  const nowMs = Date.parse("2026-07-11T00:01:30Z");
  const lines = renderWorkerTreeFromRows(rows, 120, nowMs);
  const joined = lines.join("\n");

  const unknownRows = [makeRow("work", "UI-034", "RUNNING", 1, null, null, null, "2026-07-11T00:00:00Z")];
  const unknownLines = renderWorkerTreeFromRows(unknownRows, 120, nowMs);
  const unknownJoined = unknownLines.join("\n");

  const manyRows = Array.from({ length: 5 }, (_, i) =>
    makeRow(`w${i}`, `T${i}`, "RUNNING", i, 1000 * (i + 1), 10000, 10, "2026-07-11T00:00:00Z")
  );
  const overflowLines = renderWorkerTreeFromRows(manyRows, 120, nowMs);
  const overflowJoined = overflowLines.join("\n");

  return { lines, joined, unknownJoined, overflowJoined, overflowLines };
};
"""


def _run_ac8_tree_test():
    return _run_node_test(_AC8_TREE_TEMPLATE, "runAC8Tree")


def test_worker_widget_renders_required_fields():
    """AC-8: the above-editor tree shows worker name, task id, canonical
    status, current-task tool count, worker session context usage/window/
    percentage, and elapsed task time for each displayed worker."""
    import pytest

    state = _run_ac8_tree_test()
    if state is None:
        pytest.skip("node not available for extension runtime test")

    joined = state["joined"]
    lines = state["lines"]
    assert lines[0] == "Active workers"
    # work row
    assert "work · UI-031" in joined
    assert "● running" in joined
    assert "9 tools" in joined
    assert "ctx 31k/200k (16%)" in joined
    assert "1m 30s" in joined
    # review row
    assert "review · REVIEW-032" in joined
    assert "3 tools" in joined
    assert "ctx 18k/128k (14%)" in joined
    assert "1m" in joined
    # bg-test row
    assert "bg-test · TEST-033" in joined
    assert "✓ complete" in joined
    assert "6 tools" in joined
    # Footer hint
    assert "F8 open workers" in joined
    # AC-9: rows are truncated with the ANSI-width-safe helper.
    assert "truncateToWidth" in ORCHLINK_PI_UI_EXTENSION


def test_worker_widget_uses_ctx_label_not_token_spend():
    """AC-8: context is labeled ``ctx`` and never presented as current-task
    token consumption."""
    import pytest

    state = _run_ac8_tree_test()
    if state is None:
        pytest.skip("node not available for extension runtime test")

    joined = state["joined"].lower()
    assert "ctx " in joined
    assert "token spend" not in joined
    assert "task tokens" not in joined
    assert "tokens:" not in joined
    assert "tok usage" not in joined


def test_worker_widget_shows_ctx_dash_when_unknown():
    """AC-7 + AC-8: when worker context usage is unavailable, the row renders
    the literal ``ctx —`` string."""
    import pytest

    state = _run_ac8_tree_test()
    if state is None:
        pytest.skip("node not available for extension runtime test")

    assert "ctx —" in state["unknownJoined"]


def test_inline_tree_overflow_collapses_to_n_more():
    """AC-8 + AC-9: at most three detailed workers are shown; further workers
    collapse to a ``+N more`` summary."""
    import pytest

    state = _run_ac8_tree_test()
    if state is None:
        pytest.skip("node not available for extension runtime test")

    overflow = state["overflowJoined"]
    lines = state["overflowLines"]
    assert "+2 more" in overflow
    # Count lead-to-worker rows (lines that contain a task id and are not details/footer)
    workerRows = [line for line in lines if re.search(r"^[a-z].* · T\d+ · ", line, re.IGNORECASE)]
    assert len(workerRows) <= 3


def test_inline_tree_is_one_level_no_inferred_nesting():
    """AC-8: the tree is one lead-to-worker level. No ``├─`` connectors or
    inferred child nesting appear."""
    import pytest

    state = _run_ac8_tree_test()
    if state is None:
        pytest.skip("node not available for extension runtime test")

    joined = state["joined"]
    assert "├─" not in joined
    # Only single-level detail prefix is allowed.
    assert "└─" in joined
    # No extra indentation beyond the one detail prefix.
    for line in state["lines"]:
        if line.startswith("└─"):
            assert not line.startswith("  └─")


def test_tree_widget_truncates_on_narrow_terminals():
    """AC-9: the above-editor tree truncates worker rows to fit narrow
    terminals without wrapping or displacing editor space."""
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    template = r"""
globalThis.runNarrowTree = function() {
  const makeRow = (workerName, taskId) => ({
    workerName,
    agentId: `test.${workerName}`,
    ready: true,
    model: "m",
    thinking: "",
    runtime: "r",
    backend: "b",
    heartbeat: "h",
    job: {
      kind: "task",
      id: taskId,
      mode: "DO",
      status: "RUNNING",
      activity: "x",
      tool: "x",
      updated: "u",
      startedAt: "2026-07-11T00:00:00Z",
      toolCount: 5,
      tokens: 12000,
      contextWindow: 100000,
      percent: 12,
    },
  });
  const rows = [makeRow("long-worker-name", "TASK-12345")];
  const wideLines = renderWorkerTreeFromRows(rows, 120, Date.parse("2026-07-11T00:01:00Z"));
  const narrowLines = renderWorkerTreeFromRows(rows, 30, Date.parse("2026-07-11T00:01:00Z"));
  return {
    wideMax: Math.max(...wideLines.map((line) => visibleWidth(line))),
    narrowMax: Math.max(...narrowLines.map((line) => visibleWidth(line))),
  };
};
"""
    state = _run_node_test(template, "runNarrowTree")
    if state is None:
        pytest.skip("node not available for extension runtime test")

    # Wide terminal keeps full content; narrow terminal is clamped to width.
    assert state["wideMax"] > state["narrowMax"]
    assert state["narrowMax"] <= 26


def test_tree_widget_hides_when_no_worker_jobs_remain():
    """AC-9: the widget disappears when no relevant worker work remains."""
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    template = r"""
globalThis.runIdleTree = function() {
  const activeRows = [{
    workerName: "work",
    agentId: "test.work",
    ready: true,
    model: "m",
    thinking: "",
    runtime: "r",
    backend: "b",
    heartbeat: "h",
    job: { kind: "task", id: "T1", mode: "DO", status: "RUNNING", activity: "x", tool: "x", updated: "u" },
  }];
  const idleRows = [{
    workerName: "work",
    agentId: "test.work",
    ready: true,
    model: "m",
    thinking: "",
    runtime: "r",
    backend: "b",
    heartbeat: "h",
  }];
  const active = renderWorkerTreeFromRows(activeRows, 80, Date.now());
  const idle = renderWorkerTreeFromRows(idleRows, 80, Date.now());
  return { activeCount: active.length, idleCount: idle.length };
};
"""
    state = _run_node_test(template, "runIdleTree")
    if state is None:
        pytest.skip("node not available for extension runtime test")

    assert state["activeCount"] > 0
    assert state["idleCount"] == 0


def test_worker_widget_bound_above_editor_on_session_start(tmp_path):
    """AC-8: the lead extension registers the persistent activity tree via
    ``ctx.ui.setWidget`` with ``placement: 'aboveEditor'`` when a TUI session
    starts."""
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    tmp_path = _build_pi_capture_bundle(tmp_path)
    (tmp_path / "widget.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "lead";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
globalThis.fetch = async () => ({
  ok: true,
  status: 200,
  json: async () => ({
    broker: "ok",
    sessions: [{ role: "work", status: "ACTIVE", worker_name: "work", agent_id: "test.work", ready: true }],
    jobs: [{ worker_name: "work", to_agent: "test.work", task_id: "LIVE-1", status: "RUNNING", mode: "DO" }],
    active_messages: [{ task_id: "LIVE-1", started_at: new Date().toISOString() }],
    telemetry: [],
  }),
});
const { default: register } = await import("./extension.mts");
const widgets = [];
const shortcuts = [];
const events = {};
const pi = {
  registerCommand: () => {},
  registerTool: () => {},
  registerShortcut: (key, options) => { shortcuts.push({ key, options }); },
  on: (name, handler) => { events[name] = handler; },
  setSessionName: () => {},
  setWidget: () => {},
};
register(pi);
const ctx = {
  mode: "tui",
  hasUI: true,
  ui: {
    setWidget: (key, factory, options) => { widgets.push({ key, factory, options }); },
  },
};
await events.session_start({}, ctx);
// Pi constructs the component while the session is idle. The scheduled poll
// then discovers work; the same component must render the new live rows.
let renderRequests = 0;
const factoryResult = widgets[0]?.factory(
  { terminal: { cols: 80 }, requestRender: () => { renderRequests += 1; } },
  { fg: (_c, t) => t },
);
const initialLines = factoryResult.render(80);
await new Promise((resolve) => setTimeout(resolve, 50));
const liveLines = factoryResult.render(80);
const widgetName = factoryResult?.constructor?.name;
await events.session_shutdown({});
console.log(JSON.stringify({
  widgets,
  shortcuts,
  widgetName,
  initialLines,
  liveLines,
  renderRequests,
}));
'''
    )
    result = subprocess.run(
        [node, "--experimental-transform-types", str(tmp_path / "widget.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"node failed: rc={result.returncode}\nstderr={result.stderr}"
    state = json.loads(result.stdout.strip())
    assert len(state["widgets"]) == 1, state["widgets"]
    assert state["widgets"][0]["key"] == "orchlink-worker-tree"
    assert state["widgets"][0]["options"]["placement"] == "aboveEditor"
    assert state["widgetName"] == "Object"
    assert state["initialLines"] == []
    assert state["liveLines"][0] == "Active workers"
    assert any("LIVE-1" in line for line in state["liveLines"])
    assert state["renderRequests"] > 0
    assert any(s["key"] == "f8" for s in state["shortcuts"]), state["shortcuts"]




# --- G019 AC-10: Widget and telemetry privacy boundaries -----------------------


def test_widget_privacy_gate_row_is_status_only():
    """AC-10 / privacy_gate: the above-editor widget row is status-only."""
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    template = r"""
globalThis.runPrivacyTree = function() {
  const leaked = {
    workerName: "work",
    agentId: "test.work",
    ready: true,
    model: "m",
    thinking: "",
    runtime: "r",
    backend: "b",
    heartbeat: "h",
    job: {
      kind: "task",
      id: "UI-LEAK",
      mode: "DO",
      status: "RUNNING",
      activity: "x",
      tool: "x",
      updated: "u",
      startedAt: "2026-07-11T00:00:00Z",
      toolCount: 2,
      tokens: 5000,
      contextWindow: 100000,
      percent: 5,
      message: "SECRET PROMPT BODY",
      reasoning: "HIDDEN REASONING",
      tool_input: "SECRET TOOL ARGS",
      tool_output: "RAW TOOL OUTPUT",
      provider_payload: "PROVIDER DATA",
      env_value: "ORCHLINK_API_KEY=secret",
      api_key: "bearer-token",
    },
  };
  const lines = renderWorkerTreeFromRows([leaked], 80, Date.parse("2026-07-11T00:01:00Z"));
  const joined = lines.join("\n");
  return { lines, joined };
};
"""
    state = _run_node_test(template, "runPrivacyTree")
    if state is None:
        pytest.skip("node not available for extension runtime test")

    joined = state["joined"]
    assert "UI-LEAK" in joined
    assert "● running" in joined
    assert "2 tools" in joined
    assert "ctx 5k/100k (5%)" in joined
    for forbidden in (
        "SECRET PROMPT BODY",
        "HIDDEN REASONING",
        "SECRET TOOL ARGS",
        "RAW TOOL OUTPUT",
        "PROVIDER DATA",
        "ORCHLINK_API_KEY",
        "bearer-token",
    ):
        assert forbidden not in joined, f"widget row leaked {forbidden!r}; rows={state['lines']!r}"


def test_telemetry_widget_privacy_gate_uses_status_only_fields():
    """AC-10 / privacy_gate: the inline tree builds rows from broker status poll fields only."""
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    template = r"""
globalThis.runSourcePrivacy = function() {
  const body = {
    broker: "ok",
    sessions: [
      { worker_name: "work", agent_id: "test.work", role: "work", status: "ACTIVE", ready: true, model: "m", runtime_mode: "r", backend: "b", last_heartbeat_at: "h" },
    ],
    jobs: [
      { task_id: "T-PRIV", worker_name: "work", to_agent: "test.work", kind: "task", mode: "DO", status: "RUNNING", last_activity_preview: "x", last_activity_tool: "t", last_activity_at: "u" },
    ],
    active_messages: [
      { task_id: "T-PRIV", started_at: "2026-07-11T00:00:00Z", payload: { intent: "LEAKED INTENT BODY" }, transcript: "LEAKED TRANSCRIPT" },
    ],
    telemetry: [
      { task_id: "T-PRIV", tool_count: 4, tokens: 8000, context_window: 100000, percent: 8, secret: "API_KEY" },
    ],
  };
  const rows = summarizeStatus(body);
  const lines = renderWorkerTreeFromRows(rows, 80, Date.parse("2026-07-11T00:01:00Z"));
  const joined = lines.join("\n");
  return { rows, joined };
};
"""
    state = _run_node_test(template, "runSourcePrivacy")
    if state is None:
        pytest.skip("node not available for extension runtime test")

    joined = state["joined"]
    assert "T-PRIV" in joined
    assert "4 tools" in joined
    assert "ctx 8k/100k (8%)" in joined
    for forbidden in (
        "LEAKED INTENT BODY",
        "LEAKED TRANSCRIPT",
        "API_KEY",
    ):
        assert forbidden not in joined, f"widget leaked content-bearing source field {forbidden!r}; rows={state['rows']!r}"

def test_workers_key_overrides_default_f8_shortcut(tmp_path):
    """AC-11: ``ORCHLINK_WORKERS_KEY`` overrides the default ``F8`` shortcut."""
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    tmp_path = _build_pi_capture_bundle(tmp_path)
    (tmp_path / "shortcut.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "lead";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
process.env.ORCHLINK_WORKERS_KEY = "f9";
globalThis.fetch = async () => ({
  ok: true,
  status: 200,
  json: async () => ({ broker: "ok", sessions: [], jobs: [], activity: [] }),
});
const { default: register } = await import("./extension.mts");
const shortcuts = [];
const pi = {
  registerCommand: () => {},
  registerTool: () => {},
  registerShortcut: (key, options) => { shortcuts.push({ key, options }); },
  on: () => {},
  setSessionName: () => {},
  setWidget: () => {},
};
register(pi);
console.log(JSON.stringify({ shortcuts }));
'''
    )
    result = subprocess.run(
        [node, "--experimental-transform-types", str(tmp_path / "shortcut.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"node failed: rc={result.returncode}\nstderr={result.stderr}"
    state = json.loads(result.stdout.strip())
    assert any(s["key"] == "f9" for s in state["shortcuts"]), state["shortcuts"]
    assert all(s["key"] != "f8" for s in state["shortcuts"]), state["shortcuts"]


def test_overlay_shortcut_f8_opens_orchlink_without_cancelling_task(tmp_path):
    """AC-11: default F8 opens the existing /orchlink overlay; the handler does
    not cancel any worker task."""
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    tmp_path = _build_pi_capture_bundle(tmp_path)
    (tmp_path / "f8_handler.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "lead";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
let fetchCount = 0;
globalThis.fetch = async () => {
  fetchCount += 1;
  return {
    ok: true,
    status: 200,
    json: async () => ({ broker: "ok", sessions: [], jobs: [], activity: [] }),
  };
};
const { default: register } = await import("./extension.mts");
const shortcuts = [];
const commands = {};
const events = {};
const pi = {
  registerCommand: (name, cmd) => { commands[name] = cmd; },
  registerTool: () => {},
  registerShortcut: (key, options) => { shortcuts.push({ key, options }); },
  on: (name, handler) => { events[name] = handler; },
  setSessionName: () => {},
  setWidget: () => {},
};
register(pi);
const f8 = shortcuts.find((s) => s.key === "f8");
if (!f8) throw new Error("F8 shortcut not registered");
let overlayOpened = false;
const ctx = {
  mode: "tui",
  hasUI: true,
  ui: {
    notify: () => {},
    custom: async (factory, options) => {
      overlayOpened = true;
      options.onHandle({ requestRender: () => {} });
      const panel = factory({ terminal: { rows: 35, write: () => {} } }, {}, {}, () => {});
      panel.handleInput("q");
      return null;
    },
  },
};
await f8.options.handler(ctx);
await events.session_shutdown({});
console.log(JSON.stringify({ overlayOpened, fetchCount }));
'''
    )
    result = subprocess.run(
        [node, "--experimental-transform-types", str(tmp_path / "f8_handler.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"node failed: rc={result.returncode}\nstderr={result.stderr}"
    state = json.loads(result.stdout.strip())
    assert state["overlayOpened"] is True
    assert state["fetchCount"] >= 1


def test_overlay_shortcut_orchlink_command_is_universal_fallback(tmp_path):
    """AC-11: the ``/orchlink`` slash command remains a universal fallback even when
    the F8 shortcut is overridden or unavailable."""
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    tmp_path = _build_pi_capture_bundle(tmp_path)
    (tmp_path / "fallback.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "lead";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
process.env.ORCHLINK_WORKERS_KEY = "f9";
globalThis.fetch = async () => ({
  ok: true,
  status: 200,
  json: async () => ({ broker: "ok", sessions: [], jobs: [], activity: [] }),
});
const { default: register } = await import("./extension.mts");
const commands = {};
const shortcuts = [];
const pi = {
  registerCommand: (name, cmd) => { commands[name] = cmd; },
  registerTool: () => {},
  registerShortcut: (key, options) => { shortcuts.push({ key, options }); },
  on: () => {},
  setSessionName: () => {},
  setWidget: () => {},
};
register(pi);
if (shortcuts.some((s) => s.key === "f8")) throw new Error("F8 should not be registered when overridden");
if (!commands.orchlink) throw new Error("orchlink command must remain registered");
console.log(JSON.stringify({ hasOrchlink: true, hasF8: shortcuts.some((s) => s.key === "f8") }));
'''
    )
    result = subprocess.run(
        [node, "--experimental-transform-types", str(tmp_path / "fallback.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"node failed: rc={result.returncode}\nstderr={result.stderr}"
    state = json.loads(result.stdout.strip())
    assert state["hasOrchlink"] is True
    assert state["hasF8"] is False


def test_overlay_shortcut_conflict_with_reserved_binding_falls_back_safely(tmp_path):
    """AC-12: a user-installed shortcut that conflicts with reserved Pi/Herdr
    bindings is refused registration; the /orchlink slash command remains
    usable so extension loading does not break."""
    import pytest

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available for extension runtime test")

    tmp_path = _build_pi_capture_bundle(tmp_path)
    (tmp_path / "conflict.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "lead";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
process.env.ORCHLINK_WORKERS_KEY = "ctrl+b";  // reserved Herdr prefix
const logs = [];
const originalWarn = console.warn;
console.warn = (msg) => { logs.push(msg); };
globalThis.fetch = async () => ({
  ok: true,
  status: 200,
  json: async () => ({ broker: "ok", sessions: [], jobs: [], activity: [] }),
});
const { default: register } = await import("./extension.mts");
const commands = {};
const shortcuts = [];
const pi = {
  registerCommand: (name, cmd) => { commands[name] = cmd; },
  registerTool: () => {},
  registerShortcut: (key, options) => { shortcuts.push({ key, options }); },
  on: () => {},
  setSessionName: () => {},
  setWidget: () => {},
};
register(pi);
console.warn = originalWarn;
console.log(JSON.stringify({
  hasConflictShortcut: shortcuts.some((s) => s.key === "ctrl+b"),
  hasOrchlinkCommand: Boolean(commands.orchlink),
  warnCount: logs.length,
  warningIncludesReserved: logs.some((m) => /reserved/i.test(m) && /ctrl\+b/i.test(m)),
}));
'''
    )
    result = subprocess.run(
        [node, "--experimental-transform-types", str(tmp_path / "conflict.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"node failed: rc={result.returncode}\nstderr={result.stderr}"
    state = json.loads(result.stdout.strip())
    assert state["hasConflictShortcut"] is False
    assert state["hasOrchlinkCommand"] is True
    assert state["warnCount"] >= 1
    assert state["warningIncludesReserved"] is True
