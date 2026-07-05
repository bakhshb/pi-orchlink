"""Pure, side-effect-free extension logic (M4).

The Pi extension is generated TypeScript (``pi_extension.py``), so its logic
cannot be exercised directly by the Python test suite. This module is the
**source of truth** for the rules that are correctness-critical and easy to
break by hand-editing the template string:

- recoverable transport-error detection (defers instead of failing a task),
- reply-type detection (``TYPE:`` prefix parsing),
- the job-lease heartbeat body shape (M3).

The generated TypeScript interpolates shared runtime constants from this
module (single source), and the other functions are behavioral oracles that
``tests/test_pi_extension_pure.py`` covers and cross-checks against the
generated source so the two cannot silently drift.
"""

from __future__ import annotations

import json
import re
from typing import Any

# --- Detection patterns (interpolated into the generated TS as RegExp sources) ---

# Matches recoverable transport/provider errors. The generated TS uses this
# exact source via ``new RegExp(__ORCH_RECOVERABLE_ERROR_PATTERN__, "i")``.
RECOVERABLE_ERROR_PATTERN = "WebSocket error|provider_transport_failure|transport|Request timed out|timed out|timeout"

REPLY_TYPE_VALUES = ("PLAN", "RESULT", "BLOCKER")

# Worker thinking controls shared by the Python client and generated TS.
THINKING_LEVELS = ("off", "minimal", "low", "medium", "high", "xhigh")
MODE_THINKING_DEFAULTS = {
    "DISCUSS": "xhigh",
    "PLAN": "xhigh",
    "REVIEW": "xhigh",
    "TALK": "xhigh",
    "DO": "medium",
}

# Job-lease heartbeat body keys sent by the worker to /v1/jobs/{id}/heartbeat.
LEASE_HEARTBEAT_BODY_KEYS = ("holder", "epoch", "heartbeat_ms")

# Placeholders replaced in the generated TS so runtime constants use these exact
# Python sources (single source of truth, no drift).
RECOVERABLE_ERROR_PLACEHOLDER = "__ORCH_RECOVERABLE_ERROR_PATTERN__"
THINKING_LEVELS_PLACEHOLDER = "__ORCH_THINKING_LEVELS__"
MODE_THINKING_DEFAULTS_PLACEHOLDER = "__ORCH_MODE_THINKING_DEFAULTS__"

_RECOVERABLE_RE = re.compile(RECOVERABLE_ERROR_PATTERN, re.IGNORECASE)


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


def lease_heartbeat_body(holder: str, epoch: int, heartbeat_ms: int) -> dict[str, Any]:
    """Build the body posted to ``/v1/jobs/{id}/heartbeat`` (M3 lease renewal).

    Mirrors the generated ``renewJobLease`` TS body shape.
    """
    return {"holder": holder, "epoch": epoch, "heartbeat_ms": heartbeat_ms}


def interpolation_replacements() -> dict[str, str]:
    """The placeholder -> JSON-source replacements applied to the generated TS."""
    return {
        RECOVERABLE_ERROR_PLACEHOLDER: json.dumps(RECOVERABLE_ERROR_PATTERN),
        THINKING_LEVELS_PLACEHOLDER: json.dumps(list(THINKING_LEVELS)),
        MODE_THINKING_DEFAULTS_PLACEHOLDER: json.dumps(MODE_THINKING_DEFAULTS, sort_keys=True),
    }