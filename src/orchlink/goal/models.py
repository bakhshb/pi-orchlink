from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from orchlink.goal.lifecycle import (
    AcceptanceStatus,
    GateStatus,
    GoalStatus,
    normalize_acceptance_status,
    normalize_gate_status,
    normalize_goal_status,
    refresh_goal_status_from_gates,
)


SourceType = Literal["prd", "plan", "text"]


def _value(value: object) -> object:
    if isinstance(value, StrEnum):
        return value.value
    return value


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class GoalBlocker:
    type: str = "ambiguity"
    message: str = ""
    task_id: str | None = None
    criterion_id: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GoalBlocker":
        known = {"type", "message", "task_id", "criterion_id"}
        return cls(
            type=str(data.get("type") or "ambiguity"),
            message=str(data.get("message") or ""),
            task_id=str(data["task_id"]) if data.get("task_id") is not None else None,
            criterion_id=str(data["criterion_id"]) if data.get("criterion_id") is not None else None,
            detail={key: value for key, value in data.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        data = {"type": self.type, "message": self.message, **self.detail}
        if self.task_id is not None:
            data["task_id"] = self.task_id
        if self.criterion_id is not None:
            data["criterion_id"] = self.criterion_id
        return data


@dataclass
class GoalEvidence:
    type: str
    criterion_id: str | None = None
    task_id: str | None = None
    command: str | None = None
    passed: bool | None = None
    summary: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GoalEvidence":
        known = {"type", "criterion_id", "task_id", "command", "passed", "summary"}
        return cls(
            type=str(data.get("type") or "evidence"),
            criterion_id=str(data["criterion_id"]) if data.get("criterion_id") is not None else None,
            task_id=str(data["task_id"]) if data.get("task_id") is not None else None,
            command=str(data["command"]) if data.get("command") is not None else None,
            passed=bool(data["passed"]) if data.get("passed") is not None else None,
            summary=str(data["summary"]) if data.get("summary") is not None else None,
            detail={key: value for key, value in data.items() if key not in known},
        )

    def to_dict(self) -> dict[str, Any]:
        data = {"type": self.type, **self.detail}
        if self.criterion_id is not None:
            data["criterion_id"] = self.criterion_id
        if self.task_id is not None:
            data["task_id"] = self.task_id
        if self.command is not None:
            data["command"] = self.command
        if self.passed is not None:
            data["passed"] = self.passed
        if self.summary is not None:
            data["summary"] = self.summary
        return data


@dataclass
class GoalDeferral:
    id: str
    reason: str
    detail: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GoalDeferral":
        return cls(id=str(data.get("id") or ""), reason=str(data.get("reason") or ""), detail=data.get("detail") if isinstance(data.get("detail"), dict) else None)

    def to_dict(self) -> dict[str, Any]:
        data = {"id": self.id, "reason": self.reason}
        if self.detail:
            data["detail"] = self.detail
        return data


@dataclass
class AcceptanceCriterion:
    id: str
    text: str = ""
    type: str = "objective"
    priority: str = "core"
    depends_on: list[str] = field(default_factory=list)
    check: str | None = None
    source: str = ""
    confidence: str = "medium"
    status: AcceptanceStatus = AcceptanceStatus.PENDING
    blocker: GoalBlocker | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AcceptanceCriterion":
        depends = data.get("depends_on") or []
        if isinstance(depends, str):
            depends = [depends]
        return cls(
            id=str(data.get("id") or ""),
            text=str(data.get("text") or data.get("title") or ""),
            type=str(data.get("type") or "objective"),
            priority=str(data.get("priority") or "core"),
            depends_on=[str(item) for item in depends],
            check=str(data["check"]) if data.get("check") else None,
            source=str(data.get("source") or ""),
            confidence=str(data.get("confidence") or "medium"),
            status=normalize_acceptance_status(data.get("status") or AcceptanceStatus.PENDING),
            blocker=GoalBlocker.from_dict(data["blocker"]) if isinstance(data.get("blocker"), dict) else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "type": self.type,
            "priority": self.priority,
            "depends_on": self.depends_on,
            "check": self.check,
            "source": self.source,
            "confidence": self.confidence,
            "status": _value(self.status),
            "blocker": self.blocker.to_dict() if self.blocker is not None else None,
        }


@dataclass
class Goal:
    id: str
    title: str
    source: SourceType
    status: GoalStatus = GoalStatus.DRAFT
    ac_gate: GateStatus = GateStatus.PENDING
    plan_gate: GateStatus = GateStatus.PENDING
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    active_task_id: str | None = None
    deferred: list[GoalDeferral] = field(default_factory=list)
    evidence: list[GoalEvidence] = field(default_factory=list)
    ac_status: dict[str, str] = field(default_factory=dict)
    blockers: list[GoalBlocker] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Goal":
        return cls(
            id=str(data["id"]),
            title=str(data.get("title") or data["id"]),
            source=data.get("source") or "text",
            status=normalize_goal_status(data.get("status") or GoalStatus.DRAFT),
            ac_gate=normalize_gate_status(data.get("ac_gate") or GateStatus.PENDING),
            plan_gate=normalize_gate_status(data.get("plan_gate") or GateStatus.PENDING),
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
            active_task_id=data.get("active_task_id"),
            deferred=[GoalDeferral.from_dict(item) for item in list(data.get("deferred") or []) if isinstance(item, dict)],
            evidence=[GoalEvidence.from_dict(item) for item in list(data.get("evidence") or []) if isinstance(item, dict)],
            ac_status=dict(data.get("ac_status") or {}),
            blockers=[GoalBlocker.from_dict(item) for item in list(data.get("blockers") or []) if isinstance(item, dict)],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "source": self.source,
            "status": _value(self.status),
            "ac_gate": _value(self.ac_gate),
            "plan_gate": _value(self.plan_gate),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "active_task_id": self.active_task_id,
            "deferred": [item.to_dict() for item in self.deferred],
            "evidence": [item.to_dict() for item in self.evidence],
            "ac_status": self.ac_status,
            "blockers": [item.to_dict() for item in self.blockers],
        }

    def refresh_status_from_gates(self) -> None:
        refresh_goal_status_from_gates(self)
        self.updated_at = utc_now_iso()
