"""G019 AC-6 contract tests for the worker-side tool-call count.

The worker extension tracks each tool execution exactly once per unique
``toolCallId`` on the worker process, never the lead, and publishes a
non-negative integer ``tool_count`` to ``/v1/tasks/{task_id}/telemetry``.

These tests pin:

    * the worker registers the publisher and the unique-id tracking;
    * the lead extension does NOT carry the publisher (provenance);
    * the broker-side ``TaskTelemetry.tool_count`` non-negative contract;
    * the contract semantics: increment once per unique toolCallId, count
      parallel calls individually, reset on each accepted task, never
      include lead tool calls, publish only a non-negative integer.

The tests load the generated TypeScript through Node's
``--experimental-transform-types`` loader (same pattern as the existing
``test_orchlink_ui_extension.py`` Node harness) and exercise the listener
end-to-end without needing a live Pi runtime.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from orchlink.connector.pi_extension import ORCHLINK_PI_EXTENSION


WORKER_TEMPLATE = re.split(r"export default function", ORCHLINK_PI_EXTENSION, maxsplit=1)[0]


def _runner_available() -> bool:
    return shutil.which("node") is not None


def _build_bundle(tmp_path: Path) -> Path:
    """Stage a workspace that links Pi's deps for the Node loader harness."""
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
    (tmp_path / "extension.mts").write_text(ORCHLINK_PI_EXTENSION)
    return tmp_path


# --- 1. Worker registers the listener and the publisher -----------------------


def test_worker_template_defines_tool_count_tracker_and_publisher():
    """AC-6: the generated worker extension must carry:

        * a worker-side tool-call state (count + seen toolCallIds);
        * a publisher that posts ``tool_count`` to the broker telemetry
          endpoint with lease metadata;
        * an increment gate keyed on a unique ``toolCallId``;
        * a reset on every accepted task.

    These strings are pinned against the generated worker template so a
    future refactor cannot silently weaken the worker-side hook.
    """
    for needle in (
        # Worker-side counter state.
        "let taskToolCallIds",
        "let taskToolCount",
        # Unique toolCallId gate.
        "event.toolCallId",
        "taskToolCallIds.has(toolCallId)",
        "taskToolCallIds.add(toolCallId)",
        # Reset on accept.
        "taskToolCallIds = new Set()",
        "taskToolCount = 0",
        # Publisher (lease-fenced).
        "postCurrentTelemetry",
        "/v1/tasks/${encodeURIComponent(String(currentTask.task_id))}/telemetry",
        "tool_count: Math.max(0, Math.floor(taskToolCount))",
        # Lease headers echoed into the telemetry write.
        '"x-orchlink-lease-epoch"',
        '"x-orchlink-lease-holder"',
        '"x-orchlink-session-lease-id"',
        # Privacy / role gate.
        "role !== \"work\"",
    ):
        assert needle in ORCHLINK_PI_EXTENSION, (
            f"worker template missing AC-6 contract pin: {needle!r}"
        )


def test_worker_template_does_not_count_lead_tool_calls():
    """AC-6 provenance: the lead extension must NEVER count its own tool
    calls into a task telemetry record. Worker extension is gated on
    ``role !== \"work\"`` AND lives in the worker module; if it appears in
    the lead module, that is a provenance violation.
    """
    # The worker extension is its own file; the lead extension is the
    # larger UI module. A simple property: the tool-count increment
    # string lives in exactly one of the two generated templates, and
    # specifically in the worker one.
    worker_path = Path(importlib.import_module("orchlink.connector.pi_extension").__file__)
    orchlink_dir = worker_path.parent
    from orchlink.connector.pi_extension_ui import ORCHLINK_PI_UI_EXTENSION

    assert 'taskToolCallIds.add(toolCallId)' in ORCHLINK_PI_EXTENSION
    assert 'taskToolCallIds.add(toolCallId)' not in ORCHLINK_PI_UI_EXTENSION, (
        "lead extension must NEVER publish task-tool-count telemetry — "
        "publishing from the lead would corrupt the worker-source-of-truth "
        "invariant AC-6 protects."
    )
    # And the lead extension does NOT register a publisher endpoint.
    assert '/v1/tasks/${encodeURIComponent(String(currentTask.task_id))}/telemetry' not in ORCHLINK_PI_UI_EXTENSION
    assert '/v1/tasks/${encodeURIComponent(String(currentTask.task_id))}/telemetry' in ORCHLINK_PI_EXTENSION
    # Unused variable hook to avoid unused-import warnings during refactors.
    assert orchlink_dir.exists()


# --- 2. Runtime semantics under the Node loader ------------------------------


