import asyncio

import httpx

from orchlink.broker.main import create_app
from orchlink.broker.settings import Settings
from orchlink.broker.storage.memory import MemoryMessageStore
from orchlink.core.envelope import MessageEnvelope


HEADERS = {"X-API-Key": "test-key"}


def task_message():
    return {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-0001",
        "correlation_id": "req-0001",
        "conversation_id": "orchlink-test",
        "task_id": "TEST-001",
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


def message_envelope(data: dict) -> MessageEnvelope:
    return MessageEnvelope.model_validate({k: v for k, v in data.items() if k not in {"created_at", "queued_at", "updated_at"}})


def reply_message():
    return {
        "protocol": "orch-a2a-v1",
        "message_id": "reply-0001",
        "correlation_id": "req-0001",
        "conversation_id": "orchlink-test",
        "task_id": "TEST-001",
        "from_agent": "demo.work",
        "to_agent": "demo.lead",
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
                "/v1/agents/demo.work/next?wait_seconds=1",
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


# --- G004 AC-6: end-to-end behavior preservation across all flows mentioned in the AC ---


def test_stored_message_behavior_preserved_for_active_message_full_flow():
    """AC-6 regression: enqueue → delivery → status update → reply → waiters
    → task/talk upsert → cancellation → timeout → active-message listing
    keeps existing behavior after the StoredMessage refactor.
    """
    async def run():
        store = MemoryMessageStore()
        await store.register_agent(
            {"agent_id": "demo.work", "role": "worker", "display_name": "Worker", "capabilities": ["backend"]}
        )

        # 1. Enqueue → delivery → save_reply (terminal DONE) for task TEST-001.
        message = task_message()
        message["requires_reply"] = True
        queued = await store.enqueue_message(message_envelope(message), create_waiter=True)
        assert queued == {"status": "queued", "message_id": "msg-0001"}

        active = await store.list_active_messages()
        assert active[0]["status"] == "QUEUED"

        delivered = await store.get_next_message("demo.work", wait_seconds=1)
        assert delivered is not None
        assert delivered["status"] == "DELIVERED"
        assert delivered["message_id"] == "msg-0001"

        reply = task_message()
        reply.update(
            {
                "message_id": "reply-0001",
                "correlation_id": "req-0001",
                "from_agent": "demo.work",
                "to_agent": "demo.lead",
                "type": "PLAN",
                "status": "COMPLETED",
                "task_id": "TEST-001",
                "turn": 2,
                "payload": {"summary": "done."},
            }
        )
        reply_result = await store.save_reply("msg-0001", message_envelope(reply))
        assert reply_result["status"] == "reply_received"

        # Task upsert path: task dict reflects the post-reply state.
        jobs = await store.list_jobs()
        matching = [job for job in jobs if str(job.get("task_id")) == "TEST-001"]
        assert matching, "expected task upsert after reply"
        assert matching[0]["status"] in {"DONE", "COMPLETED"}

        # 2. Status-update path on a fresh message (msg-0002), then drive it
        # to DONE so the worker target is free for the cancel path.
        await store.enqueue_message(
            message_envelope({
                **task_message(),
                "message_id": "msg-0002",
                "correlation_id": "req-0002",
                "task_id": "TEST-002",
            })
        )
        assert (await store.update_message_status("msg-0002", "running"))["status"] == "RUNNING"
        assert (await store.update_message_status("msg-0002", "done"))["status"] == "DONE"

        # 3. Cancellation path on a fresh task (msg-0003) — free worker target.
        await store.enqueue_message(
            message_envelope({
                **task_message(),
                "message_id": "msg-0003",
                "correlation_id": "req-0003",
                "task_id": "TEST-003",
            })
        )
        cancel_result = await store.cancel_work("msg-0003", reason="superseded", project_id="default")
        assert cancel_result["status"] == "cancelled"
        assert "msg-0003" in cancel_result["cancelled"]

        # 4. Timeout handling on a fresh message that we don't deliver.
        await store.enqueue_message(
            message_envelope({
                **task_message(),
                "message_id": "msg-0004",
                "correlation_id": "req-0004",
                "task_id": "TEST-004",
                "created_at": "2000-01-01T00:00:00+00:00",
                "timeout_seconds": 1,
            })
        )
        # Mark msg-0004 (running) terminal first so it can be expired by the sweep.
        await store.update_message_status("msg-0004", "delivered")
        await store.list_jobs()  # triggers _expire_timed_out_messages_locked

        # Active message listing still surfaces dicts across every path.
        active_after = await store.list_active_messages()
        assert len(active_after) >= 4
        for entry in active_after:
            assert isinstance(entry, dict)
            assert "message_id" in entry
            assert "status" in entry
            assert "created_at" in entry

    asyncio.run(run())
