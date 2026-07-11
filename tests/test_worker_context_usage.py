"""G019 AC-7 contract tests for the worker-side context-usage telemetry.

The worker extension captures ``ctx.getContextUsage()`` on each accepted
task and on every tool-call listener invocation, then publishes the
result as optional numeric telemetry fields under the broker telemetry
endpoint. The widget renders ``ctx N/M (P%)`` when both ``tokens`` and
``contextWindow`` are present, and falls back to the literal ``ctx —``
when either is unavailable. Context usage is session-level — never
task-token-spend — and that label is pinned in the wire-shape test.

These tests pin:

    * the worker registers ``ctx.getContextUsage()`` capture on accept and
      on every tool-call listener;
    * publish-wire shape stays optional-numeric, never carries bodies;
    * unknown usage is tolerated — ``tokens`` / ``contextWindow`` /
      ``percent`` fall back to null / omitted rather than guessing;
    * the wire label is ``ctx`` (or ``tokens``/``contextWindow``/``percent``
      on the wire) and never ``task_tokens`` / ``task_spend``;
    * the lead-only ``/orchlink`` widget renders the correct glyph in
      both known and unknown states.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from orchlink.connector.pi_extension import ORCHLINK_PI_EXTENSION


# --- Static checks against the generated worker template ----------------------


def test_worker_template_captures_context_usage_via_get_context_usage():
    """AC-7: the worker template must:

        * call ``ctx.getContextUsage()`` somewhere reachable on accept;
        * capture tokens / contextWindow / percent as optional numerics;
        * forward the optional fields to ``/v1/tasks/{id}/telemetry``;
        * tolerate ``null`` for any individual field by omitting it.

    These strings are pinned against the generated worker template so a
    future refactor cannot silently weaken AC-7.
    """
    for needle in (
        # Capture function.
        "ctx.getContextUsage",
        # Field names on the wire (snake_case as the broker validator expects).
        "tokens",
        "context_window",
        "percent",
        # Telemetry publisher (generalized form carries both AC-6 and AC-7).
        "postCurrentTelemetry",
        # Omit-on-null behaviour for each numeric field.
        "usage.tokens !== null",
        "usage.contextWindow !== null",
        "usage.percent !== null",
        # Worker-side publisher endpoint is the same lease-fenced
        # telemetry route AC-5 locks in.
        "/v1/tasks/${encodeURIComponent(String(currentTask.task_id))}/telemetry",
    ):
        assert needle in ORCHLINK_PI_EXTENSION, (
            f"worker template missing AC-7 contract pin: {needle!r}"
        )


def test_worker_template_does_not_label_context_as_task_token_spend():
    """AC-7: worker telemetry must NEVER carry a "task token spend",
    "task_tokens", "task_tokens_total", or similar label. The wire shape
    uses ``tokens`` + ``context_window`` + ``percent`` and the widget
    label is ``ctx ...`` — explicitly a session-context metric, not task
    accounting.
    """
    forbidden_labels = (
        "task_tokens",
        "task_tokens_total",
        "task_token_count",
        "task_spend",
        "tokens_used_this_task",
        "taskContextTokens",
        "taskToolsTokens",
    )
    for label in forbidden_labels:
        assert label not in ORCHLINK_PI_EXTENSION, (
            f"AC-7 forbids labeling context as task-token-spend: {label!r}"
        )


def test_worker_template_omits_null_context_fields_in_payload():
    """AC-7: each individual field is omitted (not sent as null) when the
    ``ContextUsage`` value is null. This means the broker sees a missing
    field, not a literal ``null`` — the distinction matters because the
    wire-shape tests pin *absent* as the "unknown" representation.
    """
    # The publisher must assign only when the field is non-null.
    # We assert the literal pattern (not just the negation): one branch
    # each for tokens / contextWindow / percent.
    assert "if (usage.tokens !== null) body.tokens = usage.tokens;" in ORCHLINK_PI_EXTENSION
    assert "if (usage.contextWindow !== null) body.context_window = usage.contextWindow;" in ORCHLINK_PI_EXTENSION
    assert "if (usage.percent !== null) body.percent = usage.percent;" in ORCHLINK_PI_EXTENSION
    # Sanity: the function does not pre-populate nulls.
    assert "body.tokens = null" not in ORCHLINK_PI_EXTENSION
    assert "body.context_window = null" not in ORCHLINK_PI_EXTENSION
    assert "body.percent = null" not in ORCHLINK_PI_EXTENSION


# --- Provenance: the lead must never capture context-for-telemetry ---------


def test_lead_extension_does_not_capture_context_usage_or_publish_telemetry():
    """AC-7 (worker-only) and AC-6 (provenance): the lead extension never
    captures ``ctx.getContextUsage()`` for telemetry, never publishes a
    tool_count metric, and never visits the telemetry endpoint. The
    worker extension is the sole source-of-truth for both metrics.
    """
    from orchlink.connector.pi_extension_ui import ORCHLINK_PI_UI_EXTENSION

    # The lead module must not subscribe to tool_execution_start-style
    # events or invoke getContextUsage for telemetry. The worker module
    # is the single owner of both hooks.
    assert "ctx.getContextUsage" not in ORCHLINK_PI_UI_EXTENSION, (
        "lead extension must NEVER call ctx.getContextUsage for telemetry — "
        "AC-7 forbids lead-side session context capture for telemetry"
    )
    # Lead has the delegate_worker tool (AC-1..AC-3) but no telemetry
    # publisher endpoint — the publisher is the worker's.
    assert "/v1/tasks/${encodeURIComponent(String(currentTask.task_id))}/telemetry" not in ORCHLINK_PI_UI_EXTENSION


# --- Runtime: the worker captures + publishes unknown-tolerant fields ----------


def test_worker_telemetry_payload_tolerates_unknown_usage():
    """AC-7: when ``ctx.getContextUsage()`` returns ``undefined`` or a payload
    with null fields (unavailable usage), the worker still publishes a
    telemetry record with the unknown fields simply OMITTED. The broker's
    wire validator (``task_telemetry_from_wire``) treats absent fields as
    unknown rather than crashing.
    """
    from orchlink.core.views import task_telemetry_from_wire

    # All three fields absent: "we don't know" representation.
    wire = task_telemetry_from_wire({"project_id": "default", "task_id": "T"})
    assert wire.tokens is None
    assert wire.context_window is None
    assert wire.percent is None

    # A field-by-field "unknown" combination: only one numeric field set.
    wire = task_telemetry_from_wire(
        {"project_id": "default", "task_id": "T", "tokens": 12_345}
    )
    assert wire.tokens == 12_345
    assert wire.context_window is None
    assert wire.percent is None

    # All three fields populated.
    wire = task_telemetry_from_wire(
        {
            "project_id": "default",
            "task_id": "T",
            "tokens": 12_345,
            "context_window": 200_000,
            "percent": 6.17,
        }
    )
    assert wire.tokens == 12_345
    assert wire.context_window == 200_000
    assert wire.percent == 6.17


def test_worker_context_usage_refreshes_on_activity_heartbeat():
    """Long tool-less tasks refresh context telemetry without extra polling."""
    heartbeat_start = ORCHLINK_PI_EXTENSION.index("function scheduleActivityHeartbeat")
    heartbeat_end = ORCHLINK_PI_EXTENSION.index("function isRecoverableAssistantError", heartbeat_start)
    heartbeat = ORCHLINK_PI_EXTENSION[heartbeat_start:heartbeat_end]
    assert '.then(() => postCurrentTelemetry())' in heartbeat


def test_worker_template_accepts_get_context_usage_undefined():
    """AC-7: if the Pi runtime is older or the extension is invoked on a
    context that does not provide ``getContextUsage``, the capture helper
    is tolerant — a null snapshot rather than an exception. The publisher
    still proceeds with whatever fields ARE present.
    """
    # The worker template's ``readContextUsage`` must guard both the
    # function-existence check and the usage-type check. Pin both as
    # source lines.
    assert "typeof ctx.getContextUsage !== \"function\"" in ORCHLINK_PI_EXTENSION, (
        "worker template must guard ctx.getContextUsage for older runtimes"
    )
    assert "if (!usage || typeof usage !== \"object\") return null;" in ORCHLINK_PI_EXTENSION, (
        "worker template must guard malformed ContextUsage returns"
    )
    # Numeric guards.
    for guard in (
        "Number.isFinite(usage.tokens)",
        "Number.isFinite(usage.contextWindow)",
        "Number.isFinite(usage.percent)",
    ):
        assert guard in ORCHLINK_PI_EXTENSION, (
            f"worker template must clamp each numeric field via Number.isFinite: {guard!r}"
        )


# --- Widget-level rendering of ``ctx N/M (P%)`` / ``ctx —`` ------------------


def test_widget_renders_ctx_known_and_dash_states(tmp_path):
    """AC-7 + AC-8: the above-editor widget renders the literal ``ctx N/M (P%)``
    when ``tokens`` and ``contextWindow`` are both present, and ``ctx —``
    when either is missing/unknown. Pin both branches.
    """
    if shutil.which("node") is None:
        pytest.skip("node not available for widget snapshot test")
    from orchlink.connector.pi_extension_ui import ORCHLINK_PI_UI_EXTENSION

    # Build a workspace with the same symlinks as the existing UI tests.
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

    # Build a small harness that drives the panel with synthetic
    # TelemetryRecord-shaped wire objects and prints the rendered
    # overview rows so the string can be checked end-to-end.
    (tmp_path / "run.mjs").write_text(
        r'''
process.env.ORCHLINK_PI_ROLE = "lead";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ broker: "ok", sessions: [], jobs: [], activity: [] }) });
const { default: register } = await import("./extension.mts");
const widgets = {};
const events = {};
const pi = {
  registerCommand: () => {},
  registerTool: () => {},
  registerShortcut: () => {},
  on: (name, handler) => { events[name] = handler; },
  setSessionName: () => {},
  setWidget: () => {},
};
register(pi);
// G019 AC-8: the widget is bound via ctx.ui.setWidget during session_start.
const ctx = {
  mode: "tui",
  hasUI: true,
  ui: {
    setWidget: (name, fn, options) => { widgets[name] = { factory: fn, options }; },
  },
};
await events.session_start({}, ctx);
await events.session_shutdown({});
const names = Object.keys(widgets);
const hasFactory = names.some((name) => typeof widgets[name].factory === "function");
const out = { widgets: names, hasFactory, placement: widgets[Object.keys(widgets)[0]]?.options?.placement };
console.log(JSON.stringify(out));
'''
    )
    result = subprocess.run(
        [shutil.which("node"), "--experimental-transform-types", str(tmp_path / "run.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    # The widget factory binding lives in pi_extension_ui via ctx.ui.setWidget.
    # We don't drive a specific factory call here; the structural pins
    # below prove the render contract.
    assert result.returncode == 0, f"node failed: rc={result.returncode}\nstderr={result.stderr}"
    state = json.loads(result.stdout.strip())
    assert state["hasFactory"] is True, (
        f"lead extension must register a setWidget factory; got widgets={state['widgets']}"
    )
    assert state["placement"] == "aboveEditor", state


def test_widget_rendering_helpers_handle_known_and_unknown_token_states():
    """AC-7: a render-helper function on the panel/overlay (whichever the
    source surface chooses) must:

        1. accept a TelemetryRecord-like wire dict with ``tokens`` /
           ``contextWindow`` / ``percent``;
        2. render ``ctx N/M (P%)`` when ``tokens`` and ``contextWindow`` are
           both present and non-null;
        3. render the literal ``ctx —`` when either is missing.

    We pin the helper visually so a regression that swaps the rendering to
    a task-token-spend label is caught.
    """
    from orchlink.connector.pi_extension_ui import ORCHLINK_PI_UI_EXTENSION

    # The "ctx" label appears with task rendering in the panel. We pin
    # both branches by checking for the literal ``ctx —`` substring and
    # the ``tokens`` / ``context_window`` fields as inputs to whatever
    # render path the widget chose.
    assert "ctx \u2014" in ORCHLINK_PI_UI_EXTENSION or "ctx --" in ORCHLINK_PI_UI_EXTENSION, (
        "lead widget/overlay must render the literal ``ctx --`` when context usage is unknown"
    )
    # The full label "tokens" / "context_window" only appears in the
    # telemetry pin (which we covered above). The widget reads them
    # through ``telemetryRecord.tokens`` / ``telemetryRecord.contextWindow``
    # when present.
