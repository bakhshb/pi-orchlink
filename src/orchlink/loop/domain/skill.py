"""Skill value object used by loop triage and execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    path: str | None = None
    description: str = ""
    body: str = ""
    invocation_rule: str = ""

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("skill name is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "description": self.description,
            "body": self.body,
            "invocation_rule": self.invocation_rule,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Skill | None":
        if data is None:
            return None
        return cls(
            name=data["name"],
            path=data.get("path"),
            description=data.get("description", ""),
            body=data.get("body", ""),
            invocation_rule=data.get("invocation_rule", data.get("invocation rule", "")),
        )
