import asyncio
import sys

import httpx

from orchlink.broker.main import create_app
from orchlink.broker.settings import Settings
from orchlink.broker.storage.memory import MemoryMessageStore
from orchlink.bridge.worker_bridge import build_reply, detect_reply_type, process_one_message


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
        "timeout_seconds": 30,
        "payload": {"intent": "Return PLAN only."},
    }


def chat_message():
    message = task_message()
    message.update(
        {
            "message_id": "msg-chat",
            "conversation_id": "C001",
            "task_id": None,
            "type": "CHAT_START",
            "delivery": "conversation",
            "payload": {"mode": "TALK", "topic": "SQLite?", "message": "Challenge memory-only."},
        }
    )
    return message


def worker_config():
    return {
        "agent_id": "worker-backend",
        "role": "worker",
        "display_name": "Backend Worker",
        "broker_url": "http://testserver",
        "api_key": "test-key",
        "agent_timeout_seconds": 5,
        "scope": {"allowed": ["apps/api/**"], "forbidden": ["apps/web/**"]},
        "command": {
            "mode": "command",
            "argv": [
                sys.executable,
                "-c",
                "print('TYPE: PLAN'); print('SUMMARY: fake worker finished')",
            ],
        },
    }


def test_detect_reply_type_reads_structured_type_line():
    assert detect_reply_type("TYPE: PLAN\nSUMMARY: done") == "PLAN"
    assert detect_reply_type("TYPE: BLOCKER\nSUMMARY: blocked") == "BLOCKER"
    assert detect_reply_type("unstructured output") == "RESULT"


def test_build_reply_turns_successful_output_into_plan_reply():
    from orchlink.bridge.agent_runner import AgentRunResult

    result = AgentRunResult(
        stdout="TYPE: PLAN\nSUMMARY: done\n",
        stderr="",
        exit_code=0,
        timed_out=False,
    )

    reply = build_reply(task_message(), worker_config(), result)

    assert reply["from_agent"] == "worker-backend"
    assert reply["to_agent"] == "orchestrator"
    assert reply["type"] == "PLAN"
    assert reply["status"] == "DONE"
    assert reply["payload"]["stdout"] == "TYPE: PLAN\nSUMMARY: done\n"


def test_build_reply_turns_chat_start_into_chat_reply():
    from orchlink.bridge.agent_runner import AgentRunResult

    result = AgentRunResult(
        stdout="TYPE: CHAT_REPLY\nPOSITION: memory first\n",
        stderr="",
        exit_code=0,
        timed_out=False,
    )

    reply = build_reply(chat_message(), worker_config(), result)

    assert reply["type"] == "CHAT_REPLY"
    assert reply["task_id"] is None
    assert reply["delivery"] == "conversation"
    assert reply["payload"]["mode"] == "TALK"


def test_build_reply_turns_command_failure_into_blocker():
    from orchlink.bridge.agent_runner import AgentRunResult

    result = AgentRunResult(
        stdout="",
        stderr="command failed",
        exit_code=2,
        timed_out=False,
    )

    reply = build_reply(task_message(), worker_config(), result)

    assert reply["type"] == "BLOCKER"
    assert reply["status"] == "FAILED"
    assert reply["payload"]["stderr"] == "command failed"


def test_process_one_message_handles_chat_start():
    from orchlink.bridge.agent_runner import AgentRunResult

    class FakeConnector:
        async def run_worker_prompt(self, prompt, timeout_seconds):
            assert "Talk Mode conversation" in prompt
            return AgentRunResult(
                stdout="TYPE: CHAT_REPLY\nPOSITION: memory first\n",
                stderr="",
                exit_code=0,
                timed_out=False,
            )

    async def run():
        store = MemoryMessageStore()
        await store.enqueue_message(chat_message())
        app = create_app(store=store, settings=Settings(api_key="test-key"))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/v1/agents/worker-backend/next?wait_seconds=1",
                headers={"X-API-Key": "test-key"},
            )
            message = response.json()["message"]
            reply_response = await process_one_message(client, worker_config(), message, connector=FakeConnector())

        assert reply_response["status"] == "reply_received"
        delivered = await store.get_next_message("orchestrator", wait_seconds=1)
        assert delivered["type"] == "CHAT_REPLY"

    asyncio.run(run())


def test_process_one_message_runs_command_and_replies_to_broker():
    async def run():
        store = MemoryMessageStore()
        await store.enqueue_message(task_message(), create_waiter=True)
        app = create_app(store=store, settings=Settings(api_key="test-key"))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get(
                "/v1/agents/worker-backend/next?wait_seconds=1",
                headers={"X-API-Key": "test-key"},
            )
            message = response.json()["message"]
            reply_response = await process_one_message(client, worker_config(), message)

        assert reply_response["status"] == "reply_received"
        wait_result = await store.wait_for_reply("req-0001", timeout_seconds=1)
        assert wait_result["status"] == "completed"
        assert wait_result["reply"]["type"] == "PLAN"

    asyncio.run(run())
