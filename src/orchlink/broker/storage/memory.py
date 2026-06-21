import asyncio
from datetime import datetime, timezone
from typing import Any

from orchlink.broker.storage.base import MessageStore


class MemoryMessageStore(MessageStore):
    def __init__(self) -> None:
        self._agents: dict[str, dict[str, Any]] = {}
        self._inboxes: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._active_messages: dict[str, dict[str, Any]] = {}
        self._pending_replies: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._events: list[dict[str, Any]] = []
        self._next_event_id = 1
        self._lock = asyncio.Lock()

    def _append_event_locked(self, event_type: str, **fields: Any) -> None:
        payload = fields.get("payload") or {}
        preview = fields.pop("preview", None)
        if preview is None and isinstance(payload, dict):
            preview = payload.get("intent") or payload.get("summary") or ""
        event = {
            "id": self._next_event_id,
            "time": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "preview": str(preview or "")[:300],
            **fields,
        }
        self._next_event_id += 1
        self._events.append(event)
        if len(self._events) > 1000:
            self._events = self._events[-1000:]

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
            stored_message = dict(message)
            self._active_messages[message_id] = stored_message
            inbox = self._inboxes.setdefault(to_agent, asyncio.Queue())
            if create_waiter and message.get("requires_reply", False):
                self._pending_replies.setdefault(
                    correlation_id,
                    asyncio.get_running_loop().create_future(),
                )
            self._append_event_locked(
                "message_queued",
                project_id=message.get("project_id"),
                task_id=message.get("task_id"),
                message_id=message_id,
                correlation_id=correlation_id,
                from_agent=message.get("from_agent"),
                to_agent=to_agent,
                message_type=message.get("type"),
                status=message.get("status"),
                payload=message.get("payload") or {},
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
            message_id = message.get("message_id")
            if message_id in self._active_messages:
                self._active_messages[str(message_id)]["status"] = "IN_PROGRESS"
            self._append_event_locked(
                "message_delivered",
                project_id=message.get("project_id"),
                task_id=message.get("task_id"),
                message_id=message.get("message_id"),
                correlation_id=message.get("correlation_id"),
                from_agent=message.get("from_agent"),
                to_agent=message.get("to_agent"),
                message_type=message.get("type"),
                status="IN_PROGRESS",
                payload=message.get("payload") or {},
            )
        return message

    async def save_reply(self, message_id: str, reply: dict[str, Any]) -> dict[str, Any]:
        correlation_id = reply["correlation_id"]
        stored_reply = dict(reply)
        async with self._lock:
            future = self._pending_replies.get(correlation_id)
            if message_id in self._active_messages:
                self._active_messages[message_id]["status"] = reply.get("status", "COMPLETED")
            reply_inbox = self._inboxes.setdefault(str(reply["to_agent"]), asyncio.Queue())
            self._append_event_locked(
                "reply_received",
                project_id=reply.get("project_id"),
                task_id=reply.get("task_id"),
                message_id=reply.get("message_id"),
                correlation_id=correlation_id,
                from_agent=reply.get("from_agent"),
                to_agent=reply.get("to_agent"),
                message_type=reply.get("type"),
                status=reply.get("status"),
                payload=reply.get("payload") or {},
            )
            if future is not None and not future.done():
                future.set_result(stored_reply)

        await reply_inbox.put(stored_reply)
        return {"status": "reply_received", "correlation_id": correlation_id}

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
                self._append_event_locked(
                    "timeout",
                    project_id=message.get("project_id"),
                    task_id=message.get("task_id"),
                    message_id=message.get("message_id"),
                    correlation_id=correlation_id,
                    from_agent=message.get("from_agent"),
                    to_agent=message.get("to_agent"),
                    message_type=message.get("type"),
                    status="TIMEOUT",
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

    async def list_agents(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [dict(agent) for agent in self._agents.values()]

    async def list_active_messages(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [dict(message) for message in self._active_messages.values()]

    async def list_events(self, since: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        async with self._lock:
            selected = [dict(event) for event in self._events if int(event["id"]) > since]
            return selected[-limit:]

    async def pending_reply_count(self) -> int:
        async with self._lock:
            return len(self._pending_replies)
