"""Triage service and typed item candidates for loop mode."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchlink.loop.adapters.connectors.base import Connector
from orchlink.loop.domain.item import LoopItem
from orchlink.loop.domain.skill import Skill
from orchlink.loop.domain.worktree import Worktree
from orchlink.loop.services.loop_service import ItemCandidate as LoopItemCandidate
from orchlink.loop.services.loop_service import LoopService

log = logging.getLogger(__name__)


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


@dataclass(frozen=True, slots=True)
class SkillRef:
    name: str
    path: str | None = None


@dataclass(frozen=True, init=False, slots=True)
class ItemCandidate:
    """Typed triage candidate.

    Canonical source_type values are: manual, github, linear, local_git.
    The legacy alias "git" is accepted and normalized to "local_git".
    """
    id: str
    source_type: str
    source_ref: str
    title: str
    objective: str
    priority: Priority
    suggested_skill: SkillRef | None
    suggested_worktree: Worktree | None
    goal_id: str | None

    def __init__(
        self,
        id: str | None = None,
        *,
        item_id: str | None = None,
        source_type: str = "manual",
        source_ref: str = "",
        title: str = "",
        objective: str = "",
        priority: Priority | str = Priority.NORMAL,
        suggested_skill: SkillRef | None = None,
        suggested_worktree: Worktree | None = None,
        worktree: Worktree | None = None,
        goal_id: str | None = None,
    ) -> None:
        candidate_id = id or item_id
        if not candidate_id:
            raise ValueError("ItemCandidate id is required")
        canonical_source_type = "local_git" if source_type == "git" else source_type
        if canonical_source_type not in {"manual", "github", "linear", "local_git"}:
            raise ValueError("unsupported source_type")
        object.__setattr__(self, "id", candidate_id)
        object.__setattr__(self, "source_type", canonical_source_type)
        object.__setattr__(self, "source_ref", source_ref)
        object.__setattr__(self, "title", title or objective or candidate_id)
        object.__setattr__(self, "objective", objective or title or candidate_id)
        object.__setattr__(self, "priority", priority if isinstance(priority, Priority) else Priority(str(priority)))
        object.__setattr__(self, "suggested_skill", suggested_skill)
        object.__setattr__(self, "suggested_worktree", suggested_worktree if suggested_worktree is not None else worktree)
        object.__setattr__(self, "goal_id", goal_id)

    @property
    def item_id(self) -> str:
        return self.id

    @property
    def worktree(self) -> Worktree | None:
        return self.suggested_worktree

    @property
    def skill(self) -> Skill | None:
        if self.suggested_skill is None:
            return None
        return Skill(name=self.suggested_skill.name, path=self.suggested_skill.path)

    @property
    def source(self) -> str | None:
        if not self.source_ref:
            return None
        return f"{self.source_type}:{self.source_ref}"


class TriageService:
    def __init__(self, config: dict | None, loop_service: LoopService, connectors: list["Connector"]) -> None:
        self.config = dict(config or {})
        self.loop_service = loop_service
        self.connectors = list(connectors)

    async def run_once(self) -> list[LoopItem]:
        if not self.connectors:
            return []
        existing_keys = self._existing_dedupe_keys()
        seen_keys = set(existing_keys)
        candidates: list[ItemCandidate] = []
        for connector in self.connectors:
            try:
                discovered = await connector.discover()
            except Exception as exc:
                log.warning("loop triage connector %s failed: %s", getattr(connector, "name", "unknown"), exc)
                continue
            for candidate in discovered:
                key = self._candidate_dedupe_key(candidate)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                candidates.append(candidate)
        loop_candidates = [
            LoopItemCandidate(
                item_id=candidate.id,
                title=candidate.title,
                source_type=candidate.source_type,
                source_ref=candidate.source_ref,
                goal_id=candidate.goal_id,
                worktree=candidate.suggested_worktree,
                skill=Skill(name=candidate.suggested_skill.name, path=candidate.suggested_skill.path)
                if candidate.suggested_skill is not None
                else None,
            )
            for candidate in candidates
        ]
        return self.loop_service.triage(loop_candidates)

    def _existing_dedupe_keys(self) -> set[tuple[str, str]]:
        keys: set[tuple[str, str]] = set()
        for item in self.loop_service.ls():
            if item.source and ":" in item.source:
                source_type, source_ref = item.source.split(":", 1)
                keys.add((source_type, source_ref))
            else:
                keys.add(("id", item.item_id))
        return keys

    def _candidate_dedupe_key(self, candidate: ItemCandidate) -> tuple[str, str]:
        if candidate.source_ref:
            return (candidate.source_type, candidate.source_ref)
        return ("id", candidate.id)
