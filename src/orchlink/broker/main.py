import asyncio
import os
import signal
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.security import APIKeyHeader

from orchlink.broker.protocol import AgentRegistration, MessageEnvelope, envelope_to_dict
from orchlink.broker.settings import Settings, get_settings
from orchlink.broker.storage import JsonlMessageStore, MemoryMessageStore, MessageStore, MessageStoreBusy


VERSION = "0.5.0"
BROKER_CAPABILITIES = [
    "project_header_scope",
    "task_activity_endpoint",
    "scoped_task_results",
    "status_filters",
    "session_leases",
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
    app.state.settings = settings_obj
    app.state.store = store_obj
    app.state.shutdown_scheduled = False

    secure_router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "service": "orchlink", "version": VERSION, "capabilities": BROKER_CAPABILITIES}

    @secure_router.post("/agents/register")
    async def register_agent(
        agent: AgentRegistration,
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, str]:
        stored_agent = await message_store.register_agent(agent.model_dump(mode="json"))
        return {"status": "registered", "agent_id": stored_agent["agent_id"]}

    @secure_router.post("/sessions/acquire")
    async def acquire_session(
        body: dict[str, Any] = Body(default_factory=dict),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        return {"status": "active", "session": await message_store.acquire_session(body)}

    @secure_router.post("/sessions/{lease_id}/heartbeat")
    async def heartbeat_session(
        lease_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        try:
            project_id = request_project_id(request, str(body.get("project_id") or "") or None)
            return {"status": "ok", "session": await message_store.heartbeat_session(lease_id, project_id=project_id)}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @secure_router.post("/sessions/{lease_id}/release")
    async def release_session(
        lease_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        try:
            project_id = request_project_id(request, str(body.get("project_id") or "") or None)
            session = await message_store.release_session(lease_id, str(body.get("reason") or ""), project_id=project_id)
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
            return await message_store.enqueue_message(envelope_to_dict(message))
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
            await message_store.enqueue_message(envelope_to_dict(message), create_waiter=True)
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
        wait_seconds: int = Query(default=30, ge=0),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        message = await message_store.get_next_message(agent_id, wait_seconds)
        if message is None:
            return {"status": "empty"}
        return {"status": "message", "message": message}

    @secure_router.post("/messages/{message_id}/reply")
    async def reply_to_message(
        message_id: str,
        reply: MessageEnvelope,
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, str]:
        return await message_store.save_reply(message_id, envelope_to_dict(reply))

    @secure_router.post("/messages/{message_id}/status")
    async def update_message_status(
        message_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        status = str(body.get("status") or "").upper()
        if status not in {"RUNNING", "IN_PROGRESS"}:
            raise HTTPException(status_code=400, detail="Only RUNNING or IN_PROGRESS status updates are allowed.")
        try:
            return await message_store.update_message_status(message_id, status)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @secure_router.post("/jobs/{item_id}/cancel")
    async def cancel_work(
        item_id: str,
        request: Request,
        body: dict[str, Any] = Body(default_factory=dict),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        try:
            project_id = request_project_id(request, str(body.get("project_id") or "") or None)
            result = await message_store.cancel_work(
                item_id,
                str(body.get("reason") or ""),
                project_id=project_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await maybe_schedule_auto_stop()
        return result

    @secure_router.post("/conversations/{conversation_id}/close")
    async def close_conversation(
        conversation_id: str,
        message: MessageEnvelope,
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, str]:
        try:
            result = await message_store.close_conversation(conversation_id, envelope_to_dict(message))
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
        body: dict[str, Any] = Body(default_factory=dict),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        return await message_store.record_activity(body)

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

    app.include_router(secure_router)
    return app


app = create_app()
