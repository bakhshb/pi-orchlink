"""M4 tests for centralized Pi-extension pure logic.

These cover behavior (not broad string presence) for:
- review-reconciliation detection,
- recoverable transport-error detection,
- reply-type (``TYPE:`` prefix) parsing,
- compaction summary / phase-instruction generation,
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
    RECONCILIATION_PATTERN,
    RECOVERABLE_ERROR_PATTERN,
    build_compaction_summary,
    build_post_compaction_resume_steer,
    compaction_instructions,
    detect_reply_type,
    is_reconciliation,
    is_recoverable_error,
    lease_heartbeat_body,
)


# --- Reconciliation detection -------------------------------------------------


@pytest.mark.parametrize(
    "output",
    [
        "Review reconciled: tests pass, proceed.",
        "Decision: ship it.",
        "Blocked: need owner approval.",
        "  Review reconciled:  ok",  # leading spaces, no colon-space required
        "Preamble.\nDecision: ship.",  # marker on a later line
    ],
)
def test_is_reconciliation_positive(output):
    assert is_reconciliation(output) is True


@pytest.mark.parametrize(
    "output",
    [
        "",
        "   ",
        "Review reconciled",  # no colon
        "I reviewed the change and reconciled it.",  # prose, no marker
        "Decision time:",  # not the marker shape
        "BLOCKER without colon",
    ],
)
def test_is_reconciliation_negative(output):
    assert is_reconciliation(output) is False


def test_is_reconciliation_lowercase_matches_due_to_ignorecase():
    assert is_reconciliation("decision: proceed") is True
    assert is_reconciliation("blocked: need owner") is True


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


# --- Compaction summary + instructions ---------------------------------------


def test_compaction_instructions_preserves_note_and_defaults():
    body = compaction_instructions("Phase 2 complete.")
    assert body.startswith("This is an Orchlink-aware compaction.")
    assert "Phase 2 complete." in body
    assert "current task ID" in body
    assert "pointers to durable .orch/ state files" in body
    # Default note when empty.
    assert "Pi compaction requested." in compaction_instructions("")


def test_build_compaction_summary_contains_state_pointers():
    summary = build_compaction_summary(
        "instructions here", role="work", project_id="smoke-full", task_id="T001", conversation_id="C001"
    )
    assert summary.startswith("## Orchlink state")
    assert "Project ID: smoke-full" in summary
    assert "Role: work" in summary
    assert "Current task ID: T001" in summary
    assert "Current conversation ID: C001" in summary
    assert ".orch/goals/, .orch/run/, .orch/project.yaml" in summary
    assert "instructions here" in summary
    assert "Reload relevant .orch goal artifacts" in summary


def test_build_compaction_summary_defaults_unknown_role_and_missing_task():
    summary = build_compaction_summary("instr", role=None, project_id="demo")
    assert "Role: unknown" in summary
    assert "Current task ID: none" in summary
    assert "Current conversation ID: none" in summary


def test_build_post_compaction_resume_steer_makes_summary_visible():
    summary = build_compaction_summary("Phase done.", role="lead", project_id="demo", task_id="T123")
    steer = build_post_compaction_resume_steer(summary)
    assert steer.startswith("[Orchlink] Compaction complete.")
    assert "Current task ID: T123" in steer
    assert "Start with `orch resume`" in steer
    assert "orch goal show <id>" in steer


# --- Lease heartbeat body -----------------------------------------------------


def test_lease_heartbeat_body_shape():
    body = lease_heartbeat_body("demo.work", 3, 15000)
    assert set(body.keys()) == set(LEASE_HEARTBEAT_BODY_KEYS)
    assert body == {"holder": "demo.work", "epoch": 3, "heartbeat_ms": 15000}


# --- Cross-checks: generated TS embeds the shared sources -------------------


def test_generated_ts_uses_shared_reconciliation_regex():
    # The placeholder must be replaced and the regex must be constructed from
    # the shared source (single source of truth).
    assert "__ORCH_RECONCILIATION_PATTERN__" not in ORCHLINK_PI_EXTENSION
    assert "const RECONCILIATION_REGEX = new RegExp(" in ORCHLINK_PI_EXTENSION
    assert "RECONCILIATION_REGEX.test(text)" in ORCHLINK_PI_EXTENSION
    # The JSON-source form of the pattern must appear verbatim.
    assert json.dumps(RECONCILIATION_PATTERN) in ORCHLINK_PI_EXTENSION


def test_generated_ts_uses_shared_recoverable_error_regex():
    assert "__ORCH_RECOVERABLE_ERROR_PATTERN__" not in ORCHLINK_PI_EXTENSION
    assert "const RECOVERABLE_ERROR_REGEX = new RegExp(" in ORCHLINK_PI_EXTENSION
    assert "RECOVERABLE_ERROR_REGEX.test(errorText)" in ORCHLINK_PI_EXTENSION
    assert json.dumps(RECOVERABLE_ERROR_PATTERN) in ORCHLINK_PI_EXTENSION


def test_generated_ts_renewjoblease_posts_shared_body_shape():
    # The worker posts {holder, epoch, heartbeat_ms} to the heartbeat endpoint.
    assert "holder: agentId, epoch, heartbeat_ms: activityHeartbeatMs" in ORCHLINK_PI_EXTENSION
    assert "/v1/jobs/${encodeURIComponent(taskId)}/heartbeat" in ORCHLINK_PI_EXTENSION


def test_generated_ts_reconciliation_steer_uses_canonical_notice():
    # Lease loss must steer the standard stop-notice so the worker halts.
    assert "Stop working now. Do not make more edits, do not call more tools" in ORCHLINK_PI_EXTENSION
    assert "lease.status === 409" in ORCHLINK_PI_EXTENSION


def test_generated_ts_summary_contains_state_pointer_lines():
    # The compaction summary key lines must be present in the generated source.
    for needle in (
        "## Orchlink state",
        "Durable state roots: .orch/goals/, .orch/run/, .orch/project.yaml",
        "Reload relevant .orch goal artifacts",
    ):
        assert needle in ORCHLINK_PI_EXTENSION


def test_generated_ts_posts_visible_resume_steer_after_compaction():
    assert "function orchlinkPostCompactionResumeSteer" in ORCHLINK_PI_EXTENSION
    assert "postCompactionResumeSteer = orchlinkPostCompactionResumeSteer(summary);" in ORCHLINK_PI_EXTENSION
    assert "pi.sendUserMessage(resumeSteer, { deliverAs: \"steer\" });" in ORCHLINK_PI_EXTENSION
    assert "Start with \\`orch resume\\`" in ORCHLINK_PI_EXTENSION