"""Application service over the pure loop kernel and markdown state repo."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, TypeAlias

from orchlink.loop.domain.errors import IllegalTransition, VerifierMismatch
from orchlink.loop.ports import BrokerStatusPort, BrokerTaskStatus, GoalEvidencePort, LoopRepository
from orchlink.loop.domain.item import (
    LoopAttempt,
    LoopItem,
    LoopItemState,
    MakerResult,
    RESERVED_TASK_PREFIX,
    WorkerAssignment,
)
from orchlink.loop.domain.skill import Skill
from orchlink.loop.domain.verdict import ReasonCode, Verdict, VerifierVerdict, parse_verdict_text, utc_now
from orchlink.loop.domain.worktree import Worktree

ItemId: TypeAlias = str
TaskId: TypeAlias = str

_RECOVER_BROKER_UNSET = object()

DEFAULT_RESERVATION_GRACE = timedelta(minutes=10)
DEFAULT_TASK_TIMEOUT = timedelta(hours=2)


@dataclass(frozen=True, slots=True)
class ItemCandidate:
    item_id: ItemId
    title: str = ""
    source_type: str | None = None
    source_ref: str | None = None
    goal_id: str | None = None
    objective: str = ""
    source_url: str | None = None
    source_context: str = ""
    source_metadata: dict[str, Any] = field(default_factory=dict)
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


# Backward-compatible aliases for the broker port contract.
RecoverableBroker = BrokerStatusPort


_SECRET_METADATA_KEYS = {"authorization", "token", "access_token", "api_key", "secret", "headers"}


def _safe_source_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in dict(metadata or {}).items():
        normalized = str(key).lower().replace("-", "_")
        if normalized in _SECRET_METADATA_KEYS or any(part in normalized for part in ("token", "secret", "api_key", "authorization")):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            safe[str(key)] = value
        elif isinstance(value, list):
            safe[str(key)] = [item for item in value if isinstance(item, (str, int, float, bool)) or item is None]
        elif isinstance(value, dict):
            safe[str(key)] = _safe_source_metadata(value)
    return safe


class LoopService:
    def __init__(
        self,
        config: dict[str, Any] | None,
        repo: LoopRepository,
        *,
        broker: BrokerStatusPort | None = None,
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
                    source_url=candidate.source_url,
                    objective=candidate.objective or candidate.title,
                    source_context=candidate.source_context,
                    source_metadata=_safe_source_metadata(candidate.source_metadata),
                    goal_id=candidate.goal_id,
                    worktree=candidate.worktree,
                    skill=candidate.skill,
                    created_at=now,
                    updated_at=now,
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
        session_lease_id: str | None = None,
    ) -> DispatchReservation:
        with self.repo.transaction("loop:next") as state:
            item = state.item(item_id)
            if item.state is not LoopItemState.READY:
                raise IllegalTransition(item.state, "next_item")
            maker = WorkerAssignment(
                worker_name=maker_worker,
                task_id=f"reserved:{item.item_id}:{len(item.attempts) + 1}",
                session_lease_id=session_lease_id,
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
            updated = item.record_broker_task(task_id)
            state.replace_item(updated)
            return updated

    def rollback_dispatch(self, item_id: ItemId, attempt_no: int) -> LoopItem:
        with self.repo.transaction("loop:rollback_dispatch") as state:
            item = state.item(item_id)
            self._require_attempt(item, attempt_no, "rollback_dispatch")
            updated = item.rollback_dispatch()
            state.replace_item(updated)
            return updated

    def mark_running(self, item_id: ItemId, *, attempt_no: int) -> LoopItem:
        with self.repo.transaction("loop:mark_running") as state:
            item = state.item(item_id)
            self._require_attempt(item, attempt_no, "mark_running")
            updated = item.confirm_dispatch_running()
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
        allow_same_worker: bool = False,
    ) -> VerificationReservation:
        with self.repo.transaction("loop:reserve_verification") as state:
            item = state.item(item_id)
            self._require_attempt(item, attempt_no, "reserve_verification")
            verifier = WorkerAssignment(
                worker_name=verifier_worker,
                task_id=f"verify:{item.item_id}:{attempt_no}",
            )
            if verifier.same_worker(item.attempts[-1].maker) and not allow_same_worker:
                raise VerifierMismatch("verifier worker must differ from maker worker")
            updated = item.start_verification(verifier, allow_same_worker=allow_same_worker)
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
            # Same-worker authorization is fixed at reservation; retain this
            # argument for caller compatibility but never use it to authorize a verdict.
            _ = allow_same_worker
            if self._same_worker_override_required(attempt) and not attempt.same_worker_verifier_override:
                raise VerifierMismatch("verifier worker must differ from maker worker")
            updated, note = self._apply_verifier_verdict(item, verdict)
            state.replace_item(updated)
            return VerdictApplication(updated, lower_confidence=note is not None, note=note)

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

    def attach_evidence_to_goal(self, item_id: ItemId, *, goal_service: GoalEvidencePort | None) -> bool:
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
            goal_service.attach_evidence(
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
            updated = item.attach_accepted_evidence_id(evidence_id)
            state.replace_item(updated)
            return True

    def recover(self, broker_client: BrokerStatusPort | None = _RECOVER_BROKER_UNSET) -> RecoveryReport:
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

    def _apply_verifier_verdict(self, item: LoopItem, verdict: VerifierVerdict) -> tuple[LoopItem, str | None]:
        """Apply a verifier verdict with retry-exhaustion blocking and same-worker note.

        Shared by the normal ``apply_verdict`` path and recovery so both honor the
        REJECTED-at-budget ``retry_exhausted`` block and the same-worker
        lower-confidence note. Returns the updated item and the note (or None).
        """
        attempt = item.attempts[-1]
        same_worker = attempt.verifier is not None and attempt.verifier.same_worker(attempt.maker)
        updated = item.apply_verdict(verdict)
        note = None
        if verdict.verdict is Verdict.REJECTED and len(updated.attempts) >= updated.retry_policy.max_attempts:
            updated = updated.block_retry_exhausted()
        if same_worker and attempt.same_worker_verifier_override:
            note = "same_worker_verifier_override"
        return updated, note

    def _recover_item(
        self,
        item: LoopItem,
        notes: list[str],
        broker: BrokerStatusPort | None,
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
                    return item.rollback_dispatch()
                return item
            snapshot = self._task_snapshot(attempt.maker.task_id, broker)
            if snapshot is None or self._is_missing_status(snapshot.status):
                notes.append(f"{item.item_id}: dispatch task missing")
                return item.rollback_dispatch()
            if self._is_cancelled_status(snapshot.status):
                notes.append(f"{item.item_id}: task_cancelled")
                return self._block_item(item, "task_cancelled")
            if self._is_timeout_status(snapshot.status):
                notes.append(f"{item.item_id}: task_timeout")
                return self._block_item(item, "task_timeout")
            if self._is_failed_status(snapshot.status):
                notes.append(f"{item.item_id}: task_failed")
                return self._block_item(item, "task_failed")
            if self._is_done_status(snapshot.status):
                maker_result = self._maker_result_from_snapshot(snapshot)
                if maker_result is None:
                    notes.append(f"{item.item_id}: task_empty_result")
                    return self._block_item(item, "task_empty_result")
                return item.broker_sent(task_id=attempt.maker.task_id).collect_result(maker_result)
            notes.append(f"{item.item_id}: dispatch task found")
            return item.confirm_dispatch_running()

        if item.state is LoopItemState.RUNNING:
            snapshot = self._task_snapshot(attempt.maker.task_id, broker)
            status = snapshot.status if snapshot is not None else None
            if self._is_cancelled_status(status):
                notes.append(f"{item.item_id}: task_cancelled")
                return self._block_item(item, "task_cancelled")
            if self._is_timeout_status(status):
                notes.append(f"{item.item_id}: task_timeout")
                return self._block_item(item, "task_timeout")
            if self._is_failed_status(status):
                notes.append(f"{item.item_id}: task_failed")
                return self._block_item(item, "task_failed")
            if self._is_done_status(status):
                maker_result = self._maker_result_from_snapshot(snapshot)
                if maker_result is None:
                    notes.append(f"{item.item_id}: task_empty_result")
                    return self._block_item(item, "task_empty_result")
                return item.collect_result(maker_result)
            if (
                snapshot is None
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
            if self._same_worker_override_required(attempt) and not attempt.same_worker_verifier_override:
                notes.append(f"{item.item_id}: verifier_same_worker_override_missing")
                return self._block_item(item, "verifier_same_worker_override_missing")
            snapshot = self._task_snapshot(attempt.verifier.task_id if attempt.verifier else None, broker)
            status = snapshot.status if snapshot is not None else None
            verdict = self._verifier_verdict_from_snapshot(snapshot, attempt)
            if verdict is not None:
                updated, note = self._apply_verifier_verdict(item, verdict)
                if note is not None:
                    notes.append(f"{item.item_id}: {note}")
                return updated
            if snapshot is None or self._is_missing_status(status):
                notes.append(f"{item.item_id}: verifier_missing")
                return self._block_item(item, "verifier_missing")
            if self._is_cancelled_status(status):
                notes.append(f"{item.item_id}: verifier_cancelled")
                return self._block_item(item, "verifier_cancelled")
            if self._is_timeout_status(status):
                notes.append(f"{item.item_id}: verifier_timeout")
                return self._block_item(item, "verifier_timeout")
            if self._is_failed_status(status):
                notes.append(f"{item.item_id}: verifier_failed")
                return self._block_item(item, "verifier_failed")
            if self._is_done_status(status):
                # Completed without a usable verdict: fail closed. A present but
                # unparseable payload is distinct from an absent one for triage.
                if self._has_verifier_payload(snapshot):
                    notes.append(f"{item.item_id}: verifier_malformed_result")
                    return self._block_item(item, "verifier_malformed_result")
                notes.append(f"{item.item_id}: verifier_empty_result")
                return self._block_item(item, "verifier_empty_result")
        return item

    def _block_item(self, item: LoopItem, reason: str) -> LoopItem:
        return item.block(reason)

    def _same_worker_override_required(self, attempt: LoopAttempt) -> bool:
        return bool(attempt.verifier is not None and attempt.verifier.same_worker(attempt.maker))

    def _reservation_expired(self, attempt: LoopAttempt) -> bool:
        if attempt.reserved_at is None:
            return True
        return utc_now() - self._aware(attempt.reserved_at) >= DEFAULT_RESERVATION_GRACE

    def _task_timed_out(self, attempt: LoopAttempt) -> bool:
        started = attempt.started_at or attempt.maker.dispatched_at
        if started is None:
            return False
        return utc_now() - self._aware(started) >= DEFAULT_TASK_TIMEOUT

    def _task_status(self, task_id: str | None, broker: BrokerStatusPort | None) -> str | VerifierVerdict | None:
        snapshot = self._task_snapshot(task_id, broker)
        if snapshot is None:
            return None
        if isinstance(snapshot.result, VerifierVerdict):
            return snapshot.result
        return snapshot.status

    def _task_snapshot(self, task_id: str | None, broker: BrokerStatusPort | None) -> BrokerTaskStatus | None:
        if task_id is None or broker is None:
            return None
        return broker.get_task_status(task_id)

    def _maker_result_from_snapshot(self, snapshot: BrokerTaskStatus | None) -> MakerResult | None:
        if snapshot is None:
            return None
        raw = snapshot.result
        if isinstance(raw, MakerResult):
            return raw if raw.result.strip() else None
        text = self._result_text(raw)
        return MakerResult(text) if text else None

    def _verifier_verdict_from_snapshot(
        self,
        snapshot: BrokerTaskStatus | None,
        attempt: LoopAttempt,
    ) -> VerifierVerdict | None:
        """Parse a verifier verdict from a broker snapshot, or None if unusable.

        Accepts an already-parsed ``VerifierVerdict`` (e.g. test fakes) or the
        verdict-text string the HTTP broker client extracts from the worker reply.
        Returns None for absent or malformed payloads so callers fail closed.
        """
        if snapshot is None:
            return None
        raw = snapshot.result
        if isinstance(raw, VerifierVerdict):
            verdict = raw
        elif isinstance(raw, str) and raw.strip():
            try:
                verdict = parse_verdict_text(raw)
            except ValueError:
                return None
        else:
            return None
        # The broker reply came from the assigned verifier; trust the persisted
        # assignment for worker/task identity so a missing or mistyped
        # VERIFIER_WORKER field cannot cause a spurious VerifierMismatch.
        if attempt.verifier is not None:
            verdict = replace(verdict, verifier_worker=attempt.verifier.worker_name, task_id=attempt.verifier.task_id)
        return verdict

    def _has_verifier_payload(self, snapshot: BrokerTaskStatus | None) -> bool:
        if snapshot is None:
            return False
        raw = snapshot.result
        if isinstance(raw, VerifierVerdict):
            return True
        return isinstance(raw, str) and bool(raw.strip())

    def _result_text(self, raw: Any) -> str:
        if raw is None:
            return ""
        if isinstance(raw, str):
            return raw.strip()
        if isinstance(raw, MakerResult):
            return raw.result.strip()
        return ""

    def _is_done_status(self, status: str | None) -> bool:
        return status in {"done", "completed", "complete", "result", "succeeded", "success"}

    def _is_cancelled_status(self, status: str | None) -> bool:
        return status in {"cancelled", "canceled"}

    def _is_timeout_status(self, status: str | None) -> bool:
        return status in {"timeout", "timed_out", "timed-out"}

    def _is_failed_status(self, status: str | None) -> bool:
        return status in {"failed", "failure", "error"}

    def _is_missing_status(self, status: str | None) -> bool:
        return status in {None, "", "missing", "not_found", "not-found", "unknown"}

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
