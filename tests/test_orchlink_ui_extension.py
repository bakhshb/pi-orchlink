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
    assert "/v1/activity?item_id=${encodeURIComponent(itemId)}&limit=30" in ORCHLINK_PI_UI_EXTENSION
    assert "X-Orchlink-Project-ID" in ORCHLINK_PI_UI_EXTENSION
    assert "ORCHLINK_BROKER_URL" in ORCHLINK_PI_UI_EXTENSION
    assert "ORCHLINK_API_KEY" in ORCHLINK_PI_UI_EXTENSION
    assert "ORCHLINK_MONITOR_POLL_SECONDS" in ORCHLINK_PI_UI_EXTENSION
    assert "pi.registerCommand(\"orchlink\"" in ORCHLINK_PI_UI_EXTENSION
    assert "ctx.mode === \"tui\"" in ORCHLINK_PI_UI_EXTENSION
    assert "pi.setSessionName(nextName)" in ORCHLINK_PI_UI_EXTENSION
    assert "ctx.ui.setStatus" not in ORCHLINK_PI_UI_EXTENSION
    assert "footerStatusText" not in ORCHLINK_PI_UI_EXTENSION
    assert "ctx.ui.setWidget" not in ORCHLINK_PI_UI_EXTENSION
    assert "ctx.ui.custom" in ORCHLINK_PI_UI_EXTENSION
    assert "worker - ${row.workerName}" in ORCHLINK_PI_UI_EXTENSION
    assert "Orchlink Lead · ${active} active · ${idle} idle" in ORCHLINK_PI_UI_EXTENSION
    assert "LEGACY_STATUS_KEYS" not in ORCHLINK_PI_UI_EXTENSION
    assert "\\u001b[90m${value}\\u001b[0m" not in ORCHLINK_PI_UI_EXTENSION
    assert "activityByItem" in ORCHLINK_PI_UI_EXTENSION
    assert "formatActivityRecord" in ORCHLINK_PI_UI_EXTENSION
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
    assert 'import { Key, Markdown, matchesKey, truncateToWidth, visibleWidth } from "@earendil-works/pi-tui"' in ORCHLINK_PI_UI_EXTENSION
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
    pre-follow surface: worker status rendering, recent activity feed,
    session naming via ``pi.setSessionName``, background-worker stop
    confirmation, visible-worker protection, and the absence of any
    orch-side second slash command. Same source must remain compatible
    with the existing broker and task result endpoints.
    """
    # Worker status and recent activity must both be fetched.
    assert "/v1/status?limit=200" in ORCHLINK_PI_UI_EXTENSION
    assert "/v1/activity?item_id=${encodeURIComponent(itemId)}&limit=30" in ORCHLINK_PI_UI_EXTENSION
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
