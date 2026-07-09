"""Application service over the pure loop kernel and markdown state repo."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Protocol, TypeAlias

from orchlink.loop.adapters.state_repo import LoopStateRepo
from orchlink.loop.domain.errors import IllegalTransition, VerifierMismatch
from orchlink.loop.domain.item import (
    LoopAttempt,
    LoopItem,
    LoopItemState,
    MakerResult,
    WorkerAssignment,
)
from orchlink.loop.domain.skill import Skill
from orchlink.loop.domain.verdict import ReasonCode, Verdict, VerifierVerdict, utc_now
from orchlink.loop.domain.worktree import Worktree

ItemId: TypeAlias = str
TaskId: TypeAlias = str

_RECOVER_BROKER_UNSET = object()

DEFAULT_RESERVATION_GRACE = timedelta(minutes=10)
DEFAULT_TASK_TIMEOUT = timedelta(hours=2)
RESERVED_TASK_PREFIX = "reserved:"
"""Internal sentinel prefix for reserved attempts; never send these ids to a real broker."""


@dataclass(frozen=True, slots=True)
class ItemCandidate:
    item_id: ItemId
    title: str = ""
    source_type: str | None = None
    source_ref: str | None = None
    goal_id: str | None = None
    worktree: Worktree | None = None
    skill: Skill | None = None

    @property
    def source(self) -> str | None:
        if self.source_type is None or self.source_ref is None:
            return None
        return f"{self.source_type}:{self.source_ref}"


@dataclass(frozen=True, slots=True)
class DispatchReservation:
    item: LoopItem
    attempt: LoopAttempt

    def __iter__(self):
        yield self.item
        yield self.attempt


@dataclass(frozen=True, slots=True)
class VerificationReservation:
    item: LoopItem
    attempt: LoopAttempt

    def __iter__(self):
        yield self.item
        yield self.attempt


@dataclass(frozen=True, slots=True)
class VerdictApplication:
    item: LoopItem
    lower_confidence: bool = False
    note: str | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.item, name)


@dataclass(frozen=True, slots=True)
class RecoveryReport:
    items_changed: int
    items_blocked: int
    items_resumed: int
    notes: list[str]


@dataclass(frozen=True, slots=True)
class BrokerTaskStatus:
    status: str
    result: MakerResult | VerifierVerdict | None = None


class RecoverableBroker(Protocol):
    def get_task_status(self, task_id: str) -> BrokerTaskStatus | str | dict[str, Any] | None:
        ...

    def get_session_active(self, lease_id: str) -> bool | None:
        ...


class LoopService:
    def __init__(
        self,
        config: dict[str, Any] | None,
        repo: LoopStateRepo,
        *,
        broker: RecoverableBroker | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.repo = repo
        self.broker = broker

    def triage(self, candidates: Iterable[ItemCandidate]) -> list[LoopItem]:
        created: list[LoopItem] = []
        with self.repo.transaction("loop:triage") as state:
            existing = {item.item_id for item in state.items}
            for candidate in candidates:
                if candidate.item_id in existing:
                    continue
                now = utc_now()
                item = LoopItem(
                    item_id=candidate.item_id,
                    title=candidate.title,
                    source=candidate.source,
                    goal_id=candidate.goal_id,
                    worktree=candidate.worktree,
                    skill=candidate.skill,
                    created_at=now,
                    updated_at=now,
                    _state=LoopItemState.TRIAGED,
                )
                state.add_item(item)
                existing.add(item.item_id)
                created.append(item)
        return created

    def ready(self, item_id: ItemId) -> LoopItem:
        # Actor attribution is not modeled in LoopItem yet; callers needing audit
        # should record it outside the Phase 2 service state.
        with self.repo.transaction("loop:ready") as state:
            item = state.item(item_id)
            updated = item.ready()
            state.replace_item(updated)
            return updated

    def next_item(
        self,
        item_id: ItemId,
        *,
        maker_worker: str,
        worktree: Worktree | None,
    ) -> DispatchReservation:
        with self.repo.transaction("loop:next") as state:
            item = state.item(item_id)
            if item.state is not LoopItemState.READY:
                raise IllegalTransition(item.state, "next_item")
            maker = WorkerAssignment(
                worker_name=maker_worker,
                task_id=f"reserved:{item.item_id}:{len(item.attempts) + 1}",
                project_dir=worktree.path if worktree is not None else None,
            )
            updated = item.dispatch(maker)
            if worktree is not None:
                updated = replace(updated, worktree=worktree)
            attempt = updated.attempts[-1]
            state.replace_item(updated)
            return DispatchReservation(updated, attempt)

    def mark_dispatched(self, item_id: ItemId, *, attempt_no: int, task_id: TaskId) -> LoopItem:
        with self.repo.transaction("loop:mark_dispatched") as state:
            item = state.item(item_id)
            self._require_attempt(item, attempt_no, "mark_dispatched")
            if item.state is not LoopItemState.DISPATCHING:
                raise IllegalTransition(item.state, "mark_dispatched")
            if task_id.startswith(RESERVED_TASK_PREFIX):
                raise ValueError(f"real broker task ids must not start with {RESERVED_TASK_PREFIX!r}")
            attempt = item.attempts[-1]
            maker = replace(attempt.maker, task_id=task_id, dispatched_at=utc_now())
            updated_attempt = replace(attempt, maker=maker)
            updated = replace(item, attempts=(*item.attempts[:-1], updated_attempt), updated_at=utc_now())
            state.replace_item(updated)
            return updated

    def rollback_dispatch(self, item_id: ItemId, attempt_no: int) -> LoopItem:
        with self.repo.transaction("loop:rollback_dispatch") as state:
            item = state.item(item_id)
            self._require_attempt(item, attempt_no, "rollback_dispatch")
            if item.state is not LoopItemState.DISPATCHING:
                raise IllegalTransition(item.state, "rollback_dispatch")
            updated = replace(
                item,
                attempts=item.attempts[:-1],
                _state=LoopItemState.READY,
                updated_at=utc_now(),
            )
            state.replace_item(updated)
            return updated

    def mark_running(self, item_id: ItemId, *, attempt_no: int) -> LoopItem:
        with self.repo.transaction("loop:mark_running") as state:
            item = state.item(item_id)
            self._require_attempt(item, attempt_no, "mark_running")
            if item.state is not LoopItemState.DISPATCHING:
                raise IllegalTransition(item.state, "mark_running")
            updated = replace(item, _state=LoopItemState.RUNNING, updated_at=utc_now())
            state.replace_item(updated)
            return updated

    def collect_maker_result(self, item_id: ItemId, *, attempt_no: int, result: MakerResult) -> LoopItem:
        with self.repo.transaction("loop:collect") as state:
            item = state.item(item_id)
            self._require_attempt(item, attempt_no, "collect_maker_result")
            updated = item.collect_result(result)
            state.replace_item(updated)
            return updated

    def reserve_verification(
        self,
        item_id: ItemId,
        *,
        attempt_no: int,
        verifier_worker: str,
    ) -> VerificationReservation:
        with self.repo.transaction("loop:reserve_verification") as state:
            item = state.item(item_id)
            self._require_attempt(item, attempt_no, "reserve_verification")
            verifier = WorkerAssignment(
                worker_name=verifier_worker,
                task_id=f"verify:{item.item_id}:{attempt_no}",
            )
            # Separation is enforced at apply_verdict so callers can reserve first
            # and get a deterministic VerifierMismatch at the verdict gate.
            updated = item.start_verification(verifier, allow_same_worker=True)
            attempt = updated.attempts[-1]
            state.replace_item(updated)
            return VerificationReservation(updated, attempt)

    def apply_verdict(
        self,
        item_id: ItemId,
        *,
        attempt_no: int,
        verdict: VerifierVerdict,
        allow_same_worker: bool = False,
    ) -> VerdictApplication:
        with self.repo.transaction("loop:apply_verdict") as state:
            item = state.item(item_id)
            self._require_attempt(item, attempt_no, "apply_verdict")
            if item.state is not LoopItemState.VERIFYING:
                raise IllegalTransition(item.state, "apply_verdict")
            attempt = item.attempts[-1]
            if attempt.verifier is None:
                raise IllegalTransition(item.state, "apply_verdict")
            same_worker = attempt.verifier.same_worker(attempt.maker)
            if same_worker and not allow_same_worker:
                raise VerifierMismatch("verifier worker must differ from maker worker")
            updated = item.apply_verdict(verdict)
            note = None
            if verdict.verdict is Verdict.REJECTED and len(updated.attempts) >= updated.retry_policy.max_attempts:
                updated = replace(updated, _state=LoopItemState.BLOCKED, blocker="retry_exhausted", updated_at=utc_now())
            if same_worker and allow_same_worker:
                note = "same_worker_verifier_override"
            state.replace_item(updated)
            return VerdictApplication(updated, lower_confidence=bool(same_worker and allow_same_worker), note=note)

    def cancel(self, item_id: ItemId, *, reason: str) -> LoopItem:
        with self.repo.transaction("loop:cancel") as state:
            item = state.item(item_id)
            updated = item.cancel(reason)
            state.replace_item(updated)
            return updated

    def block(
        self,
        item_id: ItemId,
        *,
        reason: str,
        reason_code: ReasonCode | None = None,
        actor: str | None = None,
    ) -> LoopItem:
        # reason_code and actor are accepted for the public service contract;
        # LoopItem currently persists only the human-readable blocker reason.
        if reason_code is None:
            reason_code = ReasonCode.BLOCKED
        _ = (reason_code, actor)
        with self.repo.transaction("loop:block") as state:
            item = state.item(item_id)
            updated = item.block(reason)
            state.replace_item(updated)
            return updated

    def attach_evidence_to_goal(self, item_id: ItemId, *, goal_service: object) -> bool:
        with self.repo.transaction("loop:attach_goal_evidence") as state:
            item = state.item(item_id)
            if goal_service is None or item.goal_id is None or item.state is not LoopItemState.DONE:
                return False
            attempt = item.attempts[-1] if item.attempts else None
            verdict = attempt.verdict if attempt is not None else None
            if verdict is None or verdict.verdict is not Verdict.ACCEPTED:
                return False
            evidence_id = f"goal:{item.goal_id}:loop:{item.item_id}:attempt:{attempt.number}:accepted"
            if evidence_id in item.attached_evidence_ids:
                return True
            attach_evidence = getattr(goal_service, "attach_evidence", None)
            if attach_evidence is None:
                return False
            attach_evidence(
                goal_id=item.goal_id,
                evidence={
                    "type": "loop_verdict",
                    "evidence_id": evidence_id,
                    "loop_item_id": item.item_id,
                    "attempt_no": attempt.number,
                    "task_id": verdict.task_id or (attempt.verifier.task_id if attempt.verifier else None),
                    "verdict": verdict.verdict.value,
                    "reason_code": verdict.reason_code.value,
                    "detail": verdict.detail,
                    "verifier_worker": verdict.verifier_worker,
                    "passed": True,
                    "summary": f"Loop item {item.item_id} accepted by {verdict.verifier_worker}.",
                },
            )
            updated = replace(item, attached_evidence_ids=(*item.attached_evidence_ids, evidence_id))
            state.replace_item(updated)
            return True

    def recover(self, broker_client: RecoverableBroker | None | object = _RECOVER_BROKER_UNSET) -> RecoveryReport:
        changed = 0
        blocked = 0
        resumed = 0
        notes: list[str] = []
        broker = self.broker if broker_client is _RECOVER_BROKER_UNSET else broker_client
        unavailable_reason = "stale_unrecoverable" if broker_client is _RECOVER_BROKER_UNSET else "broker_unavailable"
        with self.repo.transaction("loop:recover") as state:
            for item in list(state.items):
                if item.state not in {LoopItemState.DISPATCHING, LoopItemState.RUNNING, LoopItemState.VERIFYING}:
                    continue
                updated = self._recover_item(item, notes, broker, unavailable_reason=unavailable_reason)
                if updated is item:
                    continue
                changed += 1
                if updated.state is LoopItemState.BLOCKED:
                    blocked += 1
                if updated.state in {LoopItemState.READY, LoopItemState.RUNNING}:
                    resumed += 1
                state.replace_item(updated)
        return RecoveryReport(changed, blocked, resumed, notes)

    def ls(self) -> list[LoopItem]:
        return list(self.repo.read_only().items)

    def get(self, item_id: ItemId) -> LoopItem | None:
        try:
            return self.repo.read_only().item(item_id)
        except KeyError:
            return None

    def find_by_source_ref(self, source_type: str, ref: str) -> LoopItem | None:
        source_type = "local_git" if source_type == "git" else source_type
        expected = f"{source_type}:{ref}"
        for item in self.repo.read_only().items:
            if item.source == expected:
                return item
        return None

    def _recover_item(
        self,
        item: LoopItem,
        notes: list[str],
        broker: RecoverableBroker | None,
        *,
        unavailable_reason: str,
    ) -> LoopItem:
        if broker is None:
            notes.append(f"{item.item_id}: {unavailable_reason}")
            return self._block_item(item, unavailable_reason)

        attempt = item.attempts[-1]
        if item.state is LoopItemState.DISPATCHING:
            if self._is_synthetic_reserved_task_id(attempt.maker.task_id):
                if self._reservation_expired(attempt):
                    notes.append(f"{item.item_id}: dispatch reservation expired")
                    return replace(item, attempts=item.attempts[:-1], _state=LoopItemState.READY, updated_at=utc_now())
                return item
            status = self._task_status(attempt.maker.task_id, broker)
            if status is None:
                notes.append(f"{item.item_id}: dispatch task missing")
                return replace(item, attempts=item.attempts[:-1], _state=LoopItemState.READY, updated_at=utc_now())
            notes.append(f"{item.item_id}: dispatch task found")
            if attempt.maker.dispatched_at is None:
                attempt = replace(attempt, maker=replace(attempt.maker, dispatched_at=utc_now()))
                item = replace(item, attempts=(*item.attempts[:-1], attempt))
            return replace(item, _state=LoopItemState.RUNNING, updated_at=utc_now())

        if item.state is LoopItemState.RUNNING:
            status = self._task_status(attempt.maker.task_id, broker)
            if status == "cancelled":
                notes.append(f"{item.item_id}: task_cancelled")
                return self._block_item(item, "task_cancelled")
            if status in {"timeout", "timed_out"}:
                notes.append(f"{item.item_id}: task_timeout")
                return self._block_item(item, "task_timeout")
            if status in {"completed", "result"}:
                return item.collect_result(MakerResult("recovered result"))
            if (
                status is None
                and attempt.maker.session_lease_id
                and broker.get_session_active(attempt.maker.session_lease_id) is False
                and self._running_past_grace(attempt)
            ):
                notes.append(f"{item.item_id}: worker_stale")
                return self._block_item(item, "worker_stale")
            if self._task_timed_out(attempt):
                notes.append(f"{item.item_id}: task_timeout")
                return self._block_item(item, "task_timeout")
            return item

        if item.state is LoopItemState.VERIFYING:
            status = self._task_status(attempt.verifier.task_id if attempt.verifier else None, broker)
            if isinstance(status, VerifierVerdict):
                return item.apply_verdict(status)
            if status in {"cancelled", "failed", None}:
                notes.append(f"{item.item_id}: verifier_stale")
                return self._block_item(item, "verifier_stale")
        return item

    def _block_item(self, item: LoopItem, reason: str) -> LoopItem:
        return replace(item, _state=LoopItemState.BLOCKED, blocker=reason, updated_at=utc_now())

    def _reservation_expired(self, attempt: LoopAttempt) -> bool:
        if attempt.reserved_at is None:
            return True
        return utc_now() - self._aware(attempt.reserved_at) >= DEFAULT_RESERVATION_GRACE

    def _task_timed_out(self, attempt: LoopAttempt) -> bool:
        started = attempt.started_at or attempt.maker.dispatched_at
        if started is None:
            return False
        return utc_now() - self._aware(started) >= DEFAULT_TASK_TIMEOUT

    def _task_status(self, task_id: str | None, broker: RecoverableBroker | None) -> str | VerifierVerdict | None:
        if task_id is None or broker is None:
            return None
        raw = broker.get_task_status(task_id)
        if raw is None:
            return None
        if isinstance(raw, VerifierVerdict):
            return raw
        if isinstance(raw, BrokerTaskStatus):
            if isinstance(raw.result, VerifierVerdict):
                return raw.result
            return raw.status.lower()
        if isinstance(raw, str):
            return raw.lower()
        if isinstance(raw, dict):
            if isinstance(raw.get("result"), VerifierVerdict):
                return raw["result"]
            status = raw.get("status")
            return str(status).lower() if status is not None else None
        status = getattr(raw, "status", None)
        return str(status).lower() if status is not None else None

    def _is_synthetic_reserved_task_id(self, task_id: str | None) -> bool:
        return task_id is None or str(task_id).startswith(RESERVED_TASK_PREFIX)

    def _running_past_grace(self, attempt: LoopAttempt) -> bool:
        started = attempt.started_at or attempt.maker.dispatched_at
        if started is None:
            return False
        return utc_now() - self._aware(started) >= DEFAULT_RESERVATION_GRACE

    def _require_attempt(self, item: LoopItem, attempt_no: int, method: str) -> None:
        if not item.attempts or item.attempts[-1].number != attempt_no:
            raise IllegalTransition(item.state, method)

    def _aware(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
