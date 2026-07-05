import asyncio
import os
import signal
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.security import APIKeyHeader

from orchlink.broker.checkpoint import (
    DriftedLease,
    checkpoint_path,
    load_checkpoint,
    reconcile_checkpoint,
    record_lease,
)
from orchlink.broker.dto import (
    ActivityBody,
    CancelWorkBody,
    JobHeartbeatBody,
    JobReclaimBody,
    JournalAppendBody,
    MessageStatusBody,
    SessionAcquireBody,
    SessionHeartbeatBody,
    SessionReleaseBody,
)
from orchlink.broker.journal import Journal
from orchlink.core.envelope import ENVELOPE_VERSION, ENVELOPE_VERSION_HEADER, AgentRegistration, MessageEnvelope
from orchlink.core.models import BrokerEvent
from orchlink.broker.settings import Settings, get_settings
from orchlink.version import get_version
from orchlink.broker.storage import JsonlMessageStore, MemoryMessageStore, MessageStore, MessageStoreBusy
from orchlink.broker.storage.base import LeaseConflictError


VERSION = get_version()
BROKER_CAPABILITIES = [
    "project_header_scope",
    "task_activity_endpoint",
    "scoped_task_results",
    "status_filters",
    "session_leases",
    "session_readiness",
    "session_lease_fencing",
]
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def require_api_key(
    request: Request,
    api_key: str | None = Security(api_key_header),
) -> str:
    settings: Settings = request.app.state.settings
    if api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return str(api_key)


def get_store(request: Request) -> MessageStore:
    return request.app.state.store


def request_project_id(request: Request, explicit: str | None = None) -> str | None:
    value = explicit or request.headers.get("X-Orchlink-Project-ID")
    return str(value) if value else None


def create_store(settings: Settings) -> MessageStore:
    backend = str(settings.store_backend or "memory").lower()
    if backend == "memory":
        return MemoryMessageStore(
            require_peer_sessions=settings.require_peer_sessions,
            session_grace_seconds=settings.session_grace_seconds,
        )
    if backend == "jsonl":
        return JsonlMessageStore(
            path=settings.store_path,
            require_peer_sessions=settings.require_peer_sessions,
            session_grace_seconds=settings.session_grace_seconds,
        )
    raise ValueError(f"Unsupported broker store backend: {settings.store_backend}")


def _audit_journal_path(settings: Settings) -> Path | None:
    """Audit JSONL path: only the jsonl backend persists the audit journal.

    The memory backend is ephemeral by design, so its audit journal is
    in-memory only.
    """
    if str(settings.store_backend or "memory").lower() != "jsonl":
        return None
    return Path(settings.store_path).expanduser().with_name("audit.jsonl")


def _checkpoint_project_root(settings: Settings) -> Path:
    """Infer the project root from the configured broker store path."""
    store_path = Path(settings.store_path).expanduser()
    if not store_path.is_absolute():
        store_path = Path.cwd() / store_path
    # Default shape is <project>/.orch/run/orchlink-journal.jsonl.
    try:
        return store_path.parent.parent.parent
    except IndexError:
        return Path.cwd()


def _current_job_leases(store: MessageStore) -> dict[str, tuple[int, str]]:
    """Snapshot current task leases without coupling checkpoint.py to storage."""
    state = getattr(store, "_state", None)
    task_jobs = getattr(state, "task_jobs", {}) if state is not None else {}
    current: dict[str, tuple[int, str]] = {}
    for job in task_jobs.values():
        lease = getattr(job, "lease", None)
        task_id = getattr(job, "id", None)
        if lease and task_id:
            current[str(task_id)] = (int(lease.epoch), str(lease.holder))
    return current


