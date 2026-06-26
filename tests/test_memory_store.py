import asyncio
import json

import pytest

from orchlink.broker.storage import MessageStoreBusy
from orchlink.broker.storage.jsonl import JsonlMessageStore
from orchlink.broker.storage.memory import MemoryMessageStore
from orchlink.core.models import Job, JobEventType


def task_message(**overrides):
    data = {
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
        "timeout_seconds": 30,
        "payload": {"intent": "Return PLAN only."},
    }
    data.update(overrides)
    return data


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
        "timeout_seconds": 30,
        "payload": {"summary": "Inspection complete."},
    }


def test_register_agent_creates_agent_and_inbox():
    async def run():
        store = MemoryMessageStore()
        agent = {
            "agent_id": "demo.work",
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
        received = await store.get_next_message("demo.work", wait_seconds=1)

        active_messages = await store.list_active_messages()

        assert queued == {"status": "queued", "message_id": "msg-0001"}
        assert received is not None
        assert received["message_id"] == message["message_id"]
        assert received["status"] == "DELIVERED"
        assert active_messages[0]["status"] == "DELIVERED"

    asyncio.run(run())


def test_jsonl_store_records_mutating_operations(tmp_path):
    async def run():
        journal_path = tmp_path / "orchlink.jsonl"
        store = JsonlMessageStore(journal_path)

        await store.enqueue_message(task_message())
        await store.get_next_message("demo.work", wait_seconds=1)
        await store.save_reply("msg-0001", reply_message())

        records = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
        assert [record["operation"] for record in records] == ["enqueue_message", "save_reply"]
        assert records[-1]["result"]["status"] == "reply_received"
        assert records[-1]["snapshot"]["tasks"]["default:TEST-001"]["status"] == "DONE"

    asyncio.run(run())


def test_jsonl_store_restores_completed_task_results(tmp_path):
    async def run():
        journal_path = tmp_path / "orchlink.jsonl"
        store = JsonlMessageStore(journal_path)
        await store.enqueue_message(task_message(project_id="demo"))
        await store.save_reply("msg-0001", {**reply_message(), "project_id": "demo"})

        restored = JsonlMessageStore(journal_path)
        result = await restored.get_task_result("TEST-001", project_id="demo")
        jobs = await restored.list_jobs(project_id="demo")

        assert result["status"] == "DONE"
        assert result["reply"]["payload"]["summary"] == "Inspection complete."
        assert jobs[0]["status"] == "DONE"

    asyncio.run(run())


def test_jsonl_store_restores_queued_work_to_inbox(tmp_path):
    async def run():
        journal_path = tmp_path / "orchlink.jsonl"
        store = JsonlMessageStore(journal_path)
        await store.enqueue_message(task_message(project_id="demo"))

        restored = JsonlMessageStore(journal_path)
        delivered = await restored.get_next_message("demo.work", wait_seconds=1)

        assert delivered is not None
        assert delivered["task_id"] == "TEST-001"
        assert delivered["status"] == "DELIVERED"

    asyncio.run(run())


def test_task_lifecycle_is_backed_by_canonical_job_model():
    async def run():
        store = MemoryMessageStore()
        await store.enqueue_message(task_message(project_id="demo"))
        queued_job = store._task_jobs["demo:TEST-001"]

        await store.get_next_message("demo.work", wait_seconds=1)
        delivered_job = store._task_jobs["demo:TEST-001"]

        await store.record_activity(
            {
                "project_id": "demo",
                "agent_id": "demo.work",
                "message_id": "msg-0001",
                "task_id": "TEST-001",
                "activity_type": "tool_call",
                "tool_name": "read",
            }
        )
        running_job = store._task_jobs["demo:TEST-001"]

        await store.save_reply("msg-0001", {**reply_message(), "project_id": "demo"})
        done_job = store._task_jobs["demo:TEST-001"]

        assert isinstance(done_job, Job)
        assert [queued_job.status, delivered_job.status, running_job.status, done_job.status] == [
            "QUEUED",
            "DELIVERED",
            "RUNNING",
            "DONE",
        ]
        assert done_job is not running_job

    asyncio.run(run())


def test_task_transition_path_is_deterministic_for_non_adjacent_failure():
    store = MemoryMessageStore()

    assert store._task_transition_path("QUEUED", "FAILED") == [JobEventType.DELIVERED, JobEventType.FAILED]


def test_orphan_reply_does_not_create_canonical_task_job():
    async def run():
        store = MemoryMessageStore()
        await store.save_reply("missing-message", reply_message())

        assert "default:TEST-001" not in store._task_jobs

    asyncio.run(run())


def test_record_activity_marks_task_running_and_lists_activity():
    async def run():
        store = MemoryMessageStore()
        await store.enqueue_message(task_message(project_id="demo", from_agent="demo.lead", to_agent="demo.work"))
        await store.get_next_message("demo.work", wait_seconds=1)

        recorded = await store.record_activity(
            {
                "project_id": "demo",
                "agent_id": "demo.work",
                "message_id": "msg-0001",
                "task_id": "TEST-001",
                "activity_type": "tool_call",
                "tool_name": "read",
                "detail": "apps/api/app/api/users.py",
            }
        )
        task = await store.get_task_result("TEST-001", project_id="demo")
        activity = await store.list_activity(item_id="TEST-001", project_id="demo")
        events = await store.list_events(project_id="demo")

        assert recorded == {"status": "recorded", "activity_id": 1}
        assert task["status"] == "RUNNING"
        assert task["job"]["last_activity_tool"] == "read"
        assert task["job"]["last_activity_preview"] == "apps/api/app/api/users.py"
        assert activity[-1]["activity_type"] == "tool_call"
        assert events[-1]["type"] == "worker_activity"

    asyncio.run(run())


def test_list_jobs_hides_stale_heartbeat_after_completion_but_keeps_active_activity():
    async def run():
        store = MemoryMessageStore()
        await store.enqueue_message(task_message(project_id="demo"))
        await store.get_next_message("demo.work", wait_seconds=1)
        await store.record_activity(
            {
                "project_id": "demo",
                "agent_id": "demo.work",
                "message_id": "msg-0001",
                "task_id": "TEST-001",
                "activity_type": "heartbeat",
                "detail": "Worker still active.",
            }
        )

        running_jobs = await store.list_jobs(project_id="demo")
        assert running_jobs[0]["status"] == "RUNNING"
        assert running_jobs[0]["last_activity_type"] == "heartbeat"
        assert running_jobs[0]["last_activity_preview"] == "Worker still active."

        await store.save_reply("msg-0001", {**reply_message(), "project_id": "demo"})
        done_jobs = await store.list_jobs(project_id="demo")

        assert done_jobs[0]["status"] == "DONE"
        assert "last_activity_type" not in done_jobs[0]
        assert "last_activity_preview" not in done_jobs[0]

    asyncio.run(run())


def test_list_jobs_filters_active_status_kind_and_id():
    async def run():
        store = MemoryMessageStore()
        await store.enqueue_message(task_message(project_id="demo"))
        await store.save_reply("msg-0001", {**reply_message(), "project_id": "demo"})
        await store.enqueue_message(
            task_message(
                project_id="demo",
                message_id="msg-chat",
                correlation_id="req-chat",
                conversation_id="C001",
                task_id=None,
                type="CHAT_START",
                delivery="conversation",
                payload={"mode": "TALK", "message": "Discuss."},
            )
        )

        active = await store.list_jobs(project_id="demo", active=True)
        done_tasks = await store.list_jobs(project_id="demo", status="DONE", kind="task")
        one_talk = await store.list_jobs(project_id="demo", item_id="C001")

        assert [job["conversation_id"] for job in active] == ["C001"]
        assert [job["task_id"] for job in done_tasks] == ["TEST-001"]
        assert one_talk[0]["kind"] == "talk"
        assert one_talk[0]["conversation_id"] == "C001"

    asyncio.run(run())


def test_project_scoped_jobs_allow_same_task_id_in_different_projects():
    async def run():
        store = MemoryMessageStore()
        first = task_message(project_id="p1", from_agent="p1.lead", to_agent="p1.work")
        second = task_message(
            project_id="p2",
            message_id="msg-0002",
            correlation_id="req-0002",
            from_agent="p2.lead",
            to_agent="p2.work",
        )

        await store.enqueue_message(first)
        await store.enqueue_message(second)

        p1_jobs = await store.list_jobs(project_id="p1")
        p2_jobs = await store.list_jobs(project_id="p2")
        p1_task = await store.get_task_result("TEST-001", project_id="p1")
        p2_task = await store.get_task_result("TEST-001", project_id="p2")

        assert len(p1_jobs) == 1
        assert len(p2_jobs) == 1
        assert p1_task["job"]["from_agent"] == "p1.lead"
        assert p2_task["job"]["from_agent"] == "p2.lead"

    asyncio.run(run())


def test_worker_lane_rejects_second_task_until_reply():
    async def run():
        store = MemoryMessageStore()
        await store.enqueue_message(task_message())
        second = task_message(message_id="msg-0002", correlation_id="req-0002", task_id="TEST-002")

        with pytest.raises(MessageStoreBusy) as exc:
            await store.enqueue_message(second)

        assert exc.value.detail["error"] == "worker_busy"
        assert exc.value.detail["blocking_id"] == "TEST-001"

    asyncio.run(run())


def test_worker_lane_accepts_next_task_after_reply():
    async def run():
        store = MemoryMessageStore()
        await store.enqueue_message(task_message())
        await store.save_reply("msg-0001", reply_message())

        queued = await store.enqueue_message(task_message(message_id="msg-0002", correlation_id="req-0002", task_id="TEST-002"))

        assert queued == {"status": "queued", "message_id": "msg-0002"}

    asyncio.run(run())


def test_get_next_message_returns_none_after_wait_timeout():
    async def run():
        store = MemoryMessageStore()

        received = await store.get_next_message("demo.work", wait_seconds=0)

        assert received is None

    asyncio.run(run())


def test_open_conversation_blocks_new_task_to_worker():
    async def run():
        store = MemoryMessageStore()
        chat = task_message(
            message_id="msg-chat",
            conversation_id="C001",
            task_id=None,
            type="CHAT_START",
            delivery="conversation",
            payload={"mode": "TALK", "topic": "Repo?", "message": "What do you think?"},
        )
        await store.enqueue_message(chat)
        await store.save_reply(
            "msg-chat",
            {
                **reply_message(),
                "message_id": "reply-chat",
                "conversation_id": "C001",
                "task_id": None,
                "type": "CHAT_REPLY",
                "status": "DONE",
                "delivery": "conversation",
                "payload": {"mode": "TALK", "summary": "Looks good."},
            },
        )

        with pytest.raises(MessageStoreBusy) as exc:
            await store.enqueue_message(task_message(message_id="msg-0002", correlation_id="req-0002", task_id="TEST-002"))

        assert exc.value.detail["blocking_id"] == "C001"

    asyncio.run(run())


def test_talk_job_keeps_lead_route_and_surfaces_activity_after_reply():
    async def run():
        store = MemoryMessageStore()
        chat = task_message(
            project_id="demo",
            message_id="msg-chat",
            correlation_id="req-chat",
            conversation_id="C001",
            task_id=None,
            type="CHAT_START",
            delivery="conversation",
            payload={"mode": "TALK", "message": "Discuss."},
        )
        reply = {
            **reply_message(),
            "project_id": "demo",
            "message_id": "reply-chat",
            "correlation_id": "req-chat",
            "conversation_id": "C001",
            "task_id": None,
            "from_agent": "demo.work",
            "to_agent": "demo.lead",
            "type": "CHAT_REPLY",
            "status": "DONE",
            "delivery": "conversation",
            "payload": {"mode": "TALK", "summary": "Use memory first."},
        }

        await store.enqueue_message(chat)
        await store.get_next_message("demo.work", wait_seconds=1)
        await store.record_activity(
            {
                "project_id": "demo",
                "agent_id": "demo.work",
                "message_id": "msg-chat",
                "conversation_id": "C001",
                "activity_type": "tool_call",
                "tool_name": "read",
                "detail": "docs/prd.md",
            }
        )
        await store.save_reply("msg-chat", reply)
        jobs = await store.list_jobs(project_id="demo", kind="talk")

        assert jobs[0]["from_agent"] == "demo.lead"
        assert jobs[0]["to_agent"] == "demo.work"
        assert jobs[0]["last_activity_tool"] == "read"
        assert jobs[0]["last_activity_preview"] == "docs/prd.md"

    asyncio.run(run())


def test_chat_start_tracks_conversation():
    async def run():
        store = MemoryMessageStore()
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

        await store.enqueue_message(message)
        conversations = await store.list_conversations()
        jobs = await store.list_jobs()

        assert conversations[0]["conversation_id"] == "C001"
        assert conversations[0]["status"] == "OPEN"
        assert jobs[0]["conversation_id"] == "C001"

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


def test_chat_reply_queues_reply_for_lead_inbox():
    async def run():
        store = MemoryMessageStore()
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
        reply = reply_message()
        reply.update(
            {
                "message_id": "reply-chat",
                "conversation_id": "C001",
                "task_id": None,
                "type": "CHAT_REPLY",
                "status": "DONE",
                "delivery": "conversation",
                "payload": {"mode": "TALK", "summary": "Memory first."},
            }
        )

        await store.enqueue_message(message)
        await store.save_reply("msg-chat", reply)

        delivered = await store.get_next_message("demo.lead", wait_seconds=1)

        assert delivered is not None
        assert delivered["type"] == "CHAT_REPLY"
        assert delivered["conversation_id"] == "C001"

    asyncio.run(run())


def test_save_reply_queues_reply_for_lead_inbox():
    async def run():
        store = MemoryMessageStore()
        await store.enqueue_message(task_message())
        await store.save_reply("msg-0001", reply_message())

        delivered = await store.get_next_message("demo.lead", wait_seconds=1)

        jobs = await store.list_jobs()

        assert delivered is not None
        assert delivered["type"] == "PLAN"
        assert delivered["to_agent"] == "demo.lead"
        assert jobs[0]["task_id"] == "TEST-001"
        assert jobs[0]["status"] == "DONE"

    asyncio.run(run())


def test_wait_for_missing_scoped_task_returns_missing():
    async def run():
        store = MemoryMessageStore()

        result = await store.wait_for_task("NOPE", timeout_seconds=1, project_id="demo")

        assert result == {"status": "missing", "project_id": "demo", "task_id": "NOPE", "error": "Task not found."}

    asyncio.run(run())


def test_wait_for_task_timeout_does_not_mutate_task_status():
    async def run():
        store = MemoryMessageStore()
        await store.enqueue_message(task_message())

        result = await store.wait_for_task("TEST-001", timeout_seconds=0)
        task = await store.get_task_result("TEST-001")

        assert result["status"] == "WAIT_TIMEOUT"
        assert task["status"] == "QUEUED"

    asyncio.run(run())


def test_hard_timeout_expires_active_work_and_frees_worker_lane():
    async def run():
        store = MemoryMessageStore()
        stale = task_message(timeout_seconds=1, created_at="2000-01-01T00:00:00+00:00")
        await store.enqueue_message(stale)

        jobs = await store.list_jobs()
        queued = await store.enqueue_message(task_message(message_id="msg-0002", correlation_id="req-0002", task_id="TEST-002"))

        assert jobs[0]["status"] == "TIMEOUT"
        assert queued == {"status": "queued", "message_id": "msg-0002"}

    asyncio.run(run())


def test_cancel_completed_work_reports_terminal_status():
    async def run():
        store = MemoryMessageStore()
        message = task_message(project_id="demo", task_id="DONE-001")
        reply = reply_message()
        reply.update({"project_id": "demo", "task_id": "DONE-001", "correlation_id": message["correlation_id"], "payload": {"summary": "Done."}})

        await store.enqueue_message(message)
        await store.save_reply(message["message_id"], reply)

        try:
            await store.cancel_work("DONE-001", project_id="demo")
        except ValueError as exc:
            assert "already DONE" in str(exc)
        else:
            raise AssertionError("cancel should reject completed work")

    asyncio.run(run())


def test_cancel_work_skips_queued_message_and_frees_worker_lane():
    async def run():
        store = MemoryMessageStore()
        await store.enqueue_message(task_message())

        cancelled = await store.cancel_work("TEST-001", "No longer needed.")
        skipped = await store.get_next_message("demo.work", wait_seconds=0)
        queued = await store.enqueue_message(task_message(message_id="msg-0002", correlation_id="req-0002", task_id="TEST-002"))
        task = await store.get_task_result("TEST-001")

        assert cancelled["status"] == "cancelled"
        assert skipped is None
        assert queued == {"status": "queued", "message_id": "msg-0002"}
        assert task["status"] == "CANCELLED"

    asyncio.run(run())


def test_update_message_status_marks_task_running():
    async def run():
        store = MemoryMessageStore()
        await store.enqueue_message(task_message())

        updated = await store.update_message_status("msg-0001", "RUNNING")
        task = await store.get_task_result("TEST-001")

        assert updated == {"status": "RUNNING", "message_id": "msg-0001"}
        assert task["status"] == "RUNNING"

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


def test_peer_session_required_rejects_offline_worker():
    async def run():
        store = MemoryMessageStore(require_peer_sessions=True)

        with pytest.raises(MessageStoreBusy) as exc:
            await store.enqueue_message(task_message())

        assert exc.value.detail["error"] == "peer_offline"
        assert exc.value.detail["peer"] == "demo.work"

    asyncio.run(run())


def test_releasing_worker_session_cancels_active_task_and_frees_autostop():
    async def run():
        store = MemoryMessageStore(require_peer_sessions=True)
        session = await store.acquire_session({"project_id": "demo", "agent_id": "demo.work", "role": "work", "pid": 123})
        message = task_message(project_id="demo")

        await store.enqueue_message(message)
        received = await store.get_next_message("demo.work", wait_seconds=1)
        assert received is not None

        released = await store.release_session(session["lease_id"], "worker exited", project_id="demo")
        result = await store.get_task_result("TEST-001", project_id="demo")

        assert released["status"] == "RELEASED"
        assert released["settled_work"] == ["TEST-001"]
        assert result["status"] == "CANCELLED"
        assert result["error"] == "worker exited"
        assert await store.can_auto_stop(project_id="demo") is True

    asyncio.run(run())