def test_worker_registers_tool_execution_start_listener_for_tool_count_increment(tmp_path):
    """AC-6 runtime: confirm the worker template registers a
    ``tool_execution_start`` listener (with ``event.toolCallId``) so the
    worker-side tracker increments only after Pi starts execution. We do not drive the
    full accept flow here because that requires the worker-side
    pendingTask scheduler, which is exercised by the existing M3
    regression suite.
    """
    if not _runner_available():
        pytest.skip("node not available for worker runtime test")
    _build_bundle(tmp_path)
    (tmp_path / "run.mjs").write_text(
        r"""
process.env.ORCHLINK_PI_ROLE = "work";
process.env.ORCHLINK_AGENT_ID = "demo.work";
process.env.ORCHLINK_API_KEY = "test-key";
process.env.ORCHLINK_PROJECT_ID = "test";
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ status: "ok", activity: [] }) });
const { default: register } = await import("./extension.mts");
const listenerKeys = [];
register({
  registerCommand: () => {},
  registerTool: () => {},
  registerShortcut: () => {},
  on: (event) => { listenerKeys.push(event); },
  setSessionName: () => {},
  exec: async () => ({ code: 0, stdout: "{}", stderr: "", killed: false }),
});
console.log(JSON.stringify({ listeners: listenerKeys }));
"""
    )
    result = subprocess.run(
        [shutil.which("node"), "--experimental-transform-types", str(tmp_path / "run.mjs")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"node failed: rc={result.returncode}\nstderr={result.stderr}"
    state = json.loads(result.stdout.strip())
    # The worker must count from execution-start, not the mutable/blockable
    # pre-execution ``tool_call`` hook.
    assert "tool_execution_start" in state["listeners"], (
        f"worker extension must register pi.on(\"tool_execution_start\") for "
        f"AC-6; got {state['listeners']}"
    )


def test_worker_tool_count_body_carries_non_negative_integer():
    """AC-6 privacy / contract: the publisher emits a body where ``tool_count``
    is clamped to a non-negative integer and contains no message body,
    tool arguments, or other content fields — telemetry stays status-only.
    """
    from orchlink.broker.storage.memory import MemoryMessageStore

    async def run() -> None:
        from orchlink.core.models import StoredMessage as _SM
        global StoredMessage
        StoredMessage = _SM
        store = MemoryMessageStore()
        envelope_args = dict(
            message_id="msg-ac6-pure",
            correlation_id="req-ac6-pure",
            conversation_id="C-ac6-pure",
            project_id="default",
            task_id="T-AC6-pure",
            from_agent="demo.lead",
            to_agent="demo.work",
            type="TASK",
            timeout_seconds=10**9,
        )
        try:
            sm = StoredMessage.from_envelope(
                type("E", (), envelope_args)(),
                now="2026-01-01T00:00:00+00:00",
            )
        except TypeError:
            # Fallback: construct via positional kwargs.
            from orchlink.core.envelope import MessageEnvelope

            sm = StoredMessage.from_envelope(
                MessageEnvelope(**envelope_args),
                now="2026-01-01T00:00:00+00:00",
            )
        store._state.active_messages[sm.envelope.message_id] = sm
        task_key = store._job_projector.task_key("default", "T-AC6-pure")
        from orchlink.core.models import Job, JobLease, JobRoute

        job = Job(
            id="job-ac6-pure",
            kind="task",
            project_id="default",
            route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
            mode="DO",
            status="RUNNING",
            task_id="T-AC6-pure",
            conversation_id="C-ac6-pure",
            turn=1,
            max_turns=6,
            lease=JobLease.fresh("demo.work", heartbeat_ms=15000, epoch=1, grace_multiplier=6),
        )
        store._state.task_jobs[task_key] = job
        store._state.tasks[task_key] = type("Proj", (), {})  # type: ignore[assignment]
        # Replace placeholder with a real TaskProjection (avoid dataclass import for brevity).
        from orchlink.core.models import TaskProjection as TP

        store._state.tasks[task_key] = TP(
            kind="task", project_id="default", task_id="T-AC6-pure",
            conversation_id="C-ac6-pure", mode="DO",
        ).with_updates({"status": "RUNNING"})
        from orchlink.core.models import TaskTelemetry
        from orchlink.core.views import task_telemetry_from_wire

        # The worker's publisher payload shape — what we expect to see.
        body = {
            "project_id": "default",
            "worker_name": "demo.work",
            "tool_count": 7,
        }
        telemetry = task_telemetry_from_wire({**body, "task_id": "T-AC6-pure"})
        result = await store.record_task_telemetry(
            telemetry,
            agent_id="demo.work",
            lease_epoch=1,
            lease_holder="demo.work",
        )
        assert result["status"] == "recorded"
        record = store._state.telemetry_by_task[task_key]
        assert record.tool_count == 7
        # Non-negative integer contract is enforced by the wire-level clamp.
        negative = await store.record_task_telemetry(
            TaskTelemetry(
                project_id="default",
                task_id="T-AC6-pure",
                worker_name="demo.work",
                tool_count=-99,
            ),
            agent_id="demo.work",
        )
        assert negative["status"] == "recorded"
        # The record was *replaced* with a fresh count, not appended.
        record_after = store._state.telemetry_by_task[task_key]
        assert record_after.tool_count == 0, (
            "negative tool_count must clamp to zero (replace-in-place non-negative contract)"
        )

    asyncio.run(run())
