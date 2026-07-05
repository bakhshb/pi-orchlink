"""Broker application service boundary.

FastAPI routes depend on this facade instead of talking to storage directly.
The service keeps current wire shapes at the API boundary while centralizing
broker use-case orchestration away from route functions.
"""

from __future__ import annotations

from typing import Any

from orchlink.broker.storage.base import ActivityInput, AgentInput, MessageInput, MessageStore, SessionAcquireInput, SessionHeartbeatInput
from orchlink.core.envelope import MessageEnvelope


class BrokerService:
    def __init__(self, store: MessageStore) -> None:
        self.store = store

    @property
    def journal(self) -> Any:
        return getattr(self.store, "journal", None)

    async def register_agent(self, agent: AgentInput) -> dict[str, Any]:
        return await self.store.register_agent(agent)

    async def list_agents(self) -> list[dict[str, Any]]:
        return await self.store.list_agents()

    async def acquire_session(self, session: SessionAcquireInput) -> dict[str, Any]:
        return await self.store.acquire_session(session)

    async def heartbeat_session(self, lease_id: str, project_id: str | None = None, heartbeat: SessionHeartbeatInput | None = None) -> dict[str, Any]:
        return await self.store.heartbeat_session(lease_id, project_id=project_id, heartbeat=heartbeat)

    async def release_session(self, lease_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        return await self.store.release_session(lease_id, reason, project_id=project_id)

    async def expire_sessions(self) -> list[dict[str, Any]]:
        return await self.store.expire_sessions()

    async def list_sessions(self, project_id: str | None = None, active: bool = False) -> list[dict[str, Any]]:
        return await self.store.list_sessions(project_id=project_id, active=active)

    async def can_auto_stop(self, project_id: str | None = None) -> bool:
        return await self.store.can_auto_stop(project_id=project_id)

    async def enqueue_message(self, message: MessageInput, create_waiter: bool = False) -> dict[str, Any]:
        return await self.store.enqueue_message(message, create_waiter=create_waiter)

    async def get_next_message(self, agent_id: str, wait_seconds: int, lease_id: str | None = None, project_id: str | None = None) -> dict[str, Any] | None:
        return await self.store.get_next_message(agent_id, wait_seconds, lease_id=lease_id, project_id=project_id)

    async def save_reply(
        self,
        message_id: str,
        reply: MessageEnvelope,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
        session_lease_id: str | None = None,
    ) -> dict[str, Any]:
        return await self.store.save_reply(message_id, reply, lease_epoch=lease_epoch, lease_holder=lease_holder, session_lease_id=session_lease_id)

    async def update_message_status(self, message_id: str, status: str, session_lease_id: str | None = None) -> dict[str, Any]:
        return await self.store.update_message_status(message_id, status, session_lease_id=session_lease_id)

    async def record_activity(self, activity: ActivityInput) -> dict[str, Any]:
        return await self.store.record_activity(activity)

    async def list_activity(self, item_id: str | None = None, limit: int = 20, project_id: str | None = None) -> list[dict[str, Any]]:
        return await self.store.list_activity(item_id=item_id, limit=limit, project_id=project_id)

    async def cancel_work(self, item_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        return await self.store.cancel_work(item_id, reason=reason, project_id=project_id)

    async def heartbeat_job(self, task_id: str, holder: str, epoch: int, project_id: str | None = None, heartbeat_ms: int | None = None) -> dict[str, Any]:
        return await self.store.heartbeat_job(task_id, holder, epoch, project_id=project_id, heartbeat_ms=heartbeat_ms)

    async def reclaim_job(self, task_id: str, holder: str, project_id: str | None = None) -> dict[str, Any]:
        return await self.store.reclaim_job(task_id, holder, project_id=project_id)

    async def close_conversation(self, conversation_id: str, message: MessageInput) -> dict[str, Any]:
        return await self.store.close_conversation(conversation_id, message)

    async def wait_for_reply(self, correlation_id: str, timeout_seconds: int) -> dict[str, Any]:
        return await self.store.wait_for_reply(correlation_id, timeout_seconds)

    async def wait_for_task(self, task_id: str, timeout_seconds: int, project_id: str | None = None) -> dict[str, Any]:
        return await self.store.wait_for_task(task_id, timeout_seconds, project_id=project_id)

    async def get_task_result(self, task_id: str, project_id: str | None = None) -> dict[str, Any]:
        return await self.store.get_task_result(task_id, project_id=project_id)

    async def list_jobs(
        self,
        limit: int = 50,
        project_id: str | None = None,
        active: bool = False,
        status: str | None = None,
        kind: str | None = None,
        item_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self.store.list_jobs(limit=limit, project_id=project_id, active=active, status=status, kind=kind, item_id=item_id)

    async def list_active_messages(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return await self.store.list_active_messages(project_id=project_id)

    async def list_conversations(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return await self.store.list_conversations(project_id=project_id)

    async def list_events(self, since: int = 0, limit: int = 100, project_id: str | None = None) -> list[dict[str, Any]]:
        return await self.store.list_events(since=since, limit=limit, project_id=project_id)

    async def pending_reply_count(self) -> int:
        count_pending = getattr(self.store, "pending_reply_count", None)
        if count_pending is None:
            return 0
        return int(await count_pending())

    def current_job_leases(self) -> dict[str, tuple[int, str]]:
        return self.store.current_job_leases()

    def append_checkpoint_drifts(self, drifts: list[Any]) -> None:
        self.store.append_checkpoint_drifts(drifts)


__all__ = ["BrokerService"]
