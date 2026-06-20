import asyncio

import httpx

from orchlink.broker.main import create_app
from orchlink.broker.settings import Settings
from orchlink.broker.storage.memory import MemoryMessageStore


HEADERS = {"X-API-Key": "test-key"}


def task_message():
    return {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-0001",
        "correlation_id": "req-0001",
        "project_id": "demo",
        "conversation_id": "demo-default",
        "task_id": "T001",
        "from_agent": "demo.lead",
        "to_agent": "demo.work",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 2,
        "payload": {"intent": "Return PLAN only."},
    }


def reply_message():
    return {
        "protocol": "orch-a2a-v1",
        "message_id": "reply-0001",
        "correlation_id": "req-0001",
        "project_id": "demo",
        "conversation_id": "demo-default",
        "task_id": "T001",
        "from_agent": "demo.work",
        "to_agent": "demo.lead",
        "type": "PLAN",
        "status": "COMPLETED",
        "turn": 2,
        "max_turns": 6,
        "requires_reply": False,
        "timeout_seconds": 1,
        "payload": {"summary": "Plan ready."},
    }


def test_events_endpoint_shows_request_reply_flow():
    async def run():
        app = create_app(store=MemoryMessageStore(), settings=Settings(api_key="test-key"))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            send_task = asyncio.create_task(
                client.post("/v1/messages/send-and-wait", headers=HEADERS, json=task_message())
            )
            next_response = await client.get("/v1/agents/demo.work/next?wait_seconds=1", headers=HEADERS)
            assert next_response.json()["status"] == "message"
            await client.post("/v1/messages/msg-0001/reply", headers=HEADERS, json=reply_message())
            assert (await send_task).json()["status"] == "completed"

            events_response = await client.get("/v1/events", headers=HEADERS)

        events = events_response.json()["events"]
        event_types = [event["type"] for event in events]
        assert "message_queued" in event_types
        assert "message_delivered" in event_types
        assert "reply_received" in event_types
        assert events_response.json()["last_event_id"] == events[-1]["id"]

    asyncio.run(run())
