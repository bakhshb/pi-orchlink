"""Broker application service boundary.

FastAPI routes depend on this facade instead of talking to storage directly.
The service keeps current wire shapes at the API boundary while centralizing
broker use-case orchestration away from route functions, and it owns the
typed checkpoint ordering seam so no route handler has to read or write the
``broker-checkpoint.json`` artifact directly.

The checkpoint module (``orchlink.broker.checkpoint``) stays the only module
that knows the on-disk shape. ``BrokerService`` is the application boundary
that routes/handlers call; it delegates to ``record_lease`` /
``load_checkpoint`` / ``reconcile_checkpoint`` after decoding the wire lease
shape and wrapping each call in non-fatal error handling.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from orchlink.broker.checkpoint import (
    CheckpointLease,
    DriftedLease,
    LeaseStatus,
    checkpoint_path,
    load_checkpoint,
    reconcile_checkpoint,
    record_lease,
)
from orchlink.broker.settings import Settings
from orchlink.broker.storage.base import ActivityInput, AgentInput, MessageInput, MessageStore, SessionAcquireInput, SessionHeartbeatInput
from orchlink.core.envelope import MessageEnvelope


logger = logging.getLogger(__name__)


def project_root_from_settings(settings: Settings) -> Path:
    """Infer the project root from the configured broker store path.

    Mirrors the historical layout ``<project>/.orch/run/orchlink-journal.jsonl``
    so callers do not have to know the path convention. Returns ``cwd`` if
    the store path is so shallow that no project root can be inferred.
    """
    store_path = Path(settings.store_path).expanduser()
    if not store_path.is_absolute():
        store_path = Path.cwd() / store_path
    try:
        return store_path.parent.parent.parent
    except IndexError:
        return Path.cwd()


def _decode_lease_wire(lease_wire: dict[str, Any] | None) -> tuple[int | None, str | None]:
    """Decode a lease wire dict to ``(epoch, holder)``.

    Returns ``(None, None)`` when the wire dict is missing, not a dict, or
    is missing a usable epoch/holder pair. Routes hand the wire lease dict
    straight to the service so they never reach into the on-disk shape.
    """
    if not isinstance(lease_wire, dict):
        return None, None
    raw_epoch = lease_wire.get("epoch")
    try:
        epoch = int(raw_epoch) if raw_epoch is not None else None
    except (TypeError, ValueError):
        epoch = None
    raw_holder = lease_wire.get("holder")
    holder = str(raw_holder).strip() if raw_holder not in (None, "") else ""
    if epoch is None or not holder:
        return None, None
    return epoch, holder


class BrokerService:
    """Typed application boundary for broker use cases.

    Owns the checkpoint ordering seam: every lease transition the broker
    observes flows through :meth:`record_in_flight` or
    :meth:`record_recently_settled`, both of which wrap the on-disk write in
    non-fatal error handling so a checkpoint failure never breaks an HTTP
    request.

    The startup reconciliation (load prior checkpoint, compute drifted
    leases, append them to the event stream) is exposed as
    :meth:`startup_reconcile_checkpoint` so the FastAPI lifespan can drive
    it; ``create_app`` itself performs no checkpoint I/O.
    """

    def __init__(
        self,
        store: MessageStore,
        *,
        project_root: Path | str | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.store = store
        if project_root is not None:
            self._project_root: Path | None = Path(project_root)
        elif settings is not None:
            self._project_root = project_root_from_settings(settings)
        else:
            self._project_root = None
        self._checkpoint_lock = asyncio.Lock()

    @property
    def project_root(self) -> Path | None:
        return self._project_root

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
        async with self._checkpoint_lock:
            session = await self.store.release_session(lease_id, reason, project_id=project_id)
            self._record_session_settlements(session)
            return session

    async def expire_sessions(self) -> list[dict[str, Any]]:
        async with self._checkpoint_lock:
            sessions = await self.store.expire_sessions()
            for session in sessions:
                self._record_session_settlements(session)
            return sessions

    async def list_sessions(self, project_id: str | None = None, active: bool = False) -> list[dict[str, Any]]:
        return await self.store.list_sessions(project_id=project_id, active=active)

    async def can_auto_stop(self, project_id: str | None = None) -> bool:
        return await self.store.can_auto_stop(project_id=project_id)

    async def enqueue_message(self, message: MessageInput, create_waiter: bool = False) -> dict[str, Any]:
        return await self.store.enqueue_message(message, create_waiter=create_waiter)

    async def get_next_message(self, agent_id: str, wait_seconds: int, lease_id: str | None = None, project_id: str | None = None) -> dict[str, Any] | None:
        # Wait without the service checkpoint lock. The store delivers the
        # message under its own mutation lock and invokes ``record_in_flight``
        # synchronously before releasing that lock (after the JSONL append has
        # landed). This keeps the durable checkpoint and the authoritative
        # store mutation atomic from the caller's point of view while still
        # allowing cancellation / session settlement to run concurrently with
        # the long-poll wait.
        return await self.store.get_next_message(
            agent_id,
            wait_seconds,
            lease_id=lease_id,
            project_id=project_id,
            on_delivered=self._on_message_delivered,
        )

    def _on_message_delivered(self, delivered: dict[str, Any]) -> None:
        """BrokerService callback passed to the store's delivery seam."""
        self.record_in_flight(delivered.get("task_id"), delivered.get("lease"))

    async def save_reply(
        self,
        message_id: str,
        reply: MessageEnvelope,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
        session_lease_id: str | None = None,
    ) -> dict[str, Any]:
        async with self._checkpoint_lock:
            result = await self.store.save_reply(
                message_id,
                reply,
                lease_epoch=lease_epoch,
                lease_holder=lease_holder,
                session_lease_id=session_lease_id,
            )
            if reply.task_id and str(result.get("status") or "") == "reply_received":
                lease = {"epoch": lease_epoch, "holder": lease_holder} if lease_epoch is not None and lease_holder else None
                self.record_recently_settled(str(reply.task_id), lease)
            return result

    async def update_message_status(self, message_id: str, status: str, session_lease_id: str | None = None) -> dict[str, Any]:
        return await self.store.update_message_status(message_id, status, session_lease_id=session_lease_id)

    async def record_activity(self, activity: ActivityInput) -> dict[str, Any]:
        return await self.store.record_activity(activity)

    async def list_activity(self, item_id: str | None = None, limit: int = 20, project_id: str | None = None) -> list[dict[str, Any]]:
        return await self.store.list_activity(item_id=item_id, limit=limit, project_id=project_id)

    async def cancel_work(self, item_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        async with self._checkpoint_lock:
            result = await self.store.cancel_work(item_id, reason=reason, project_id=project_id)
            if str(result.get("status") or "") == "cancelled":
                self.record_recently_settled(item_id, None)
            return result

    async def heartbeat_job(self, task_id: str, holder: str, epoch: int, project_id: str | None = None, heartbeat_ms: int | None = None) -> dict[str, Any]:
        async with self._checkpoint_lock:
            result = await self.store.heartbeat_job(task_id, holder, epoch, project_id=project_id, heartbeat_ms=heartbeat_ms)
            self.record_in_flight(task_id, result.get("lease"))
            return result

    async def reclaim_job(self, task_id: str, holder: str, project_id: str | None = None) -> dict[str, Any]:
        async with self._checkpoint_lock:
            result = await self.store.reclaim_job(task_id, holder, project_id=project_id)
            self.record_in_flight(task_id, result.get("lease"))
            return result

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

    # --- Transcript (G018) -------------------------------------------------

    async def append_transcript_batch(
        self,
        task_id: str,
        batch: Any,
        project_id: str,
        agent_id: str,
        session_lease_id: str | None = None,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
    ) -> dict[str, Any]:
        return await self.store.append_transcript_batch(
            batch,
            task_id=task_id,
            project_id=project_id,
            agent_id=agent_id,
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
        return await self.store.read_transcript_events(task_id, project_id, after=after, limit=limit)

    async def wait_transcript_events(
        self,
        task_id: str,
        project_id: str,
        after: int = 0,
        limit: int = 100,
        wait_seconds: int = 0,
    ) -> dict[str, Any]:
        return await self.store.wait_transcript_events(
            task_id, project_id, after=after, limit=limit, wait_seconds=wait_seconds
        )

    # ------------------------------------------------------------------
    # Checkpoint ordering seam
    # ------------------------------------------------------------------

    def record_in_flight(self, task_id: str | None, lease_wire: dict[str, Any] | None) -> None:
        """Record ``task_id`` as in-flight in the durable broker checkpoint.

        Accepts the wire-shape lease dict (``{"epoch": int, "holder": str,
        ...}``) so callers do not have to decode the lease themselves. A
        missing ``task_id`` or a lease with no usable epoch/holder is a
        no-op; checkpoint write failures are logged and swallowed so the
        broker keeps serving traffic even when the checkpoint file is
        unavailable.
        """
        if not task_id:
            return
        epoch, holder = _decode_lease_wire(lease_wire)
        if epoch is None or holder is None:
            return
        self._safe_record_lease(str(task_id), int(epoch), str(holder), "in_flight")

    def record_recently_settled(
        self,
        task_id: str | None,
        lease_wire: dict[str, Any] | None,
    ) -> None:
        """Record ``task_id`` as recently-settled in the durable broker checkpoint.

        If the caller does not supply a usable lease, the prior in-flight
        lease for ``task_id`` (if any) is read from the checkpoint file so
        the settlement record retains the original epoch/holder. Missing
        prior data is a no-op; checkpoint write failures are logged and
        swallowed.
        """
        if not task_id:
            return
        epoch, holder = _decode_lease_wire(lease_wire)
        if epoch is None or holder is None:
            prior = self._load_prior_lease(str(task_id))
            if prior is None:
                return
            epoch, holder = prior.epoch, prior.holder
        self._safe_record_lease(str(task_id), int(epoch), str(holder), "recently_settled")

    def startup_reconcile_checkpoint(self) -> list[DriftedLease]:
        """Load the prior checkpoint, compute drift against the live store,
        and append drift records to the store event stream.

        Designed for the FastAPI lifespan startup hook. Returns the drift
        list (for tests and observability) and never raises; a missing,
        corrupt, or unreadable checkpoint file is treated as no prior state.
        """
        if self._project_root is None:
            return []
        try:
            path = checkpoint_path(self._project_root)
            prior = load_checkpoint(path)
        except Exception as exc:
            logger.warning("Broker checkpoint load failed during startup: %s", exc)
            return []
        try:
            drifts = reconcile_checkpoint(prior, self.store.current_job_leases())
        except Exception as exc:
            logger.warning("Broker checkpoint reconcile failed during startup: %s", exc)
            return []
        try:
            self.store.append_checkpoint_drifts(drifts)
        except Exception as exc:
            logger.warning("Broker checkpoint drift append failed during startup: %s", exc)
        return drifts

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_session_settlements(self, session: Any) -> None:
        if isinstance(session, dict):
            settled_work = session.get("settled_work") or []
        else:
            settled_work = getattr(session, "settled_work", []) or []
        for item_id in settled_work:
            self.record_recently_settled(str(item_id), None)

    def _safe_record_lease(self, task_id: str, epoch: int, holder: str, status: LeaseStatus) -> None:
        """Wrap :func:`record_lease` in non-fatal error handling.

        Returns silently (logging a warning) when no project root is
        configured or when the on-disk write fails for any reason. The
        broker must continue serving requests even when the checkpoint
        artifact is unavailable.
        """
        if self._project_root is None:
            return
        try:
            record_lease(self._project_root, task_id, epoch, holder, status)
        except Exception as exc:
            logger.warning(
                "Broker checkpoint write failed for task %s (status=%s): %s",
                task_id,
                status,
                exc,
            )

    def _load_prior_lease(self, task_id: str) -> CheckpointLease | None:
        if self._project_root is None:
            return None
        try:
            prior = load_checkpoint(checkpoint_path(self._project_root))
        except Exception as exc:
            logger.warning("Broker checkpoint read failed for task %s: %s", task_id, exc)
            return None
        return next((lease for lease in prior.in_flight if lease.task_id == task_id), None)


__all__ = ["BrokerService", "project_root_from_settings"]
