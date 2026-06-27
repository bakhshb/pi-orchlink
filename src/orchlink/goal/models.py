from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


GoalStatus = Literal["draft", "ready", "running", "gated", "blocked", "done", "cancelled"]
GateStatus = Literal["pending", "approved", "rejected"]
SourceType = Literal["prd", "plan", "text"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    status: str = "pending"
    blocker: dict[str, Any] | None = None

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
            status=str(data.get("status") or "pending"),
            blocker=data.get("blocker") if isinstance(data.get("blocker"), dict) else None,
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
            "status": self.status,
            "blocker": self.blocker,
        }


@dataclass
class Goal:
    id: str
    title: str
    source: SourceType
    status: GoalStatus = "draft"
    ac_gate: GateStatus = "pending"
    plan_gate: GateStatus = "pending"
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    active_task_id: str | None = None
    deferred: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    ac_status: dict[str, str] = field(default_factory=dict)
    blockers: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Goal":
        return cls(
            id=str(data["id"]),
            title=str(data.get("title") or data["id"]),
            source=data.get("source") or "text",
            status=data.get("status") or "draft",
            ac_gate=data.get("ac_gate") or "pending",
            plan_gate=data.get("plan_gate") or "pending",
            created_at=str(data.get("created_at") or utc_now_iso()),
            updated_at=str(data.get("updated_at") or utc_now_iso()),
            active_task_id=data.get("active_task_id"),
            deferred=list(data.get("deferred") or []),
            evidence=list(data.get("evidence") or []),
            ac_status=dict(data.get("ac_status") or {}),
            blockers=list(data.get("blockers") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "source": self.source,
            "status": self.status,
            "ac_gate": self.ac_gate,
            "plan_gate": self.plan_gate,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "active_task_id": self.active_task_id,
            "deferred": self.deferred,
            "evidence": self.evidence,
            "ac_status": self.ac_status,
            "blockers": self.blockers,
        }

    def refresh_status_from_gates(self) -> None:
        if self.status == "cancelled":
            return
        if self.ac_gate == "approved" and self.plan_gate == "approved":
            self.status = "ready"
        elif self.status == "ready":
            self.status = "draft"
        self.updated_at = utc_now_iso()
