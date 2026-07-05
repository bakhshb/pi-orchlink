"""Typed helpers for Goal Mode worker replies.

Goal workers still return broker wire dictionaries, but Goal Mode should parse
that shape once at the boundary and pass typed reply/blocker objects through the
runner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from orchlink.goal.models import GoalBlocker


class WorkerReplyKind(StrEnum):
    RESULT = "RESULT"
    BLOCKER = "BLOCKER"


class WorkerBlockerType(StrEnum):
    DECISION = "decision"
    ASSET = "asset"
    UPSTREAM = "upstream"
    EXTERNAL = "external"
    AMBIGUITY = "ambiguity"
    FAILED_CHECK = "failed_check"


@dataclass(frozen=True)
class ParsedWorkerReply:
    kind: WorkerReplyKind = WorkerReplyKind.RESULT
    status: str | None = None
    task_id: str | None = None
    summary: str = ""
    blocker: GoalBlocker | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


def normalize_worker_reply_kind(value: object) -> WorkerReplyKind:
    try:
        return WorkerReplyKind(str(value or WorkerReplyKind.RESULT.value).upper())
    except ValueError:
        return WorkerReplyKind.RESULT


def normalize_worker_blocker_type(value: object) -> WorkerBlockerType:
    try:
        return WorkerBlockerType(str(value or WorkerBlockerType.AMBIGUITY.value).lower())
    except ValueError:
        return WorkerBlockerType.AMBIGUITY


def parse_worker_reply(result: dict[str, Any], *, task_id: str | None = None, criterion_id: str | None = None) -> ParsedWorkerReply:
    reply = result.get("reply") if isinstance(result.get("reply"), dict) else {}
    payload = reply.get("payload") if isinstance(reply.get("payload"), dict) else {}
    summary = str(payload.get("summary") or payload.get("stdout") or payload or "")
    kind = normalize_worker_reply_kind(reply.get("type"))
    resolved_task_id = str(task_id or result.get("task_id") or "") or None
    blocker = _parse_worker_blocker(payload, summary, resolved_task_id, criterion_id) if kind == WorkerReplyKind.BLOCKER else None
    return ParsedWorkerReply(
        kind=kind,
        status=str(result["status"]) if result.get("status") is not None else None,
        task_id=resolved_task_id,
        summary=summary,
        blocker=blocker,
        raw_payload=dict(payload),
    )


def compact_worker_result(result: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_worker_reply(result)
    return {
        "status": parsed.status,
        "task_id": parsed.task_id,
        "reply_type": parsed.kind.value,
        "summary": parsed.summary[:8000],
    }


def _parse_worker_blocker(payload: dict[str, Any], summary: str, task_id: str | None, criterion_id: str | None) -> GoalBlocker:
    typed = payload.get("blocker") if isinstance(payload.get("blocker"), dict) else {}
    blocker_type = normalize_worker_blocker_type(typed.get("type") if typed else _blocker_type_from_summary(summary))
    message = str(typed.get("message") or summary)
    detail = {key: value for key, value in typed.items() if key not in {"type", "message", "task_id", "criterion_id"}}
    return GoalBlocker(
        type=blocker_type.value,
        message=message,
        task_id=str(typed.get("task_id") or task_id) if typed.get("task_id") or task_id else None,
        criterion_id=str(typed.get("criterion_id") or criterion_id) if typed.get("criterion_id") or criterion_id else None,
        detail=detail,
    )


def _blocker_type_from_summary(summary: str) -> str:
    for line in summary.splitlines():
        key, _, value = line.partition(":")
        if key.strip().lower() in {"type", "blocker_type", "blocker type"}:
            return value.strip().lower()
    return WorkerBlockerType.AMBIGUITY.value


__all__ = [
    "ParsedWorkerReply",
    "WorkerBlockerType",
    "WorkerReplyKind",
    "compact_worker_result",
    "normalize_worker_blocker_type",
    "normalize_worker_reply_kind",
    "parse_worker_reply",
]
