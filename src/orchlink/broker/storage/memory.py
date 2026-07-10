"""In-memory broker message store facade.

The five focused helpers (``MemoryEventLog``, ``MemorySessionStore``,
``MemoryActivityStore``, ``MemoryJobProjector``, ``MemoryWorkQueue``) live
in their own sibling modules and are re-exported from here so historical
imports like ``from orchlink.broker.storage.memory import MemorySessionStore``
keep working. ``MemoryMessageStore`` composes the focused components and
implements the :class:`orchlink.broker.storage.base.MessageStore` contract.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable

from orchlink.broker.state import (
    BUSY_MESSAGE_STATUSES,
    TERMINAL_MESSAGE_STATUSES,
    is_busy_status,
    is_talk_message_type,
)
from orchlink.broker.storage.base import (
    ActivityInput,
    AgentInput,
    LeaseConflictError,
    MessageInput,
    MessageStore,
    SessionAcquireInput,
    SessionHeartbeatInput,
)
from orchlink.broker.storage.memory_activity_store import MemoryActivityStore
from orchlink.broker.storage.memory_event_log import MemoryEventLog
from orchlink.broker.storage.memory_job_projector import MemoryJobProjector
from orchlink.broker.storage.memory_session_store import MemorySessionStore
from orchlink.broker.storage.memory_state import (
    DEFAULT_JOB_HEARTBEAT_MS,
    JOB_LEASE_GRACE_MULTIPLIER,
    InMemoryBrokerState,
    MessageProjectionContext,
)
from orchlink.broker.storage.memory_work_queue import MemoryWorkQueue
from orchlink.core.job_lifecycle import BrokerJobLifecycle
from orchlink.core.models import (
    ActivityRecord,
    Agent,
    BrokerEvent,
    BrokerEventContext,
    Job,
    ReplyResult,
    StoredMessage,
    TalkJobPayload,
    TaskResult,
    WaitBlocker,
)
from orchlink.core.views import (
    agent_to_wire,
    agent_input_to_agent,
    conversation_from_wire,
    lease_to_wire,
    message_input_to_stored,
    reply_result_to_wire,
    session_acquire_from_wire,
    session_heartbeat_from_wire,
    session_release_from_wire,
    stored_message_to_wire,
    talk_job_to_wire,
    task_result_to_wire,
    wait_blocker_to_wire,
    worker_activity_from_wire,
)


# Backward-compat re-exports: historic imports and tests reference these
# names from ``orchlink.broker.storage.memory``. Production code should
# import them from the focused modules directly.
__all__ = [
    "DEFAULT_JOB_HEARTBEAT_MS",
    "InMemoryBrokerState",
    "JOB_LEASE_GRACE_MULTIPLIER",
    "MemoryActivityStore",
    "MemoryEventLog",
    "MemoryJobProjector",
    "MemoryMessageStore",
    "MemorySessionStore",
    "MemoryWorkQueue",
    "MessageProjectionContext",
]


class MemoryMessageStore(MessageStore):
    def __init__(self, require_peer_sessions: bool = False, session_grace_seconds: int = 25) -> None:
        self.require_peer_sessions = require_peer_sessions
        self.session_grace_seconds = session_grace_seconds
        self._state = InMemoryBrokerState()
        # Observability-only audit journal (M1); attached via attach_journal.
        self.journal: Any = None
        self._job_lifecycle = BrokerJobLifecycle()
        # Components are wired with direct references (state, clock, and
        # each other) rather than callbacks. Cross-component refs are
        # established in dependency order below so every focused component
        # owns its own logic without the facade acting as a proxy.
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
        self._lock = asyncio.Lock()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _parse_time(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _active_job_count_locked(self, project_id: str | None = None) -> int:
        return sum(
            1 for stored in self._state.active_messages.values()
            if (project_id is None or str(stored.envelope.project_id or "default") == str(project_id)) and is_busy_status(stored.status)
        )

    def attach_journal(self, journal: Any) -> None:
        """Wire the observability audit journal (M1).

        Once attached, every broker event recorded via
        ``MemoryEventLog.append_event_locked`` is mirrored into the journal.
        The journal is observability-only and never the source of truth.
        """
        self.journal = journal
        self._event_log._journal = journal

    def current_job_leases(self) -> dict[str, tuple[int, str]]:
        """Snapshot active task leases for startup checkpoint reconciliation."""
        current: dict[str, tuple[int, str]] = {}
        for job in self._state.task_jobs.values():
            if job.lease is not None:
                current[str(job.id)] = (int(job.lease.epoch), str(job.lease.holder))
        return current

    def append_checkpoint_drifts(self, drifts: list[Any]) -> None:
        """Expose checkpoint drift through /events without coupling routes to memory internals."""
        for drift in drifts:
            preview = (
                f"lease drift {drift.task_id}: {drift.reason} "
                f"previous_epoch={drift.previous_epoch} current_epoch={drift.current_epoch}"
            )
            self._state.events.append(
                BrokerEvent(
                    id=self._state.next_event_id,
                    time=self._now(),
                    type="lease_expired_during_downtime",
                    preview=preview,
                    fields={
                        "task_id": drift.task_id,
                        "message_type": "LEASE",
                        "status": "DRIFTED",
                        "payload": drift.to_dict(),
                    },
                )
            )
            self._state.next_event_id += 1

    def _settle_work_for_offline_agent_locked(self, agent_id: str, project_id: str, reason: str) -> list[str]:
        settled: list[str] = []
        for stored in list(self._state.active_messages.values()):
            if str(stored.envelope.project_id or "default") != str(project_id):
                continue
            if stored.envelope.from_agent != agent_id and stored.envelope.to_agent != agent_id:
                continue
            if not is_busy_status(stored.status):
                continue
            now_str = self._now()
            new_stored = stored.with_status("CANCELLED", now_str)
            self._state.active_messages[new_stored.envelope.message_id] = new_stored
            message = stored_message_to_wire(new_stored)
            context = MessageProjectionContext.from_stored(new_stored, status="CANCELLED", updated_at=now_str)
            task_id = new_stored.envelope.task_id
            if task_id:
                result = TaskResult(
                    status="CANCELLED",
                    project_id=new_stored.envelope.project_id,
                    task_id=str(task_id),
                    error=reason,
                    job=new_stored,
                )
                self._job_projector.upsert_task_locked(context, "CANCELLED")
                self._work_queue.store_task_result_locked(result)
                settled.append(str(task_id))
            if is_talk_message_type(new_stored.envelope.type):
                self._job_projector.touch_conversation_locked(context, status="CANCELLED")
                if new_stored.envelope.conversation_id:
                    settled.append(str(new_stored.envelope.conversation_id))
            future = self._state.pending_replies.get(str(new_stored.envelope.correlation_id or ""))
            if future is not None and not future.done():
                future.set_result(WaitBlocker(
                    status="CANCELLED",
                    correlation_id=str(new_stored.envelope.correlation_id or ""),
                    error="peer_offline",
                    summary=reason,
                    reason=reason,
                ))
            self._event_log.append_event_locked(
                self._event_log.event_context(
                    "work_cancelled",
                    message,
                    "CANCELLED",
                    preview=reason,
                )
            )
        return settled

    def _expire_sessions_locked(self) -> list[dict[str, Any]]:
        return self._session_store.expire_sessions_locked(self._settle_work_for_offline_agent_locked)

    def _expire_timed_out_messages_locked(self) -> None:
        self._expire_sessions_locked()
        now = datetime.now(timezone.utc)
        for stored in list(self._state.active_messages.values()):
            message_id = stored.envelope.message_id
            status = str(stored.status or "").upper()
            if status not in BUSY_MESSAGE_STATUSES:
                continue
            timeout_seconds = int(stored.envelope.timeout_seconds or 0)
            if timeout_seconds <= 0:
                continue
            started_at = self._parse_time(stored.created_at or stored.queued_at)
            if started_at is None:
                continue
            if (now - started_at).total_seconds() < timeout_seconds:
                continue

            now_str = self._now()
            new_stored = stored.with_status("TIMEOUT", now_str)
            self._state.active_messages[message_id] = new_stored
            message = stored_message_to_wire(new_stored)
            context = MessageProjectionContext.from_stored(new_stored, status="TIMEOUT", updated_at=now_str)
            task_id = new_stored.envelope.task_id
            if task_id:
                result = TaskResult(
                    status="TIMEOUT",
                    project_id=str(new_stored.envelope.project_id or "default"),
                    task_id=str(task_id),
                    error="Task exceeded its timeout before the worker replied.",
                    job=new_stored,
                )
                self._job_projector.upsert_task_locked(context, "TIMEOUT")
                self._work_queue.store_task_result_locked(result)
            if is_talk_message_type(new_stored.envelope.type):
                self._job_projector.touch_conversation_locked(context, status="TIMEOUT")
            future = self._state.pending_replies.get(str(new_stored.envelope.correlation_id or ""))
            if future is not None and not future.done():
                future.set_result(WaitBlocker(
                    status="TIMEOUT",
                    correlation_id=str(new_stored.envelope.correlation_id or ""),
                    error="Worker did not reply before the timeout.",
                    summary="Worker did not reply before the timeout.",
                ))
            self._event_log.append_event_locked(
                self._event_log.event_context(
                    "timeout",
                    message,
                    "TIMEOUT",
                    preview="Work exceeded the timeout before the worker replied.",
                )
            )

    def _apply_activity_to_work_locked(self, activity: ActivityRecord, timestamp: str) -> None:
        project_id = str(activity.project_id or "default")
        task_id = str(activity.task_id or "")
        conversation_id = str(activity.conversation_id or "")
        message_id = str(activity.message_id or "")
        preview = str(activity.detail or activity.phase or activity.activity_type or "")[:300]

        if conversation_id:
            conversation_key = self._job_projector.conversation_key(project_id, conversation_id)
            conversation = self._state.conversations.get(conversation_key)
            if conversation is not None:
                touched = conversation.touch(
                    activity_at=timestamp,
                    activity_type=activity.activity_type,
                    activity_tool=activity.tool_name,
                    activity_preview=preview,
                    now=timestamp,
                )
                self._state.conversations[conversation_key] = touched
                talk_job = self._state.talk_jobs.get(conversation_key)
                if talk_job is not None:
                    current_payload = talk_job.payload if isinstance(talk_job.payload, TalkJobPayload) else TalkJobPayload()
                    payload = replace(
                        current_payload,
                        updated_at=timestamp,
                        last_activity_at=timestamp,
                        last_activity_type=activity.activity_type,
                        last_activity_tool=activity.tool_name,
                        last_activity_preview=preview,
                    )
                    talk_job = self._job_lifecycle.talk.with_payload(talk_job, payload, turn=talk_job.turn, max_turns=talk_job.max_turns)
                    self._state.talk_jobs[conversation_key] = talk_job
                    self._state.conversations[conversation_key] = conversation_from_wire(talk_job_to_wire(talk_job))

        target_stored: StoredMessage | None = None
        if message_id:
            target_stored = self._state.active_messages.get(message_id)
        if target_stored is None:
            target_stored = next(
                (
                    stored
                    for stored in self._state.active_messages.values()
                    if str(stored.envelope.project_id or "default") == project_id
                    and (
                        (task_id and str(stored.envelope.task_id or "") == task_id)
                        or (conversation_id and str(stored.envelope.conversation_id or "") == conversation_id)
                    )
                ),
                None,
            )
        if target_stored is not None and str(target_stored.status or "").upper() in BUSY_MESSAGE_STATUSES:
            new_stored = target_stored.with_status("RUNNING", timestamp)
            self._state.active_messages[new_stored.envelope.message_id] = new_stored
            context = MessageProjectionContext.from_stored(
                new_stored,
                status="RUNNING",
                updated_at=timestamp,
                last_activity_at=timestamp,
                last_activity_type=activity.activity_type,
                last_activity_tool=activity.tool_name,
                last_activity_preview=preview,
            )
            if new_stored.envelope.task_id and new_stored.envelope.type == "TASK":
                self._job_projector.upsert_task_locked(context, "RUNNING")

        if task_id:
            task_key = self._job_projector.task_key(project_id, task_id)
            task = self._job_projector.task_projection_locked(task_key)
            if task:
                updates = {
                    "updated_at": timestamp,
                    "last_activity_at": timestamp,
                    "last_activity_type": activity.activity_type,
                    "last_activity_tool": activity.tool_name,
                    "last_activity_preview": preview,
                }
                if str(task.status or "").upper() in BUSY_MESSAGE_STATUSES:
                    updates["status"] = "RUNNING"
                self._job_projector.update_task_projection_locked(task_key, updates)

    def _coerce_to_agent(self, agent: AgentInput) -> Agent:
        return agent_input_to_agent(agent)

    async def register_agent(self, agent: AgentInput) -> dict[str, Any]:
        stored_agent = self._coerce_to_agent(agent)
        async with self._lock:
            return self._work_queue.register_agent_locked(stored_agent)

    def _coerce_to_stored(self, message: MessageInput) -> StoredMessage:
        """Convert an enqueue input into the active-message domain record."""
        return message_input_to_stored(message, now=self._now())

    async def enqueue_message(
        self,
        message: MessageInput,
        create_waiter: bool = False,
    ) -> dict[str, Any]:
        stored = self._coerce_to_stored(message)
        async with self._lock:
            self._expire_timed_out_messages_locked()
            result, inbox, stored_message = self._work_queue.enqueue_message_locked(stored, create_waiter=create_waiter)

        await inbox.put(stored_message)
        return result

    def _invoke_on_delivered(
        self,
        delivered: dict[str, Any],
        on_delivered: Callable[[dict[str, Any]], None] | None,
    ) -> None:
        """Call the optional delivery callback while still under ``self._lock``.

        Callback failures are logged and swallowed: they are a checkpoint
        concern, not a reason to roll back a successful store delivery.
        """
        if on_delivered is None:
            return
        try:
            on_delivered(delivered)
        except Exception as exc:
            logging.getLogger(__name__).warning("on_delivered callback failed: %s", exc)

    async def get_next_message(
        self,
        agent_id: str,
        wait_seconds: int,
        lease_id: str | None = None,
        project_id: str | None = None,
        on_delivered: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any] | None:
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
                delivered = self._work_queue.deliver_message_locked(message)
                if delivered is None:
                    if wait_seconds <= 0 or loop.time() >= deadline:
                        return None
                    continue
                self._invoke_on_delivered(delivered, on_delivered)
            return delivered

    async def save_reply(
        self,
        message_id: str,
        reply: MessageInput,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
        session_lease_id: str | None = None,
    ) -> dict[str, Any]:
        stored_reply = self._coerce_to_stored(reply)
        async with self._lock:
            self._expire_timed_out_messages_locked()
            if session_lease_id:
                self._session_store.assert_active_session_lease_locked(
                    str(stored_reply.envelope.from_agent or ""),
                    project_id=str(stored_reply.envelope.project_id or "default"),
                    lease_id=session_lease_id,
                )
            result, reply_inbox, reply_message = self._work_queue.save_reply_locked(message_id, stored_reply, lease_epoch=lease_epoch, lease_holder=lease_holder)

        if reply_inbox is not None and reply_message is not None:
            await reply_inbox.put(reply_message)
        return result

    async def update_message_status(
        self,
        message_id: str,
        status: str,
        session_lease_id: str | None = None,
    ) -> dict[str, Any]:
        # Use the canonical status normalizer from `core.states` so the
        # storage layer agrees with the domain status vocabulary rather than
        # re-implementing ad-hoc upper-casing.
        from orchlink.core.states import normalize_status

        normalized_status = normalize_status(status)
        if normalized_status not in BUSY_MESSAGE_STATUSES | TERMINAL_MESSAGE_STATUSES:
            raise ValueError(f"Unsupported status: {status}")
        async with self._lock:
            self._expire_timed_out_messages_locked()
            if session_lease_id:
                stored = self._state.active_messages.get(message_id)
                if stored is not None:
                    self._session_store.assert_active_session_lease_locked(
                        str(stored.envelope.to_agent or ""),
                        project_id=str(stored.envelope.project_id or "default"),
                        lease_id=session_lease_id,
                    )
            return self._work_queue.update_message_status_locked(message_id, normalized_status)

    async def record_activity(self, activity: ActivityInput) -> dict[str, Any]:
        command = worker_activity_from_wire(activity)
        async with self._lock:
            self._expire_timed_out_messages_locked()
            session_lease_id = str(command.session_lease_id or "")
            if session_lease_id:
                self._session_store.assert_active_session_lease_locked(
                    str(command.agent_id or ""),
                    project_id=str(command.project_id or "default"),
                    lease_id=session_lease_id,
                )
            return self._activity_store.record_activity_locked(command)

    async def list_activity(
        self,
        item_id: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            return self._activity_store.list_activity_locked(
                item_id=item_id,
                limit=limit,
                project_id=project_id,
            )

    async def acquire_session(self, session: SessionAcquireInput) -> dict[str, Any]:
        command = session_acquire_from_wire(session)
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._session_store.acquire_session_locked(command)

    async def heartbeat_session(
        self,
        lease_id: str,
        project_id: str | None = None,
        heartbeat: SessionHeartbeatInput | None = None,
    ) -> dict[str, Any]:
        command = session_heartbeat_from_wire(lease_id, project_id=project_id, heartbeat=heartbeat)
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._session_store.heartbeat_session_locked(command)

    async def release_session(self, lease_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        command = session_release_from_wire(lease_id, reason=reason, project_id=project_id)
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._session_store.release_session_locked(
                command,
                on_session_ended=self._settle_work_for_offline_agent_locked,
            )

    async def expire_sessions(self) -> list[dict[str, Any]]:
        async with self._lock:
            return self._expire_sessions_locked()

    async def list_sessions(self, project_id: str | None = None, active: bool = False) -> list[dict[str, Any]]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._session_store.list_sessions_locked(project_id=project_id, active=active)

    async def can_auto_stop(self, project_id: str | None = None) -> bool:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._session_store.active_session_count_locked(project_id) == 0 and self._active_job_count_locked(project_id) == 0

    async def cancel_work(self, item_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._work_queue.cancel_work_locked(item_id, reason=reason, project_id=project_id)

    def _find_task_job_locked(self, task_id: str, project_id: str | None = None) -> tuple[str, Job]:
        if project_id is not None:
            task_key = self._job_projector.task_key(project_id, task_id)
            job = self._state.task_jobs.get(task_key)
            if job is not None:
                return task_key, job
        matches = [(key, job) for key, job in self._state.task_jobs.items() if key.endswith(f":{task_id}") or key == task_id]
        if len(matches) == 1:
            return matches[0]
        raise ValueError(f"Job not found: {task_id}")

    def heartbeat_job_locked(
        self,
        task_id: str,
        holder: str,
        epoch: int,
        project_id: str | None = None,
        heartbeat_ms: int | None = None,
    ) -> dict[str, Any]:
        task_key, job = self._find_task_job_locked(task_id, project_id=project_id)
        lease = job.lease
        if lease is None:
            raise LeaseConflictError(f"Job {task_id} has no lease to renew.")
        if not lease.matches(str(holder), int(epoch)):
            raise LeaseConflictError(f"Stale lease heartbeat for {task_id}: holder/epoch mismatch.")
        hb = heartbeat_ms or lease.heartbeat_ms or DEFAULT_JOB_HEARTBEAT_MS
        new_lease = lease.renew(heartbeat_ms=hb, grace_multiplier=JOB_LEASE_GRACE_MULTIPLIER)
        job = job.with_lease(new_lease)
        self._state.task_jobs[task_key] = job
        self._job_projector.store_task_projection_locked(task_key, job)
        self._event_log.append_event_locked(BrokerEventContext.from_fields(
            "job_heartbeat",
            project_id=job.project_id,
            task_id=str(task_id),
            from_agent=holder,
            to_agent=holder,
            message_type="LEASE",
            mode=job.mode,
            status=job.status,
            payload={},
            preview=f"lease renewed epoch={epoch}",
        ))
        return {"status": "renewed", "task_id": task_id, "lease": lease_to_wire(new_lease)}

    def reclaim_job_locked(self, task_id: str, holder: str, project_id: str | None = None) -> dict[str, Any]:
        task_key, job = self._find_task_job_locked(task_id, project_id=project_id)
        lease = job.lease
        if lease is None:
            raise LeaseConflictError(f"Job {task_id} is not reclaimable (no lease).")
        now = datetime.now(timezone.utc)
        if lease.is_active(now):
            # Idempotent: the current holder reclaiming a still-valid lease is a no-op.
            if lease.holder == str(holder):
                return {"status": "active", "task_id": task_id, "lease": lease_to_wire(lease), "reclaimed": False}
            raise LeaseConflictError(f"Job {task_id} lease has not expired.")
        new_lease = lease.reclaim(str(holder), grace_multiplier=JOB_LEASE_GRACE_MULTIPLIER)
        # Transition RUNNING/DELIVERED -> RECLAIMABLE -> RUNNING with the new lease.
        job = job.reclaim_with_lease(new_lease)
        self._state.task_jobs[task_key] = job
        self._job_projector.store_task_projection_locked(task_key, job)
        self._event_log.append_event_locked(BrokerEventContext.from_fields(
            "job_reclaimed",
            project_id=job.project_id,
            task_id=str(task_id),
            from_agent=holder,
            to_agent=holder,
            message_type="LEASE",
            mode=job.mode,
            status=job.status,
            payload={},
            preview=f"lease reclaimed epoch={new_lease.epoch}",
        ))
        return {"status": "reclaimed", "task_id": task_id, "lease": lease_to_wire(new_lease), "reclaimed": True}

    async def heartbeat_job(
        self,
        task_id: str,
        holder: str,
        epoch: int,
        project_id: str | None = None,
        heartbeat_ms: int | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self.heartbeat_job_locked(task_id, holder, epoch, project_id=project_id, heartbeat_ms=heartbeat_ms)

    async def reclaim_job(self, task_id: str, holder: str, project_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self.reclaim_job_locked(task_id, holder, project_id=project_id)

    async def close_conversation(self, conversation_id: str, message: MessageInput) -> dict[str, Any]:
        stored = self._coerce_to_stored(message)
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._work_queue.close_conversation_locked(conversation_id, stored)

    async def wait_for_reply(self, correlation_id: str, timeout_seconds: int) -> dict[str, Any]:
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
                self._work_queue.timeout_reply_locked(correlation_id)
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
            self._expire_timed_out_messages_locked()
            result, task_key, future = self._work_queue.prepare_task_wait_locked(task_id, project_id=project_id)
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

    async def get_task_result(self, task_id: str, project_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._job_projector.get_task_result_locked(task_id, project_id=project_id)

    async def list_jobs(
        self,
        limit: int = 50,
        project_id: str | None = None,
        active: bool = False,
        status: str | None = None,
        kind: str | None = None,
        item_id: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._job_projector.list_jobs_locked(
                limit=limit,
                project_id=project_id,
                active=active,
                status=status,
                kind=kind,
                item_id=item_id,
            )

    async def list_agents(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [agent_to_wire(agent) for agent in self._state.agents.values()]

    async def list_active_messages(self, project_id: str | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._work_queue.list_active_messages_locked(project_id=project_id)

    async def list_conversations(self, project_id: str | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return self._job_projector.list_conversations_locked(project_id=project_id)

    async def list_events(self, since: int = 0, limit: int = 100, project_id: str | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            return self._event_log.list_events_locked(since=since, limit=limit, project_id=project_id)

    async def pending_reply_count(self) -> int:
        async with self._lock:
            return self._work_queue.pending_reply_count_locked()