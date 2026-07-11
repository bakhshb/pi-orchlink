"""M4 tests for centralized Pi-extension pure logic.

These cover behavior (not broad string presence) for:
- recoverable transport-error detection,
- reply-type (``TYPE:`` prefix) parsing,
- lease heartbeat body shape,
and cross-check that the generated TypeScript embeds the shared regex sources
and key shapes so the Python oracle and the runtime cannot drift.
"""

from __future__ import annotations

import json

import pytest

from orchlink.connector.pi_extension import ORCHLINK_PI_EXTENSION
from orchlink.connector.pi_extension_pure import (
    LEASE_HEARTBEAT_BODY_KEYS,
    MODE_THINKING_DEFAULTS,
    RECOVERABLE_ERROR_PATTERN,
    THINKING_LEVELS,
    detect_reply_type,
    is_recoverable_error,
    lease_heartbeat_body,
)


# --- Recoverable transport-error detection ------------------------------------


@pytest.mark.parametrize(
    "message, diagnostics",
    [
        ("WebSocket error during stream", []),
        ("provider_transport_failure", []),
        ("Request timed out", []),
        ("the call timed out waiting", []),
        ("network TIMEOUT", []),
        ("", [{"code": "transport_error"}]),  # matched via diagnostics JSON
    ],
)
def test_is_recoverable_error_positive(message, diagnostics):
    assert is_recoverable_error(message, diagnostics) is True


@pytest.mark.parametrize(
    "message, diagnostics",
    [
        ("Model returned invalid JSON", []),
        ("Tool failed: permission denied", []),
        ("", []),
        ("regular error", [{"code": "auth_error"}]),
    ],
)
def test_is_recoverable_error_negative(message, diagnostics):
    assert is_recoverable_error(message, diagnostics) is False


def test_is_recoverable_error_mirrors_ts_text_construction():
    # The TS builds "<errorMessage> <JSON.stringify(diagnostics)>" then tests.
    # A diagnostic containing the word "transport" must trigger a match.
    assert is_recoverable_error("oops", [{"detail": "transport reset"}]) is True


# --- Reply-type detection -----------------------------------------------------


@pytest.mark.parametrize(
    "output, expected",
    [
        ("TYPE: PLAN\nmore", "PLAN"),
        ("TYPE:RESULT", "RESULT"),
        ("TYPE: BLOCKER", "BLOCKER"),
        ("  TYPE: PLAN", "PLAN"),  # leading whitespace on the line
        ("TYPE: PLAN EXTRA", "PLAN"),  # only the first token counts
        ("Plan: do something", "RESULT"),  # not the TYPE: prefix
        ("", "RESULT"),
        ("TYPE: UNKNOWN", "RESULT"),  # unknown value defaults to RESULT
        ("TYPE: result", "RESULT"),  # case-insensitive token
    ],
)
def test_detect_reply_type(output, expected):
    assert detect_reply_type(output) == expected


def test_detect_reply_type_uses_first_non_empty_line():
    assert detect_reply_type("\n\nTYPE: BLOCKER\n") == "BLOCKER"


# --- Lease heartbeat body -----------------------------------------------------


def test_lease_heartbeat_body_shape():
    body = lease_heartbeat_body("demo.work", 3, 15000)
    assert set(body.keys()) == set(LEASE_HEARTBEAT_BODY_KEYS)
    assert body == {"holder": "demo.work", "epoch": 3, "heartbeat_ms": 15000}


# --- Cross-checks: generated TS embeds the shared sources -------------------


def test_generated_ts_uses_shared_recoverable_error_regex():
    assert "__ORCH_RECOVERABLE_ERROR_PATTERN__" not in ORCHLINK_PI_EXTENSION
    assert "const RECOVERABLE_ERROR_REGEX = new RegExp(" in ORCHLINK_PI_EXTENSION
    assert "RECOVERABLE_ERROR_REGEX.test(errorText)" in ORCHLINK_PI_EXTENSION
    assert json.dumps(RECOVERABLE_ERROR_PATTERN) in ORCHLINK_PI_EXTENSION


def test_generated_ts_renewjoblease_posts_shared_body_shape():
    # The worker posts {holder, epoch, heartbeat_ms} to the heartbeat endpoint.
    assert 'holder: env("ORCHLINK_AGENT_ID")' in ORCHLINK_PI_EXTENSION
    assert 'env("ORCHLINK_ACTIVITY_HEARTBEAT_MS", "15000")' in ORCHLINK_PI_EXTENSION
    assert "/v1/jobs/${encodeURIComponent(taskId)}/heartbeat" in ORCHLINK_PI_EXTENSION


def test_generated_ts_sends_extension_ready_heartbeat():
    assert "ORCHLINK_SESSION_LEASE_ID" in ORCHLINK_PI_EXTENSION
    assert "async function sendReadyHeartbeat" in ORCHLINK_PI_EXTENSION
    assert "ready: true" in ORCHLINK_PI_EXTENSION
    assert "last_ready_heartbeat_at" not in ORCHLINK_PI_EXTENSION
    assert "scheduleReadyHeartbeat(ctx)" in ORCHLINK_PI_EXTENSION