def _emit_checkpoint_drifts(store: MessageStore, drifts: list[DriftedLease]) -> None:
    """Expose startup drift through /events without polluting the audit journal."""
    state = getattr(store, "_state", None)
    events = getattr(state, "events", None) if state is not None else None
    if not isinstance(events, list):
        return
    next_event_id = int(getattr(state, "next_event_id", 1) or 1)
    for drift in drifts:
        preview = (
            f"lease drift {drift.task_id}: {drift.reason} "
            f"previous_epoch={drift.previous_epoch} current_epoch={drift.current_epoch}"
        )
        events.append(
            BrokerEvent(
                id=next_event_id,
                time=datetime.now(timezone.utc).isoformat(),
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
        next_event_id += 1
    state.next_event_id = next_event_id


def _record_checkpoint_from_wire(settings: Settings, item: dict[str, Any], status: str) -> None:
    task_id = item.get("task_id")
    lease = item.get("lease") or {}
    if not task_id or not lease:
        return
    record_lease(
        _checkpoint_project_root(settings),
        str(task_id),
        int(lease.get("epoch") or 0),
        str(lease.get("holder") or ""),
        status=status,  # type: ignore[arg-type]
    )


def _record_recently_settled(settings: Settings, task_id: str, epoch: int | None, holder: str | None) -> None:
    if epoch is None or holder is None:
        checkpoint = load_checkpoint(checkpoint_path(_checkpoint_project_root(settings)))
        prior = next((lease for lease in checkpoint.in_flight if lease.task_id == task_id), None)
        if prior is None:
            return
        epoch = prior.epoch
        holder = prior.holder
    record_lease(_checkpoint_project_root(settings), task_id, epoch, holder, "recently_settled")


def create_app(
    store: MessageStore | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings_obj = settings or get_settings()
    store_obj = store or create_store(settings_obj)

    async def maybe_schedule_auto_stop() -> None:
        if not settings_obj.auto_stop or app.state.shutdown_scheduled:
            return
        if not await app.state.store.can_auto_stop():
            return
        app.state.shutdown_scheduled = True

        async def stop_soon() -> None:
            await asyncio.sleep(0.5)
            if await app.state.store.can_auto_stop():
                os.kill(os.getpid(), signal.SIGTERM)
            app.state.shutdown_scheduled = False

        asyncio.create_task(stop_soon())

    async def session_expiry_loop() -> None:
        while True:
            await asyncio.sleep(max(1, min(5, settings_obj.session_heartbeat_interval_seconds)))
            expired = await app.state.store.expire_sessions()
            if expired:
                await maybe_schedule_auto_stop()

    @asynccontextmanager
    async def lifespan(app_obj: FastAPI):
        if settings_obj.auto_stop or settings_obj.require_peer_sessions:
            app_obj.state.session_expiry_task = asyncio.create_task(session_expiry_loop())
        try:
            yield
        finally:
            task = getattr(app_obj.state, "session_expiry_task", None)
            if task is not None:
                task.cancel()

    app = FastAPI(title="Orchlink Broker", version=VERSION, lifespan=lifespan)

    @app.middleware("http")
    async def add_envelope_version_header(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/v1"):
            response.headers[ENVELOPE_VERSION_HEADER] = ENVELOPE_VERSION
        return response

    app.state.settings = settings_obj
    app.state.store = store_obj
    app.state.shutdown_scheduled = False
    # Observability-only audit journal (M1). Attached to the store so every
    # broker event is mirrored. Never the source of truth.
    journal = Journal(path=_audit_journal_path(settings_obj))
    attach_journal = getattr(store_obj, "attach_journal", None)
    if callable(attach_journal):
        attach_journal(journal)

    checkpoint = load_checkpoint(checkpoint_path(_checkpoint_project_root(settings_obj)))
    app.state.drifted_leases = reconcile_checkpoint(checkpoint, _current_job_leases(store_obj))
    _emit_checkpoint_drifts(store_obj, app.state.drifted_leases)

    secure_router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": "orchlink", "version": VERSION, "capabilities": BROKER_CAPABILITIES}

    @secure_router.post("/agents/register")
    async def register_agent(
        agent: AgentRegistration,
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, str]:
        stored_agent = await message_store.register_agent(agent)
        return {"status": "registered", "agent_id": stored_agent["agent_id"]}

    @secure_router.post("/sessions/acquire")
    async def acquire_session(
        body: SessionAcquireBody = Body(default_factory=SessionAcquireBody),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        try:
            return {"status": "active", "session": await message_store.acquire_session(body.to_command())}
        except LeaseConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @secure_router.post("/sessions/{lease_id}/heartbeat")
    async def heartbeat_session(
        lease_id: str,
        request: Request,
        body: SessionHeartbeatBody = Body(default_factory=SessionHeartbeatBody),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        try:
            project_id = request_project_id(request, str(body.project_id or "") or None)
            return {
                "status": "ok",
                "session": await message_store.heartbeat_session(
                    lease_id,
                    project_id=project_id,
                    heartbeat=body.to_command(lease_id, project_id=project_id),
                ),
            }
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @secure_router.post("/sessions/{lease_id}/release")
    async def release_session(
        lease_id: str,
        request: Request,
        body: SessionReleaseBody = Body(default_factory=SessionReleaseBody),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        try:
            project_id = request_project_id(request, str(body.project_id or "") or None)
            command = body.to_command(lease_id, project_id=project_id)
            session = await message_store.release_session(command.lease_id, command.reason, project_id=command.project_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await maybe_schedule_auto_stop()
        return {"status": "released", "session": session}

    @secure_router.get("/sessions")
    async def sessions(
        request: Request,
        project_id: str | None = Query(default=None),
        active: bool = Query(default=False),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        project_id = request_project_id(request, project_id)
        return {"project_id": project_id, "sessions": await message_store.list_sessions(project_id=project_id, active=active)}

    @secure_router.post("/messages/send")
    async def send_message(
        message: MessageEnvelope,
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, str]:
        try:
            return await message_store.enqueue_message(message)
        except MessageStoreBusy as exc:
            raise HTTPException(status_code=409, detail=exc.detail) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @secure_router.post("/messages/send-and-wait")
    async def send_and_wait(
        message: MessageEnvelope,
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        try:
            await message_store.enqueue_message(message, create_waiter=True)
        except MessageStoreBusy as exc:
            raise HTTPException(status_code=409, detail=exc.detail) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await message_store.wait_for_reply(
            message.correlation_id,
            message.timeout_seconds,
        )

    @secure_router.get("/agents/{agent_id}/next")
    async def get_next_message(
        agent_id: str,
        request: Request,
        wait_seconds: int = Query(default=30, ge=0),
        lease_id: str | None = Query(default=None),
        project_id: str | None = Query(default=None),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        scoped_project_id = request_project_id(request, project_id)
        try:
            message = await message_store.get_next_message(
                agent_id,
                wait_seconds,
                lease_id=lease_id,
                project_id=scoped_project_id,
            )
        except LeaseConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if message is None:
            return {"status": "empty"}
        _record_checkpoint_from_wire(settings_obj, message, "in_flight")
        return {"status": "message", "message": message}

    @secure_router.post("/messages/{message_id}/reply")
    async def reply_to_message(
        message_id: str,
        request: Request,
        reply: MessageEnvelope,
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, str]:
        # Enforce holder/epoch only when the caller asserts a job lease.
        lease_epoch_header = request.headers.get("x-orchlink-lease-epoch")
        lease_holder = request.headers.get("x-orchlink-lease-holder")
        session_lease_id = request.headers.get("x-orchlink-session-lease-id")
        try:
            lease_epoch = int(lease_epoch_header) if lease_epoch_header is not None else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="x-orchlink-lease-epoch must be an integer") from exc
        try:
            result = await message_store.save_reply(
                message_id,
                reply,
                lease_epoch=lease_epoch,
                lease_holder=lease_holder,
                session_lease_id=session_lease_id,
            )
        except LeaseConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        task_id = reply.task_id
        if task_id and str(result.get("status") or "") == "reply_received":
            _record_recently_settled(settings_obj, str(task_id), lease_epoch, lease_holder)
        return result

    @secure_router.post("/messages/{message_id}/status")
    async def update_message_status(
        message_id: str,
        body: MessageStatusBody = Body(default_factory=MessageStatusBody),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        status = str(body.status or "").upper()
        if status not in {"RUNNING", "IN_PROGRESS"}:
            raise HTTPException(status_code=400, detail="Only RUNNING or IN_PROGRESS status updates are allowed.")
        try:
            return await message_store.update_message_status(
                message_id,
                status,
                session_lease_id=str(body.session_lease_id or "") or None,
            )
        except LeaseConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @secure_router.post("/jobs/{item_id}/cancel")
    async def cancel_work(
        item_id: str,
        request: Request,
        body: CancelWorkBody = Body(default_factory=CancelWorkBody),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        try:
            project_id = request_project_id(request, str(body.project_id or "") or None)
            result = await message_store.cancel_work(
                item_id,
                str(body.reason or ""),
                project_id=project_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if str(result.get("status") or "") == "cancelled":
            _record_recently_settled(settings_obj, item_id, None, None)
        await maybe_schedule_auto_stop()
        return result

    @secure_router.post("/jobs/{item_id}/heartbeat")
    async def heartbeat_job(
        item_id: str,
        request: Request,
        body: JobHeartbeatBody = Body(default_factory=JobHeartbeatBody),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        project_id = request_project_id(request, str(body.project_id or "") or None)
        holder = str(body.holder or "")
        try:
            epoch = int(body.epoch or 0)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="epoch must be an integer") from exc
        try:
            result = await message_store.heartbeat_job(
                item_id, holder, epoch, project_id=project_id, heartbeat_ms=body.heartbeat_ms
            )
        except LeaseConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _record_checkpoint_from_wire(settings_obj, {"task_id": item_id, "lease": result.get("lease")}, "in_flight")
        return result

    @secure_router.post("/jobs/{item_id}/reclaim")
    async def reclaim_job(
        item_id: str,
        request: Request,
        body: JobReclaimBody = Body(default_factory=JobReclaimBody),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        project_id = request_project_id(request, str(body.project_id or "") or None)
        holder = str(body.holder or "")
        try:
            result = await message_store.reclaim_job(item_id, holder, project_id=project_id)
        except LeaseConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        _record_checkpoint_from_wire(settings_obj, {"task_id": item_id, "lease": result.get("lease")}, "in_flight")
        return result

    @secure_router.post("/conversations/{conversation_id}/close")
    async def close_conversation(
        conversation_id: str,
        message: MessageEnvelope,
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, str]:
        try:
            result = await message_store.close_conversation(conversation_id, message)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return result

    @secure_router.get("/events")
    async def events(
        request: Request,
        since: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
        project_id: str | None = Query(default=None),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        project_id = request_project_id(request, project_id)
        recent_events = await message_store.list_events(since=since, limit=limit, project_id=project_id)
        return {"events": recent_events, "last_event_id": recent_events[-1]["id"] if recent_events else since}

    @secure_router.post("/activity")
    async def record_activity(
        body: ActivityBody = Body(default_factory=ActivityBody),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        try:
            return await message_store.record_activity(body.to_command())
        except LeaseConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @secure_router.get("/activity")
    async def activity(
        request: Request,
        item_id: str | None = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
        project_id: str | None = Query(default=None),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        project_id = request_project_id(request, project_id)
        return {"activity": await message_store.list_activity(item_id=item_id, limit=limit, project_id=project_id)}

    @secure_router.get("/tasks/{task_id}/activity")
    async def task_activity(
        task_id: str,
        request: Request,
        limit: int = Query(default=20, ge=1, le=100),
        project_id: str | None = Query(default=None),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        project_id = request_project_id(request, project_id)
        return {"project_id": project_id, "task_id": task_id, "activity": await message_store.list_activity(item_id=task_id, limit=limit, project_id=project_id)}

    @secure_router.get("/jobs")
    async def jobs(
        request: Request,
        limit: int = Query(default=50, ge=1, le=500),
        project_id: str | None = Query(default=None),
        active: bool = Query(default=False),
        status: str | None = Query(default=None),
        kind: str | None = Query(default=None, pattern="^(task|talk)$"),
        item_id: str | None = Query(default=None, alias="id"),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        project_id = request_project_id(request, project_id)
        return {
            "project_id": project_id,
            "jobs": await message_store.list_jobs(
                limit=limit,
                project_id=project_id,
                active=active,
                status=status.upper() if status else None,
                kind=kind,
                item_id=item_id,
            ),
        }

    @secure_router.get("/tasks/{task_id}")
    async def get_task(
        task_id: str,
        request: Request,
        project_id: str | None = Query(default=None),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        return await message_store.get_task_result(task_id, project_id=request_project_id(request, project_id))

    @secure_router.get("/tasks/{task_id}/wait")
    async def wait_task(
        task_id: str,
        request: Request,
        timeout_seconds: int = Query(default=1800, ge=1),
        project_id: str | None = Query(default=None),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        return await message_store.wait_for_task(task_id, timeout_seconds, project_id=request_project_id(request, project_id))

    @secure_router.get("/status")
    async def status(
        request: Request,
        project_id: str | None = Query(default=None),
        task_id: str | None = Query(default=None),
        since: int = Query(default=0, ge=0),
        limit: int = Query(default=20, ge=1, le=500),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        project_id = request_project_id(request, project_id)
        agents = await message_store.list_agents()
        if project_id is not None:
            agents = [agent for agent in agents if str(agent.get("project_id") or "default") == project_id]
        sessions = await message_store.list_sessions(project_id=project_id)
        active_messages = await message_store.list_active_messages(project_id=project_id)
        conversations = await message_store.list_conversations(project_id=project_id)
        jobs = await message_store.list_jobs(limit=500 if task_id is not None else limit, project_id=project_id)
        events = await message_store.list_events(since=since, limit=500 if task_id is not None else limit, project_id=project_id)
        if task_id is not None:
            active_messages = [item for item in active_messages if str(item.get("task_id") or "") == task_id]
            jobs = [item for item in jobs if str(item.get("task_id") or "") == task_id][-limit:]
            events = [item for item in events if str(item.get("task_id") or "") == task_id][-limit:]
        pending_reply_count = 0
        count_pending = getattr(message_store, "pending_reply_count", None)
        if count_pending is not None:
            pending_reply_count = await count_pending()
        return {
            "broker": "ok",
            "agent_count": len(agents),
            "agents": agents,
            "session_count": len(sessions),
            "sessions": sessions,
            "active_message_count": len(active_messages),
            "active_messages": active_messages,
            "conversation_count": len(conversations),
            "conversations": conversations,
            "job_count": len(jobs),
            "jobs": jobs,
            "pending_reply_count": pending_reply_count,
            "recent_events": events,
        }

    @secure_router.get("/journal")
    async def get_journal(
        request: Request,
        project_id: str | None = Query(default=None),
        since: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=1000),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        project_id = request_project_id(request, project_id)
        journal = getattr(message_store, "journal", None)
        if journal is None:
            return {"project_id": project_id, "entries": [], "last_seq": since}
        entries = journal.query(project_id=project_id, since=since, limit=limit)
        return {
            "project_id": project_id,
            "entries": [entry.to_dict() for entry in entries],
            "last_seq": entries[-1].seq if entries else since,
        }

    @secure_router.post("/journal")
    async def post_journal(
        request: Request,
        body: JournalAppendBody = Body(default_factory=JournalAppendBody),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        """Append an external transition entry (e.g. a Goal Mode transition).

        Observability-only. Used by the goal layer to record goal.* transitions
        in the same audit journal from v1, without making the journal a source
        of truth.
        """
        journal = getattr(message_store, "journal", None)
        if journal is None:
            return {"status": "ignored", "seq": None}
        body_data = body.to_store_dict()
        action = str(body.action or "").strip()
        if not action:
            raise HTTPException(status_code=400, detail="Journal action is required.")
        project_id = request_project_id(request, str(body.project_id or "") or None)
        entry = journal.append(
            project_id=str(project_id or body.project_id or "default"),
            actor=body.actor,
            action=action,
            target_type=body.target_type,
            target_id=body.target_id,
            before=body.before,
            after=body.after,
            meta=body_data.get("meta") or {},
        )
        return {"status": "recorded", "seq": entry.seq}

    app.include_router(secure_router)
    return app


app = create_app()
