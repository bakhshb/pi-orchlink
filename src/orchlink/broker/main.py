from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Security
from fastapi.security import APIKeyHeader

from orchlink.broker.protocol import AgentRegistration, MessageEnvelope, envelope_to_dict
from orchlink.broker.settings import Settings, get_settings
from orchlink.broker.storage import MemoryMessageStore, MessageStore


VERSION = "0.1.0"
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


def create_app(
    store: MessageStore | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    app = FastAPI(title="Orchlink Broker", version=VERSION)
    app.state.settings = settings or get_settings()
    app.state.store = store or MemoryMessageStore()

    secure_router = APIRouter(prefix="/v1", dependencies=[Depends(require_api_key)])

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "orchlink", "version": VERSION}

    @secure_router.post("/agents/register")
    async def register_agent(
        agent: AgentRegistration,
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, str]:
        stored_agent = await message_store.register_agent(agent.model_dump(mode="json"))
        return {"status": "registered", "agent_id": stored_agent["agent_id"]}

    @secure_router.post("/messages/send")
    async def send_message(
        message: MessageEnvelope,
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, str]:
        return await message_store.enqueue_message(envelope_to_dict(message))

    @secure_router.post("/messages/send-and-wait")
    async def send_and_wait(
        message: MessageEnvelope,
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        await message_store.enqueue_message(envelope_to_dict(message), create_waiter=True)
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

    @secure_router.get("/events")
    async def events(
        since: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=500),
        message_store: MessageStore = Depends(get_store),
    ) -> dict[str, Any]:
        recent_events = await message_store.list_events(since=since, limit=limit)
        return {"events": recent_events, "last_event_id": recent_events[-1]["id"] if recent_events else since}

    @secure_router.get("/status")
    async def status(message_store: MessageStore = Depends(get_store)) -> dict[str, Any]:
        agents = await message_store.list_agents()
        active_messages = await message_store.list_active_messages()
        events = await message_store.list_events(limit=20)
        pending_reply_count = 0
        count_pending = getattr(message_store, "pending_reply_count", None)
        if count_pending is not None:
            pending_reply_count = await count_pending()
        return {
            "broker": "ok",
            "agent_count": len(agents),
            "agents": agents,
            "active_message_count": len(active_messages),
            "active_messages": active_messages,
            "pending_reply_count": pending_reply_count,
            "recent_events": events,
        }

    app.include_router(secure_router)
    return app


app = create_app()
