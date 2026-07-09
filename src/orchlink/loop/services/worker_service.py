"""Worker dispatch orchestration over the shared WorkerGateway boundary."""

from __future__ import annotations

import asyncio
from typing import Any

from orchlink.loop.domain.item import LoopAttempt, LoopItem, MakerResult, WorkerAssignment
from orchlink.loop.domain.worktree import Worktree
from orchlink.loop.services.verifier_service import VerifierHandle, WorkerGateway, WorkerGatewayUnavailable


class MakerDispatchError(RuntimeError):
    """Raised when maker dispatch fails before a result is available."""


class MakerTimeoutError(TimeoutError):
    """Raised when maker dispatch or result collection times out."""


class MakerUnreachable(WorkerGatewayUnavailable):
    """Raised when no maker gateway is available for dispatch."""


class WorkerService:
    """Thin orchestrator for maker/verifier dispatch through WorkerGateway.

    The gateway Protocol remains the edge boundary. This service only builds the
    common loop prompts and maps gateway failures into loop service errors.
    """

    def __init__(self, config: dict[str, Any] | None, gateway: WorkerGateway | None = None) -> None:
        self.config = dict(config or {})
        self.gateway = gateway

    def build_maker_prompt(self, item: LoopItem, attempt: LoopAttempt, worktree: Worktree | None) -> str:
        objective = item.title or item.source or item.item_id
        worktree_line = "WORKTREE: none"
        if worktree is not None:
            worktree_line = f"WORKTREE: {worktree.path}"
        return "\n".join(
            [
                "# Orchlink Loop Maker",
                f"ITEM_ID: {item.item_id}",
                f"ATTEMPT: {attempt.number}",
                f"MAKER_WORKER: {attempt.maker.worker_name}",
                f"OBJECTIVE: {objective}",
                worktree_line,
                "Implement the requested loop item in the provided working scope.",
                "Reply with a concise result summary and any blocker if the work cannot proceed.",
            ]
        )

    async def start_maker(
        self,
        item: LoopItem,
        attempt: LoopAttempt,
        *,
        worktree: Worktree | None = None,
    ) -> VerifierHandle:
        if self.gateway is None:
            raise MakerUnreachable("WorkerService requires a WorkerGateway to dispatch maker work")
        dispatch_maker = getattr(self.gateway, "dispatch_maker", None)
        if dispatch_maker is None:
            raise MakerUnreachable("WorkerGateway does not support maker dispatch")
        prompt = self.build_maker_prompt(item, attempt, worktree)
        try:
            return await dispatch_maker(attempt.maker, prompt)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise MakerTimeoutError(str(exc) or "maker dispatch timed out") from exc
        except Exception as exc:
            raise MakerDispatchError(str(exc)) from exc

    async def await_maker_result(self, handle: VerifierHandle, timeout_seconds: int = 1800) -> MakerResult:
        if self.gateway is None:
            raise MakerUnreachable("WorkerService requires a WorkerGateway to await maker results")
        try:
            return await self.gateway.await_result(handle, timeout_seconds)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise MakerTimeoutError(str(exc) or "maker timed out") from exc
        except Exception as exc:
            raise MakerDispatchError(str(exc)) from exc

    async def dispatch_and_collect_maker(
        self,
        item: LoopItem,
        attempt: LoopAttempt,
        *,
        worktree: Worktree | None = None,
        timeout_seconds: int = 1800,
    ) -> MakerResult:
        handle = await self.start_maker(item, attempt, worktree=worktree)
        return await self.await_maker_result(handle, timeout_seconds=timeout_seconds)

    async def start_verifier(self, verifier_assignment: WorkerAssignment, prompt: str) -> VerifierHandle:
        if self.gateway is None:
            raise WorkerGatewayUnavailable("WorkerService requires a WorkerGateway to dispatch verifier work")
        try:
            return await self.gateway.dispatch_verifier(verifier_assignment, prompt)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise MakerTimeoutError(str(exc) or "verifier dispatch timed out") from exc
        except Exception as exc:
            raise MakerDispatchError(str(exc)) from exc


__all__ = ["MakerDispatchError", "MakerTimeoutError", "MakerUnreachable", "WorkerService"]
