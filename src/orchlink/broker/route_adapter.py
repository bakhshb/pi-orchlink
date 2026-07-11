"""FastAPI route adapter for broker use cases.

Routes depend on this HTTP-facing adapter instead of translating storage/service
exceptions inline. The adapter keeps response-shape decisions in the route while
centralizing exception-to-HTTP mapping.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from orchlink.broker.service import BrokerService
from orchlink.broker.storage import MessageStoreBusy
from orchlink.broker.storage.base import ActivityInput, AgentInput, LeaseConflictError, MessageInput, MessageStore, SessionAcquireInput, SessionHeartbeatInput, TaskTelemetryInput, TranscriptBatchInput
from orchlink.broker.storage.memory_telemetry_store import TelemetryRejected
from orchlink.core.envelope import MessageEnvelope
from orchlink.core.models import TranscriptBatch


class BrokerRouteAdapter:
    def __init__(self, service: BrokerService | MessageStore) -> None:
        if isinstance(service, BrokerService):
            self.service = service
        else:
            # Raw MessageStore injection is kept for older tests and extension
            # code; routes still interact with the typed BrokerService facade.
            self.service = BrokerService(service)

    @property
    def journal(self) -> Any:
        return self.service.journal

    async def register_agent(self, agent: AgentInput) -> dict[str, Any]:
        return await self.service.register_agent(agent)

    async def list_agents(self) -> list[dict[str, Any]]:
        return await self.service.list_agents()

    async def acquire_session(self, session: SessionAcquireInput) -> dict[str, Any]:
        try:
            return await self.service.acquire_session(session)
        except LeaseConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def heartbeat_session(self, lease_id: str, project_id: str | None = None, heartbeat: SessionHeartbeatInput | None = None) -> dict[str, Any]:
        try:
            return await self.service.heartbeat_session(lease_id, project_id=project_id, heartbeat=heartbeat)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def release_session(self, lease_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        try:
            return await self.service.release_session(lease_id, reason, project_id=project_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def expire_sessions(self) -> list[dict[str, Any]]:
        return await self.service.expire_sessions()

    async def list_sessions(self, project_id: str | None = None, active: bool = False) -> list[dict[str, Any]]:
        return await self.service.list_sessions(project_id=project_id, active=active)

    async def can_auto_stop(self, project_id: str | None = None) -> bool:
        return await self.service.can_auto_stop(project_id=project_id)

    async def enqueue_message(self, message: MessageInput, create_waiter: bool = False) -> dict[str, Any]:
        try:
            return await self.service.enqueue_message(message, create_waiter=create_waiter)
        except MessageStoreBusy as exc:
            raise HTTPException(status_code=409, detail=exc.detail) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def get_next_message(self, agent_id: str, wait_seconds: int, lease_id: str | None = None, project_id: str | None = None) -> dict[str, Any] | None:
        try:
            return await self.service.get_next_message(agent_id, wait_seconds, lease_id=lease_id, project_id=project_id)
        except LeaseConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def save_reply(
        self,
        message_id: str,
        reply: MessageEnvelope,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
        session_lease_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await self.service.save_reply(
                message_id,
                reply,
                lease_epoch=lease_epoch,
                lease_holder=lease_holder,
                session_lease_id=session_lease_id,
            )
        except LeaseConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def update_message_status(self, message_id: str, status: str, session_lease_id: str | None = None) -> dict[str, Any]:
        try:
            return await self.service.update_message_status(message_id, status, session_lease_id=session_lease_id)
        except LeaseConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def record_activity(self, activity: ActivityInput) -> dict[str, Any]:
        try:
            return await self.service.record_activity(activity)
        except LeaseConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    async def list_activity(self, item_id: str | None = None, limit: int = 20, project_id: str | None = None) -> list[dict[str, Any]]:
        return await self.service.list_activity(item_id=item_id, limit=limit, project_id=project_id)

    async def cancel_work(self, item_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        try:
            return await self.service.cancel_work(item_id, reason=reason, project_id=project_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def heartbeat_job(self, task_id: str, holder: str, epoch: int, project_id: str | None = None, heartbeat_ms: int | None = None) -> dict[str, Any]:
        try:
            return await self.service.heartbeat_job(task_id, holder, epoch, project_id=project_id, heartbeat_ms=heartbeat_ms)
        except LeaseConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def reclaim_job(self, task_id: str, holder: str, project_id: str | None = None) -> dict[str, Any]:
        try:
            return await self.service.reclaim_job(task_id, holder, project_id=project_id)
        except LeaseConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def close_conversation(self, conversation_id: str, message: MessageInput) -> dict[str, Any]:
        try:
            return await self.service.close_conversation(conversation_id, message)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def wait_for_reply(self, correlation_id: str, timeout_seconds: int) -> dict[str, Any]:
        return await self.service.wait_for_reply(correlation_id, timeout_seconds)

    async def wait_for_task(self, task_id: str, timeout_seconds: int, project_id: str | None = None) -> dict[str, Any]:
        return await self.service.wait_for_task(task_id, timeout_seconds, project_id=project_id)

    async def get_task_result(self, task_id: str, project_id: str | None = None) -> dict[str, Any]:
        return await self.service.get_task_result(task_id, project_id=project_id)

    async def list_jobs(
        self,
        limit: int = 50,
        project_id: str | None = None,
        active: bool = False,
        status: str | None = None,
        kind: str | None = None,
        item_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self.service.list_jobs(limit=limit, project_id=project_id, active=active, status=status, kind=kind, item_id=item_id)

    async def list_active_messages(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return await self.service.list_active_messages(project_id=project_id)

    async def list_conversations(self, project_id: str | None = None) -> list[dict[str, Any]]:
        return await self.service.list_conversations(project_id=project_id)

    async def list_events(self, since: int = 0, limit: int = 100, project_id: str | None = None) -> list[dict[str, Any]]:
        return await self.service.list_events(since=since, limit=limit, project_id=project_id)

    async def pending_reply_count(self) -> int:
        return await self.service.pending_reply_count()

    # --- Transcript (G018) -------------------------------------------------

    async def append_transcript_batch(
        self,
        task_id: str,
        batch: TranscriptBatchInput,
        project_id: str,
        agent_id: str,
        session_lease_id: str | None = None,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
    ) -> dict[str, Any]:
        try:
            if isinstance(batch, dict):
                batch = TranscriptBatch.from_wire(batch)
            return await self.service.append_transcript_batch(
                task_id,
                batch,
                project_id=project_id,
                agent_id=agent_id,
                session_lease_id=session_lease_id,
                lease_epoch=lease_epoch,
                lease_holder=lease_holder,
            )
        except LeaseConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    async def read_transcript_events(
        self,
        task_id: str,
        project_id: str,
        after: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        return await self.service.read_transcript_events(task_id, project_id, after=after, limit=limit)

    async def wait_transcript_events(
        self,
        task_id: str,
        project_id: str,
        after: int = 0,
        limit: int = 100,
        wait_seconds: int = 0,
    ) -> dict[str, Any]:
        return await self.service.wait_transcript_events(
            task_id, project_id, after=after, limit=limit, wait_seconds=wait_seconds
        )

    # --- Telemetry (G019 AC-5) --------------------------------------------

    async def record_task_telemetry(
        self,
        task_id: str,
        telemetry: TaskTelemetryInput,
        *,
        project_id: str,
        agent_id: str,
        session_lease_id: str | None = None,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
    ) -> dict[str, Any]:
        try:
            return await self.service.record_task_telemetry(
                task_id,
                telemetry,
                project_id=project_id,
                agent_id=agent_id,
                session_lease_id=session_lease_id,
                lease_epoch=lease_epoch,
                lease_holder=lease_holder,
            )
        except TelemetryRejected as exc:
            # Lease / terminal rejections are 409 Conflict; the worker
            # surfaces the structured ``reason`` via the response body so
            # both Pi and the terminal CLI can observe why a write was
            # dropped.
            raise HTTPException(
                status_code=409,
                detail={"reason": exc.reason, "message": str(exc)},
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    async def get_task_telemetry(
        self,
        task_id: str,
        project_id: str,
    ) -> dict[str, Any] | None:
        return await self.service.get_task_telemetry(task_id, project_id=project_id)

    async def list_task_telemetry(
        self,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self.service.list_task_telemetry(project_id=project_id)

__all__ = ["BrokerRouteAdapter"]
