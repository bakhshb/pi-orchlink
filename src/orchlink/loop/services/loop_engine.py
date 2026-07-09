"""Foreground loop engine orchestration."""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from orchlink.loop.domain.errors import IllegalTransition
from orchlink.loop.domain.item import LoopAttempt, LoopItem, LoopItemState, MakerResult
from orchlink.loop.domain.verdict import ReasonCode, Verdict, VerifierVerdict
from orchlink.loop.services.loop_service import LoopService
from orchlink.loop.services.objective_check_service import ObjectiveCheckService
from orchlink.loop.services.triage_service import TriageService
from orchlink.loop.services.verifier_service import VerifierHandle, VerifierService, WorkerGateway, WorkerGatewayUnavailable
from orchlink.loop.services.worker_service import MakerDispatchError, MakerTimeoutError, MakerUnreachable, WorkerService

log = logging.getLogger(__name__)


class BrokerClient(Protocol):
    def get_task_status(self, task_id: str) -> Any | None:
        ...

    def get_session_active(self, lease_id: str) -> bool | None:
        ...


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
    schedulers. Callers drive it with ``tick()`` or ``run()``. Call ``tick()``
    from synchronous code; async connector/verifier calls are driven internally,
    so do not call it from an active event loop.
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
        broker_client: BrokerClient | None = None,
        goal_service: object | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] | None = None,
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
        self.sleeper = sleeper or time.sleep
        self.stopped = False
        self._tick_items_verified = 0
        self._tick_items_blocked = 0
        self._tick_items_done = 0
        self._tick_notes: list[str] = []

    def run(
        self,
        *,
        max_steps: int = 10,
        interval_seconds: float = 5.0,
        allow_active_attempts: bool = False,
    ) -> RunSummary:
        summary = RunSummary()
        while not self.stopped and summary.steps < max_steps:
            try:
                result = self.tick(allow_active_attempts=allow_active_attempts)
            except KeyboardInterrupt:
                summary.stopped = True
                summary.stop_reason = "keyboard_interrupt"
                summary.notes.append("stopped by KeyboardInterrupt")
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
                self.sleeper(interval_seconds)
            except KeyboardInterrupt:
                summary.stopped = True
                summary.stop_reason = "keyboard_interrupt"
                summary.notes.append("stopped by KeyboardInterrupt")
                self.stop()
                break
        return summary

    def tick(self, *, allow_active_attempts: bool = False) -> TickResult:
        result = TickResult()
        has_active_attempts = self._has_active_attempts()
        if has_active_attempts and not allow_active_attempts:
            result.notes.append("active attempts present; refused dispatch/advance (set allow_active_attempts=True)")
            return result
        if has_active_attempts and allow_active_attempts:
            log.warning("loop engine running with active attempts present")

        self._tick_items_verified = 0
        self._tick_items_blocked = 0
        self._tick_items_done = 0
        self._tick_notes = []

        try:
            result.recovered = self._recover_once()
        except Exception as exc:
            result.errors.append(f"recover failed: {exc}")

        try:
            result.triaged = self._triage_once()
        except Exception as exc:
            result.errors.append(f"triage failed: {exc}")

        limit = int(self.config.get("per_tick_dispatch_limit", self.config.get("dispatch_limit", 10)))
        try:
            result.items_dispatched = self._dispatch_ready(limit=limit)
        except Exception as exc:
            result.errors.append(f"dispatch failed: {exc}")

        try:
            result.items_advanced = self._advance_open_attempts(limit=limit)
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

    def _triage_once(self) -> int:
        if self.triage_service is None:
            return 0
        created = self._await_if_needed(self.triage_service.run_once())
        return len(created)

    def _dispatch_ready(self, limit: int) -> int:
        dispatched = 0
        maker_worker = str(self.config.get("maker_worker", "maker"))
        for item in self.loop_service.ls():
            if dispatched >= limit:
                break
            if item.state is not LoopItemState.READY:
                continue
            try:
                self.loop_service.next_item(item.item_id, maker_worker=maker_worker, worktree=item.worktree)
            except IllegalTransition:
                continue
            dispatched += 1
        return dispatched

    def _advance_open_attempts(self, limit: int) -> int:
        advanced_items = 0
        for item in self.loop_service.ls():
            if advanced_items >= limit:
                break
            if item.state not in self._ACTIVE_STATES:
                continue
            advanced = self._advance_item_until_waiting(item.item_id)
            if advanced:
                advanced_items += 1
        return advanced_items

    def _advance_item_until_waiting(self, item_id: str) -> int:
        advanced = 0
        while True:
            item = self.loop_service.get(item_id)
            if item is None or item.state not in self._ACTIVE_STATES:
                return advanced
            attempt = item.attempts[-1]

            if item.state is LoopItemState.DISPATCHING:
                if self._dispatch_maker(item, attempt):
                    advanced += 1
                    continue
                return advanced + 1

            if item.state is LoopItemState.RUNNING:
                if self._collect_maker_result(item, attempt):
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
                    verdict = self._await_if_needed(
                        self.verifier_service.dispatch_and_collect(
                            item,
                            attempt,
                            worktree=item.worktree,
                            run_checks=True,
                            check_service=ObjectiveCheckService(self.config),
                        )
                    )
                else:
                    verdict = self._await_if_needed(
                        self.verifier_service.dispatch_and_collect(
                            item,
                            attempt,
                            worktree=item.worktree,
                        )
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

    def _dispatch_maker(self, item: LoopItem, attempt: LoopAttempt) -> bool:
        if self.worker_service is None:
            self._block_maker_failure(item, "maker_unavailable")
            return False
        try:
            handle = self._await_if_needed(
                self.worker_service.start_maker(
                    item,
                    attempt,
                    worktree=item.worktree,
                )
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

    def _collect_maker_result(self, item: LoopItem, attempt: LoopAttempt) -> bool:
        if self.worker_service is None:
            self._block_maker_failure(item, "maker_unavailable")
            return False
        try:
            handle = VerifierHandle(task_id=attempt.maker.task_id or "", worker_name=attempt.maker.worker_name)
            result = self._await_if_needed(self.worker_service.await_maker_result(handle, timeout_seconds=1800))
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

    def _has_active_attempts(self) -> bool:
        return any(item.state in self._ACTIVE_STATES for item in self.loop_service.ls())

    def _note_once(self, note: str) -> None:
        if note not in self._tick_notes:
            self._tick_notes.append(note)

    def _maker_task_id(self, item_id: str, attempt_no: int) -> str:
        return f"engine:maker:{item_id}:{attempt_no}"

    def _status_name(self, raw: Any | None) -> str | None:
        if raw is None:
            return None
        if isinstance(raw, MakerResult):
            return "completed"
        if isinstance(raw, VerifierVerdict):
            return "completed"
        if isinstance(raw, str):
            return raw.lower()
        if isinstance(raw, dict):
            status = raw.get("status")
            if status is None and raw.get("result") is not None:
                return "completed"
            return str(status).lower() if status is not None else None
        status = getattr(raw, "status", None)
        result = getattr(raw, "result", None)
        if status is None and result is not None:
            return "completed"
        return str(status).lower() if status is not None else None

    def _maker_result(self, raw: Any | None) -> MakerResult:
        result = raw
        if isinstance(raw, dict):
            result = raw.get("result")
        elif hasattr(raw, "result"):
            result = getattr(raw, "result")
        if isinstance(result, MakerResult):
            return result
        if isinstance(result, str):
            return MakerResult(result)
        return MakerResult("completed")

    def _await_if_needed(self, value: Any) -> Any:
        if inspect.isawaitable(value):
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(value)
            close = getattr(value, "close", None)
            if close is not None:
                close()
            raise RuntimeError("LoopEngine.tick() must be called from sync code; do not call it from an active event loop")
        return value
