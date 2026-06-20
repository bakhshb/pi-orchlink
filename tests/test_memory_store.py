import asyncio

from orchlink.broker.storage.memory import MemoryMessageStore


def task_message(**overrides):
    data = {
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
        "timeout_seconds": 30,
        "payload": {"intent": "Return PLAN only."},
    }
    data.update(overrides)
    return data


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
        "timeout_seconds": 30,
        "payload": {"summary": "Inspection complete."},
    }


def test_register_agent_creates_agent_and_inbox():
    async def run():
        store = MemoryMessageStore()
        agent = {
            "agent_id": "worker-backend",
            "role": "worker",
            "display_name": "Backend Worker",
            "capabilities": ["backend"],
        }

        registered = await store.register_agent(agent)

        assert registered == agent
        assert await store.list_agents() == [agent]

    asyncio.run(run())


def test_enqueue_then_get_next_message():
    async def run():
        store = MemoryMessageStore()
        message = task_message()

        queued = await store.enqueue_message(message)
        received = await store.get_next_message("worker-backend", wait_seconds=1)

        assert queued == {"status": "queued", "message_id": "msg-0001"}
        assert received == message

    asyncio.run(run())


def test_get_next_message_returns_none_after_wait_timeout():
    async def run():
        store = MemoryMessageStore()

        received = await store.get_next_message("worker-backend", wait_seconds=0)

        assert received is None

    asyncio.run(run())


def test_reply_resolves_pending_waiter():
    async def run():
        store = MemoryMessageStore()
        await store.enqueue_message(task_message(), create_waiter=True)
        waiter = asyncio.create_task(store.wait_for_reply("req-0001", timeout_seconds=1))
        await store.save_reply("msg-0001", reply_message())

        result = await waiter

        assert result["status"] == "completed"
        assert result["correlation_id"] == "req-0001"
        assert result["reply"]["type"] == "PLAN"

    asyncio.run(run())


def test_save_reply_queues_reply_for_lead_inbox():
    async def run():
        store = MemoryMessageStore()
        await store.enqueue_message(task_message())
        await store.save_reply("msg-0001", reply_message())

        delivered = await store.get_next_message("orchestrator", wait_seconds=1)

        assert delivered is not None
        assert delivered["type"] == "PLAN"
        assert delivered["to_agent"] == "orchestrator"

    asyncio.run(run())


def test_wait_for_reply_times_out():
    async def run():
        store = MemoryMessageStore()

        result = await store.wait_for_reply("missing-correlation", timeout_seconds=0)

        assert result == {
            "status": "timeout",
            "correlation_id": "missing-correlation",
            "error": "Worker did not reply before timeout.",
        }

    asyncio.run(run())
