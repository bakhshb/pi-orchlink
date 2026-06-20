import asyncio

import httpx

from orchlink.broker.main import create_app
from orchlink.broker.settings import Settings
from orchlink.broker.storage.memory import MemoryMessageStore


HEADERS = {"X-API-Key": "test-key"}


def task_message():
    return {
        "protocol": "orchlink-a2a-v1",
        "message_id": "msg-0001",
        "correlation_id": "req-0001",
        "conversation_id": "orchlink-test",
        "task_id": "TEST-001",
        "from_agent": "orchestrator",
        "to_agent": "worker-backend",
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
        "protocol": "orchlink-a2a-v1",
        "message_id": "reply-0001",
        "correlation_id": "req-0001",
        "conversation_id": "orchlink-test",
        "task_id": "TEST-001",
        "from_agent": "worker-backend",
        "to_agent": "orchestrator",
        "type": "PLAN",
        "status": "COMPLETED",
        "turn": 2,
        "max_turns": 6,
        "requires_reply": False,
        "timeout_seconds": 1,
        "payload": {"summary": "Inspection complete."},
    }


def test_send_and_wait_completes_after_worker_reply():
    async def run():
        app = create_app(
            store=MemoryMessageStore(),
            settings=Settings(api_key="test-key"),
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            send_task = asyncio.create_task(
                client.post(
                    "/v1/messages/send-and-wait",
                    headers=HEADERS,
                    json=task_message(),
                )
            )

            next_response = await client.get(
                "/v1/agents/worker-backend/next?wait_seconds=1",
                headers=HEADERS,
            )
            assert next_response.status_code == 200
            assert next_response.json()["status"] == "message"

            reply_response = await client.post(
                "/v1/messages/msg-0001/reply",
                headers=HEADERS,
                json=reply_message(),
            )
            assert reply_response.status_code == 200
            assert reply_response.json() == {
                "status": "reply_received",
                "correlation_id": "req-0001",
            }

            final_response = await send_task
            assert final_response.status_code == 200
            assert final_response.json()["status"] == "completed"
            assert final_response.json()["reply"]["type"] == "PLAN"

    asyncio.run(run())


def test_send_and_wait_returns_timeout_when_worker_does_not_reply(monkeypatch):
    async def fake_wait_for_reply(self, correlation_id, timeout_seconds):
        return {
            "status": "timeout",
            "correlation_id": correlation_id,
            "error": "Worker did not reply before timeout.",
        }

    async def run():
        store = MemoryMessageStore()
        monkeypatch.setattr(store, "wait_for_reply", fake_wait_for_reply.__get__(store, MemoryMessageStore))
        app = create_app(store=store, settings=Settings(api_key="test-key"))
        message = task_message()
        message["correlation_id"] = "req-timeout"
        message["timeout_seconds"] = 1
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/v1/messages/send-and-wait",
                headers=HEADERS,
                json=message,
            )

        assert response.status_code == 200
        assert response.json() == {
            "status": "timeout",
            "correlation_id": "req-timeout",
            "error": "Worker did not reply before timeout.",
        }

    asyncio.run(run())
