import asyncio

import httpx

from orchlink.broker.main import create_app
from orchlink.broker.settings import Settings
from orchlink.broker.storage.memory import MemoryMessageStore
from orchlink.bridge.monitor import format_event


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


def test_format_event_includes_chat_and_task_context():
    chat = format_event(
        {
            "time": "2026-06-20T10:12:01+00:00",
            "from_agent": "demo.lead",
            "to_agent": "demo.work",
            "message_type": "CHAT_START",
            "conversation_id": "C001",
            "preview": "Should we add SQLite now?",
        }
    )
    task = format_event(
        {
            "time": "2026-06-20T10:20:01+00:00",
            "from_agent": "demo.lead",
            "to_agent": "demo.work",
            "message_type": "TASK",
            "task_id": "T002",
            "mode": "PLAN",
            "delivery": "async",
            "preview": "Inspect test coverage.",
        }
    )

    activity = format_event(
        {
            "time": "2026-06-20T10:21:01+00:00",
            "type": "worker_activity",
            "from_agent": "demo.work",
            "message_type": "ACTIVITY",
            "task_id": "T002",
            "preview": "bash: rg organization_id",
            "payload": {"activity_type": "tool_call"},
        }
    )

    assert "lead → work CHAT_START C001" in chat
    assert "Should we add SQLite" in chat
    assert "lead → work TASK T002 PLAN ASYNC" in task
    assert "work ACTIVITY T002 tool_call" in activity
    assert "bash: rg organization_id" in activity

    settled = format_event(
        {
            "time": "2026-06-20T10:22:01+00:00",
            "type": "reply_received",
            "from_agent": "demo.work",
            "to_agent": "demo.lead",
            "message_type": "RESULT",
            "task_id": "T002",
            "status": "DONE",
            "preview": "Done.",
        }
    )
    assert "work → lead SETTLED RESULT T002 DONE" in settled


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
