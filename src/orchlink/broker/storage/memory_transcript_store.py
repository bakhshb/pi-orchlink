"""Focused component: per-task transcript event storage and long-poll.

Lifted from ``memory.py`` for G018 so the transcript surface is owned by
a focused component while ``MemoryMessageStore`` delegates to it.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Callable

from orchlink.core.models import TranscriptBatch, TranscriptEvent, TranscriptTruncation
from orchlink.core.states import CANONICAL_TERMINAL_STATUSES
from orchlink.core.views import transcript_event_to_wire


if TYPE_CHECKING:
    from orchlink.broker.storage.memory_session_store import MemorySessionStore
    from orchlink.broker.storage.memory_state import InMemoryBrokerState
    from orchlink.broker.storage.memory_job_projector import MemoryJobProjector


# --- Retention bounds (per-task) ---------------------------------------------
# Both caps are enforced every append; the more restrictive one wins each
# iteration. Dropping is whole-event only: we never truncate mid-event to
# keep seq arithmetic stable for surviving cursors.
_DEFAULT_TRANSCRIPT_EVENTS_PER_TASK = 1000
_DEFAULT_TRANSCRIPT_BYTES_PER_TASK = 256 * 1024
MAX_TRANSCRIPT_EVENTS_PER_TASK = _DEFAULT_TRANSCRIPT_EVENTS_PER_TASK
# 256 KiB of JSON-encoded retained events per task. Bounding the encoded wire
# form accounts for metadata, escaping, and non-ASCII expansion in the journal.
MAX_TRANSCRIPT_BYTES_PER_TASK = _DEFAULT_TRANSCRIPT_BYTES_PER_TASK


def _transcript_event_size_bytes(event: TranscriptEvent) -> int:
    """Return the event's JSONL snapshot footprint in UTF-8 bytes."""
    wire = transcript_event_to_wire(event)
    return len(json.dumps(wire, sort_keys=True, default=str).encode("utf-8"))


def _transcript_events_byte_size(events: list[TranscriptEvent]) -> int:
    return sum(_transcript_event_size_bytes(e) for e in events)


def set_retention_limits(events: int | None = None, bytes_limit: int | None = None) -> None:
    """Override retention limits for deterministic focused tests."""
    global MAX_TRANSCRIPT_EVENTS_PER_TASK, MAX_TRANSCRIPT_BYTES_PER_TASK
    if events is not None:
        MAX_TRANSCRIPT_EVENTS_PER_TASK = events
    if bytes_limit is not None:
        MAX_TRANSCRIPT_BYTES_PER_TASK = bytes_limit


def reset_retention_limits() -> None:
    """Restore production retention limits after a focused test."""
    global MAX_TRANSCRIPT_EVENTS_PER_TASK, MAX_TRANSCRIPT_BYTES_PER_TASK
    MAX_TRANSCRIPT_EVENTS_PER_TASK = _DEFAULT_TRANSCRIPT_EVENTS_PER_TASK
    MAX_TRANSCRIPT_BYTES_PER_TASK = _DEFAULT_TRANSCRIPT_BYTES_PER_TASK


def _drop_oldest_events(
    events: list[TranscriptEvent],
    truncated_before: dict[str, int],
    key: str,
) -> None:
    """Drop oldest events and advance the first-retained-sequence watermark."""
    while events and (
        len(events) > MAX_TRANSCRIPT_EVENTS_PER_TASK
        or _transcript_events_byte_size(events) > MAX_TRANSCRIPT_BYTES_PER_TASK
    ):
        dropped = events.pop(0)
        truncated_before[key] = max(truncated_before.get(key, 0), int(dropped.seq) + 1)


