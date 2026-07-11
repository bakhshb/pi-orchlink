"""JSONL-backed broker store with bounded growth and atomic durability.

The store keeps :class:`MemoryMessageStore`'s behavior, persists every
durable mutation through the journal, and replays the latest snapshot on
startup.

S04 durability properties:

* Every durable mutation, snapshot capture, journal append, and compaction
  run inside a single critical section keyed off the inherited state lock.
  The same task that mutates state captures the snapshot and writes the
  journal line; no other mutation can interleave, so the journal's record
  order matches the authoritative mutation order.
* Each journal line is flushed and ``fsync``'d before the mutation returns,
  so a process crash can lose at most the in-flight mutation.
* The journal tolerates a partial final line on read: the truncated tail
  is repaired (rewritten to the valid prefix via the same atomic path as
  compaction) so a later append never concatenates after corrupt bytes and
  a future restart never ignores a successful mutation.
* The journal compacts atomically (``sibling tmp + fsync + os.replace``)
  to a single-snapshot record whenever the file size or record count
  crosses a bounded threshold. Compaction is maintenance: if it fails the
  exception is logged but never propagated, because the preceding append
  is already durable.
* Mutations that raise never reach the journal: the append runs only after
  the in-memory mutation returns successfully.
* Every state-changing method inherited from :class:`MemoryMessageStore`
  is overridden here so persistence cannot be bypassed by a future caller.
  Read-only paths that may only change state through background expiry
  journal a record only when expiry actually mutated something.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar

from orchlink.broker.state import is_terminal_status
from orchlink.broker.storage.base import ActivityInput, AgentInput, MessageInput, SessionAcquireInput, SessionHeartbeatInput
from orchlink.broker.storage.memory import MemoryMessageStore
from orchlink.broker.storage.memory_activity_store import MemoryActivityStore
from orchlink.broker.storage.memory_event_log import MemoryEventLog
from orchlink.broker.storage.memory_job_projector import MemoryJobProjector
from orchlink.broker.storage.memory_session_store import MemorySessionStore

from orchlink.broker.storage.memory_transcript_store import MemoryTranscriptStore
from orchlink.broker.storage.memory_work_queue import MemoryWorkQueue
from orchlink.broker.storage.persistence import (
    atomic_append_jsonl_line,
    atomic_write_text,
    encode_jsonl_record,
)
from orchlink.core.models import Job, JobRoute, ReplyResult, SessionAcquire, SessionHeartbeat, TaskResult, WaitBlocker, WorkerActivityInput
from orchlink.core.views import (
    activity_record_from_wire,
    activity_record_to_wire,
    agent_input_to_agent,
    agent_to_wire,
    broker_event_from_wire,
    broker_event_to_wire,
    conversation_from_wire,
    conversation_to_wire,
    job_payload,
    lease_from_wire,
    lease_to_wire,
    message_input_to_wire,
    reply_result_to_wire,
    session_from_wire,
    session_to_wire,
    stored_message_from_wire,
    stored_message_to_wire,
    talk_job_payload_from_wire,
    task_job_payload_from_wire,
    task_projection_from_wire,
    task_projection_to_wire,
    task_result_from_wire,
    task_result_to_wire,
    task_telemetry_from_wire,
    task_telemetry_to_wire,
    wait_blocker_to_wire,
)
T = TypeVar("T")


# Compaction thresholds. Defaults keep the file comfortably under 256 KiB
# of accumulated audit lines while still amortizing the cost of the
# compaction ``fsync`` over many appends. Tests override these to exercise
# compaction deterministically without paying for thousands of mutations.
DEFAULT_MAX_RECORDS = 64
DEFAULT_MAX_BYTES = 256 * 1024


class _ReentrantAsyncLock:
    """Minimal asyncio re-entrant lock used by :class:`JsonlMessageStore`.

    ``_recorded`` holds the state lock across ``mutation -> snapshot ->
    append -> fsync``. The inherited :class:`MemoryMessageStore` mutation
    methods also acquire ``self._lock``. ``asyncio.Lock`` is not re-entrant,
    so we wrap it with depth tracking keyed by the current task so a single
    task can hold the lock across both the journal and the parent method
    without deadlocking.
    """

    __slots__ = ("_depth", "_lock", "_owner")

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task | None = None
        self._depth = 0

    def locked(self) -> bool:
        return self._lock.locked() or self._depth > 0

    async def __aenter__(self) -> "_ReentrantAsyncLock":
        task = asyncio.current_task()
        if self._owner is task:
            self._depth += 1
            return self
        await self._lock.acquire()
        self._owner = task
        self._depth = 1
        return self

    async def __aexit__(self, *exc_info: Any) -> bool:
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
            self._lock.release()
        return False


def _job_to_dict(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "project_id": job.project_id,
        "route": {"from_agent": job.route.from_agent, "to_agent": job.route.to_agent},
        "mode": job.mode,
        "status": job.status,
        "task_id": job.task_id,
        "conversation_id": job.conversation_id,
        "turn": job.turn,
        "max_turns": job.max_turns,
        "payload": job_payload(job),
        "lease": lease_to_wire(job.lease),
    }


def _job_from_dict(data: dict[str, Any]) -> Job:
    route = data.get("route") or {}
    kind = str(data.get("kind") or "task")
    payload_data = dict(data.get("payload") or {})
    payload = talk_job_payload_from_wire(payload_data) if kind == "talk" else task_job_payload_from_wire(payload_data)
    return Job(
        id=str(data.get("id") or data.get("task_id") or data.get("conversation_id") or ""),
        kind=kind,
        project_id=str(data.get("project_id") or "default"),
        route=JobRoute(from_agent=str(route.get("from_agent") or ""), to_agent=str(route.get("to_agent") or "")),
        mode=str(data.get("mode") or "PLAN"),
        status=str(data.get("status") or "CREATED"),
        task_id=data.get("task_id"),
        conversation_id=data.get("conversation_id"),
        turn=int(data.get("turn") or 1),
        max_turns=int(data.get("max_turns") or 1),
        payload=payload,
        lease=lease_from_wire(data.get("lease")),
    )


def _agent_to_journal_request(agent: AgentInput) -> dict[str, Any]:
    """Render an agent input as JSON-serializable request data for JSONL."""
    return agent_to_wire(agent_input_to_agent(agent))


def _message_to_journal_request(message: MessageInput) -> dict[str, Any]:
    """Render a message input as JSON-serializable request data for JSONL."""
    return message_input_to_wire(message)


def _drop_none(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def _session_acquire_to_journal_request(session: SessionAcquireInput) -> dict[str, Any]:
    if isinstance(session, SessionAcquire):
        return _drop_none(asdict(session))
    raise TypeError(f"Unsupported session acquire input: {type(session).__name__}")


def _session_heartbeat_to_journal_request(heartbeat: SessionHeartbeatInput | None) -> dict[str, Any] | None:
    if heartbeat is None:
        return None
    if isinstance(heartbeat, SessionHeartbeat):
        data = _drop_none(asdict(heartbeat))
        data.pop("lease_id", None)
        data.pop("project_id", None)
        return data
    raise TypeError(f"Unsupported session heartbeat input: {type(heartbeat).__name__}")


def _activity_to_journal_request(activity: ActivityInput) -> dict[str, Any]:
    if isinstance(activity, WorkerActivityInput):
        return _drop_none(asdict(activity))
    raise TypeError(f"Unsupported activity input: {type(activity).__name__}")


class JsonlMessageStore(MemoryMessageStore):
    """Memory store with hardened JSONL persistence.

    See module docstring for the full S04 durability contract.
    """

    def __init__(
        self,
        path: str | Path,
        require_peer_sessions: bool = False,
        session_grace_seconds: int = 25,
        *,
        max_records: int | None = None,
        max_bytes: int | None = None,
    ) -> None:
        super().__init__(require_peer_sessions=require_peer_sessions, session_grace_seconds=session_grace_seconds)
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._transcript_path = self._transcript_path_for(str(self.path))
        # Replace the parent's non-reentrant lock with a re-entrant one so
        # ``_recorded`` can hold the lock across ``mutation -> snapshot ->
        # append -> fsync`` without deadlocking the inherited methods that
        # also acquire ``self._lock``.
        self._lock = _ReentrantAsyncLock()
        self._max_records = max_records if max_records is not None else DEFAULT_MAX_RECORDS
        self._max_bytes = max_bytes if max_bytes is not None else DEFAULT_MAX_BYTES
        self._record_count = 0
        # Recreate focused components after path is known so the transcript store
        # receives the derived transcript journal path.
        self._event_log = MemoryEventLog(self._state, self._now)
        self._job_projector = MemoryJobProjector(
            self._state,
            self._job_lifecycle,
            self._now,
            self._event_log,
        )
        self._session_store = MemorySessionStore(
            self._state,
            self._now,
            self._parse_time,
            self._event_log,
            session_grace_seconds,
        )
        self._activity_store = MemoryActivityStore(
            self._state,
            self._event_log,
            self._now,
            self._apply_activity_to_work_locked,
        )
        self._work_queue = MemoryWorkQueue(
            self._state,
            self._now,
            self._event_log,
            self._session_store,
            self._job_projector,
            require_peer_sessions,
        )
        self._transcript_store = MemoryTranscriptStore(
            self._state,
            self._now,
            self._session_store,
            self._job_projector,
            journal_path=self._transcript_path,
        )
        self._load_latest_snapshot()
        # Transcript journal replay/restore after main snapshot loaded.
        self._load_latest_transcript_snapshot()

    # ------------------------------------------------------------------
    # Transcript snapshot / restore
    # ------------------------------------------------------------------

    def _transcript_path_for(self, journal_path: str) -> str:
        path = Path(journal_path)
        return str(path.parent / (path.stem + ".transcript.jsonl"))

    def _load_latest_transcript_snapshot(self) -> None:
        from orchlink.broker.storage.persistence import read_latest_snapshot

        latest = read_latest_snapshot(self._transcript_path)
        if latest is not None:
            self._load_transcript_snapshot(latest)

    # ------------------------------------------------------------------
    # Snapshot capture / restore
    # ------------------------------------------------------------------

    def _snapshot(self) -> dict[str, Any]:
        return {
            "agents": {key: agent_to_wire(agent) for key, agent in self._state.agents.items()},
            "active_messages": {key: stored_message_to_wire(stored) for key, stored in self._state.active_messages.items()},
            "tasks": {key: task_projection_to_wire(task) for key, task in self._state.tasks.items()},
            "task_jobs": {key: _job_to_dict(job) for key, job in self._state.task_jobs.items()},
            "results_by_task": {key: task_result_to_wire(result) for key, result in self._state.results_by_task.items()},
            "conversations": {
                key: conversation_to_wire(conversation)
                for key, conversation in self._state.conversations.items()
            },
            "talk_jobs": {key: _job_to_dict(job) for key, job in self._state.talk_jobs.items()},
            "events": [broker_event_to_wire(event) for event in self._state.events],
            "activity": [activity_record_to_wire(activity) for activity in self._state.activity],
            "sessions": {key: session_to_wire(value) for key, value in self._state.sessions.items()},
            "next_event_id": self._state.next_event_id,
            "next_activity_id": self._state.next_activity_id,
            # G019 AC-5: latest-state telemetry records live in the main
            # broker snapshot — they replace in place and are bounded by
            # the number of distinct active tasks, so an unbounded
            # heartbeat history is impossible by construction.
            "telemetry_by_task": {
                key: task_telemetry_to_wire(telemetry_record)
                for key, telemetry_record in self._state.telemetry_by_task.items()
            },
            # NOTE: per G018, transcript state lives only in the adjacent
            # ``.transcript.jsonl`` journal and never in the main broker
            # snapshot. A replayed or partially-corrupt main snapshot must
            # not resurrect transcript events the transcript journal has
            # already dropped, compacted, or never recorded.
        }

    def _restore_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._state.agents = {str(key): agent_input_to_agent(value) for key, value in (snapshot.get("agents") or {}).items()}
        # Active messages are reconstituted as StoredMessage via the same
        # boundary validation used at enqueue.
        self._state.active_messages = {
            str(key): stored_message_from_wire(dict(value))
            for key, value in (snapshot.get("active_messages") or {}).items()
        }
        self._state.tasks = {str(key): task_projection_from_wire(value) for key, value in (snapshot.get("tasks") or {}).items()}
        self._state.task_jobs = {str(key): _job_from_dict(value) for key, value in (snapshot.get("task_jobs") or {}).items()}
        self._state.results_by_task = {str(key): task_result_from_wire(value) for key, value in (snapshot.get("results_by_task") or {}).items()}
        self._state.conversations = {
            str(key): conversation_from_wire(dict(value))
            for key, value in (snapshot.get("conversations") or {}).items()
        }
        self._state.talk_jobs = {str(key): _job_from_dict(value) for key, value in (snapshot.get("talk_jobs") or {}).items()}
        self._state.events = [broker_event_from_wire(value) for value in (snapshot.get("events") or [])]
        self._state.activity = [activity_record_from_wire(value) for value in (snapshot.get("activity") or [])]
        self._state.sessions = {str(key): session_from_wire(value) for key, value in (snapshot.get("sessions") or {}).items()}
        # G019 AC-5: telemetry latest-state records are reconstituted via
        # the same wire validators so a stale or corrupted record cannot
        # bypass the lease-fence metadata or inject negative tool counts.
        self._state.telemetry_by_task = {
            str(key): task_telemetry_from_wire(dict(value))
            for key, value in (snapshot.get("telemetry_by_task") or {}).items()
        }
        self._state.next_event_id = int(snapshot.get("next_event_id") or (self._state.events[-1].id + 1 if self._state.events else 1))
        self._state.next_activity_id = int(snapshot.get("next_activity_id") or (self._state.activity[-1].id + 1 if self._state.activity else 1))
        self._state.inboxes = {agent_id: asyncio.Queue() for agent_id in self._state.agents}
        for stored in self._state.active_messages.values():
            if is_terminal_status(stored.status):
                continue
            to_agent = str(stored.envelope.to_agent or "")
            if to_agent:
                self._state.inboxes.setdefault(to_agent, asyncio.Queue()).put_nowait(stored)

    def _load_latest_snapshot(self) -> None:
        """Load the latest snapshot and repair a truncated trailing line.

        S04: a process crash mid-append can leave a partial trailing line
        in the journal. We recover from the preceding valid snapshot *and*
        repair the file so the partial bytes do not survive. Without the
        repair, a later durable append would concatenate after the corrupt
        tail and a future restart would stop at the corrupt line and
        silently ignore every mutation appended after it.

        The repair runs at construction — before the store is exposed to
        any caller — so it is serialized ahead of every append under the
        same critical section that owns the journal. We keep every valid
        preceding record, drop the corrupt tail, and rewrite the file
        through the same atomic tmp + fsync + ``os.replace`` path used by
        compaction, so the canonical file is either fully repaired or fully
        unchanged. ``_record_count`` is seeded from the surviving records
        so the compaction policy does not fire on the next append purely
        from inherited history.
        """
        if not self.path.is_file():
            return
        latest: dict[str, Any] | None = None
        valid_count = 0
        valid_lines: list[str] = []
        corrupt = False
        with self.path.open("r", encoding="utf-8") as file:
            for raw in file:
                stripped = raw.rstrip("\n")
                if not stripped:
                    continue
                try:
                    record = json.loads(stripped)
                except json.JSONDecodeError:
                    # Truncated / corrupt line — stop trusting this point
                    # forward. The valid prefix collected so far is what the
                    # repair rewrites the file down to.
                    corrupt = True
                    break
                valid_lines.append(stripped)
                if not isinstance(record, dict):
                    continue
                valid_count += 1
                snapshot = record.get("snapshot")
                if isinstance(snapshot, dict):
                    latest = snapshot
        if corrupt:
            # Drop the corrupt trailing bytes by atomically rewriting the
            # journal to the valid prefix. Every preceding record is
            # preserved and the file ends on a clean ``\n`` so the next
            # append starts a fresh line instead of concatenating after
            # corrupt bytes.
            atomic_write_text(self.path, "".join(line + "\n" for line in valid_lines))
        self._record_count = valid_count
        if latest is not None:
            self._restore_snapshot(latest)

    # ------------------------------------------------------------------
    # Append + compaction
    # ------------------------------------------------------------------

    def _append_record_locked(
        self,
        operation: str,
        request: dict[str, Any],
        result: Any,
        snapshot: dict[str, Any],
    ) -> None:
        """Append a journal line and fsync it. Must be called under ``self._lock``.

        Holding the state lock through the append guarantees that another
        mutation cannot interleave its ``_snapshot`` capture with our write,
        so every journal record's snapshot reflects the state visible at
        the moment the mutation committed.
        """
        record = {
            "time": self._now(),
            "operation": operation,
            "request": request,
            "result": result,
            "snapshot": snapshot,
        }
        atomic_append_jsonl_line(self.path, encode_jsonl_record(record) + "\n")
        self._record_count += 1

    def _maybe_compact_locked(self) -> None:
        """Compact the journal to a single-snapshot record if the threshold
        is exceeded. Must be called under ``self._lock`` (so the snapshot
        reflects the authoritative state) and before the next append
        potentially observes a stale ``_record_count``.
        """
        try:
            file_size = self.path.stat().st_size
        except OSError:
            return
        if file_size < self._max_bytes and self._record_count < self._max_records:
            return
        snapshot = self._snapshot()
        compact_record = {
            "time": self._now(),
            "operation": "_compact",
            "request": {},
            "result": {
                "compacted_from": self._record_count,
                "compacted_size": file_size,
            },
            "snapshot": snapshot,
        }
        line = encode_jsonl_record(compact_record) + "\n"
        atomic_write_text(self.path, line)
        self._record_count = 1
        self._maybe_compact_transcript_locked()

    def _maybe_compact_transcript_locked(self) -> None:
        """Compact the transcript journal similarly to the main journal."""
        if not self._transcript_path:
            return
        try:
            from orchlink.broker.storage.persistence import atomic_write_text, encode_jsonl_record, count_complete_jsonl_lines

            file_size = Path(self._transcript_path).stat().st_size
            record_count = count_complete_jsonl_lines(self._transcript_path)
            if file_size < self._max_bytes and record_count < self._max_records:
                return
            snapshot = self._snapshot_transcripts()
            compact_record = {
                "time": self._now(),
                "operation": "_compact_transcript",
                "request": {},
                "result": {"compacted_from": record_count, "compacted_size": file_size},
                "snapshot": snapshot,
            }
            atomic_write_text(self._transcript_path, encode_jsonl_record(compact_record) + "\n")
        except Exception as exc:
            logging.getLogger(__name__).warning("Transcript JSONL compaction failed: %s", exc)

    # ------------------------------------------------------------------
    # Recorded mutation envelope
    # ------------------------------------------------------------------

    def _compact_or_log(self) -> None:
        """Attempt compaction; log failures but never let them escape.

        Compaction is maintenance, not part of the mutation contract. The
        journal line for the successful mutation is already durable, so a
        compaction failure cannot be allowed to fail the caller.
        """
        try:
            self._maybe_compact_locked()
        except Exception as exc:
            logging.getLogger(__name__).warning("JSONL compaction failed: %s", exc)

    async def _recorded(self, operation: str, request: dict[str, Any], call: Callable[[], Awaitable[T]]) -> T:
        """Run a mutation, capture the resulting snapshot, and journal it.

        Ordering invariants:

        * The mutation runs first. If it raises, the journal is untouched
          and the exception propagates — S04 never persists a failed
          mutation.
        * The snapshot is captured under the state lock so no concurrent
          mutation can mutate ``self._state`` while we read it.
        * The append + ``fsync`` happen under the same state lock, so the
          authoritative state and the journal line land together.
        * Compaction runs under the same state lock, but a compaction
          failure is logged and swallowed so the mutation still returns
          successfully.
        """
        async with self._lock:
            result = await call()
            snapshot = self._snapshot()
            self._append_record_locked(operation, request, result, snapshot)
            self._compact_or_log()
        return result

    async def _recorded_read(self, operation: str, request: dict[str, Any], call: Callable[[], Awaitable[T]]) -> T:
        """Run a read, journal only if background expiry changed state.

        Read-only paths may still mutate state through
        ``_expire_timed_out_messages_locked``. If that (or any other side
        effect) changed the snapshot, we persist the resulting state so a
        later replay does not silently lose the timeout-driven transition.
        """
        async with self._lock:
            before = self._snapshot()
            result = await call()
            after = self._snapshot()
            if before != after:
                self._append_record_locked(operation, request, result, after)
                self._compact_or_log()
            return result

    # ------------------------------------------------------------------
    # Sync override: checkpoint drifts persisted through the same pipeline
    # ------------------------------------------------------------------

    def append_checkpoint_drifts(self, drifts: list[Any]) -> None:
        """Persist checkpoint drift records through the journal.

        ``append_checkpoint_drifts`` is a synchronous startup hook (see
        ``BrokerService.startup_reconcile_checkpoint``), so it cannot
        ``await self._lock`` like the async mutations. It reuses the *same*
        ordered snapshot/append/compact pipeline — mutate first, capture
        the snapshot, append + ``fsync`` the journal line, then maybe
        compact — so the drift events survive restart and replay
        identically to every other mutation.

        Startup reconciliation runs before the broker serves traffic, so no
        async mutation can interleave; this matches the parent
        ``MemoryMessageStore``, which also mutates state here without the
        async lock. A failed durable append propagates to the caller (which
        logs and continues), exactly as a failed append propagates out of
        ``_recorded``.
        """
        super().append_checkpoint_drifts(drifts)
        snapshot = self._snapshot()
        self._append_record_locked(
            "append_checkpoint_drifts",
            {"drifts": [drift.to_dict() for drift in drifts]},
            {"count": len(drifts)},
            snapshot,
        )
        self._compact_or_log()

    # ------------------------------------------------------------------
    # Inherited mutation overrides (each routes through ``_recorded``)
    # ------------------------------------------------------------------

    async def register_agent(self, agent: AgentInput) -> dict[str, Any]:
        return await self._recorded(
            "register_agent",
            {"agent": _agent_to_journal_request(agent)},
            lambda: MemoryMessageStore.register_agent(self, agent),
        )

    async def enqueue_message(self, message: MessageInput, create_waiter: bool = False) -> dict[str, Any]:
        return await self._recorded(
            "enqueue_message",
            {"message": _message_to_journal_request(message), "create_waiter": create_waiter},
            lambda: MemoryMessageStore.enqueue_message(self, message, create_waiter=create_waiter),
        )

    async def save_reply(
        self,
        message_id: str,
        reply: MessageInput,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
        session_lease_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._recorded(
            "save_reply",
            {"message_id": message_id, "reply": _message_to_journal_request(reply), "session_lease_id": session_lease_id},
            lambda: MemoryMessageStore.save_reply(
                self,
                message_id,
                reply,
                lease_epoch=lease_epoch,
                lease_holder=lease_holder,
                session_lease_id=session_lease_id,
            ),
        )

    async def update_message_status(
        self,
        message_id: str,
        status: str,
        session_lease_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._recorded(
            "update_message_status",
            {"message_id": message_id, "status": status, "session_lease_id": session_lease_id},
            lambda: MemoryMessageStore.update_message_status(self, message_id, status, session_lease_id=session_lease_id),
        )

    async def record_activity(self, activity: ActivityInput) -> dict[str, Any]:
        return await self._recorded(
            "record_activity",
            {"activity": _activity_to_journal_request(activity)},
            lambda: MemoryMessageStore.record_activity(self, activity),
        )

    async def acquire_session(self, session: SessionAcquireInput) -> dict[str, Any]:
        return await self._recorded(
            "acquire_session",
            {"session": _session_acquire_to_journal_request(session)},
            lambda: MemoryMessageStore.acquire_session(self, session),
        )

    async def heartbeat_session(
        self,
        lease_id: str,
        project_id: str | None = None,
        heartbeat: SessionHeartbeatInput | None = None,
    ) -> dict[str, Any]:
        return await self._recorded(
            "heartbeat_session",
            {"lease_id": lease_id, "project_id": project_id, "heartbeat": _session_heartbeat_to_journal_request(heartbeat)},
            lambda: MemoryMessageStore.heartbeat_session(self, lease_id, project_id=project_id, heartbeat=heartbeat),
        )

    async def release_session(self, lease_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        return await self._recorded(
            "release_session",
            {"lease_id": lease_id, "reason": reason, "project_id": project_id},
            lambda: MemoryMessageStore.release_session(self, lease_id, reason=reason, project_id=project_id),
        )

    async def expire_sessions(self) -> list[dict[str, Any]]:
        return await self._recorded(
            "expire_sessions",
            {},
            lambda: MemoryMessageStore.expire_sessions(self),
        )

    async def cancel_work(self, item_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        return await self._recorded(
            "cancel_work",
            {"item_id": item_id, "reason": reason, "project_id": project_id},
            lambda: MemoryMessageStore.cancel_work(self, item_id, reason=reason, project_id=project_id),
        )

    async def heartbeat_job(self, task_id: str, holder: str, epoch: int, project_id: str | None = None, heartbeat_ms: int | None = None) -> dict[str, Any]:
        return await self._recorded(
            "heartbeat_job",
            {"task_id": task_id, "holder": holder, "epoch": epoch, "project_id": project_id, "heartbeat_ms": heartbeat_ms},
            lambda: MemoryMessageStore.heartbeat_job(self, task_id, holder, epoch, project_id=project_id, heartbeat_ms=heartbeat_ms),
        )

    async def reclaim_job(self, task_id: str, holder: str, project_id: str | None = None) -> dict[str, Any]:
        return await self._recorded(
            "reclaim_job",
            {"task_id": task_id, "holder": holder, "project_id": project_id},
            lambda: MemoryMessageStore.reclaim_job(self, task_id, holder, project_id=project_id),
        )

    async def close_conversation(self, conversation_id: str, message: MessageInput) -> dict[str, Any]:
        return await self._recorded(
            "close_conversation",
            {"conversation_id": conversation_id, "message": _message_to_journal_request(message)},
            lambda: MemoryMessageStore.close_conversation(self, conversation_id, message),
        )

    # ------------------------------------------------------------------
    # Polling / wait mutators (release the lock while waiting)
    # ------------------------------------------------------------------

    async def get_next_message(
        self,
        agent_id: str,
        wait_seconds: int,
        lease_id: str | None = None,
        project_id: str | None = None,
        on_delivered: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any] | None:
        """Deliver the next inbox message and journal the state transition.

        This mirrors :meth:`MemoryMessageStore.get_next_message` so the
        delivery status change (``QUEUED`` -> ``DELIVERED``) is persisted.
        The lock is released while waiting on the inbox, exactly as in the
        parent implementation. The optional ``on_delivered`` callback is
        invoked synchronously after the journal append has landed under the
        store lock, so the caller can record the in-flight lease before
        the delivery critical section ends.
        """
        async with self._lock:
            self._expire_timed_out_messages_locked()
            self._session_store.assert_poll_lease_locked(agent_id, project_id=project_id, lease_id=lease_id)
            inbox = self._work_queue.inbox_for_agent_locked(agent_id)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_seconds
        while True:
            timeout = max(0, deadline - loop.time()) if wait_seconds > 0 else 0
            try:
                message = await asyncio.wait_for(inbox.get(), timeout=timeout)
            except asyncio.TimeoutError:
                return None

            async with self._lock:
                self._expire_timed_out_messages_locked()
                self._session_store.assert_poll_lease_locked(agent_id, project_id=project_id, lease_id=lease_id)
                before = self._snapshot()
                delivered = self._work_queue.deliver_message_locked(message)
                if delivered is None:
                    after = self._snapshot()
                    if before != after:
                        self._append_record_locked(
                            "get_next_message",
                            {"agent_id": agent_id, "project_id": project_id, "wait_seconds": wait_seconds, "result": "skipped_stale"},
                            None,
                            after,
                        )
                        self._compact_or_log()
                    if wait_seconds <= 0 or loop.time() >= deadline:
                        return None
                    continue
                after = self._snapshot()
                self._append_record_locked(
                    "get_next_message",
                    {"agent_id": agent_id, "project_id": project_id, "wait_seconds": wait_seconds, "message_id": delivered.get("message_id")},
                    delivered,
                    after,
                )
                self._compact_or_log()
                self._invoke_on_delivered(delivered, on_delivered)
            return delivered

    async def wait_for_reply(self, correlation_id: str, timeout_seconds: int) -> dict[str, Any]:
        """Wait for a reply; journal only if the timeout path mutates state."""
        async with self._lock:
            self._expire_timed_out_messages_locked()
            future = self._work_queue.reply_future_locked(correlation_id)

        try:
            reply_result = await asyncio.wait_for(future, timeout=timeout_seconds)
            if isinstance(reply_result, ReplyResult):
                return reply_result_to_wire(reply_result)
            return wait_blocker_to_wire(reply_result)
        except asyncio.TimeoutError:
            async with self._lock:
                before = self._snapshot()
                self._work_queue.timeout_reply_locked(correlation_id)
                after = self._snapshot()
                if before != after:
                    self._append_record_locked(
                        "wait_for_reply",
                        {"correlation_id": correlation_id, "timeout_seconds": timeout_seconds, "result": "timeout"},
                        None,
                        after,
                    )
                    self._compact_or_log()
            return {
                "status": "timeout",
                "correlation_id": correlation_id,
                "error": "Worker did not reply before timeout.",
            }
        finally:
            if future.done() or future.cancelled():
                async with self._lock:
                    self._work_queue.cleanup_reply_waiter_locked(correlation_id, future)

    async def wait_for_task(self, task_id: str, timeout_seconds: int, project_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            before = self._snapshot()
            self._expire_timed_out_messages_locked()
            result, task_key, future = self._work_queue.prepare_task_wait_locked(task_id, project_id=project_id)
            after = self._snapshot()
            if before != after:
                self._append_record_locked(
                    "wait_for_task",
                    {"task_id": task_id, "project_id": project_id},
                    {"status": "expiry_applied"},
                    after,
                )
                self._compact_or_log()
            if isinstance(result, TaskResult):
                return task_result_to_wire(result)
            if isinstance(result, WaitBlocker):
                return wait_blocker_to_wire(result)
            assert future is not None

        try:
            completed = await asyncio.wait_for(future, timeout=timeout_seconds)
            return task_result_to_wire(completed)
        except asyncio.TimeoutError:
            async with self._lock:
                self._work_queue.cleanup_task_waiter_locked(task_key, future)
            return {"status": "WAIT_TIMEOUT", "project_id": project_id, "task_id": task_id, "error": "No task result arrived before the wait timeout."}

    # ------------------------------------------------------------------
    # Read-only paths that may mutate state through background expiry
    # ------------------------------------------------------------------

    async def list_jobs(
        self,
        limit: int = 50,
        project_id: str | None = None,
        active: bool = False,
        status: str | None = None,
        kind: str | None = None,
        item_id: str | None = None,
    ) -> list[dict[str, Any]]:
        async def _call() -> list[dict[str, Any]]:
            self._expire_timed_out_messages_locked()
            return self._job_projector.list_jobs_locked(
                limit=limit,
                project_id=project_id,
                active=active,
                status=status,
                kind=kind,
                item_id=item_id,
            )

        return await self._recorded_read(
            "list_jobs",
            {"limit": limit, "project_id": project_id, "active": active, "status": status, "kind": kind, "item_id": item_id},
            _call,
        )

    async def list_active_messages(self, project_id: str | None = None) -> list[dict[str, Any]]:
        async def _call() -> list[dict[str, Any]]:
            self._expire_timed_out_messages_locked()
            return self._work_queue.list_active_messages_locked(project_id=project_id)

        return await self._recorded_read(
            "list_active_messages",
            {"project_id": project_id},
            _call,
        )

    async def list_conversations(self, project_id: str | None = None) -> list[dict[str, Any]]:
        async def _call() -> list[dict[str, Any]]:
            self._expire_timed_out_messages_locked()
            return self._job_projector.list_conversations_locked(project_id=project_id)

        return await self._recorded_read(
            "list_conversations",
            {"project_id": project_id},
            _call,
        )

    async def can_auto_stop(self, project_id: str | None = None) -> bool:
        async def _call() -> bool:
            self._expire_timed_out_messages_locked()
            return self._session_store.active_session_count_locked(project_id) == 0 and self._active_job_count_locked(project_id) == 0

        return await self._recorded_read(
            "can_auto_stop",
            {"project_id": project_id},
            _call,
        )

    async def get_task_result(self, task_id: str, project_id: str | None = None) -> dict[str, Any]:
        async def _call() -> dict[str, Any]:
            self._expire_timed_out_messages_locked()
            return self._job_projector.get_task_result_locked(task_id, project_id=project_id)

        return await self._recorded_read(
            "get_task_result",
            {"task_id": task_id, "project_id": project_id},
            _call,
        )

    async def list_sessions(self, project_id: str | None = None, active: bool = False) -> list[dict[str, Any]]:
        async def _call() -> list[dict[str, Any]]:
            self._expire_timed_out_messages_locked()
            return self._session_store.list_sessions_locked(project_id=project_id, active=active)

        return await self._recorded_read(
            "list_sessions",
            {"project_id": project_id, "active": active},
            _call,
        )

    # ------------------------------------------------------------------
    # Transcript overrides (G018)
    # ------------------------------------------------------------------

    async def append_transcript_batch(
        self,
        batch: Any,
        task_id: str,
        project_id: str,
        agent_id: str,
        session_lease_id: str | None = None,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
    ) -> dict[str, Any]:
        from orchlink.core.models import TranscriptBatch

        if isinstance(batch, dict):
            batch = TranscriptBatch.from_wire(batch)
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._transcript_store.append_transcript_batch_locked(
                batch,
                task_id,
                project_id,
                agent_id,
                session_lease_id=session_lease_id,
                lease_epoch=lease_epoch,
                lease_holder=lease_holder,
            )

    async def read_transcript_events(
        self,
        task_id: str,
        project_id: str,
        after: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        async with self._lock:
            return self._transcript_store.read_transcript_events_locked(task_id, project_id, after=after, limit=limit)

    async def wait_transcript_events(
        self,
        task_id: str,
        project_id: str,
        after: int = 0,
        limit: int = 100,
        wait_seconds: int = 0,
    ) -> dict[str, Any]:
        return await self._transcript_store.wait_transcript_events(
            task_id, project_id, after=after, limit=limit, wait_seconds=wait_seconds
        )

    # ------------------------------------------------------------------
    # Telemetry overrides (G019 AC-5)
    # ------------------------------------------------------------------

    async def record_task_telemetry(
        self,
        telemetry: Any,
        *,
        agent_id: str,
        session_lease_id: str | None = None,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
    ) -> dict[str, Any]:
        from orchlink.core.models import TaskTelemetry
        from orchlink.core.views import task_telemetry_from_wire

        if not isinstance(telemetry, TaskTelemetry):
            telemetry = task_telemetry_from_wire(telemetry)

        async def _mutate() -> dict[str, Any]:
            self._expire_timed_out_messages_locked()
            return self._telemetry_store.record_telemetry_locked(
                telemetry,
                agent_id=agent_id,
                session_lease_id=session_lease_id,
                lease_epoch=lease_epoch,
                lease_holder=lease_holder,
            )

        # _recorded runs the mutation, then captures the snapshot and
        # appends a journal line under the same lock. A rejected mutation
        # raises before any state mutation, so the journal is never
        # touched on rejection and the JSONL replay stays stable.
        return await self._recorded("record_task_telemetry", {}, _mutate)

    async def get_task_telemetry(
        self,
        task_id: str,
        project_id: str,
    ) -> dict[str, Any] | None:
        async with self._lock:
            return self._telemetry_store.get_task_telemetry_locked(project_id, task_id)

    async def list_task_telemetry(
        self,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return self._telemetry_store.list_task_telemetry_locked(project_id=project_id)
