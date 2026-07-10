"""Narrow async-aware port contracts for Loop mode boundaries.

This module owns the contracts between Loop application services and their
external/adapters: repository state, broker snapshots, worker gateways, goal
evidence, and worktree evidence. Application services import only from here or
the domain; concrete adapters and the runtime composition module implement and
wire these ports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Protocol, runtime_checkable

if TYPE_CHECKING:
    from orchlink.loop.domain.item import LoopState, MakerResult, WorkerAssignment
    from orchlink.loop.domain.worktree import Worktree
    from orchlink.loop.services.triage_service import ItemCandidate
    from orchlink.loop.services.verifier_service import VerifierHandle


class LoopRepository(Protocol):
    """Read-only and transactional access to the loop state aggregate."""

    def read_only(self) -> LoopState:
        ...

    def transaction(self, actor: str) -> Iterator[LoopState]:
        """Yield the aggregate root inside a write transaction."""
        ...


@dataclass(frozen=True, slots=True)
class BrokerTaskStatus:
    """Normalized broker task snapshot used by loop recovery."""

    status: str
    result: Any | None = None


class BrokerStatusPort(Protocol):
    """Synchronous broker snapshot port consumed by loop recovery."""

    def get_task_status(self, task_id: str) -> BrokerTaskStatus | None:
        ...

    def get_session_active(self, lease_id: str) -> bool | None:
        ...


class WorkerGateway(Protocol):
    """Async boundary for maker/verifier dispatch and result collection."""

    async def dispatch_maker(self, maker_assignment: WorkerAssignment, prompt: str) -> VerifierHandle:
        ...

    async def dispatch_verifier(self, verifier_assignment: WorkerAssignment, prompt: str) -> VerifierHandle:
        ...

    async def await_result(self, handle: VerifierHandle, timeout_seconds: int) -> MakerResult:
        ...


@runtime_checkable
class MakerWorktreeResolverPort(Protocol):
    """Optional worker-gateway extension for resolving an isolated maker worktree."""

    async def maker_session_project_dir(self, worker_name: str) -> dict[str, Any] | None:
        ...


class GoalEvidencePort(Protocol):
    """Narrow attachment surface for loop verdict evidence into goal mode."""

    def attach_evidence(self, *, goal_id: str, evidence: dict[str, Any]) -> None:
        ...


@dataclass(frozen=True, slots=True)
class WorktreeEvidence:
    """Value object returned by worktree evidence collection."""

    changed_files: tuple[str, ...] | None = None
    diff_evidence: str | None = None
    unavailable_reason: str | None = None


class WorktreeEvidencePort(Protocol):
    """Sync boundary for collecting bounded git evidence from a worktree."""

    def collect(self, worktree: Worktree | None) -> WorktreeEvidence:
        ...


class Connector(Protocol):
    """Read-only triage source."""

    name: str

    async def discover(self) -> Iterable[ItemCandidate]:
        ...


__all__ = [
    "BrokerStatusPort",
    "BrokerTaskStatus",
    "Connector",
    "GoalEvidencePort",
    "LoopRepository",
    "MakerWorktreeResolverPort",
    "WorktreeEvidence",
    "WorktreeEvidencePort",
    "WorkerGateway",
]
