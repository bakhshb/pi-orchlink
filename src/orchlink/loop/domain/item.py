"""Pure loop item lifecycle kernel."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any

from orchlink.loop.domain.errors import BudgetExhausted, IllegalTransition, VerifierMismatch
from orchlink.loop.domain.policy import LoopPolicy, RetryPolicy
from orchlink.loop.domain.skill import Skill
from orchlink.loop.domain.verdict import (
    ReasonCode,
    VerifierVerdict,
    Verdict,
    datetime_to_json,
    parse_datetime,
    utc_now,
)
from orchlink.loop.domain.worktree import Worktree


class LoopItemState(str, Enum):
    TRIAGED = "triaged"
    READY = "ready"
    DISPATCHING = "dispatching"
    RUNNING = "running"
    AWAITING_VERDICT = "awaiting_verdict"
    VERIFYING = "verifying"
    REJECTED = "rejected"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


State = LoopItemState


@dataclass(frozen=True, slots=True)
class WorkerAssignment:
    worker_name: str
    model: str | None = None
    thinking: str | None = None
    task_id: str | None = None
    session_lease_id: str | None = None
    project_dir: str | None = None
    dispatched_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.worker_name:
            raise ValueError("worker_name is required")
        if self.dispatched_at is not None and not isinstance(self.dispatched_at, datetime):
            object.__setattr__(self, "dispatched_at", parse_datetime(self.dispatched_at))

    def same_worker(self, other: "WorkerAssignment") -> bool:
        return self.worker_name == other.worker_name

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_name": self.worker_name,
            "model": self.model,
            "thinking": self.thinking,
            "task_id": self.task_id,
            "session_lease_id": self.session_lease_id,
            "project_dir": self.project_dir,
            "dispatched_at": datetime_to_json(self.dispatched_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerAssignment":
        return cls(
            worker_name=data["worker_name"],
            model=data.get("model"),
            thinking=data.get("thinking"),
            task_id=data.get("task_id"),
            session_lease_id=data.get("session_lease_id"),
            project_dir=data.get("project_dir"),
            dispatched_at=parse_datetime(data.get("dispatched_at")),
        )


@dataclass(frozen=True, slots=True)
class MakerResult:
    result: str
    result_collected_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.result_collected_at is None:
            object.__setattr__(self, "result_collected_at", utc_now())
        elif not isinstance(self.result_collected_at, datetime):
            object.__setattr__(self, "result_collected_at", parse_datetime(self.result_collected_at))

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result,
            "result_collected_at": datetime_to_json(self.result_collected_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | str) -> "MakerResult":
        if isinstance(data, str):
            return cls(result=data)
        return cls(
            result=data["result"],
            result_collected_at=parse_datetime(data.get("result_collected_at")),
        )


@dataclass(frozen=True, slots=True)
class LoopAttempt:
    number: int
    maker: WorkerAssignment
    verifier: WorkerAssignment | None = None
    maker_result: MakerResult | None = None
    verdict: VerifierVerdict | None = None
    reserved_at: datetime | None = None
    started_at: datetime | None = None
    collected_at: datetime | None = None
    verification_started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError("attempt number must be >= 1")
        if not isinstance(self.maker, WorkerAssignment):
            object.__setattr__(self, "maker", WorkerAssignment.from_dict(self.maker))
        if self.verifier is not None and not isinstance(self.verifier, WorkerAssignment):
            object.__setattr__(self, "verifier", WorkerAssignment.from_dict(self.verifier))
        if self.maker_result is not None and not isinstance(self.maker_result, MakerResult):
            object.__setattr__(self, "maker_result", MakerResult.from_dict(self.maker_result))
        if self.verdict is not None and not isinstance(self.verdict, VerifierVerdict):
            object.__setattr__(self, "verdict", VerifierVerdict.from_dict(self.verdict))
        for name in (
            "reserved_at",
            "started_at",
            "collected_at",
            "verification_started_at",
            "finished_at",
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, datetime):
                object.__setattr__(self, name, parse_datetime(value))

    @property
    def is_completed(self) -> bool:
        return self.verdict is not None

    @property
    def is_active(self) -> bool:
        return self.verdict is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "maker": self.maker.to_dict(),
            "verifier": self.verifier.to_dict() if self.verifier else None,
            "maker_result": self.maker_result.to_dict() if self.maker_result else None,
            "verdict": self.verdict.to_dict() if self.verdict else None,
            "reserved_at": datetime_to_json(self.reserved_at),
            "started_at": datetime_to_json(self.started_at),
            "collected_at": datetime_to_json(self.collected_at),
            "verification_started_at": datetime_to_json(self.verification_started_at),
            "finished_at": datetime_to_json(self.finished_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LoopAttempt":
        return cls(
            number=int(data["number"]),
            maker=WorkerAssignment.from_dict(data["maker"]),
            verifier=WorkerAssignment.from_dict(data["verifier"]) if data.get("verifier") else None,
            maker_result=MakerResult.from_dict(data["maker_result"]) if data.get("maker_result") else None,
            verdict=VerifierVerdict.from_dict(data["verdict"]) if data.get("verdict") else None,
            reserved_at=parse_datetime(data.get("reserved_at")),
            started_at=parse_datetime(data.get("started_at")),
            collected_at=parse_datetime(data.get("collected_at")),
            verification_started_at=parse_datetime(data.get("verification_started_at")),
            finished_at=parse_datetime(data.get("finished_at")),
        )


@dataclass(frozen=True, slots=True)
class LoopItem:
    item_id: str
    title: str = ""
    attempts: tuple[LoopAttempt, ...] = field(default_factory=tuple)
    worktree: Worktree | None = None
    skill: Skill | None = None
    verify_policy: LoopPolicy = field(default_factory=LoopPolicy)
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    goal_id: str | None = None
    source: str | None = None
    blocker: str | None = None
    cancellation_reason: str | None = None
    attached_evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    _state: LoopItemState = LoopItemState.TRIAGED

    def __post_init__(self) -> None:
        if not self.item_id:
            raise ValueError("item_id is required")
        object.__setattr__(self, "_state", LoopItemState(self._state))
        object.__setattr__(self, "attempts", tuple(self.attempts))
        if any(not isinstance(attempt, LoopAttempt) for attempt in self.attempts):
            object.__setattr__(
                self,
                "attempts",
                tuple(LoopAttempt.from_dict(attempt) for attempt in self.attempts),
            )
        object.__setattr__(self, "attached_evidence_ids", tuple(str(item) for item in self.attached_evidence_ids))
        if self.worktree is not None and not isinstance(self.worktree, Worktree):
            object.__setattr__(self, "worktree", Worktree.from_dict(self.worktree))
        if self.skill is not None and not isinstance(self.skill, Skill):
            object.__setattr__(self, "skill", Skill.from_dict(self.skill))
        if not isinstance(self.verify_policy, LoopPolicy):
            object.__setattr__(self, "verify_policy", LoopPolicy.from_dict(self.verify_policy))
        if not isinstance(self.retry_policy, RetryPolicy):
            object.__setattr__(self, "retry_policy", RetryPolicy.from_dict(self.retry_policy))
        if self.created_at is not None and not isinstance(self.created_at, datetime):
            object.__setattr__(self, "created_at", parse_datetime(self.created_at))
        if self.updated_at is not None and not isinstance(self.updated_at, datetime):
            object.__setattr__(self, "updated_at", parse_datetime(self.updated_at))
        self._validate_invariants()

    @property
    def state(self) -> LoopItemState:
        return self._state

    @property
    def active_attempt(self) -> LoopAttempt | None:
        if self.state in {
            LoopItemState.DISPATCHING,
            LoopItemState.RUNNING,
            LoopItemState.AWAITING_VERDICT,
            LoopItemState.VERIFYING,
        }:
            return self.attempts[-1] if self.attempts else None
        return None

    def _transition(self, state: LoopItemState, **changes: Any) -> "LoopItem":
        return replace(self, _state=state, updated_at=utc_now(), **changes)

    def _require(self, allowed: set[LoopItemState], method: str) -> None:
        if self.state not in allowed:
            raise IllegalTransition(self.state, method)

    def _invalid(self) -> None:
        raise IllegalTransition(self.state, "_validate_invariants")

    def _current_attempt(self) -> LoopAttempt | None:
        return self.attempts[-1] if self.attempts else None

    def _active_attempts(self) -> tuple[LoopAttempt, ...]:
        return tuple(attempt for attempt in self.attempts if attempt.is_active)

    def _validate_invariants(self) -> None:
        if [attempt.number for attempt in self.attempts] != list(range(1, len(self.attempts) + 1)):
            self._invalid()

        active_attempts = self._active_attempts()
        if len(active_attempts) > 1:
            self._invalid()
        current = self._current_attempt()

        if self.state is LoopItemState.TRIAGED:
            if any(not attempt.is_completed for attempt in self.attempts):
                self._invalid()
            return

        if self.state is LoopItemState.READY:
            if active_attempts:
                self._invalid()
            return

        if self.state is LoopItemState.DISPATCHING:
            if len(active_attempts) != 1 or current is not active_attempts[0]:
                self._invalid()
            if current.maker.task_id is None or current.verifier is not None:
                self._invalid()
            if current.maker_result is not None:
                self._invalid()
            return

        if self.state is LoopItemState.RUNNING:
            if len(active_attempts) != 1 or current is not active_attempts[0]:
                self._invalid()
            if current.maker.dispatched_at is None or current.maker_result is not None:
                self._invalid()
            if current.verifier is not None:
                self._invalid()
            return

        if self.state is LoopItemState.AWAITING_VERDICT:
            if len(active_attempts) != 1 or current is not active_attempts[0]:
                self._invalid()
            if current.maker_result is None or current.maker_result.result_collected_at is None:
                self._invalid()
            if current.verifier is not None and current.verifier.dispatched_at is not None:
                self._invalid()
            return

        if self.state is LoopItemState.VERIFYING:
            if len(active_attempts) != 1 or current is not active_attempts[0]:
                self._invalid()
            if current.verifier is None or current.verifier.task_id is None:
                self._invalid()
            return

        if self.state is LoopItemState.DONE:
            if current is None or current.verdict is None or current.verdict.verdict is not Verdict.ACCEPTED:
                self._invalid()
            return

        if self.state is LoopItemState.REJECTED:
            if current is None or current.verdict is None or current.verdict.verdict is not Verdict.REJECTED:
                self._invalid()
            return

        if self.state is LoopItemState.BLOCKED:
            if not self.blocker:
                self._invalid()
            return

        if self.state is LoopItemState.CANCELLED:
            if not self.cancellation_reason:
                self._invalid()
            return

    def _replace_current_attempt(self, attempt: LoopAttempt) -> tuple[LoopAttempt, ...]:
        if not self.attempts:
            raise IllegalTransition(self.state, "attempt")
        return (*self.attempts[:-1], attempt)

    def ready(self) -> "LoopItem":
        self._require({LoopItemState.TRIAGED, LoopItemState.REJECTED, LoopItemState.BLOCKED}, "ready")
        if self.state in {LoopItemState.REJECTED, LoopItemState.BLOCKED} and len(self.attempts) >= self.retry_policy.max_attempts:
            raise BudgetExhausted(f"retry budget exhausted for {self.item_id}")
        return self._transition(LoopItemState.READY, blocker=None, cancellation_reason=None)

    mark_ready = ready

    def dispatch(self, maker: WorkerAssignment) -> "LoopItem":
        self._require({LoopItemState.READY}, "dispatch")
        if self.active_attempt is not None or self._active_attempts():
            raise IllegalTransition(self.state, "dispatch")
        if len(self.attempts) >= self.retry_policy.max_attempts:
            raise BudgetExhausted(f"attempt budget exhausted for {self.item_id}")
        if maker.task_id is None:
            maker = replace(maker, task_id=f"reserved:{self.item_id}:{len(self.attempts) + 1}")
        attempt = LoopAttempt(number=len(self.attempts) + 1, maker=maker, reserved_at=utc_now())
        return self._transition(LoopItemState.DISPATCHING, attempts=(*self.attempts, attempt))

    reserve_attempt = dispatch

    def broker_sent(self, *, task_id: str | None = None, session_lease_id: str | None = None) -> "LoopItem":
        self._require({LoopItemState.DISPATCHING}, "broker_sent")
        attempt = self.active_attempt
        if attempt is None:
            raise IllegalTransition(self.state, "broker_sent")
        maker = replace(
            attempt.maker,
            task_id=task_id if task_id is not None else attempt.maker.task_id,
            session_lease_id=(
                session_lease_id if session_lease_id is not None else attempt.maker.session_lease_id
            ),
            dispatched_at=utc_now(),
        )
        return self._transition(
            LoopItemState.RUNNING,
            attempts=self._replace_current_attempt(replace(attempt, maker=maker, started_at=utc_now())),
        )

    mark_running = broker_sent

    def collect_result(self, maker_result: str | MakerResult) -> "LoopItem":
        self._require({LoopItemState.RUNNING}, "collect_result")
        attempt = self.active_attempt
        if attempt is None:
            raise IllegalTransition(self.state, "collect_result")
        result = maker_result if isinstance(maker_result, MakerResult) else MakerResult(str(maker_result))
        return self._transition(
            LoopItemState.AWAITING_VERDICT,
            attempts=self._replace_current_attempt(
                replace(attempt, maker_result=result, collected_at=result.result_collected_at)
            ),
        )

    collect = collect_result

    def start_verification(
        self,
        verifier: WorkerAssignment,
        *,
        allow_same_worker: bool = False,
    ) -> "LoopItem":
        self._require({LoopItemState.AWAITING_VERDICT}, "start_verification")
        attempt = self.active_attempt
        if attempt is None:
            raise IllegalTransition(self.state, "start_verification")
        if (
            self.verify_policy.require_separate_verifier_worker
            and not allow_same_worker
            and verifier.same_worker(attempt.maker)
        ):
            raise VerifierMismatch("verifier worker must differ from maker worker")
        if verifier.task_id is None:
            verifier = replace(verifier, task_id=f"verify:{self.item_id}:{attempt.number}")
        return self._transition(
            LoopItemState.VERIFYING,
            attempts=self._replace_current_attempt(
                replace(attempt, verifier=verifier, verification_started_at=utc_now())
            ),
        )

    verify = start_verification

    def apply_verdict(self, verdict: VerifierVerdict) -> "LoopItem":
        self._require({LoopItemState.VERIFYING}, "apply_verdict")
        attempt = self.active_attempt
        if attempt is None or attempt.verifier is None:
            raise IllegalTransition(self.state, "apply_verdict")
        if verdict.verifier_worker != attempt.verifier.worker_name:
            raise VerifierMismatch("verdict worker does not match verifier assignment")
        if verdict.verdict is Verdict.ACCEPTED:
            next_state = LoopItemState.DONE
            blocker = None
        elif verdict.verdict is Verdict.REJECTED:
            next_state = LoopItemState.REJECTED
            blocker = None
        else:
            next_state = LoopItemState.BLOCKED
            blocker = verdict.detail or verdict.reason_code.value
        return self._transition(
            next_state,
            blocker=blocker,
            attempts=self._replace_current_attempt(replace(attempt, verdict=verdict, finished_at=utc_now())),
        )

    def cancel(self, reason: str) -> "LoopItem":
        if self.state in {LoopItemState.DONE, LoopItemState.CANCELLED}:
            raise IllegalTransition(self.state, "cancel")
        if not reason:
            raise ValueError("cancellation reason is required")
        return self._transition(LoopItemState.CANCELLED, cancellation_reason=reason)

    def block(self, blocker: str) -> "LoopItem":
        if self.state in {LoopItemState.DONE, LoopItemState.CANCELLED}:
            raise IllegalTransition(self.state, "block")
        if not blocker:
            raise ValueError("blocker is required")
        return self._transition(LoopItemState.BLOCKED, blocker=blocker)

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "state": self.state.value,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "worktree": self.worktree.to_dict() if self.worktree else None,
            "skill": self.skill.to_dict() if self.skill else None,
            "verify_policy": self.verify_policy.to_dict(),
            "retry_policy": self.retry_policy.to_dict(),
            "goal_id": self.goal_id,
            "source": self.source,
            "blocker": self.blocker,
            "cancellation_reason": self.cancellation_reason,
            "attached_evidence_ids": list(self.attached_evidence_ids),
            "created_at": datetime_to_json(self.created_at),
            "updated_at": datetime_to_json(self.updated_at),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LoopItem":
        item = cls(
            item_id=data["item_id"],
            title=data.get("title", ""),
            attempts=tuple(LoopAttempt.from_dict(item) for item in data.get("attempts", ())),
            worktree=Worktree.from_dict(data.get("worktree")),
            skill=Skill.from_dict(data.get("skill")),
            verify_policy=LoopPolicy.from_dict(data.get("verify_policy")),
            retry_policy=RetryPolicy.from_dict(data.get("retry_policy")),
            goal_id=data.get("goal_id"),
            source=data.get("source"),
            blocker=data.get("blocker"),
            cancellation_reason=data.get("cancellation_reason"),
            attached_evidence_ids=tuple(data.get("attached_evidence_ids") or ()),
            created_at=parse_datetime(data.get("created_at")),
            updated_at=parse_datetime(data.get("updated_at")),
            _state=LoopItemState(data.get("state", LoopItemState.TRIAGED.value)),
        )
        item._validate_invariants()
        return item


@dataclass(slots=True)
class LoopState:
    items: tuple[LoopItem, ...] = field(default_factory=tuple)
    schema_version: str = "orchloop.v1"

    def __post_init__(self) -> None:
        self.items = tuple(self.items)

    def item(self, item_id: str) -> LoopItem:
        for loop_item in self.items:
            if loop_item.item_id == item_id:
                return loop_item
        raise KeyError(item_id)

    def add_item(self, item: LoopItem) -> None:
        if any(existing.item_id == item.item_id for existing in self.items):
            raise ValueError(f"duplicate loop item {item.item_id}")
        self.items = (*self.items, item)

    def replace_item(self, item: LoopItem) -> None:
        replaced = False
        next_items: list[LoopItem] = []
        for existing in self.items:
            if existing.item_id == item.item_id:
                next_items.append(item)
                replaced = True
            else:
                next_items.append(existing)
        if not replaced:
            raise KeyError(item.item_id)
        self.items = tuple(next_items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "items": [item.to_dict() for item in self.items],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LoopState":
        if data is None:
            return cls()
        return cls(
            schema_version=data.get("schema_version", "orchloop.v1"),
            items=tuple(LoopItem.from_dict(item) for item in data.get("items", ())),
        )
