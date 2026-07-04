"""Pure, side-effect-free extension logic (M4).

The Pi extension is generated TypeScript (``pi_extension.py``), so its logic
cannot be exercised directly by the Python test suite. This module is the
**source of truth** for the rules that are correctness-critical and easy to
break by hand-editing the template string:

- review-reconciliation detection (used by opt-in auto phase compaction),
- recoverable transport-error detection (defers instead of failing a task),
- reply-type detection (``TYPE:`` prefix parsing),
- compaction summary / phase-instruction text (the state-pointer summary
  injected into Pi's ``session_before_compact`` hook),
- the job-lease heartbeat body shape (M3).

The generated TypeScript interpolates the two detection regexes from this
module (single source), and the other functions are behavioral oracles that
``tests/test_pi_extension_pure.py`` covers and cross-checks against the
generated source so the two cannot silently drift.
"""

from __future__ import annotations

import json
import re
from typing import Any

# --- Detection patterns (interpolated into the generated TS as RegExp sources) ---

# Matches a lead review-reconciliation line. The generated TS uses this exact
# source via ``new RegExp(__ORCH_RECONCILIATION_PATTERN__, "i")``.
RECONCILIATION_PATTERN = r"(^|\n)\s*(Review reconciled|Decision|Blocked)\s*:"

# Matches recoverable transport/provider errors. The generated TS uses this
# exact source via ``new RegExp(__ORCH_RECOVERABLE_ERROR_PATTERN__, "i")``.
RECOVERABLE_ERROR_PATTERN = "WebSocket error|provider_transport_failure|transport|Request timed out|timed out|timeout"

RECONCILIATION_KEYWORDS = ("Review reconciled", "Decision", "Blocked")
REPLY_TYPE_VALUES = ("PLAN", "RESULT", "BLOCKER")

# Job-lease heartbeat body keys sent by the worker to /v1/jobs/{id}/heartbeat.
LEASE_HEARTBEAT_BODY_KEYS = ("holder", "epoch", "heartbeat_ms")

# Placeholders replaced in the generated TS so the runtime RegExp uses these
# exact sources (single source of truth, no drift).
RECONCILIATION_PLACEHOLDER = "__ORCH_RECONCILIATION_PATTERN__"
RECOVERABLE_ERROR_PLACEHOLDER = "__ORCH_RECOVERABLE_ERROR_PATTERN__"

_RECONCILIATION_RE = re.compile(RECONCILIATION_PATTERN, re.IGNORECASE)
_RECOVERABLE_RE = re.compile(RECOVERABLE_ERROR_PATTERN, re.IGNORECASE)


def is_reconciliation(output: str | None) -> bool:
    """True if ``output`` opens with a reconciliation/decision/blocked marker.

    Mirrors the generated ``looksLikeReviewReconciliation`` TS: the text is
    trimmed, empty output is rejected, and the regex is matched anywhere.
    """
    text = str(output or "").strip()
    if not text:
        return False
    return _RECONCILIATION_RE.search(text) is not None


def is_recoverable_error(error_message: str | None = None, diagnostics: list[Any] | None = None) -> bool:
    """True if the assistant error text indicates a transient transport error.

    Mirrors the generated ``isRecoverableAssistantError`` regex test. The
    caller (the TS ``message_end`` handler) gates this on
    ``stopReason === "error"`` first; this function only classifies the text.
    """
    error_text = f"{error_message or ''} {json.dumps(diagnostics or [])}"
    return _RECOVERABLE_RE.search(error_text) is not None


def detect_reply_type(output: str | None) -> str:
    """Parse a ``TYPE: PLAN|RESULT|BLOCKER`` prefix; default ``RESULT``.

    Mirrors the generated ``detectReplyType`` TS: the first non-empty line is
    inspected; only an exact leading ``TYPE:`` token in the allowed set is
    honored.
    """
    first_line = ""
    for line in str(output or "").splitlines():
        stripped = line.strip()
        if stripped:
            first_line = stripped
            break
    if not first_line.startswith("TYPE:"):
        return "RESULT"
    rest = first_line[len("TYPE:"):].strip()
    value = rest.split(None, 1)[0] if rest else ""
    return value if value in REPLY_TYPE_VALUES else "RESULT"


def compaction_instructions(note: str | None) -> str:
    """Build the Orchlink-aware phase compaction instruction text.

    Mirrors the generated ``phaseCompactionInstructions`` TS.
    """
    phase_note = str(note or "").strip() or "Pi compaction requested."
    return f"""This is an Orchlink-aware compaction. Compact old context while preserving the state needed for the next phase.

Preserve:
- completed phase summary
- review verdict
- files changed
- tests run
- current task ID
- current goal ID, if any
- scope guardrails and forbidden paths
- unresolved blockers
- next exact step
- pointers to durable .orch/ state files
- cumulative readFiles and modifiedFiles

Phase note:
{phase_note}"""


def build_compaction_summary(
    instructions: str,
    role: str | None,
    project_id: str,
    task_id: str = "none",
    conversation_id: str = "none",
) -> str:
    """Build the state-pointer summary injected into Pi compaction.

    Mirrors the generated ``orchlinkCompactionSummary`` TS. ``task_id`` and
    ``conversation_id`` default to ``"none"`` when no task is in flight.
    """
    return f"""## Orchlink state

## Goal
Continue the current Orchlink project with compact context and reload details from durable state instead of old chat.

## Critical Context
- Project ID: {project_id}
- Role: {role or "unknown"}
- Current task ID: {task_id}
- Current conversation ID: {conversation_id}
- Durable state roots: .orch/goals/, .orch/run/, .orch/project.yaml
- Preserve task/goal scope guardrails and forbidden paths from the latest Orchlink task prompt.

## Phase compaction instructions
{instructions}

## Next Steps
1. Reload relevant .orch goal artifacts before making claims about goal status.
2. Continue only from verified task results, checks, and goal state files.
3. If scope or blocker details are unclear, ask one specific unblock question."""


def build_post_compaction_resume_steer(summary: str) -> str:
    """Build the visible steer sent to the lead after compaction completes.

    Mirrors the generated ``orchlinkPostCompactionResumeSteer`` TS. The compacted
    summary is preserved inside Pi's history; this visible steer nudges the lead
    to resume from durable state instead of waiting silently after compaction.
    """
    return f"""[Orchlink] Compaction complete. Resume from this state:

{summary}

Action: reload durable state before continuing. Start with `orch resume`; use `orch goal show <id>` when a goal is active."""


def lease_heartbeat_body(holder: str, epoch: int, heartbeat_ms: int) -> dict[str, Any]:
    """Build the body posted to ``/v1/jobs/{id}/heartbeat`` (M3 lease renewal).

    Mirrors the generated ``renewJobLease`` TS body shape.
    """
    return {"holder": holder, "epoch": epoch, "heartbeat_ms": heartbeat_ms}


def interpolation_replacements() -> dict[str, str]:
    """The placeholder -> JSON-source replacements applied to the generated TS."""
    return {
        RECONCILIATION_PLACEHOLDER: json.dumps(RECONCILIATION_PATTERN),
        RECOVERABLE_ERROR_PLACEHOLDER: json.dumps(RECOVERABLE_ERROR_PATTERN),
    }