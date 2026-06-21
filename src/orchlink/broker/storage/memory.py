import asyncio
from datetime import datetime, timezone
from typing import Any

from orchlink.broker.storage.base import MessageStore


FAILED_STATUSES = {"FAILED", "TIMEOUT", "CANCELLED"}


class MemoryMessageStore(MessageStore):
    def __init__(self) -> None:
        self._agents: dict[str, dict[str, Any]] = {}
        self._inboxes: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._active_messages: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, dict[str, Any]] = {}
        self._results_by_task: dict[str, dict[str, Any]] = {}
        self._conversations: dict[str, dict[str, Any]] = {}
        self._pending_replies: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._task_waiters: dict[str, list[asyncio.Future[dict[str, Any]]]] = {}
        self._events: list[dict[str, Any]] = []
        self._next_event_id = 1
        self._lock = asyncio.Lock()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _payload_preview(self, payload: dict[str, Any]) -> str:
        for key in ("message", "intent", "topic", "summary", "stdout"):
            value = payload.get(key)
            if value:
                return str(value)
        return ""

    def _message_preview(self, message: dict[str, Any]) -> str:
        return self._payload_preview(message.get("payload") or {})

    def _append_event_locked(self, event_type: str, **fields: Any) -> None:
        payload = fields.get("payload") or {}
        preview = fields.pop("preview", None)
        if preview is None and isinstance(payload, dict):
            preview = self._payload_preview(payload)
        event = {
            "id": self._next_event_id,
            "time": self._now(),
            "type": event_type,
            "preview": str(preview or "")[:300],
            **fields,
        }
        self._next_event_id += 1
        self._events.append(event)
        if len(self._events) > 1000:
            self._events = self._events[-1000:]

    def _job_mode(self, message: dict[str, Any]) -> str:
        payload = message.get("payload") or {}
        mode = payload.get("mode")
        if mode:
            return str(mode)
        message_type = str(message.get("type") or "")
        if message_type.startswith("CHAT_"):
            return "TALK"
        return "PLAN"

    def _event_fields(self, message: dict[str, Any], status: str | None = None) -> dict[str, Any]:
        payload = message.get("payload") or {}
        return {
            "project_id": message.get("project_id"),
            "task_id": message.get("task_id"),
            "conversation_id": message.get("conversation_id"),
            "message_id": message.get("message_id"),
            "correlation_id": message.get("correlation_id"),
            "from_agent": message.get("from_agent"),
            "to_agent": message.get("to_agent"),
            "message_type": message.get("type"),
            "mode": self._job_mode(message),
            "delivery": message.get("delivery"),
            "status": status or message.get("status"),
            "payload": payload,
        }

    def _upsert_task_locked(self, message: dict[str, Any], status: str) -> None:
        task_id = message.get("task_id")
        if not task_id:
            return
        now = self._now()
        existing = self._tasks.get(str(task_id), {})
        is_reply = bool(existing) and message.get("type") != "TASK"
        self._tasks[str(task_id)] = {
            "kind": "task",
            "task_id": str(task_id),
            "conversation_id": message.get("conversation_id") or existing.get("conversation_id"),
            "mode": existing.get("mode") if is_reply else self._job_mode(message),
            "delivery": existing.get("delivery") if is_reply else message.get("delivery", "async"),
            "status": status,
            "from_agent": existing.get("from_agent") if is_reply else message.get("from_agent"),
            "to_agent": existing.get("to_agent") if is_reply else message.get("to_agent"),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
            "preview": self._message_preview(message)[:300],
            "message_id": existing.get("message_id") if is_reply else message.get("message_id"),
            "correlation_id": message.get("correlation_id") or existing.get("correlation_id"),
            "message_type": message.get("type"),
        }

    def _touch_conversation_locked(self, message: dict[str, Any], status: str | None = None) -> None:
        conversation_id = message.get("conversation_id")
        if not conversation_id:
            return
        message_type = str(message.get("type") or "")
        if not message_type.startswith("CHAT_"):
            return
        now = self._now()
        existing = self._conversations.get(str(conversation_id), {})
        next_status = status or existing.get("status") or "OPEN"
        if message_type == "CHAT_CLOSE":
            next_status = "CLOSED"
        elif next_status not in {"CLOSED", "TIMEOUT", "FAILED"}:
            next_status = "OPEN"
        turn = int(message.get("turn") or existing.get("turn") or 1)
        max_turns = int(message.get("max_turns") or existing.get("max_turns") or 6)
        if turn >= max_turns and message_type == "CHAT_REPLY":
            next_status = "CLOSED"
        participants = existing.get("participants") or []
        for agent in (message.get("from_agent"), message.get("to_agent")):
            if agent and agent not in participants:
                participants.append(agent)
        self._conversations[str(conversation_id)] = {
            "kind": "conversation",
            "conversation_id": str(conversation_id),
            "project_id": message.get("project_id") or existing.get("project_id"),
            "participants": participants,
            "mode": "TALK",
            "status": next_status,
            "turn": turn,
            "max_turns": max_turns,
            "from_agent": message.get("from_agent") or existing.get("from_agent"),
            "to_agent": message.get("to_agent") or existing.get("to_agent"),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
            "last_message_preview": self._message_preview(message)[:300],
            "preview": self._message_preview(message)[:300],
            "message_type": message_type,
        }

    def _assert_conversation_can_receive_locked(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type") or "")
        if message_type not in {"CHAT_TURN", "CHAT_REPLY"}:
            return
        conversation_id = str(message.get("conversation_id") or "")
        conversation = self._conversations.get(conversation_id)
        if not conversation:
            raise ValueError(f"Conversation not found: {conversation_id}")
        if conversation.get("status") != "OPEN":
            raise ValueError(f"Conversation is not open: {conversation_id}")
        current_turn = int(conversation.get("turn") or 1)
        max_turns = int(conversation.get("max_turns") or 6)
        if current_turn >= max_turns:
            raise ValueError(f"Conversation reached max turns: {conversation_id}")

    def _resolve_task_waiters_locked(self, task_id: str, result: dict[str, Any]) -> None:
        waiters = self._task_waiters.pop(task_id, [])
        for future in waiters:
            if not future.done():
                future.set_result(result)

    async def register_agent(self, agent: dict[str, Any]) -> dict[str, Any]:
        agent_id = agent["agent_id"]
        async with self._lock:
            stored_agent = dict(agent)
            self._agents[agent_id] = stored_agent
            self._inboxes.setdefault(agent_id, asyncio.Queue())
            self._append_event_locked(
                "agent_registered",
                project_id=stored_agent.get("project_id"),
                agent_id=agent_id,
                role=stored_agent.get("role"),
                preview=f"registered {agent_id}",
            )
            return dict(stored_agent)

    async def enqueue_message(
        self,
        message: dict[str, Any],
        create_waiter: bool = False,
    ) -> dict[str, Any]:
        message_id = message["message_id"]
        to_agent = message["to_agent"]
        correlation_id = message["correlation_id"]

        async with self._lock:
            self._assert_conversation_can_receive_locked(message)
            stored_message = dict(message)
            stored_message["status"] = "CLOSED" if message.get("type") == "CHAT_CLOSE" else "QUEUED"
            self._active_messages[message_id] = stored_message
            if stored_message.get("type") == "CHAT_CLOSE":
                self._touch_conversation_locked(stored_message, status="CLOSED")
            elif str(stored_message.get("type") or "").startswith("CHAT_"):
                self._touch_conversation_locked(stored_message)
            else:
                self._upsert_task_locked(stored_message, "QUEUED")
            inbox = self._inboxes.setdefault(to_agent, asyncio.Queue())
            if create_waiter and message.get("requires_reply", False):
                self._pending_replies.setdefault(
                    correlation_id,
                    asyncio.get_running_loop().create_future(),
                )
            self._append_event_locked(
                "message_queued",
                **self._event_fields(stored_message, status=stored_message["status"]),
            )

        await inbox.put(stored_message)
        return {"status": "queued", "message_id": message_id}

    async def get_next_message(self, agent_id: str, wait_seconds: int) -> dict[str, Any] | None:
        async with self._lock:
            inbox = self._inboxes.setdefault(agent_id, asyncio.Queue())

        try:
            message = await asyncio.wait_for(inbox.get(), timeout=wait_seconds)
        except asyncio.TimeoutError:
            return None

        async with self._lock:
            message_id = str(message.get("message_id"))
            delivered = dict(message)
            if delivered.get("status") != "CLOSED":
                delivered["status"] = "DELIVERED"
            if message_id in self._active_messages:
                self._active_messages[message_id]["status"] = delivered["status"]
            if delivered.get("task_id"):
                self._upsert_task_locked(delivered, delivered["status"])
            self._touch_conversation_locked(delivered)
            self._append_event_locked(
                "message_delivered",
                **self._event_fields(delivered, status=delivered["status"]),
            )
        return delivered

    async def save_reply(self, message_id: str, reply: dict[str, Any]) -> dict[str, Any]:
        correlation_id = reply["correlation_id"]
        stored_reply = dict(reply)
        reply_status = str(stored_reply.get("status") or "DONE")
        job_status = "FAILED" if reply_status in FAILED_STATUSES else "DONE"
        if stored_reply.get("type") == "CHAT_CLOSE":
            job_status = "CLOSED"
        task_id = stored_reply.get("task_id")

        async with self._lock:
            future = self._pending_replies.get(correlation_id)
            if message_id in self._active_messages:
                self._active_messages[message_id]["status"] = job_status
            if task_id:
                result = {"status": job_status, "task_id": str(task_id), "reply": stored_reply}
                self._results_by_task[str(task_id)] = result
                self._upsert_task_locked(stored_reply, job_status)
                self._resolve_task_waiters_locked(str(task_id), result)
            if str(stored_reply.get("type") or "").startswith("CHAT_"):
                self._touch_conversation_locked(stored_reply, status="OPEN" if job_status == "DONE" else job_status)
            reply_inbox = self._inboxes.setdefault(str(stored_reply["to_agent"]), asyncio.Queue())
            self._append_event_locked(
                "reply_received",
                **self._event_fields(stored_reply, status=job_status),
            )
            if future is not None and not future.done():
                future.set_result(stored_reply)

        await reply_inbox.put(stored_reply)
        return {"status": "reply_received", "correlation_id": correlation_id}

    async def close_conversation(self, conversation_id: str, message: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                raise ValueError(f"Conversation not found: {conversation_id}")
            conversation["status"] = "CLOSED"
            conversation["updated_at"] = self._now()
            if message:
                conversation["last_message_preview"] = self._message_preview(message)[:300]
                conversation["preview"] = conversation["last_message_preview"]
            self._append_event_locked(
                "conversation_closed",
                project_id=conversation.get("project_id"),
                conversation_id=conversation_id,
                from_agent=message.get("from_agent") if message else None,
                to_agent=message.get("to_agent") if message else None,
                message_type="CHAT_CLOSE",
                mode="TALK",
                delivery="conversation",
                status="CLOSED",
                payload=message.get("payload") if message else {},
            )
            return {"status": "closed", "conversation_id": conversation_id}

    async def wait_for_reply(self, correlation_id: str, timeout_seconds: int) -> dict[str, Any]:
        async with self._lock:
            future = self._pending_replies.setdefault(
                correlation_id,
                asyncio.get_running_loop().create_future(),
            )

        try:
            reply = await asyncio.wait_for(future, timeout=timeout_seconds)
            return {"status": "completed", "correlation_id": correlation_id, "reply": reply}
        except asyncio.TimeoutError:
            async with self._lock:
                message = next(
                    (item for item in self._active_messages.values() if item.get("correlation_id") == correlation_id),
                    {},
                )
                if message:
                    message["status"] = "TIMEOUT"
                    if message.get("task_id"):
                        self._upsert_task_locked(message, "TIMEOUT")
                    if str(message.get("type") or "").startswith("CHAT_"):
                        self._touch_conversation_locked(message, status="TIMEOUT")
                self._append_event_locked(
                    "timeout",
                    **self._event_fields(message, status="TIMEOUT"),
                    preview="Worker did not reply before timeout.",
                )
            return {
                "status": "timeout",
                "correlation_id": correlation_id,
                "error": "Worker did not reply before timeout.",
            }
        finally:
            if future.done() or future.cancelled():
                async with self._lock:
                    if self._pending_replies.get(correlation_id) is future:
                        self._pending_replies.pop(correlation_id, None)

    async def wait_for_task(self, task_id: str, timeout_seconds: int) -> dict[str, Any]:
        async with self._lock:
            if task_id in self._results_by_task:
                return dict(self._results_by_task[task_id])
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            self._task_waiters.setdefault(task_id, []).append(future)

        try:
            return await asyncio.wait_for(future, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            async with self._lock:
                waiters = self._task_waiters.get(task_id, [])
                self._task_waiters[task_id] = [item for item in waiters if item is not future]
                if not self._task_waiters[task_id]:
                    self._task_waiters.pop(task_id, None)
                if task_id in self._tasks:
                    self._tasks[task_id]["status"] = "TIMEOUT"
                    self._tasks[task_id]["updated_at"] = self._now()
            return {"status": "TIMEOUT", "task_id": task_id, "error": "Task did not finish before timeout."}

    async def get_task_result(self, task_id: str) -> dict[str, Any]:
        async with self._lock:
            if task_id in self._results_by_task:
                return dict(self._results_by_task[task_id])
            if task_id in self._tasks:
                return {"status": self._tasks[task_id].get("status", "QUEUED"), "task_id": task_id, "job": dict(self._tasks[task_id])}
            return {"status": "missing", "task_id": task_id, "error": "Task not found."}

    async def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._lock:
            jobs = [dict(task) for task in self._tasks.values()]
            jobs.extend(dict(conversation) for conversation in self._conversations.values())
            jobs.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
            return jobs[:limit]

    async def list_agents(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [dict(agent) for agent in self._agents.values()]

    async def list_active_messages(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [dict(message) for message in self._active_messages.values()]

    async def list_conversations(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [dict(conversation) for conversation in self._conversations.values()]

    async def list_events(self, since: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        async with self._lock:
            selected = [dict(event) for event in self._events if int(event["id"]) > since]
            return selected[-limit:]

    async def pending_reply_count(self) -> int:
        async with self._lock:
            return len(self._pending_replies)
