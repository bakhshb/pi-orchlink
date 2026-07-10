"""Foreground loop engine orchestration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from orchlink.loop.domain.errors import IllegalTransition
from orchlink.loop.domain.item import LoopAttempt, LoopItem, LoopItemState
from orchlink.loop.domain.verdict import ReasonCode, Verdict
from orchlink.loop.ports import BrokerStatusPort, GoalEvidencePort, WorkerGateway
from orchlink.loop.services.loop_service import LoopService
from orchlink.loop.services.objective_check_service import ObjectiveCheckService
from orchlink.loop.services.triage_service import TriageService
from orchlink.loop.services.verifier_service import VerifierHandle, VerifierService, WorkerGatewayUnavailable
from orchlink.loop.services.worker_service import MakerDispatchError, MakerTimeoutError, MakerUnreachable, MakerWorktreeUnavailable, WorkerService

log = logging.getLogger(__name__)

@dataclass(slots=True)
class TickResult:
    recovered: int = 0
    triaged: int = 0
    items_dispatched: int = 0
    items_advanced: int = 0
    items_verified: int = 0
    items_blocked: int = 0
    items_done: int = 0
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RunSummary:
    steps: int = 0
    ticks: int = 0
    items_dispatched: int = 0
    items_verified: int = 0
    items_blocked: int = 0
    items_done: int = 0
    stopped: bool = False
    stop_reason: str = ""
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class LoopEngine:
    """Caller-driven foreground loop engine.

    The engine does not start background workers, threads, processes, cron jobs, or
    schedulers, and it never creates an event loop. Callers drive it with
    ``await tick()`` or ``await run()`` from inside an active event loop; the one
    synchronous wrapper that owns the event loop lives at the Typer CLI edge.
    """

    _ACTIVE_STATES = {
        LoopItemState.DISPATCHING,
        LoopItemState.RUNNING,
        LoopItemState.AWAITING_VERDICT,
        LoopItemState.VERIFYING,
    }

    def __init__(
        self,
        config: dict[str, Any] | None,
        loop_service: LoopService,
        triage_service: TriageService | None = None,
        verifier_service: VerifierService | None = None,
        worker_gateway: WorkerGateway | None = None,
        worker_service: WorkerService | None = None,
        broker_client: BrokerStatusPort | None = None,
        goal_service: GoalEvidencePort | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.loop_service = loop_service
        self.triage_service = triage_service
        self.verifier_service = verifier_service
        self.worker_gateway = worker_gateway
        self.worker_service = worker_service or (WorkerService(self.config, worker_gateway) if worker_gateway is not None else None)
        self.broker_client = broker_client
        self.goal_service = goal_service
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.sleeper = sleeper or asyncio.sleep
        self.stopped = False
        self._tick_items_verified = 0
        self._tick_items_blocked = 0
        self._tick_items_done = 0
        self._tick_notes: list[str] = []

    async def run(
        self,
        *,
        max_steps: int = 10,
        interval_seconds: float = 5.0,
        allow_active_attempts: bool = False,
    ) -> RunSummary:
        summary = RunSummary()
        while not self.stopped and summary.steps < max_steps:
            try:
                result = await self.tick(allow_active_attempts=allow_active_attempts)
            except (KeyboardInterrupt, asyncio.CancelledError):
                summary.stopped = True
                summary.stop_reason = "cancelled"
                summary.notes.append("stopped by cancellation")
                self.stop()
                break
            except Exception as exc:
                summary.stopped = True
                summary.stop_reason = "error"
                summary.errors.append(f"tick failed: {exc}")
                self.stop()
                break
            summary.steps += 1
            summary.ticks += 1
            summary.items_dispatched += result.items_dispatched
            summary.items_verified += result.items_verified
            summary.items_blocked += result.items_blocked
            summary.items_done += result.items_done
            summary.errors.extend(result.errors)
            summary.notes.extend(result.notes)
            if result.errors:
                summary.stopped = True
                summary.stop_reason = "error"
                self.stop()
                break
            if any("active attempts present" in note for note in result.notes) and not allow_active_attempts:
                summary.stopped = True
                summary.stop_reason = "active_attempts"
                break
            if self.stopped:
                summary.stopped = True
                summary.stop_reason = "stopped"
                break
            if summary.steps >= max_steps:
                break
            try:
                await self.sleeper(interval_seconds)
            except (KeyboardInterrupt, asyncio.CancelledError):
                summary.stopped = True
                summary.stop_reason = "cancelled"
                summary.notes.append("stopped by cancellation")
                self.stop()
                break
        return summary

    async def tick(self, *, allow_active_attempts: bool = False) -> TickResult:
        result = TickResult()
        self._tick_items_verified = 0
        self._tick_items_blocked = 0
        self._tick_items_done = 0
        self._tick_notes = []

        try:
            result.recovered = self._recover_once()
        except Exception as exc:
            result.errors.append(f"recover failed: {exc}")

        has_active_attempts = self._has_active_attempts()
        if has_active_attempts and not allow_active_attempts:
            result.items_verified = self._tick_items_verified
            result.items_blocked = self._tick_items_blocked
            result.items_done = self._tick_items_done
            result.notes.extend(self._tick_notes)
            result.notes.append("active attempts present; refused dispatch/advance (set allow_active_attempts=True)")
            return result
        if has_active_attempts and allow_active_attempts:
            log.warning("loop engine running with active attempts present")

        try:
            result.triaged = await self._triage_once()
        except Exception as exc:
            result.errors.append(f"triage failed: {exc}")

        limit = int(self.config.get("per_tick_dispatch_limit", self.config.get("dispatch_limit", 10)))
        try:
            result.items_dispatched = await self._dispatch_ready(limit=limit)
        except Exception as exc:
            result.errors.append(f"dispatch failed: {exc}")

        try:
            result.items_advanced = await self._advance_open_attempts(limit=limit)
        except Exception as exc:
            result.errors.append(f"advance failed: {exc}")

        result.items_verified = self._tick_items_verified
        result.items_blocked = self._tick_items_blocked
        result.items_done = self._tick_items_done
        result.notes.extend(self._tick_notes)
        return result

    def stop(self) -> None:
        self.stopped = True

    def _recover_once(self) -> int:
        # If no maker service is configured, active maker states should be
        # handled by the foreground advance path as maker_unavailable instead of
        # being preemptively converted to broker_unavailable by recovery.
        if self.worker_service is None and self.broker_client is None:
            return 0
        report = self.loop_service.recover(broker_client=self.broker_client)
        self._tick_items_blocked += report.items_blocked
        for note in report.notes:
            if "broker_unavailable" in note:
                self._note_once("broker_unavailable")
        return report.items_changed

    async def _triage_once(self) -> int:
        if self.triage_service is None:
            return 0
        created = await self.triage_service.run_once()
        return len(created)

    async def _dispatch_ready(self, limit: int) -> int:
        dispatched = 0
        maker_worker = str(self.config.get("maker_worker", "maker"))
        require_worktree = self._require_worktree_isolation()
        for item in self.loop_service.ls():
            if dispatched >= limit:
                break
            if item.state is not LoopItemState.READY:
                continue
            worktree = item.worktree
            session_lease_id = None
            if require_worktree:
                if self.worker_service is None:
                    self._block_maker_failure(item, "maker_worktree_unavailable")
                    continue
                try:
                    maker_worktree = await self.worker_service.resolve_maker_worktree(maker_worker)
                except (MakerWorktreeUnavailable, MakerUnreachable, WorkerGatewayUnavailable):
                    self._block_maker_failure(item, "maker_worktree_unavailable")
                    continue
                worktree = maker_worktree.worktree
                session_lease_id = maker_worktree.session_lease_id
            try:
                self.loop_service.next_item(
                    item.item_id,
                    maker_worker=maker_worker,
                    worktree=worktree,
                    session_lease_id=session_lease_id,
                )
            except IllegalTransition:
                continue
            dispatched += 1
        return dispatched

    async def _advance_open_attempts(self, limit: int) -> int:
        advanced_items = 0
        for item in self.loop_service.ls():
            if advanced_items >= limit:
                break
            if item.state not in self._ACTIVE_STATES:
                continue
            advanced = await self._advance_item_until_waiting(item.item_id)
            if advanced:
                advanced_items += 1
        return advanced_items

    async def _advance_item_until_waiting(self, item_id: str) -> int:
        advanced = 0
        while True:
            item = self.loop_service.get(item_id)
            if item is None or item.state not in self._ACTIVE_STATES:
                return advanced
            attempt = item.attempts[-1]

            if item.state is LoopItemState.DISPATCHING:
                if await self._dispatch_maker(item, attempt):
                    advanced += 1
                    continue
                return advanced + 1

            if item.state is LoopItemState.RUNNING:
                if await self._collect_maker_result(item, attempt):
                    advanced += 1
                    continue
                return advanced + 1

            if item.state is LoopItemState.AWAITING_VERDICT:
                if self.verifier_service is None:
                    self.loop_service.block(item.item_id, reason="verifier_unavailable", reason_code=ReasonCode.BLOCKED)
                    self._tick_items_blocked += 1
                    self._note_once("verifier_unavailable")
                    advanced += 1
                    continue
                verifier_worker = str(self.config.get("verifier_worker", "review"))
                allow_same_worker = not item.verify_policy.require_separate_verifier_worker
                self.verifier_service.validate_separation(
                    attempt.maker.worker_name,
                    verifier_worker,
                    allow_same_worker=allow_same_worker,
                )
                self.loop_service.reserve_verification(
                    item.item_id,
                    attempt_no=attempt.number,
                    verifier_worker=verifier_worker,
                    allow_same_worker=allow_same_worker,
                )
                advanced += 1
                continue

            if item.state is LoopItemState.VERIFYING:
                if self.broker_client is None:
                    self.loop_service.block(item.item_id, reason="broker_unavailable", reason_code=ReasonCode.BLOCKED)
                    self._tick_items_blocked += 1
                    self._note_once("broker_unavailable")
                    advanced += 1
                    continue
                if self.verifier_service is None:
                    self.loop_service.block(item.item_id, reason="verifier_unavailable", reason_code=ReasonCode.BLOCKED)
                    self._tick_items_blocked += 1
                    self._note_once("verifier_unavailable")
                    advanced += 1
                    continue
                run_checks = bool(self.config.get("run_checks", False))
                if run_checks:
                    verdict = await self.verifier_service.dispatch_and_collect(
                        item,
                        attempt,
                        worktree=item.worktree,
                        run_checks=True,
                        check_service=ObjectiveCheckService(self.config),
                    )
                else:
                    verdict = await self.verifier_service.dispatch_and_collect(
                        item,
                        attempt,
                        worktree=item.worktree,
                    )
                application = self.loop_service.apply_verdict(
                    item.item_id,
                    attempt_no=attempt.number,
                    verdict=verdict,
                    allow_same_worker=not item.verify_policy.require_separate_verifier_worker,
                )
                self._tick_items_verified += 1
                if application.item.state is LoopItemState.DONE:
                    self._tick_items_done += 1
                    if verdict.verdict is Verdict.ACCEPTED and self.loop_service.attach_evidence_to_goal(
                        application.item.item_id,
                        goal_service=self.goal_service,
                    ):
                        self._tick_notes.append(f"{application.item.item_id}: goal_evidence_attached")
                if application.item.state is LoopItemState.BLOCKED:
                    self._tick_items_blocked += 1
                advanced += 1
                continue

            return advanced

    async def _dispatch_maker(self, item: LoopItem, attempt: LoopAttempt) -> bool:
        if self.worker_service is None:
            self._block_maker_failure(item, "maker_unavailable")
            return False
        try:
            handle = await self.worker_service.start_maker(
                item,
                attempt,
                worktree=item.worktree,
            )
            self.loop_service.mark_dispatched(item.item_id, attempt_no=attempt.number, task_id=handle.task_id)
            self.loop_service.mark_running(item.item_id, attempt_no=attempt.number)
            return True
        except (MakerUnreachable, WorkerGatewayUnavailable):
            self._block_maker_failure(item, "maker_unavailable")
        except MakerTimeoutError:
            self._block_maker_failure(item, "maker_timeout")
        except MakerDispatchError:
            self._block_maker_failure(item, "maker_dispatch_error")
        return False

    async def _collect_maker_result(self, item: LoopItem, attempt: LoopAttempt) -> bool:
        if self.worker_service is None:
            self._block_maker_failure(item, "maker_unavailable")
            return False
        try:
            handle = VerifierHandle(task_id=attempt.maker.task_id or "", worker_name=attempt.maker.worker_name)
            result = await self.worker_service.await_maker_result(handle, timeout_seconds=1800)
            self.loop_service.collect_maker_result(item.item_id, attempt_no=attempt.number, result=result)
            return True
        except (MakerUnreachable, WorkerGatewayUnavailable):
            self._block_maker_failure(item, "maker_unavailable")
        except MakerTimeoutError:
            self._block_maker_failure(item, "maker_timeout")
        except MakerDispatchError:
            self._block_maker_failure(item, "maker_dispatch_error")
        return False

    def _block_maker_failure(self, item: LoopItem, reason: str) -> None:
        self.loop_service.block(item.item_id, reason=reason, reason_code=ReasonCode.BLOCKED)
        self._tick_items_blocked += 1
        self._note_once(f"{item.item_id}: {reason}")

    def _require_worktree_isolation(self) -> bool:
        loop_config = self.config.get("loop") if isinstance(self.config.get("loop"), dict) else {}
        return bool(self.config.get("require_worktree_isolation") or loop_config.get("require_worktree_isolation"))

    def _has_active_attempts(self) -> bool:
        return any(item.state in self._ACTIVE_STATES for item in self.loop_service.ls())

    def _note_once(self, note: str) -> None:
        if note not in self._tick_notes:
            self._tick_notes.append(note)
