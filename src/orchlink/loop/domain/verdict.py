"""Verifier verdict value objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Verdict(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    BLOCKER = "blocker"


class ReasonCode(str, Enum):
    UNKNOWN = "unknown"
    ACCEPTED = "accepted"
    TESTS_FAILED = "tests_failed"
    REVIEW_FAILED = "review_failed"
    OBJECTIVE_CHECK_FAILED = "objective_check_failed"
    BLOCKED = "blocked"
    POLICY = "policy"
    USER_REQUEST = "user_request"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        if value.endswith("Z"):
            return datetime.fromisoformat(value[:-1] + "+00:00")
        raise


def datetime_to_json(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True, slots=True)
class VerifierVerdict:
    verdict: Verdict
    reason_code: ReasonCode
    detail: str
    required_fixes: tuple[str, ...]
    verifier_worker: str
    task_id: str | None = None
    issued_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.verifier_worker:
            raise ValueError("verifier_worker is required")
        object.__setattr__(self, "verdict", Verdict(self.verdict))
        object.__setattr__(self, "reason_code", ReasonCode(self.reason_code))
        object.__setattr__(self, "required_fixes", tuple(self.required_fixes))
        if self.issued_at is None:
            object.__setattr__(self, "issued_at", utc_now())
        elif not isinstance(self.issued_at, datetime):
            object.__setattr__(self, "issued_at", parse_datetime(self.issued_at))

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reason_code": self.reason_code.value,
            "detail": self.detail,
            "required_fixes": list(self.required_fixes),
            "verifier_worker": self.verifier_worker,
            "task_id": self.task_id,
            "issued_at": datetime_to_json(self.issued_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VerifierVerdict":
        return cls(
            verdict=Verdict(data["verdict"]),
            reason_code=ReasonCode(data.get("reason_code", ReasonCode.UNKNOWN.value)),
            detail=data.get("detail", ""),
            required_fixes=tuple(data.get("required_fixes", ())),
            verifier_worker=data["verifier_worker"],
            task_id=data.get("task_id"),
            issued_at=parse_datetime(data.get("issued_at")),
        )


_VERDICT_BLOCK_KEYS = ("VERDICT", "REASON", "DETAIL", "FIXES", "VERIFIER_WORKER", "TASK_ID")


def _split_structured_line(line: str) -> tuple[str, str]:
    if ":" not in line:
        return "", ""
    key, value = line.split(":", 1)
    return key.strip().upper(), value.strip()


def _verdict_block_fields(text: str) -> dict[str, str]:
    lines = (text or "").splitlines()
    verdict_index: int | None = None
    for index in range(len(lines) - 1, -1, -1):
        key, _ = _split_structured_line(lines[index])
        if key == "VERDICT":
            verdict_index = index
            break
    if verdict_index is None:
        return {}
    fields: dict[str, str] = {}
    for line in lines[verdict_index:]:
        key, value = _split_structured_line(line)
        if key in _VERDICT_BLOCK_KEYS:
            fields[key] = value
    return fields


def parse_verdict_text(text: str) -> VerifierVerdict:
    """Parse the structured verdict block a verifier worker emits in its reply.

    The block is the trailing section produced by the verifier prompt::

        VERDICT: accepted | rejected | blocker
        REASON: accepted | tests_failed | review_failed | objective_check_failed | ...
        DETAIL: <text>
        FIXES: <comma-separated, or none>
        VERIFIER_WORKER: <worker name>

    Raises ``ValueError`` when the block is absent or malformed; callers (notably
    loop recovery) should fail closed on ``ValueError`` rather than fabricate a
    verdict. This is the canonical parser for both live and recovered replies.
    """
    fields = _verdict_block_fields(text)
    raw_verdict = fields.get("VERDICT")
    if not raw_verdict:
        raise ValueError("missing VERDICT line")
    try:
        verdict = Verdict(raw_verdict.strip().lower())
    except ValueError as exc:
        raise ValueError(f"unknown verdict: {raw_verdict}") from exc

    raw_reason = fields.get("REASON", "").strip()
    if verdict is Verdict.REJECTED and not raw_reason:
        raise ValueError("REASON is required for REJECTED verdicts")
    if not raw_reason:
        raw_reason = ReasonCode.UNKNOWN.value
    normalized_reason = raw_reason.lower()
    if normalized_reason == "checks_failed":
        normalized_reason = ReasonCode.OBJECTIVE_CHECK_FAILED.value
    try:
        reason = ReasonCode(normalized_reason)
    except ValueError as exc:
        raise ValueError(f"unknown reason code: {raw_reason}") from exc

    detail = fields.get("DETAIL", "").strip()
    fixes = tuple(
        part.strip()
        for part in fields.get("FIXES", "").split(",")
        if part.strip() and part.strip().lower() != "none"
    )
    verifier_worker = fields.get("VERIFIER_WORKER", "verifier").strip() or "verifier"
    task_id = fields.get("TASK_ID", "").strip() or None
    return VerifierVerdict(
        verdict=verdict,
        reason_code=reason,
        detail=detail,
        required_fixes=fixes,
        verifier_worker=verifier_worker,
        task_id=task_id,
    )