def test_generated_ts_supports_background_oneshot_after_task_reply():
    assert "ORCHLINK_ONESHOT" in ORCHLINK_PI_EXTENSION
    assert "function stopAfterOneshotReply" in ORCHLINK_PI_EXTENSION
    assert 'backgroundBackend !== "rpc-supervisor"' in ORCHLINK_PI_EXTENSION
    assert "isChatRequest(task)" in ORCHLINK_PI_EXTENSION
    assert "process.exit(0)" in ORCHLINK_PI_EXTENSION


def test_generated_ts_reconciliation_steer_uses_canonical_notice():
    # Lease loss must steer the standard stop-notice so the worker halts.
    assert "Stop working now. Do not make more edits, do not call more tools" in ORCHLINK_PI_EXTENSION
    assert "lease.status === 409" in ORCHLINK_PI_EXTENSION


def test_generated_ts_does_not_override_pi_compaction():
    # Orchlink should not hook, trigger, or customize Pi context compaction.
    for needle in (
        "session_before_compact",
        "session_compact",
        "ctx.compact",
        "orchlinkCompactionSummary",
        "phaseCompactionInstructions",
        "ORCHLINK_AUTO_COMPACT_PHASES",
        "pendingReviewCompaction",
        "Compaction complete",
    ):
        assert needle not in ORCHLINK_PI_EXTENSION


def test_generated_ts_captures_text_delta_only():
    # G018: worker listens to message_update, accepts only text_delta, buffers,
    # and posts assistant_delta events to the broker transcript endpoint.
    assert 'pi.on("message_update"' in ORCHLINK_PI_EXTENSION
    assert 'assistantEvent.type !== "text_delta"' in ORCHLINK_PI_EXTENSION
    assert "appendTranscriptDelta" in ORCHLINK_PI_EXTENSION
    assert "flushTranscriptBuffer" in ORCHLINK_PI_EXTENSION
    assert "transcriptFlushTimer" in ORCHLINK_PI_EXTENSION
    assert "assistant_delta" in ORCHLINK_PI_EXTENSION
    assert '/v1/tasks/${encodeURIComponent(String(task.task_id || ""))}/transcript' in ORCHLINK_PI_EXTENSION
    assert "x-orchlink-lease-epoch" in ORCHLINK_PI_EXTENSION
    assert "x-orchlink-lease-holder" in ORCHLINK_PI_EXTENSION
    assert "x-orchlink-session-lease-id" in ORCHLINK_PI_EXTENSION


def test_generated_ts_drops_thinking_and_unknown_transcript_events():
    # The worker must ignore thinking_delta and any non-text_delta event.
    assert "thinking_delta" not in ORCHLINK_PI_EXTENSION
    assert "message_update" in ORCHLINK_PI_EXTENSION
    assert 'assistantEvent.type !== "text_delta"' in ORCHLINK_PI_EXTENSION


def test_generated_ts_transcript_flush_boundaries():
    # Flush timer and byte cap exist; flush runs before tool calls, message_end,
    # and shutdown / cancellation / lease loss paths.
    assert "TRANSCRIPT_FLUSH_MS" in ORCHLINK_PI_EXTENSION
    assert "TRANSCRIPT_MAX_BYTES" in ORCHLINK_PI_EXTENSION
    assert "Buffer.byteLength" in ORCHLINK_PI_EXTENSION
    assert 'flushTranscriptBuffer(true)' in ORCHLINK_PI_EXTENSION
    assert "resetTranscriptState" in ORCHLINK_PI_EXTENSION
    assert "finalizeTranscript" in ORCHLINK_PI_EXTENSION


def test_generated_ts_message_update_handler_does_not_use_send_message():
    # Transcript capture must not inject user/assistant messages into Pi context.
    follow_index = ORCHLINK_PI_EXTENSION.find('pi.on("message_update"')
    assert follow_index > 0
    block = ORCHLINK_PI_EXTENSION[follow_index:follow_index + 800]
    assert "pi.sendUserMessage" not in block
    assert "pi.sendMessage" not in block


def test_generated_ts_transcript_post_failure_is_observability_only():
    # Post failures are logged, not thrown, so task execution continues.
    post_index = ORCHLINK_PI_EXTENSION.find("/v1/tasks/${encodeURIComponent(String(task.task_id")
    assert post_index > 0
    block = ORCHLINK_PI_EXTENSION[post_index:post_index + 600]
    assert ".catch(" in block
    assert "transcript post failed" in block


# --- Thinking-default single source: Python client and generated TS ------------


def test_python_client_uses_shared_thinking_defaults():
    from orchlink.client.messages import MODE_THINKING_DEFAULTS as client_defaults
    from orchlink.client.messages import THINKING_LEVELS as client_levels

    assert client_defaults == MODE_THINKING_DEFAULTS
    assert client_levels == set(THINKING_LEVELS)


def test_generated_ts_uses_shared_thinking_constants():
    levels_json = json.dumps(list(THINKING_LEVELS))
    defaults_json = json.dumps(MODE_THINKING_DEFAULTS, sort_keys=True)

    assert f"const THINKING_LEVELS = new Set({levels_json});" in ORCHLINK_PI_EXTENSION
    assert f"const MODE_THINKING_DEFAULTS: Record<string, string> = {defaults_json};" in ORCHLINK_PI_EXTENSION
    assert "__ORCH_THINKING_LEVELS__" not in ORCHLINK_PI_EXTENSION
    assert "__ORCH_MODE_THINKING_DEFAULTS__" not in ORCHLINK_PI_EXTENSION
