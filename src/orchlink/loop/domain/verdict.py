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
