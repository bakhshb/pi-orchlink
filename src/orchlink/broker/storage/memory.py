import asyncio
from datetime import datetime, timezone
from typing import Any

from orchlink.broker.storage.base import MessageStore, MessageStoreBusy


FAILED_STATUSES = {"FAILED", "TIMEOUT", "CANCELLED"}
BUSY_MESSAGE_STATUSES = {"PENDING", "QUEUED", "DELIVERED", "RUNNING", "IN_PROGRESS"}
ACTIVE_ACTIVITY_STATUSES = {"DELIVERED", "RUNNING", "IN_PROGRESS"}
ACTIVE_JOB_STATUSES = BUSY_MESSAGE_STATUSES | {"OPEN"}
TERMINAL_MESSAGE_STATUSES = {"DONE", "COMPLETED", "FAILED", "TIMEOUT", "CANCELLED", "CLOSED"}
WORKER_BOUND_TYPES = {"TASK", "CHAT_START", "CHAT_TURN", "CHAT_CLOSE"}


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
        self._activity: list[dict[str, Any]] = []
        self._next_event_id = 1
        self._next_activity_id = 1
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

    def _project_id(self, message: dict[str, Any]) -> str:
        return str(message.get("project_id") or "default")

    def _task_key(self, project_id: str | None, task_id: str) -> str:
        return f"{project_id or 'default'}:{task_id}"

    def _conversation_key(self, project_id: str | None, conversation_id: str) -> str:
        return f"{project_id or 'default'}:{conversation_id}"

    def _same_project(self, item: dict[str, Any], project_id: str | None) -> bool:
        return project_id is None or str(item.get("project_id") or "default") == str(project_id)

    def _payload_preview(self, payload: dict[str, Any]) -> str:
        for key in ("message", "intent", "topic", "summary", "stdout"):
            value = payload.get(key)
            if value:
                return str(value)
        return ""

    def _message_preview(self, message: dict[str, Any]) -> str:
        return self._payload_preview(message.get("payload") or {})

    def _append_event_locked(self, event_type: str, **fields: Any) -> dict[str, Any]:
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
        return event

    def _expire_timed_out_messages_locked(self) -> None:
        now = datetime.now(timezone.utc)
        for message in list(self._active_messages.values()):
            status = str(message.get("status") or "").upper()
            if status not in BUSY_MESSAGE_STATUSES:
                continue
            try:
                timeout_seconds = int(message.get("timeout_seconds") or 0)
            except (TypeError, ValueError):
                timeout_seconds = 0
            if timeout_seconds <= 0:
                continue
            started_at = self._parse_time(message.get("created_at") or message.get("queued_at"))
            if started_at is None:
                continue
            if (now - started_at).total_seconds() < timeout_seconds:
                continue

            message["status"] = "TIMEOUT"
            message["updated_at"] = self._now()
            task_id = message.get("task_id")
            if task_id:
                result = {
                    "status": "TIMEOUT",
                    "project_id": self._project_id(message),
                    "task_id": str(task_id),
                    "error": "Task exceeded its timeout before the worker replied.",
                    "job": dict(message),
                }
                task_key = self._task_key(self._project_id(message), str(task_id))
                self._results_by_task[task_key] = result
                self._upsert_task_locked(message, "TIMEOUT")
                self._resolve_task_waiters_locked(task_key, result)
                self._resolve_task_waiters_locked(str(task_id), result)
            if str(message.get("type") or "").startswith("CHAT_"):
                self._touch_conversation_locked(message, status="TIMEOUT")
            future = self._pending_replies.get(str(message.get("correlation_id") or ""))
            if future is not None and not future.done():
                future.set_result({
                    "type": "BLOCKER",
                    "status": "TIMEOUT",
                    "correlation_id": message.get("correlation_id"),
                    "payload": {"summary": "Worker did not reply before the timeout."},
                })
            self._append_event_locked(
                "timeout",
                **self._event_fields(message, status="TIMEOUT"),
                preview="Work exceeded the timeout before the worker replied.",
            )

    def _job_mode(self, message: dict[str, Any]) -> str:
        payload = message.get("payload") or {}
        mode = payload.get("mode")
        if mode:
            return str(mode)
        message_type = str(message.get("type") or "")
        if message_type.startswith("CHAT_"):
            return "TALK"
        return "PLAN"

    def _job_kind(self, job: dict[str, Any]) -> str:
        if job.get("task_id"):
            return "task"
        if job.get("conversation_id"):
            return "talk"
        return str(job.get("kind") or "-").lower()

    def _hide_stale_heartbeat_locked(self, job: dict[str, Any]) -> dict[str, Any]:
        status = str(job.get("status") or "").upper()
        if job.get("last_activity_type") == "heartbeat" and status not in ACTIVE_ACTIVITY_STATUSES:
            job.pop("last_activity_at", None)
            job.pop("last_activity_type", None)
            job.pop("last_activity_tool", None)
            job.pop("last_activity_preview", None)
        return job

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

    def _activity_preview(self, activity: dict[str, Any]) -> str:
        detail = str(activity.get("detail") or activity.get("phase") or activity.get("activity_type") or "")
        tool_name = str(activity.get("tool_name") or "")
        if tool_name and detail:
            return f"{tool_name}: {detail}"
        return tool_name or detail

    def _apply_activity_to_work_locked(self, activity: dict[str, Any], timestamp: str) -> None:
        project_id = str(activity.get("project_id") or "default")
        task_id = str(activity.get("task_id") or "")
        conversation_id = str(activity.get("conversation_id") or "")
        message_id = str(activity.get("message_id") or "")
        preview = str(activity.get("detail") or activity.get("phase") or activity.get("activity_type") or "")[:300]

        if conversation_id:
            conversation = self._conversations.get(self._conversation_key(project_id, conversation_id))
            if conversation:
                conversation["updated_at"] = timestamp
                conversation["last_activity_at"] = timestamp
                conversation["last_activity_type"] = activity.get("activity_type")
                conversation["last_activity_tool"] = activity.get("tool_name")
                conversation["last_activity_preview"] = preview

        target_message: dict[str, Any] | None = None
        if message_id:
            target_message = self._active_messages.get(message_id)
        if target_message is None:
            target_message = next(
                (
                    item
                    for item in self._active_messages.values()
                    if self._same_project(item, project_id)
                    and (
                        (task_id and str(item.get("task_id") or "") == task_id)
                        or (conversation_id and str(item.get("conversation_id") or "") == conversation_id)
                    )
                ),
                None,
            )
        if target_message and str(target_message.get("status") or "").upper() in BUSY_MESSAGE_STATUSES:
            target_message["status"] = "RUNNING"
            target_message["updated_at"] = timestamp
            target_message["last_activity_at"] = timestamp
            target_message["last_activity_type"] = activity.get("activity_type")
            target_message["last_activity_tool"] = activity.get("tool_name")
            target_message["last_activity_preview"] = preview
            if target_message.get("task_id") and target_message.get("type") == "TASK":
                self._upsert_task_locked(target_message, "RUNNING")

        if task_id:
            task_key = self._task_key(project_id, task_id)
            task = self._tasks.get(task_key)
            if task:
                if str(task.get("status") or "").upper() in BUSY_MESSAGE_STATUSES:
                    task["status"] = "RUNNING"
                task["updated_at"] = timestamp
                task["last_activity_at"] = timestamp
                task["last_activity_type"] = activity.get("activity_type")
                task["last_activity_tool"] = activity.get("tool_name")
                task["last_activity_preview"] = preview

    def _upsert_task_locked(self, message: dict[str, Any], status: str) -> None:
        task_id = message.get("task_id")
        if not task_id:
            return
        project_id = self._project_id(message)
        task_key = self._task_key(project_id, str(task_id))
        now = self._now()
        existing = self._tasks.get(task_key, {})
        is_reply = bool(existing) and message.get("type") != "TASK"
        self._tasks[task_key] = {
            "kind": "task",
            "project_id": project_id,
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
            "last_activity_at": message.get("last_activity_at") or existing.get("last_activity_at"),
            "last_activity_type": message.get("last_activity_type") or existing.get("last_activity_type"),
            "last_activity_tool": message.get("last_activity_tool") or existing.get("last_activity_tool"),
            "last_activity_preview": message.get("last_activity_preview") or existing.get("last_activity_preview"),
        }

    def _touch_conversation_locked(self, message: dict[str, Any], status: str | None = None) -> None:
        conversation_id = message.get("conversation_id")
        if not conversation_id:
            return
        message_type = str(message.get("type") or "")
        if not message_type.startswith("CHAT_"):
            return
        project_id = self._project_id(message)
        conversation_key = self._conversation_key(project_id, str(conversation_id))
        now = self._now()
        existing = self._conversations.get(conversation_key, {})
        next_status = status or existing.get("status") or "OPEN"
        if message_type == "CHAT_CLOSE":
            next_status = "CLOSED"
        elif next_status not in {"CLOSED", "TIMEOUT", "FAILED", "CANCELLED"}:
            next_status = "OPEN"
        turn = int(message.get("turn") or existing.get("turn") or 1)
        max_turns = int(message.get("max_turns") or existing.get("max_turns") or 6)
        if turn >= max_turns and message_type == "CHAT_REPLY":
            next_status = "CLOSED"
        participants = existing.get("participants") or []
        for agent in (message.get("from_agent"), message.get("to_agent")):
            if agent and agent not in participants:
                participants.append(agent)
        self._conversations[conversation_key] = {
            "kind": "talk",
            "conversation_id": str(conversation_id),
            "project_id": project_id,
            "participants": participants,
            "mode": "TALK",
            "status": next_status,
            "turn": turn,
            "max_turns": max_turns,
            "from_agent": existing.get("from_agent") or message.get("from_agent"),
            "to_agent": existing.get("to_agent") or message.get("to_agent"),
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
            "last_message_preview": self._message_preview(message)[:300],
            "preview": self._message_preview(message)[:300],
            "message_type": message_type,
            "last_activity_at": existing.get("last_activity_at"),
            "last_activity_type": existing.get("last_activity_type"),
            "last_activity_tool": existing.get("last_activity_tool"),
            "last_activity_preview": existing.get("last_activity_preview"),
        }

    def _assert_conversation_can_receive_locked(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type") or "")
        if message_type not in {"CHAT_TURN", "CHAT_REPLY"}:
            return
        project_id = self._project_id(message)
        conversation_id = str(message.get("conversation_id") or "")
        conversation = self._conversations.get(self._conversation_key(project_id, conversation_id))
        if not conversation:
            raise ValueError(f"Conversation not found: {conversation_id}")
        if conversation.get("status") != "OPEN":
            raise ValueError(f"Conversation is not open: {conversation_id}")
        current_turn = int(conversation.get("turn") or 1)
        max_turns = int(conversation.get("max_turns") or 6)
        if current_turn >= max_turns:
            raise ValueError(f"Conversation reached max turns: {conversation_id}")

    def _busy_detail(self, blocker: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
        blocking_id = blocker.get("task_id") or blocker.get("conversation_id") or blocker.get("message_id")
        return {
            "error": "worker_busy",
            "message": "Worker already has pending work. Wait for that reply before sending another task or talk turn.",
            "blocking_id": blocking_id,
            "blocking_kind": blocker.get("kind") or ("conversation" if blocker.get("conversation_id") and not blocker.get("task_id") else "task"),
            "blocking_status": blocker.get("status"),
            "blocking_type": blocker.get("message_type") or blocker.get("type"),
            "requested_type": message.get("type"),
            "requested_task_id": message.get("task_id"),
            "requested_conversation_id": message.get("conversation_id"),
        }

    def _assert_worker_lane_free_locked(self, message: dict[str, Any]) -> None:
        message_type = str(message.get("type") or "")
        if message_type not in WORKER_BOUND_TYPES:
            return

        to_agent = message.get("to_agent")
        project_id = self._project_id(message)
        for active in self._active_messages.values():
            if not self._same_project(active, project_id):
                continue
            if active.get("to_agent") != to_agent:
                continue
            if str(active.get("status") or "").upper() not in BUSY_MESSAGE_STATUSES:
                continue
            raise MessageStoreBusy(self._busy_detail(active, message))

        conversation_id = str(message.get("conversation_id") or "")
        if message_type in {"CHAT_TURN", "CHAT_CLOSE"}:
            return
        for conversation in self._conversations.values():
            if not self._same_project(conversation, project_id):
                continue
            if conversation.get("status") != "OPEN":
                continue
            if to_agent not in conversation.get("participants", []):
                continue
            if conversation.get("conversation_id") == conversation_id:
                continue
            raise MessageStoreBusy(self._busy_detail(conversation, message))

    def _resolve_task_waiters_locked(self, task_key: str, result: dict[str, Any]) -> None:
        waiters = self._task_waiters.pop(task_key, [])
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
            self._expire_timed_out_messages_locked()
            self._assert_conversation_can_receive_locked(message)
            self._assert_worker_lane_free_locked(message)
            stored_message = dict(message)
            now = self._now()
            stored_message["status"] = "CLOSED" if message.get("type") == "CHAT_CLOSE" else "QUEUED"
            stored_message.setdefault("created_at", now)
            stored_message["queued_at"] = now
            stored_message["updated_at"] = now
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
            self._expire_timed_out_messages_locked()
            inbox = self._inboxes.setdefault(agent_id, asyncio.Queue())

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
                message_id = str(message.get("message_id"))
                active_message = self._active_messages.get(message_id)
                if active_message and str(active_message.get("status") or "").upper() in TERMINAL_MESSAGE_STATUSES:
                    if wait_seconds <= 0 or loop.time() >= deadline:
                        return None
                    continue

                delivered = dict(active_message or message)
                if delivered.get("status") != "CLOSED":
                    delivered["status"] = "DELIVERED"
                delivered["updated_at"] = self._now()
                if message_id in self._active_messages:
                    self._active_messages[message_id]["status"] = delivered["status"]
                    self._active_messages[message_id]["updated_at"] = delivered["updated_at"]
                if delivered.get("task_id") and delivered.get("type") == "TASK":
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
            self._expire_timed_out_messages_locked()
            active = self._active_messages.get(message_id)
            if active and str(active.get("status") or "").upper() in {"TIMEOUT", "CANCELLED"}:
                self._append_event_locked(
                    "late_reply_ignored",
                    **self._event_fields(stored_reply, status=str(active.get("status") or "")),
                    preview="Late worker reply ignored because the original work is no longer active.",
                )
                return {"status": "reply_ignored", "correlation_id": correlation_id}

            future = self._pending_replies.get(correlation_id)
            if message_id in self._active_messages:
                self._active_messages[message_id]["status"] = job_status
                self._active_messages[message_id]["updated_at"] = self._now()
            if task_id:
                result = {"status": job_status, "project_id": self._project_id(stored_reply), "task_id": str(task_id), "reply": stored_reply}
                task_key = self._task_key(self._project_id(stored_reply), str(task_id))
                self._results_by_task[task_key] = result
                self._upsert_task_locked(stored_reply, job_status)
                self._resolve_task_waiters_locked(task_key, result)
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

    async def update_message_status(self, message_id: str, status: str) -> dict[str, Any]:
        normalized_status = status.upper()
        if normalized_status not in BUSY_MESSAGE_STATUSES | TERMINAL_MESSAGE_STATUSES:
            raise ValueError(f"Unsupported status: {status}")
        async with self._lock:
            self._expire_timed_out_messages_locked()
            message = self._active_messages.get(message_id)
            if message is None:
                raise ValueError(f"Message not found: {message_id}")
            if str(message.get("status") or "").upper() in TERMINAL_MESSAGE_STATUSES:
                return {"status": str(message.get("status")), "message_id": message_id}
            message["status"] = normalized_status
            message["updated_at"] = self._now()
            if message.get("task_id") and message.get("type") == "TASK":
                self._upsert_task_locked(message, normalized_status)
            if str(message.get("type") or "").startswith("CHAT_"):
                self._touch_conversation_locked(message, status=normalized_status)
            self._append_event_locked(
                "message_status",
                **self._event_fields(message, status=normalized_status),
            )
            return {"status": normalized_status, "message_id": message_id}

    async def record_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            timestamp = self._now()
            stored = {
                "id": self._next_activity_id,
                "time": timestamp,
                "project_id": str(activity.get("project_id") or "default"),
                "task_id": activity.get("task_id"),
                "conversation_id": activity.get("conversation_id"),
                "message_id": activity.get("message_id"),
                "agent_id": activity.get("agent_id"),
                "activity_type": str(activity.get("activity_type") or "activity"),
                "phase": activity.get("phase"),
                "tool_name": activity.get("tool_name"),
                "detail": str(activity.get("detail") or "")[:500],
                "status": str(activity.get("status") or "RUNNING"),
                "mode": activity.get("mode"),
            }
            self._next_activity_id += 1
            self._activity.append(stored)
            if len(self._activity) > 1000:
                self._activity = self._activity[-1000:]
            self._apply_activity_to_work_locked(stored, timestamp)
            self._append_event_locked(
                "worker_activity",
                project_id=stored.get("project_id"),
                task_id=stored.get("task_id"),
                conversation_id=stored.get("conversation_id"),
                message_id=stored.get("message_id"),
                from_agent=stored.get("agent_id"),
                message_type="ACTIVITY",
                mode=stored.get("mode"),
                status=stored.get("status"),
                payload=stored,
                preview=self._activity_preview(stored),
            )
            return {"status": "recorded", "activity_id": stored["id"]}

    async def list_activity(
        self,
        item_id: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        async with self._lock:
            selected = [dict(item) for item in self._activity if self._same_project(item, project_id)]
            if item_id:
                selected = [
                    item
                    for item in selected
                    if str(item.get("task_id") or "") == item_id
                    or str(item.get("conversation_id") or "") == item_id
                    or str(item.get("message_id") or "") == item_id
                ]
            return selected[-limit:]

    def _inactive_work_message_locked(self, item_id: str, project_id: str | None = None) -> str:
        for result in self._results_by_task.values():
            if not self._same_project(result.get("job") or result.get("reply") or result, project_id):
                continue
            if str(result.get("task_id") or "") == item_id:
                return f"No active work found: {item_id} (already {result.get('status', 'DONE')})."
        for task in self._tasks.values():
            if not self._same_project(task, project_id):
                continue
            if str(task.get("task_id") or "") == item_id:
                status = str(task.get("status") or "UNKNOWN")
                if status.upper() in TERMINAL_MESSAGE_STATUSES:
                    return f"No active work found: {item_id} (already {status})."
        for conversation in self._conversations.values():
            if not self._same_project(conversation, project_id):
                continue
            if str(conversation.get("conversation_id") or "") == item_id:
                status = str(conversation.get("status") or "UNKNOWN")
                if status.upper() != "OPEN":
                    return f"No active work found: {item_id} (already {status})."
        return f"No active work found: {item_id}."

    async def cancel_work(self, item_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        cancelled: list[str] = []
        async with self._lock:
            self._expire_timed_out_messages_locked()
            targets = [
                message
                for message in self._active_messages.values()
                if self._same_project(message, project_id)
                and (
                    str(message.get("message_id") or "") == item_id
                    or str(message.get("task_id") or "") == item_id
                    or str(message.get("conversation_id") or "") == item_id
                )
            ]
            targets = [message for message in targets if str(message.get("status") or "").upper() not in TERMINAL_MESSAGE_STATUSES]
            if not targets:
                conversation = None
                if project_id is not None:
                    conversation = self._conversations.get(self._conversation_key(project_id, item_id))
                else:
                    matches = [item for item in self._conversations.values() if item.get("conversation_id") == item_id]
                    conversation = matches[0] if len(matches) == 1 else None
                if conversation and conversation.get("status") == "OPEN":
                    conversation["status"] = "CANCELLED"
                    conversation["updated_at"] = self._now()
                    self._append_event_locked(
                        "work_cancelled",
                        project_id=conversation.get("project_id"),
                        conversation_id=item_id,
                        mode="TALK",
                        status="CANCELLED",
                        preview=reason or "Conversation cancelled.",
                    )
                    return {"status": "cancelled", "item_id": item_id, "cancelled": [item_id]}
                raise ValueError(self._inactive_work_message_locked(item_id, project_id=project_id))

            for message in targets:
                message["status"] = "CANCELLED"
                message["updated_at"] = self._now()
                message_id = str(message.get("message_id") or "")
                if message_id:
                    cancelled.append(message_id)
                task_id = message.get("task_id")
                if task_id:
                    result = {
                        "status": "CANCELLED",
                        "project_id": self._project_id(message),
                        "task_id": str(task_id),
                        "error": reason or "Work was cancelled.",
                        "job": dict(message),
                    }
                    task_key = self._task_key(self._project_id(message), str(task_id))
                    self._results_by_task[task_key] = result
                    self._upsert_task_locked(message, "CANCELLED")
                    self._resolve_task_waiters_locked(task_key, result)
                    self._resolve_task_waiters_locked(str(task_id), result)
                if str(message.get("type") or "").startswith("CHAT_"):
                    self._touch_conversation_locked(message, status="CANCELLED")
                future = self._pending_replies.get(str(message.get("correlation_id") or ""))
                if future is not None and not future.done():
                    future.set_result({
                        "type": "BLOCKER",
                        "status": "CANCELLED",
                        "correlation_id": message.get("correlation_id"),
                        "payload": {"summary": reason or "Work was cancelled."},
                    })
                self._append_event_locked(
                    "work_cancelled",
                    **self._event_fields(message, status="CANCELLED"),
                    preview=reason or "Work was cancelled.",
                )
        return {"status": "cancelled", "item_id": item_id, "cancelled": cancelled}

    async def close_conversation(self, conversation_id: str, message: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            project_id = self._project_id(message) if message else None
            conversation = self._conversations.get(self._conversation_key(project_id, conversation_id)) if project_id else None
            if conversation is None and project_id is None:
                matches = [item for item in self._conversations.values() if item.get("conversation_id") == conversation_id]
                conversation = matches[0] if len(matches) == 1 else None
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
            self._expire_timed_out_messages_locked()
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

    async def wait_for_task(self, task_id: str, timeout_seconds: int, project_id: str | None = None) -> dict[str, Any]:
        task_key = self._task_key(project_id, task_id) if project_id is not None else task_id
        async with self._lock:
            self._expire_timed_out_messages_locked()
            if project_id is not None and task_key in self._results_by_task:
                return dict(self._results_by_task[task_key])
            if project_id is not None and task_key not in self._tasks:
                return {"status": "missing", "project_id": project_id, "task_id": task_id, "error": "Task not found."}
            if project_id is None:
                matches = [dict(result) for key, result in self._results_by_task.items() if key.endswith(f":{task_id}") or key == task_id]
                if len(matches) == 1:
                    return matches[0]
            future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
            self._task_waiters.setdefault(task_key, []).append(future)

        try:
            return await asyncio.wait_for(future, timeout=timeout_seconds)
        except asyncio.TimeoutError:
            async with self._lock:
                waiters = self._task_waiters.get(task_key, [])
                self._task_waiters[task_key] = [item for item in waiters if item is not future]
                if not self._task_waiters[task_key]:
                    self._task_waiters.pop(task_key, None)
            return {"status": "WAIT_TIMEOUT", "project_id": project_id, "task_id": task_id, "error": "No task result arrived before the wait timeout."}

    async def get_task_result(self, task_id: str, project_id: str | None = None) -> dict[str, Any]:
        task_key = self._task_key(project_id, task_id) if project_id is not None else task_id
        async with self._lock:
            self._expire_timed_out_messages_locked()
            if project_id is not None:
                if task_key in self._results_by_task:
                    return dict(self._results_by_task[task_key])
                if task_key in self._tasks:
                    return {"status": self._tasks[task_key].get("status", "QUEUED"), "project_id": project_id, "task_id": task_id, "job": dict(self._tasks[task_key])}
            else:
                result_matches = [dict(result) for key, result in self._results_by_task.items() if key.endswith(f":{task_id}") or key == task_id]
                if len(result_matches) == 1:
                    return result_matches[0]
                task_matches = [dict(task) for key, task in self._tasks.items() if key.endswith(f":{task_id}") or key == task_id]
                if len(task_matches) == 1:
                    return {"status": task_matches[0].get("status", "QUEUED"), "project_id": task_matches[0].get("project_id"), "task_id": task_id, "job": task_matches[0]}
            return {"status": "missing", "project_id": project_id, "task_id": task_id, "error": "Task not found."}

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
            jobs = [dict(task) for task in self._tasks.values() if self._same_project(task, project_id)]
            jobs.extend(dict(conversation) for conversation in self._conversations.values() if self._same_project(conversation, project_id))
            if active:
                jobs = [job for job in jobs if str(job.get("status") or "").upper() in ACTIVE_JOB_STATUSES]
            if status:
                expected_status = status.upper()
                jobs = [job for job in jobs if str(job.get("status") or "").upper() == expected_status]
            if kind:
                expected_kind = kind.lower()
                jobs = [job for job in jobs if self._job_kind(job) == expected_kind]
            if item_id:
                jobs = [
                    job
                    for job in jobs
                    if str(job.get("task_id") or "") == item_id
                    or str(job.get("conversation_id") or "") == item_id
                    or str(job.get("message_id") or "") == item_id
                ]
            jobs = [self._hide_stale_heartbeat_locked(job) for job in jobs]
            jobs.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
            return jobs[:limit]

    async def list_agents(self) -> list[dict[str, Any]]:
        async with self._lock:
            return [dict(agent) for agent in self._agents.values()]

    async def list_active_messages(self, project_id: str | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return [dict(message) for message in self._active_messages.values() if self._same_project(message, project_id)]

    async def list_conversations(self, project_id: str | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            self._expire_timed_out_messages_locked()
            return [dict(conversation) for conversation in self._conversations.values() if self._same_project(conversation, project_id)]

    async def list_events(self, since: int = 0, limit: int = 100, project_id: str | None = None) -> list[dict[str, Any]]:
        async with self._lock:
            selected = [
                dict(event)
                for event in self._events
                if int(event["id"]) > since and self._same_project(event, project_id)
            ]
            return selected[-limit:]

    async def pending_reply_count(self) -> int:
        async with self._lock:
            return len(self._pending_replies)
