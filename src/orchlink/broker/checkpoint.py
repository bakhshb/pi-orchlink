"""Durable broker checkpoint artifact for Orchlink interruption recovery.

This module owns ``.orch/run/broker-checkpoint.json``, the on-disk artifact that
records lease epoch, lease holder, and task id for in-flight and recently-
settled tasks. It is intentionally backend-agnostic: the broker writes to this
file regardless of whether it is using the in-memory or jsonl store, so
``orch resume`` and the broker's startup reconciliation can rely on the file
even when the journal backend is memory-only.

The module is pure: no broker imports, no HTTP imports, no storage imports.
Callers use :func:`record_lease` from the broker service boundary for each
lease transition they observe.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

CHECKPOINT_FILENAME = "broker-checkpoint.json"
CHECKPOINT_VERSION = 1

LeaseStatus = Literal["in_flight", "recently_settled"]
_KNOWN_STATUSES = ("in_flight", "recently_settled")
_STATUS_ORDER: dict[LeaseStatus, int] = {"in_flight": 0, "recently_settled": 1}
_CHECKPOINT_LOCK = threading.RLock()


@dataclass
class CheckpointLease:
    """A single in-flight or recently-settled lease entry."""

    task_id: str
    epoch: int
    holder: str
    status: LeaseStatus
    updated_at: str  # ISO-8601 UTC timestamp

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CheckpointLease":
        status = str(data["status"])
        if status not in _KNOWN_STATUSES:
            raise ValueError(f"Unknown lease status: {status!r}")
        return cls(
            task_id=str(data["task_id"]),
            epoch=int(data["epoch"]),
            holder=str(data["holder"]),
            status=status,  # type: ignore[arg-type]
            updated_at=str(data["updated_at"]),
        )


@dataclass
class Checkpoint:
    """The full broker checkpoint state persisted to disk."""

    version: int = CHECKPOINT_VERSION
    last_checkpoint_at: str = field(
        default_factory=lambda: _now_iso()
    )
    leases: list[CheckpointLease] = field(default_factory=list)

    @property
    def in_flight(self) -> list[CheckpointLease]:
        return [lease for lease in self.leases if lease.status == "in_flight"]

    @property
    def recently_settled(self) -> list[CheckpointLease]:
        return [lease for lease in self.leases if lease.status == "recently_settled"]


@dataclass
class DriftedLease:
    """A lease recorded in a prior checkpoint that no longer matches the
    broker's current view after a restart. The previous epoch and holder are
    the values from the prior checkpoint; the current epoch and holder are the
    broker's current values (or ``None`` if the task is no longer present).
    """

    task_id: str
    previous_epoch: int
    previous_holder: str
    previous_updated_at: str
    current_epoch: int | None
    current_holder: str | None
    reason: str  # "missing_after_restart" | "epoch_changed" | "holder_changed"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def checkpoint_path(project_root: Path | str) -> Path:
    """Return the canonical checkpoint path under ``<project_root>/.orch/run``."""
    root = Path(project_root)
    return root / ".orch" / "run" / CHECKPOINT_FILENAME


def empty_checkpoint() -> Checkpoint:
    return Checkpoint()


def load_checkpoint(path: Path | str) -> Checkpoint:
    """Load the checkpoint at ``path``.

    Returns an empty checkpoint when the file is missing, unreadable, or has an
    unrecognized shape — the broker should never fail to start because of a
    corrupt checkpoint file.
    """
    file_path = Path(path)
    if not file_path.is_file():
        return empty_checkpoint()
    try:
        raw = file_path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return empty_checkpoint()
    if not isinstance(data, dict):
        return empty_checkpoint()
    leases_raw = data.get("leases", [])
    leases: list[CheckpointLease] = []
    if isinstance(leases_raw, list):
        for entry in leases_raw:
            if isinstance(entry, dict):
                try:
                    leases.append(CheckpointLease.from_dict(entry))
                except (KeyError, TypeError, ValueError):
                    continue
    version = data.get("version", CHECKPOINT_VERSION)
    last_checkpoint_at = data.get("last_checkpoint_at") or _now_iso()
    return Checkpoint(
        version=int(version) if isinstance(version, int) else CHECKPOINT_VERSION,
        last_checkpoint_at=str(last_checkpoint_at),
        leases=leases,
    )


def dump_checkpoint(path: Path | str, checkpoint: Checkpoint) -> None:
    """Atomically write ``checkpoint`` to ``path`` as JSON."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": checkpoint.version,
        "last_checkpoint_at": _now_iso(),
        "leases": [lease.to_dict() for lease in checkpoint.leases],
    }
    serialized = json.dumps(payload, sort_keys=True, indent=2)
    _atomic_write_text(file_path, serialized)


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a sibling tmp file + os.replace."""
    # NamedTemporaryFile on its own opens the file; we want unlinked + manual rename.
    tmp_dir = path.parent
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(tmp_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(text)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _lease_order(lease: CheckpointLease) -> tuple[int, int]:
    """Ordering key for same-task checkpoint transitions.

    Epoch is the primary version. Within the same epoch, a settlement is later
    than delivery/in-flight. This prevents a delayed delivery checkpoint from
    resurrecting work that was already settled while still allowing a reclaimed
    higher-epoch lease to become in-flight again.
    """
    return int(lease.epoch), _STATUS_ORDER[lease.status]


def _should_replace_lease(existing: CheckpointLease | None, lease: CheckpointLease) -> bool:
    if existing is None:
        return True
    return _lease_order(lease) >= _lease_order(existing)


def _upsert_lease(checkpoint: Checkpoint, lease: CheckpointLease) -> bool:
    """Apply an ordered same-task lease transition and preserve other entries."""
    existing = next((item for item in checkpoint.leases if item.task_id == lease.task_id), None)
    if not _should_replace_lease(existing, lease):
        return False
    checkpoint.leases = [
        item
        for item in checkpoint.leases
        if item.task_id != lease.task_id
    ]
    checkpoint.leases.append(lease)
    return True


def record_lease(
    project_root: Path | str,
    task_id: str,
    epoch: int,
    holder: str,
    status: LeaseStatus,
) -> Checkpoint:
    """Record a lease transition for ``task_id`` and persist the checkpoint.

    This is the single seam through which the broker writes lease state to
    ``.orch/run/broker-checkpoint.json``. It is store-backend-agnostic: the
    function reads the on-disk checkpoint, applies the transition, and writes
    it back atomically. Callers include the in-memory and jsonl store
    implementations, and the broker's HTTP lease handlers, so the file is
    updated on every lease change regardless of which backend is active.
    """
    if status not in _KNOWN_STATUSES:
        raise ValueError(
            f"Unknown lease status: {status!r}; expected one of {_KNOWN_STATUSES}"
        )
    path = checkpoint_path(project_root)
    with _CHECKPOINT_LOCK:
        checkpoint = load_checkpoint(path)
        changed = _upsert_lease(
            checkpoint,
            CheckpointLease(
                task_id=str(task_id),
                epoch=int(epoch),
                holder=str(holder),
                status=status,
                updated_at=_now_iso(),
            ),
        )
        if changed:
            dump_checkpoint(path, checkpoint)
        return checkpoint


def list_leases(checkpoint: Checkpoint) -> Iterable[CheckpointLease]:
    """Yield all leases currently recorded in ``checkpoint``."""
    return iter(checkpoint.leases)


def reconcile_checkpoint(
    prior: Checkpoint,
    current_leases: dict[str, tuple[int, str]],
    *,
    now_in_flight: dict[str, tuple[int, str]] | None = None,
) -> list[DriftedLease]:
    """Compare ``prior`` against the broker's current view and return drifted
    leases.

    This is the AC-4 reconciliation seam the broker should call on startup with
    a prior checkpoint read from disk and a snapshot of the live task-job
    leases (``{task_id: (epoch, holder)}``). Each entry the broker currently
    considers ``in_flight`` can be passed in ``now_in_flight`` for finer-grained
    bookkeeping on the call site (it does not change drift detection because
    drift is computed strictly against ``current_leases``).

    Drift detection rules, applied to every prior ``in_flight`` lease:

    1. ``task_id`` absent from ``current_leases`` -> drift, reason
       ``"missing_after_restart"`` (the worker holding the lease is gone and
       no one re-acquired it).
    2. ``task_id`` present but epoch differs -> drift, reason
       ``"epoch_changed"`` (someone re-acquired the lease during downtime).
    3. ``task_id`` present with same epoch but different holder -> drift,
       reason ``"holder_changed"`` (a different agent took over the work).

    Prior ``recently_settled`` leases are history, not drift, because they had
    already reached a terminal state before the checkpoint was written. They
    are intentionally not surfaced here.
    """
    drifted: list[DriftedLease] = []
    for lease in prior.in_flight:
        present = current_leases.get(lease.task_id)
        if present is None:
            drifted.append(
                DriftedLease(
                    task_id=lease.task_id,
                    previous_epoch=lease.epoch,
                    previous_holder=lease.holder,
                    previous_updated_at=lease.updated_at,
                    current_epoch=None,
                    current_holder=None,
                    reason="missing_after_restart",
                )
            )
            continue
        current_epoch, current_holder = present
        if int(current_epoch) != int(lease.epoch):
            drifted.append(
                DriftedLease(
                    task_id=lease.task_id,
                    previous_epoch=lease.epoch,
                    previous_holder=lease.holder,
                    previous_updated_at=lease.updated_at,
                    current_epoch=int(current_epoch),
                    current_holder=str(current_holder),
                    reason="epoch_changed",
                )
            )
            continue
        if str(current_holder) != str(lease.holder):
            drifted.append(
                DriftedLease(
                    task_id=lease.task_id,
                    previous_epoch=lease.epoch,
                    previous_holder=lease.holder,
                    previous_updated_at=lease.updated_at,
                    current_epoch=int(current_epoch),
                    current_holder=str(current_holder),
                    reason="holder_changed",
                )
            )
    return drifted


__all__ = [
    "CHECKPOINT_FILENAME",
    "CHECKPOINT_VERSION",
    "Checkpoint",
    "CheckpointLease",
    "DriftedLease",
    "LeaseStatus",
    "checkpoint_path",
    "dump_checkpoint",
    "empty_checkpoint",
    "list_leases",
    "load_checkpoint",
    "reconcile_checkpoint",
    "record_lease",
]  