class MemoryTranscriptStore:
    """Focused component for transcript event storage, ordering, and reads."""

    def __init__(
        self,
        state: InMemoryBrokerState,
        now: Callable[[], str],
        session_store: MemorySessionStore,
        job_projector: MemoryJobProjector,
        journal_path: str | None = None,
    ) -> None:
        self._state = state
        self._now = now
        self._session_store = session_store
        self._job_projector = job_projector
        self._journal_path = journal_path

    @property
    def _journal(self):
        from orchlink.broker.storage.persistence import atomic_append_jsonl_line, encode_jsonl_record

        return atomic_append_jsonl_line, encode_jsonl_record

    def _append_to_journal_locked(self, operation: str, request: dict[str, Any], result: dict[str, Any], snapshot: dict[str, Any]) -> None:
        if not self._journal_path:
            return
        from orchlink.broker.storage.persistence import atomic_append_jsonl_line, encode_jsonl_record

        record = {
            "time": self._now(),
            "operation": operation,
            "request": request,
            "result": result,
            "snapshot": snapshot,
        }
        atomic_append_jsonl_line(self._journal_path, encode_jsonl_record(record) + "\n")

    @staticmethod
    def _key(project_id: str, task_id: str) -> str:
        return f"{project_id}:{task_id}"

    def append_transcript_batch_locked(
        self,
        batch: TranscriptBatch,
        task_id: str,
        project_id: str,
        agent_id: str,
        session_lease_id: str | None = None,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
    ) -> dict[str, Any]:
        project_id = str(project_id or "default")
        task_id = str(task_id)
        agent_id = str(agent_id or "")
        key = self._key(project_id, task_id)

        # Lease/session validation
        if session_lease_id:
            self._session_store.assert_active_session_lease_locked(agent_id, project_id=project_id, lease_id=session_lease_id)
        if lease_epoch is not None:
            task_key = self._job_projector.task_key(project_id, task_id)
            job = self._state.task_jobs.get(task_key)
            lease = job.lease if job is not None else None
            if lease is None or not lease.matches(str(lease_holder or ""), int(lease_epoch)):
                from orchlink.broker.storage.base import LeaseConflictError
                raise LeaseConflictError(f"Stale lease transcript write for {task_id}: holder/epoch mismatch.")
        # Reject terminal task writes
        task_key = self._job_projector.task_key(project_id, task_id)
        job = self._state.task_jobs.get(task_key)
        if job is not None and str(job.status or "").upper() in CANONICAL_TERMINAL_STATUSES:
            from orchlink.broker.storage.base import LeaseConflictError
            raise LeaseConflictError(f"Cannot write transcript for terminal task {task_id} status={job.status}")

        # Idempotency
        seen = self._state.transcript_batch_ids.setdefault(key, set())
        if batch.batch_id and batch.batch_id in seen:
            return {"status": "deduplicated", "task_id": task_id, "batch_id": batch.batch_id}
        if batch.batch_id:
            seen.add(batch.batch_id)

        events = self._state.transcripts.setdefault(key, [])
        next_seq = self._state.transcript_next_seq.setdefault(key, 1)
        now = self._now()
        first_seq = next_seq
        for raw in batch.events:
            kind = str(raw.get("kind") or "assistant_delta")
            text = str(raw.get("text") or "")
            tool_name = raw.get("tool_name")
            event = TranscriptEvent(
                seq=next_seq,
                time=now,
                project_id=project_id,
                task_id=task_id,
                agent_id=batch.agent_id or agent_id,
                worker_name=batch.worker_name,
                kind=kind,
                text=text,
                tool_name=str(tool_name) if tool_name is not None else None,
            )
            events.append(event)
            next_seq += 1
        self._state.transcript_next_seq[key] = next_seq

        # Bounded retention: keep last MAX_TRANSCRIPT_EVENTS_PER_TASK events
        # and at most MAX_TRANSCRIPT_BYTES_PER_TASK total UTF-8 bytes per task.
        # Both caps are evaluated each iteration; the more restrictive one
        # drives each drop. Whole-event dropping keeps seq arithmetic stable
        # for surviving cursors.
        _drop_oldest_events(events, self._state.transcript_truncated_before, key)

        snapshot = self._snapshot_transcripts_locked()
        self._append_to_journal_locked(
            "append_transcript_batch",
            {"task_id": task_id, "project_id": project_id, "agent_id": agent_id, "batch_id": batch.batch_id},
            {"status": "recorded", "first_seq": first_seq, "count": len(batch.events)},
            snapshot,
        )

        # Wake waiters
        waiters = self._state.transcript_waiters.pop(key, []) or []
        for waiter in waiters:
            if not waiter.done():
                waiter.set_result(True)

        return {"status": "recorded", "task_id": task_id, "batch_id": batch.batch_id, "first_seq": first_seq, "count": len(batch.events)}

    def _snapshot_transcripts_locked(self) -> dict[str, Any]:
        """Capture every per-task transcript record for JSONL snapshotting.

        A per-task journal line must reflect the full per-task transcript
        state so a restart from any single snapshot can recover every task.
        Snapshots that only carried the just-written task's records would
        lose any prior tasks on reload.
        """
        snapshot: dict[str, Any] = {
            "transcripts": {
                key: [transcript_event_to_wire(event) for event in events]
                for key, events in self._state.transcripts.items()
            },
            "transcript_next_seq": dict(self._state.transcript_next_seq),
            "transcript_batch_ids": {
                key: list(ids) for key, ids in self._state.transcript_batch_ids.items()
            },
        }
        truncated = {key: int(value) for key, value in self._state.transcript_truncated_before.items() if value is not None}
        if truncated:
            snapshot["transcript_truncated_before"] = truncated
        return snapshot

    def read_transcript_events_locked(
        self,
        task_id: str,
        project_id: str,
        after: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        key = self._key(project_id, task_id)
        events = self._state.transcripts.get(key, [])
        truncated_before = self._state.transcript_truncated_before.get(key)
        selected = [transcript_event_to_wire(event) for event in events if event.seq > after]
        selected = selected[:limit]
        # If the cursor predates retained history, prepend a synthetic marker.
        # Its sequence can equal the first retained event; consumers must not
        # treat the marker itself as consuming that retained event.
        if truncated_before is not None and after < truncated_before:
            marker = TranscriptTruncation(truncated_before).to_event(project_id, task_id)
            selected = [marker] + selected
        return {"project_id": project_id, "task_id": task_id, "events": selected, "next_seq": self._state.transcript_next_seq.get(key, 1)}

    async def wait_transcript_events(
        self,
        task_id: str,
        project_id: str,
        after: int = 0,
        limit: int = 100,
        wait_seconds: int = 0,
    ) -> dict[str, Any]:
        project_id = str(project_id or "default")
        task_id = str(task_id)
        key = self._key(project_id, task_id)
        waiter: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        result = self.read_transcript_events_locked(task_id, project_id, after=after, limit=limit)
        if result["events"] or wait_seconds <= 0:
            return result
        self._state.transcript_waiters.setdefault(key, []).append(waiter)
        try:
            await asyncio.wait_for(waiter, timeout=wait_seconds)
        except asyncio.TimeoutError:
            pass
        finally:
            waiters = self._state.transcript_waiters.get(key, [])
            if waiter in waiters:
                waiters.remove(waiter)
        return self.read_transcript_events_locked(task_id, project_id, after=after, limit=limit)


__all__ = [
    "MAX_TRANSCRIPT_BYTES_PER_TASK",
    "MAX_TRANSCRIPT_EVENTS_PER_TASK",
    "MemoryTranscriptStore",
    "reset_retention_limits",
    "set_retention_limits",
]
