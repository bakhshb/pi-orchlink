"""Pure worktree value objects.

Pure Phase 0 cannot prove git cleanliness from disk. ``clean`` is a snapshot
provided by an adapter/caller at construction time, or ``None`` when unknown.
The domain object never shells out. Real git worktree operations belong at the edge.
"""

from __future__ import annotations

from dataclasses import InitVar, dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class WorktreeResult:
    ok: bool
    operation: str
    path: str
    message: str = ""
    error: str | None = None


@dataclass(frozen=True, slots=True)
class Worktree:
    path: str
    branch: str | None = None
    base_ref: str | None = None
    clean: bool | None = None
    cleanliness_probe: InitVar[Callable[[], bool | None] | None] = None

    def __post_init__(self, cleanliness_probe: Callable[[], bool | None] | None) -> None:
        if not self.path:
            raise ValueError("worktree path is required")
        object.__setattr__(self, "path", str(Path(self.path)))
        if cleanliness_probe is not None:
            object.__setattr__(self, "clean", cleanliness_probe())

    def is_clean(self) -> bool | None:
        return self.clean

    def create(self) -> WorktreeResult:
        try:
            return WorktreeResult(ok=True, operation="create", path=self.path, message="worktree create requested")
        except Exception as exc:  # pragma: no cover - defensive: method must never raise
            return WorktreeResult(ok=False, operation="create", path=self.path, error=str(exc))

    def remove(self) -> WorktreeResult:
        try:
            return WorktreeResult(ok=True, operation="remove", path=self.path, message="worktree remove requested")
        except Exception as exc:  # pragma: no cover - defensive: method must never raise
            return WorktreeResult(ok=False, operation="remove", path=self.path, error=str(exc))

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "branch": self.branch, "base_ref": self.base_ref, "clean": self.clean}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Worktree | None":
        if data is None:
            return None
        return cls(
            path=data["path"],
            branch=data.get("branch"),
            base_ref=data.get("base_ref"),
            clean=data.get("clean"),
        )
