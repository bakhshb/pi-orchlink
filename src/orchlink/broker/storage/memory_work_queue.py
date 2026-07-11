"""Focused component: message queue, work lifecycle, reply resolution.

Lifted from ``memory.py``; preserves every method, body, and side effect.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

from orchlink.broker.state import (
    BUSY_MESSAGE_STATUSES,
    TERMINAL_MESSAGE_STATUSES,
    WORKER_BOUND_TYPES,
    is_talk_message_type,
    is_terminal_status,
    reply_job_status,
)
from orchlink.broker.storage.base import LeaseConflictError, MessageStoreBusy
from orchlink.broker.storage.memory_event_log import MemoryEventLog
from orchlink.broker.storage.memory_job_projector import MemoryJobProjector
from orchlink.broker.storage.memory_session_store import MemorySessionStore
from orchlink.broker.storage.memory_state import (
    InMemoryBrokerState,
    InboxItem,
    MessageProjectionContext,
    matches_project,
)
from orchlink.core.models import (
    Agent,
    BrokerEventContext,
    ReplyResult,
    StoredMessage,
    TaskResult,
    WaitBlocker,
)
from orchlink.core.views import (
    agent_to_wire,
    conversation_to_wire,
    lease_to_wire,
    reply_message_to_wire,
    stored_message_to_wire,
    task_result_to_wire,
)


class MemoryWorkQueue:
    def __init__(
        self,
        state: InMemoryBrokerState,
        now: "Any",
        event_log: MemoryEventLog,
        session_store: MemorySessionStore,
        job_projector: MemoryJobProjector,
        require_peer_sessions: bool,
    ) -> None:
        self._state = state
        self._now = now
        self._event_log = event_log
        self._session_store = session_store
        self._job_projector = job_projector
        self._require_peer_sessions = require_peer_sessions

    @staticmethod
    def peer_offline_detail(stored: StoredMessage) -> dict[str, Any]:
        envelope = stored.envelope
        return {
            "error": "peer_offline",
            "message": f"Recipient session is offline: {envelope.to_agent}",
            "peer": envelope.to_agent,
            "requested_type": envelope.type,
            "requested_task_id": envelope.task_id,
            "requested_conversation_id": envelope.conversation_id,
        }

    def assert_conversation_can_receive_locked(self, stored: StoredMessage) -> None:
        envelope = stored.envelope
        if envelope.type not in {"CHAT_TURN", "CHAT_REPLY"}:
            return
        project_id = str(envelope.project_id or "default")
        conversation_id = str(envelope.conversation_id or "")
        conversation_record = self._state.conversations.get(self._job_projector.conversation_key(project_id, conversation_id))
        if conversation_record is None:
            raise ValueError(f"Conversation not found: {conversation_id}")
        if conversation_record.status != "OPEN":
            raise ValueError(f"Conversation is not open: {conversation_id}")
        if conversation_record.turn >= conversation_record.max_turns:
            raise ValueError(f"Conversation reached max turns: {conversation_id}")

    def busy_detail(self, blocker: dict[str, Any], stored: StoredMessage) -> dict[str, Any]:
        envelope = stored.envelope
        blocking_id = blocker.get("task_id") or blocker.get("conversation_id") or blocker.get("message_id")
        return {
            "error": "worker_busy",
            "message": "Worker already has pending work. Wait for that reply before sending another task or talk turn.",
            "blocking_id": blocking_id,
            "blocking_kind": blocker.get("kind") or ("conversation" if blocker.get("conversation_id") and not blocker.get("task_id") else "task"),
            "blocking_status": blocker.get("status"),
            "blocking_type": blocker.get("message_type") or blocker.get("type"),
            "requested_type": envelope.type,
            "requested_task_id": envelope.task_id,
            "requested_conversation_id": envelope.conversation_id,
        }

    def assert_worker_target_free_locked(self, stored: StoredMessage) -> None:
        envelope = stored.envelope
        message_type = str(envelope.type or "")
        if message_type not in WORKER_BOUND_TYPES:
            return

        to_agent = envelope.to_agent
        project_id = str(envelope.project_id or "default")
        if self._require_peer_sessions and to_agent and self._session_store.active_session_locked(str(to_agent), project_id) is None:
            raise MessageStoreBusy(self.peer_offline_detail(stored))
        for active_stored in self._state.active_messages.values():
            if str(active_stored.envelope.project_id or "default") != project_id:
                continue
            if active_stored.envelope.to_agent != to_agent:
                continue
            if str(active_stored.status or "").upper() not in BUSY_MESSAGE_STATUSES:
                continue
            raise MessageStoreBusy(self.busy_detail(stored_message_to_wire(active_stored), stored))

        conversation_id = str(envelope.conversation_id or "")
        if message_type in {"CHAT_TURN", "CHAT_CLOSE"}:
            return
        for conversation_record in self._state.conversations.values():
            if str(conversation_record.project_id or "default") != project_id:
                continue
            if conversation_record.status != "OPEN":
                continue
            if to_agent not in conversation_record.participants:
                continue
            if conversation_record.conversation_id == conversation_id:
                continue
            raise MessageStoreBusy(self.busy_detail(conversation_to_wire(conversation_record), stored))

    def resolve_task_waiters_locked(self, task_key: str, result: TaskResult) -> None:
        waiters = self._state.task_waiters.pop(task_key, [])
        for future in waiters:
            if not future.done():
                future.set_result(result)

    def store_task_result_locked(self, result: TaskResult) -> dict[str, Any]:
        result_wire = task_result_to_wire(result)
        task_key = self._job_projector.task_key(result.project_id, result.task_id)
        self._state.results_by_task[task_key] = result
        self.resolve_task_waiters_locked(task_key, result)
        self.resolve_task_waiters_locked(result.task_id, result)
        return result_wire

    def register_agent_locked(self, agent: Agent) -> dict[str, Any]:
        agent_id = agent.agent_id
        self._state.agents[agent_id] = agent
        self._state.inboxes.setdefault(agent_id, asyncio.Queue())
        self._event_log.append_event_locked(BrokerEventContext.from_fields(
            "agent_registered",
            project_id=agent.project_id,
            agent_id=agent_id,
            role=agent.role,
            preview=f"registered {agent_id}",
        ))
        return agent_to_wire(agent)

    def enqueue_message_locked(self, stored: StoredMessage, create_waiter: bool = False) -> tuple[dict[str, Any], asyncio.Queue[InboxItem], StoredMessage]:
        """Store a validated `StoredMessage` as the next active message.

        The work queue accepts a `StoredMessage` (validated envelope + broker
        lifecycle metadata) at the storage boundary. Wire-dict consumers use
        the centralized `stored_message_to_wire` serializer; the in-memory
        record stays the canonical domain object.
        """
        envelope = stored.envelope
        message_id = envelope.message_id
        to_agent = envelope.to_agent
        correlation_id = envelope.correlation_id
        self.assert_conversation_can_receive_locked(stored)
        self.assert_worker_target_free_locked(stored)
        # Active messages are stored as `StoredMessage` (validated envelope + broker metadata).
        self._state.active_messages[message_id] = stored
        message_context = MessageProjectionContext.from_stored(stored)
        if envelope.type == "CHAT_CLOSE":
            self._job_projector.touch_conversation_locked(message_context, "CLOSED")
        elif is_talk_message_type(envelope.type):
            self._job_projector.touch_conversation_locked(message_context, None)
        else:
            self._job_projector.upsert_task_locked(message_context, "QUEUED")
        # Wire dict view for downstream event/API boundaries only.
        message = stored_message_to_wire(stored)
        inbox = self._state.inboxes.setdefault(to_agent, asyncio.Queue())
        if create_waiter and envelope.requires_reply:
            self._state.pending_replies.setdefault(
                correlation_id,
                asyncio.get_running_loop().create_future(),
            )
        self._event_log.append_event_locked(
            self._event_log.event_context("message_queued", message, message["status"])
        )
        return {"status": "queued", "message_id": message_id}, inbox, stored

    def inbox_for_agent_locked(self, agent_id: str) -> asyncio.Queue[InboxItem]:
        return self._state.inboxes.setdefault(agent_id, asyncio.Queue())

    def deliver_message_locked(self, item: InboxItem) -> dict[str, Any] | None:
        message_id = item.envelope.message_id
        active_stored = self._state.active_messages.get(message_id)
        if active_stored and is_terminal_status(active_stored.status):
            return None

        now_str = self._now()
        if active_stored is not None:
            next_status = "CLOSED" if str(active_stored.status or "") == "CLOSED" else "DELIVERED"
            active_stored = active_stored.with_status(next_status, now_str)
            self._state.active_messages[message_id] = active_stored
            delivered = stored_message_to_wire(active_stored)
            delivered_context = MessageProjectionContext.from_stored(active_stored, status=next_status, updated_at=now_str)
            if active_stored.envelope.task_id and active_stored.envelope.type == "TASK":
                job = self._job_projector.upsert_task_locked(delivered_context, next_status)
                if job is not None:
                    delivered["lease"] = lease_to_wire(job.lease)
            self._job_projector.touch_conversation_locked(delivered_context, None)
        else:
            delivered = reply_message_to_wire(item)
            if delivered.get("status") != "CLOSED":
                delivered["status"] = "DELIVERED"
            delivered["updated_at"] = now_str
        self._event_log.append_event_locked(
            self._event_log.event_context("message_delivered", delivered, delivered["status"])
        )
        return delivered

    def save_reply_locked(self, message_id: str, reply: StoredMessage, lease_epoch: int | None = None, lease_holder: str | None = None) -> tuple[dict[str, Any], asyncio.Queue[InboxItem] | None, StoredMessage | None]:
        envelope = reply.envelope
        correlation_id = envelope.correlation_id
        reply_wire = reply_message_to_wire(reply)
        job_status = reply_job_status(envelope.type, envelope.status)
        task_id = envelope.task_id
        project_id = str(envelope.project_id or "default")
        # Reject stale-holder replies when the caller asserts a lease epoch.
        # If no epoch is provided, session-lease fencing is the authority.
        if task_id and lease_epoch is not None:
            task_key = self._job_projector.task_key(project_id, str(task_id))
            job = self._state.task_jobs.get(task_key)
            lease = job.lease if job is not None else None
            if lease is not None:
                if not lease.matches(str(lease_holder or ""), int(lease_epoch)):
                    raise LeaseConflictError(
                        f"Stale lease reply for {task_id}: holder/epoch mismatch (lease epoch={lease.epoch}, reply epoch={lease_epoch})."
                    )
        active = self._state.active_messages.get(message_id)
        if active is not None and str(active.status or "").upper() in {"TIMEOUT", "CANCELLED"}:
            self._event_log.append_event_locked(
                self._event_log.event_context(
                    "late_reply_ignored",
                    reply_wire,
                    str(active.status or ""),
                    preview="Late worker reply ignored because the original work is no longer active.",
                )
            )
            return {"status": "reply_ignored", "correlation_id": correlation_id}, None, None

        future = self._state.pending_replies.get(correlation_id)
        if active is not None:
            self._state.active_messages[message_id] = active.with_status(job_status, self._now())
        reply_context = MessageProjectionContext.from_stored(reply, status=job_status)
        if task_id:
            result = TaskResult(status=job_status, project_id=project_id, task_id=str(task_id), reply=reply)
            self._job_projector.upsert_task_locked(reply_context, job_status)
            self.store_task_result_locked(result)
        if is_talk_message_type(envelope.type):
            self._job_projector.touch_conversation_locked(reply_context, "OPEN" if job_status == "DONE" else job_status)
        # Blocking delivery is consumed through wait/task-result APIs by the
        # foreground caller. Do not also enqueue it into the lead agent inbox,
        # which would create a second unsolicited chat notification after the
        # native Pi tool has already returned the same authoritative result.
        reply_inbox = None
        if str(envelope.delivery or "async").lower() != "blocking":
            reply_inbox = self._state.inboxes.setdefault(str(envelope.to_agent), asyncio.Queue())
        self._event_log.append_event_locked(
            self._event_log.event_context("reply_received", reply_wire, job_status)
        )
        if future is not None and not future.done():
            future.set_result(ReplyResult(correlation_id=str(correlation_id), reply=reply))
        return {
            "status": "reply_received",
            "correlation_id": correlation_id,
        }, reply_inbox, reply if reply_inbox is not None else None

    def update_message_status_locked(self, message_id: str, normalized_status: str) -> dict[str, Any]:
        stored = self._state.active_messages.get(message_id)
        if stored is None:
            raise ValueError(f"Message not found: {message_id}")
        if is_terminal_status(stored.status):
            return {"status": str(stored.status), "message_id": message_id}
        now_str = self._now()
        updated_stored = stored.with_status(normalized_status, now_str)
        self._state.active_messages[message_id] = updated_stored
        context = MessageProjectionContext.from_stored(updated_stored, status=normalized_status, updated_at=now_str)
        message = stored_message_to_wire(updated_stored)
        if updated_stored.envelope.task_id and updated_stored.envelope.type == "TASK":
            self._job_projector.upsert_task_locked(context, normalized_status)
        if is_talk_message_type(updated_stored.envelope.type):
            self._job_projector.touch_conversation_locked(context, normalized_status)
        self._event_log.append_event_locked(
            self._event_log.event_context("message_status", message, normalized_status)
        )
        return {"status": normalized_status, "message_id": message_id}

    def inactive_work_message_locked(self, item_id: str, project_id: str | None = None) -> str:
        for result in self._state.results_by_task.values():
            if project_id is not None and str(result.project_id or "default") != str(project_id):
                continue
            if result.task_id == item_id:
                return f"No active work found: {item_id} (already {result.status or 'DONE'})."
        for task in self._state.tasks.values():
            if project_id is not None and str(task.project_id or "default") != str(project_id):
                continue
            if task.task_id == item_id:
                status = str(task.status or "UNKNOWN")
                if status.upper() in TERMINAL_MESSAGE_STATUSES:
                    return f"No active work found: {item_id} (already {status})."
        for conversation_record in self._state.conversations.values():
            if project_id is not None and str(conversation_record.project_id or "default") != str(project_id):
                continue
            if conversation_record.conversation_id == item_id:
                status = str(conversation_record.status or "UNKNOWN")
                if status.upper() != "OPEN":
                    return f"No active work found: {item_id} (already {status})."
        return f"No active work found: {item_id}."

    def cancel_work_locked(self, item_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        cancelled: list[str] = []
        targets: list[StoredMessage] = [
            stored
            for stored in self._state.active_messages.values()
            if (project_id is None or str(stored.envelope.project_id or "default") == str(project_id))
            and (
                str(stored.envelope.message_id) == item_id
                or str(stored.envelope.task_id or "") == item_id
                or str(stored.envelope.conversation_id or "") == item_id
            )
            and not is_terminal_status(stored.status)
        ]
        if not targets:
            if project_id is not None:
                conversation = self._state.conversations.get(self._job_projector.conversation_key(project_id, item_id))
            else:
                matches = [record for record in self._state.conversations.values() if record.conversation_id == item_id]
                conversation = matches[0] if len(matches) == 1 else None
            if conversation is not None and conversation.status == "OPEN":
                self._job_projector.touch_conversation_locked(
                    MessageProjectionContext(
                        project_id=conversation.project_id,
                        conversation_id=item_id,
                        task_id=None,
                        message_id="",
                        correlation_id="",
                        from_agent=str(conversation.from_agent or ""),
                        to_agent=str(conversation.to_agent or ""),
                        message_type="CHAT_TURN",
                        status="CANCELLED",
                        turn=conversation.turn,
                        max_turns=conversation.max_turns,
                        delivery="conversation",
                        payload={"summary": reason or "Conversation cancelled."},
                    ),
                    "CANCELLED",
                )
                self._event_log.append_event_locked(BrokerEventContext.from_fields(
                    "work_cancelled",
                    project_id=conversation.project_id,
                    conversation_id=item_id,
                    mode="TALK",
                    status="CANCELLED",
                    preview=reason or "Conversation cancelled.",
                ))
                return {"status": "cancelled", "item_id": item_id, "cancelled": [item_id]}
            raise ValueError(self.inactive_work_message_locked(item_id, project_id=project_id))

        for stored in targets:
            now_str = self._now()
            new_stored = stored.with_status("CANCELLED", now_str)
            self._state.active_messages[new_stored.envelope.message_id] = new_stored
            message = stored_message_to_wire(new_stored)
            context = MessageProjectionContext.from_stored(new_stored, status="CANCELLED", updated_at=now_str)
            message_id = new_stored.envelope.message_id
            if message_id:
                cancelled.append(message_id)
            task_id = new_stored.envelope.task_id
            if task_id:
                result = TaskResult(
                    status="CANCELLED",
                    project_id=new_stored.envelope.project_id,
                    task_id=str(task_id),
                    error=reason or "Work was cancelled.",
                    job=new_stored,
                )
                self._job_projector.upsert_task_locked(context, "CANCELLED")
                self.store_task_result_locked(result)
            if is_talk_message_type(new_stored.envelope.type):
                self._job_projector.touch_conversation_locked(context, "CANCELLED")
            future = self._state.pending_replies.get(str(new_stored.envelope.correlation_id or ""))
            if future is not None and not future.done():
                future.set_result(WaitBlocker(
                    status="CANCELLED",
                    correlation_id=str(new_stored.envelope.correlation_id or ""),
                    error=reason or "Work was cancelled.",
                    summary=reason or "Work was cancelled.",
                ))
            self._event_log.append_event_locked(
                self._event_log.event_context(
                    "work_cancelled",
                    message,
                    "CANCELLED",
                    preview=reason or "Work was cancelled.",
                )
            )
        return {"status": "cancelled", "item_id": item_id, "cancelled": cancelled}

    def close_conversation_locked(self, conversation_id: str, stored: StoredMessage) -> dict[str, Any]:
        envelope = stored.envelope
        project_id = str(envelope.project_id or "default")
        conversation_record = self._state.conversations.get(self._job_projector.conversation_key(project_id, conversation_id))
        if conversation_record is None:
            matches = [record for record in self._state.conversations.values() if record.conversation_id == conversation_id]
            conversation_record = matches[0] if len(matches) == 1 else None
        if conversation_record is None:
            raise ValueError(f"Conversation not found: {conversation_id}")
        turn = min(max(int(envelope.turn or 1), int(conversation_record.turn or 1) + 1), int(conversation_record.max_turns or envelope.max_turns or 6))
        close_context = MessageProjectionContext.from_stored(stored, status="CLOSED")
        close_context = replace(
            close_context,
            project_id=conversation_record.project_id,
            conversation_id=conversation_id,
            from_agent=close_context.from_agent or str(conversation_record.from_agent or ""),
            to_agent=close_context.to_agent or str(conversation_record.to_agent or ""),
            message_type="CHAT_CLOSE",
            status="CLOSED",
            turn=turn,
            max_turns=conversation_record.max_turns,
            delivery="conversation",
        )
        self._job_projector.touch_conversation_locked(close_context, "CLOSED")
        self._event_log.append_event_locked(BrokerEventContext.from_fields(
            "conversation_closed",
            project_id=conversation_record.project_id,
            conversation_id=conversation_id,
            from_agent=close_context.from_agent,
            to_agent=close_context.to_agent,
            message_type="CHAT_CLOSE",
            mode="TALK",
            delivery="conversation",
            status="CLOSED",
            payload=close_context.payload,
        ))
        return {"status": "closed", "conversation_id": conversation_id}

    def reply_future_locked(self, correlation_id: str) -> asyncio.Future[ReplyResult | WaitBlocker]:
        return self._state.pending_replies.setdefault(
            correlation_id,
            asyncio.get_running_loop().create_future(),
        )

    def timeout_reply_locked(self, correlation_id: str) -> None:
        stored_match = next(
            (stored for stored in self._state.active_messages.values() if stored.envelope.correlation_id == correlation_id),
            None,
        )
        if stored_match is not None:
            now_str = self._now()
            new_stored = stored_match.with_status("TIMEOUT", now_str)
            self._state.active_messages[new_stored.envelope.message_id] = new_stored
            message = stored_message_to_wire(new_stored)
            context = MessageProjectionContext.from_stored(new_stored, status="TIMEOUT", updated_at=now_str)
            if new_stored.envelope.task_id:
                self._job_projector.upsert_task_locked(context, "TIMEOUT")
            if is_talk_message_type(new_stored.envelope.type):
                self._job_projector.touch_conversation_locked(context, "TIMEOUT")
        else:
            message: dict[str, Any] = {}
        self._event_log.append_event_locked(
            self._event_log.event_context(
                "timeout",
                message,
                "TIMEOUT",
                preview="Worker did not reply before timeout.",
            )
        )

    def cleanup_reply_waiter_locked(self, correlation_id: str, future: asyncio.Future[ReplyResult | WaitBlocker]) -> None:
        if self._state.pending_replies.get(correlation_id) is future:
            self._state.pending_replies.pop(correlation_id, None)

    def prepare_task_wait_locked(self, task_id: str, project_id: str | None = None) -> tuple[TaskResult | WaitBlocker | None, str, asyncio.Future[TaskResult] | None]:
        task_key = self._job_projector.task_key(project_id, task_id) if project_id is not None else task_id
        if project_id is not None and task_key in self._state.results_by_task:
            return self._state.results_by_task[task_key], task_key, None
        if project_id is not None and not self._job_projector.has_task_projection_locked(task_key):
            return WaitBlocker(status="missing", project_id=project_id, task_id=task_id, error="Task not found."), task_key, None
        if project_id is None:
            matches = [result for key, result in self._state.results_by_task.items() if key.endswith(f":{task_id}") or key == task_id]
            if len(matches) == 1:
                return matches[0], task_key, None
        future: asyncio.Future[TaskResult] = asyncio.get_running_loop().create_future()
        self._state.task_waiters.setdefault(task_key, []).append(future)
        return None, task_key, future

    def cleanup_task_waiter_locked(self, task_key: str, future: asyncio.Future[TaskResult]) -> None:
        waiters = self._state.task_waiters.get(task_key, [])
        self._state.task_waiters[task_key] = [item for item in waiters if item is not future]
        if not self._state.task_waiters[task_key]:
            self._state.task_waiters.pop(task_key, None)

    def list_active_messages_locked(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return [dict(stored_message_to_wire(stored)) for stored in self._state.active_messages.values() if matches_project(stored_message_to_wire(stored), project_id)]

    def pending_reply_count_locked(self) -> int:
        return len(self._state.pending_replies)


__all__ = ["MemoryWorkQueue"]