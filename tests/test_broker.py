import asyncio

import httpx

from orchlink.broker.main import BROKER_CAPABILITIES, VERSION, create_app
from orchlink.broker.settings import Settings
from orchlink.broker.storage.memory import MemoryMessageStore


class ASGITestClient:
    def __init__(self) -> None:
        self.app = create_app(
            store=MemoryMessageStore(),
            settings=Settings(api_key="test-key"),
        )

    async def _request(self, method: str, path: str, **kwargs):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)

    def get(self, path: str, **kwargs):
        return asyncio.run(self._request("GET", path, **kwargs))

    def post(self, path: str, **kwargs):
        return asyncio.run(self._request("POST", path, **kwargs))


def make_client():
    return ASGITestClient()


def auth_headers():
    return {"X-API-Key": "test-key"}


def test_health_is_public():
    client = make_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "orchlink", "version": VERSION, "capabilities": BROKER_CAPABILITIES}


def test_v1_endpoint_rejects_missing_api_key():
    client = make_client()

    response = client.get("/v1/status")

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid API key"}


def test_register_agent():
    client = make_client()
    response = client.post(
        "/v1/agents/register",
        headers=auth_headers(),
        json={
            "agent_id": "demo.work",
            "role": "worker",
            "display_name": "Backend Worker",
            "capabilities": ["backend", "tests"],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "registered", "agent_id": "demo.work"}


def test_send_message_queues_without_waiting():
    client = make_client()
    message = {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-0001",
        "correlation_id": "req-0001",
        "project_id": "test",
        "conversation_id": "test-default",
        "task_id": "T001",
        "from_agent": "test.lead",
        "to_agent": "test.work",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 1800,
        "payload": {"intent": "Return PLAN only."},
    }

    response = client.post("/v1/messages/send", headers=auth_headers(), json=message)
    next_response = client.get("/v1/agents/test.work/next?wait_seconds=1", headers=auth_headers())

    assert response.status_code == 200
    assert response.json() == {"status": "queued", "message_id": "msg-0001"}
    assert next_response.json()["status"] == "message"


def test_broker_rejects_second_worker_message_while_busy():
    client = make_client()
    first = {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-0001",
        "correlation_id": "req-0001",
        "project_id": "test",
        "conversation_id": "test-default",
        "task_id": "T001",
        "from_agent": "test.lead",
        "to_agent": "test.work",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 1800,
        "payload": {"intent": "Return PLAN only."},
    }
    second = {**first, "message_id": "msg-0002", "correlation_id": "req-0002", "task_id": "T002"}

    first_response = client.post("/v1/messages/send", headers=auth_headers(), json=first)
    second_response = client.post("/v1/messages/send", headers=auth_headers(), json=second)

    assert first_response.status_code == 200
    assert second_response.status_code == 409
    assert second_response.json()["detail"]["error"] == "worker_busy"
    assert second_response.json()["detail"]["blocking_id"] == "T001"


def test_chat_start_creates_conversation_job():
    client = make_client()
    message = {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-chat",
        "correlation_id": "req-chat",
        "project_id": "test",
        "conversation_id": "C001",
        "task_id": None,
        "from_agent": "test.lead",
        "to_agent": "test.work",
        "type": "CHAT_START",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 1800,
        "delivery": "conversation",
        "payload": {"mode": "TALK", "topic": "SQLite?", "message": "Challenge memory-only."},
    }

    response = client.post("/v1/messages/send", headers=auth_headers(), json=message)
    jobs_response = client.get("/v1/jobs", headers=auth_headers())

    assert response.status_code == 200
    jobs = jobs_response.json()["jobs"]
    assert jobs[0]["conversation_id"] == "C001"
    assert jobs[0]["status"] == "OPEN"


def test_project_header_filters_jobs_when_query_missing():
    client = make_client()
    first = {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-h-p1",
        "correlation_id": "req-h-p1",
        "project_id": "p1",
        "conversation_id": "p1-tasks",
        "task_id": "T001",
        "from_agent": "p1.lead",
        "to_agent": "p1.work",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 1800,
        "delivery": "async",
        "payload": {"mode": "PLAN", "intent": "P1 task."},
    }
    second = {**first, "message_id": "msg-h-p2", "correlation_id": "req-h-p2", "project_id": "p2", "conversation_id": "p2-tasks", "from_agent": "p2.lead", "to_agent": "p2.work", "payload": {"mode": "PLAN", "intent": "P2 task."}}
    client.post("/v1/messages/send", headers=auth_headers(), json=first)
    client.post("/v1/messages/send", headers=auth_headers(), json=second)

    response = client.get("/v1/jobs", headers={**auth_headers(), "X-Orchlink-Project-ID": "p2"})

    assert response.status_code == 200
    assert response.json()["project_id"] == "p2"
    assert [job["project_id"] for job in response.json()["jobs"]] == ["p2"]


def test_jobs_and_tasks_filter_by_project_id():
    client = make_client()
    first = {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-p1",
        "correlation_id": "req-p1",
        "project_id": "p1",
        "conversation_id": "p1-tasks",
        "task_id": "T001",
        "from_agent": "p1.lead",
        "to_agent": "p1.work",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 1800,
        "delivery": "async",
        "payload": {"mode": "PLAN", "intent": "P1 task."},
    }
    second = {**first, "message_id": "msg-p2", "correlation_id": "req-p2", "project_id": "p2", "conversation_id": "p2-tasks", "from_agent": "p2.lead", "to_agent": "p2.work", "payload": {"mode": "PLAN", "intent": "P2 task."}}

    client.post("/v1/messages/send", headers=auth_headers(), json=first)
    client.post("/v1/messages/send", headers=auth_headers(), json=second)
    jobs_response = client.get("/v1/jobs?project_id=p1", headers=auth_headers())
    task_response = client.get("/v1/tasks/T001?project_id=p2", headers=auth_headers())

    jobs = jobs_response.json()["jobs"]
    assert len(jobs) == 1
    assert jobs[0]["project_id"] == "p1"
    assert task_response.json()["job"]["from_agent"] == "p2.lead"


def test_activity_endpoint_marks_task_running_and_lists_activity():
    client = make_client()
    message = {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-activity",
        "correlation_id": "req-activity",
        "project_id": "demo",
        "conversation_id": "demo-tasks",
        "task_id": "T123",
        "from_agent": "demo.lead",
        "to_agent": "demo.work",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 1800,
        "delivery": "async",
        "payload": {"mode": "REVIEW", "intent": "Review."},
    }
    client.post("/v1/messages/send", headers=auth_headers(), json=message)
    client.get("/v1/agents/demo.work/next?wait_seconds=1", headers=auth_headers())

    response = client.post(
        "/v1/activity",
        headers=auth_headers(),
        json={
            "project_id": "demo",
            "agent_id": "demo.work",
            "message_id": "msg-activity",
            "task_id": "T123",
            "activity_type": "tool_call",
            "tool_name": "bash",
            "detail": "rg organization_id",
        },
    )
    task_response = client.get("/v1/tasks/T123?project_id=demo", headers=auth_headers())
    activity_response = client.get("/v1/activity?item_id=T123&project_id=demo", headers=auth_headers())
    task_activity_response = client.get("/v1/tasks/T123/activity?project_id=demo", headers=auth_headers())

    assert response.json() == {"status": "recorded", "activity_id": 1}
    assert task_response.json()["status"] == "RUNNING"
    assert task_response.json()["job"]["last_activity_tool"] == "bash"
    assert activity_response.json()["activity"][0]["detail"] == "rg organization_id"
    assert task_activity_response.json()["activity"][0]["task_id"] == "T123"


def test_get_and_wait_task_result():
    client = make_client()
    message = {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-0001",
        "correlation_id": "req-0001",
        "project_id": "test",
        "conversation_id": "test-tasks",
        "task_id": "T001",
        "from_agent": "test.lead",
        "to_agent": "test.work",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 1800,
        "delivery": "async",
        "payload": {"mode": "PLAN", "intent": "Return PLAN only."},
    }
    reply = {
        **message,
        "message_id": "reply-0001",
        "from_agent": "test.work",
        "to_agent": "test.lead",
        "type": "RESULT",
        "status": "DONE",
        "turn": 2,
        "requires_reply": False,
        "payload": {"mode": "PLAN", "summary": "Done."},
    }

    client.post("/v1/messages/send", headers=auth_headers(), json=message)
    client.post("/v1/messages/msg-0001/reply", headers=auth_headers(), json=reply)
    get_response = client.get("/v1/tasks/T001", headers=auth_headers())
    wait_response = client.get("/v1/tasks/T001/wait?timeout_seconds=1", headers=auth_headers())

    assert get_response.json()["status"] == "DONE"
    assert get_response.json()["reply"]["payload"]["summary"] == "Done."
    assert wait_response.json()["status"] == "DONE"


def test_status_update_rejects_terminal_status():
    client = make_client()

    response = client.post("/v1/messages/missing/status", headers=auth_headers(), json={"status": "DONE"})

    assert response.status_code == 400
    assert "RUNNING" in response.json()["detail"]


def test_update_message_status_marks_message_running():
    client = make_client()
    message = {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-0001",
        "correlation_id": "req-0001",
        "project_id": "test",
        "conversation_id": "test-default",
        "task_id": "T001",
        "from_agent": "test.lead",
        "to_agent": "test.work",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 1800,
        "delivery": "async",
        "payload": {"mode": "PLAN", "intent": "Return PLAN only."},
    }

    client.post("/v1/messages/send", headers=auth_headers(), json=message)
    response = client.post("/v1/messages/msg-0001/status", headers=auth_headers(), json={"status": "RUNNING"})
    status_response = client.get("/v1/status", headers=auth_headers())

    assert response.status_code == 200
    assert response.json() == {"status": "RUNNING", "message_id": "msg-0001"}
    assert status_response.json()["active_messages"][0]["status"] == "RUNNING"


def test_cancel_work_endpoint_unblocks_worker():
    client = make_client()
    message = {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-0001",
        "correlation_id": "req-0001",
        "project_id": "test",
        "conversation_id": "test-default",
        "task_id": "T001",
        "from_agent": "test.lead",
        "to_agent": "test.work",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 1800,
        "delivery": "async",
        "payload": {"mode": "PLAN", "intent": "Return PLAN only."},
    }
    second = {**message, "message_id": "msg-0002", "correlation_id": "req-0002", "task_id": "T002"}

    client.post("/v1/messages/send", headers=auth_headers(), json=message)
    cancel_response = client.post("/v1/jobs/T001/cancel", headers=auth_headers(), json={"reason": "No longer needed."})
    second_response = client.post("/v1/messages/send", headers=auth_headers(), json=second)

    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "cancelled"
    assert second_response.status_code == 200


def test_get_next_returns_empty_when_no_message_arrives():
    client = make_client()

    response = client.get(
        "/v1/agents/demo.work/next?wait_seconds=0",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {"status": "empty"}


def test_cancel_completed_task_reports_terminal_status():
    client = make_client()
    message = {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-cancel-done",
        "correlation_id": "req-cancel-done",
        "project_id": "demo",
        "conversation_id": "demo-tasks",
        "task_id": "T-DONE",
        "from_agent": "demo.lead",
        "to_agent": "demo.work",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 1800,
        "delivery": "async",
        "payload": {"mode": "PLAN", "intent": "Return result."},
    }
    reply = {
        **message,
        "message_id": "reply-cancel-done",
        "from_agent": "demo.work",
        "to_agent": "demo.lead",
        "type": "RESULT",
        "status": "COMPLETED",
        "requires_reply": False,
        "payload": {"summary": "Done."},
    }
    client.post("/v1/messages/send", headers=auth_headers(), json=message)
    client.post("/v1/messages/msg-cancel-done/reply", headers=auth_headers(), json=reply)

    response = client.post("/v1/jobs/T-DONE/cancel", headers=auth_headers(), json={"project_id": "demo"})

    assert response.status_code == 404
    assert "already DONE" in response.json()["detail"]


def test_jobs_filters_active_status_kind_and_id():
    client = make_client()
    task = {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-jobs-1",
        "correlation_id": "req-jobs-1",
        "project_id": "demo",
        "conversation_id": "demo-tasks",
        "task_id": "T010",
        "from_agent": "demo.lead",
        "to_agent": "demo.work",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 1800,
        "delivery": "async",
        "payload": {"mode": "PLAN", "intent": "Inspect."},
    }
    reply = {
        **task,
        "message_id": "reply-jobs-1",
        "from_agent": "demo.work",
        "to_agent": "demo.lead",
        "type": "RESULT",
        "status": "COMPLETED",
        "requires_reply": False,
        "payload": {"summary": "Done."},
    }
    talk = {
        **task,
        "message_id": "msg-jobs-talk",
        "correlation_id": "req-jobs-talk",
        "conversation_id": "C001",
        "task_id": None,
        "type": "CHAT_START",
        "delivery": "conversation",
        "payload": {"mode": "TALK", "message": "Discuss."},
    }
    client.post("/v1/messages/send", headers=auth_headers(), json=task)
    client.post("/v1/messages/msg-jobs-1/reply", headers=auth_headers(), json=reply)
    client.post("/v1/messages/send", headers=auth_headers(), json=talk)

    active = client.get("/v1/jobs?project_id=demo&active=true", headers=auth_headers()).json()["jobs"]
    done_tasks = client.get("/v1/jobs?project_id=demo&status=DONE&kind=task", headers=auth_headers()).json()["jobs"]
    one_talk = client.get("/v1/jobs?project_id=demo&id=C001", headers=auth_headers()).json()["jobs"]

    assert [job["conversation_id"] for job in active] == ["C001"]
    assert [job["task_id"] for job in done_tasks] == ["T010"]
    assert one_talk[0]["kind"] == "talk"
    assert one_talk[0]["conversation_id"] == "C001"


def test_status_filters_task_and_events_since():
    client = make_client()
    first = {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-status-1",
        "correlation_id": "req-status-1",
        "project_id": "demo",
        "conversation_id": "demo-tasks",
        "task_id": "T010",
        "from_agent": "demo.lead",
        "to_agent": "demo.work",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 1800,
        "delivery": "async",
        "payload": {"mode": "PLAN", "intent": "One."},
    }
    second = {**first, "message_id": "msg-status-2", "correlation_id": "req-status-2", "task_id": "T011", "payload": {"mode": "PLAN", "intent": "Two."}}
    reply = {
        **first,
        "message_id": "reply-status-1",
        "from_agent": "demo.work",
        "to_agent": "demo.lead",
        "type": "RESULT",
        "status": "COMPLETED",
        "requires_reply": False,
        "payload": {"summary": "Done."},
    }
    client.post("/v1/messages/send", headers=auth_headers(), json=first)
    client.post("/v1/messages/msg-status-1/reply", headers=auth_headers(), json=reply)
    since = client.get("/v1/events", headers=auth_headers()).json()["last_event_id"]
    client.post("/v1/messages/send", headers=auth_headers(), json=second)

    response = client.get(f"/v1/status?project_id=demo&task_id=T011&since={since}&limit=10", headers=auth_headers())
    body = response.json()

    assert response.status_code == 200
    assert [job["task_id"] for job in body["jobs"]] == ["T011"]
    assert all(event.get("task_id") == "T011" for event in body["recent_events"])


def test_status_reports_basic_counts():
    client = make_client()
    client.post(
        "/v1/agents/register",
        headers=auth_headers(),
        json={
            "agent_id": "demo.lead",
            "role": "lead",
            "display_name": "Lead",
            "capabilities": [],
        },
    )

    response = client.get("/v1/status", headers=auth_headers())

    body = response.json()

    assert response.status_code == 200
    assert body["broker"] == "ok"
    assert body["agent_count"] == 1
    assert body["active_message_count"] == 0
    assert body["pending_reply_count"] == 0


def test_session_endpoints_and_peer_offline_guard():
    client = ASGITestClient()
    client.app = create_app(store=MemoryMessageStore(require_peer_sessions=True), settings=Settings(api_key="test-key"))

    offline = client.post(
            "/v1/messages/send",
            headers=auth_headers(),
            json={
                "protocol": "orch-a2a-v1",
                "message_id": "msg-offline",
                "correlation_id": "req-offline",
                "conversation_id": "test-tasks",
                "task_id": "T-OFFLINE",
                "from_agent": "test.lead",
                "to_agent": "test.work",
                "type": "TASK",
                "status": "PENDING",
                "turn": 1,
                "max_turns": 6,
                "requires_reply": True,
                "timeout_seconds": 30,
                "delivery": "async",
                "payload": {"mode": "PLAN", "intent": "Inspect."},
            },
        )
    assert offline.status_code == 409
    assert offline.json()["detail"]["error"] == "peer_offline"

    acquired = client.post(
            "/v1/sessions/acquire",
            headers=auth_headers(),
            json={"project_id": "test", "agent_id": "test.work", "role": "work", "pid": 123},
        )
    assert acquired.status_code == 200
    lease_id = acquired.json()["session"]["lease_id"]

    heartbeat = client.post(f"/v1/sessions/{lease_id}/heartbeat", headers=auth_headers(), json={"project_id": "test"})
    assert heartbeat.status_code == 200

    sessions = client.get("/v1/sessions?project_id=test&active=true", headers=auth_headers())
    assert sessions.status_code == 200
    assert len(sessions.json()["sessions"]) == 1

    released = client.post(f"/v1/sessions/{lease_id}/release", headers=auth_headers(), json={"project_id": "test", "reason": "done"})
    assert released.status_code == 200
    assert released.json()["session"]["status"] == "RELEASED"


def test_auto_stop_keeps_shared_broker_alive_when_another_project_is_active(monkeypatch):
    killed = []

    def fake_kill(pid, sig):
        killed.append((pid, sig))

    monkeypatch.setattr("orchlink.broker.main.os.kill", fake_kill)

    async def run():
        app = create_app(store=MemoryMessageStore(), settings=Settings(api_key="test-key", auto_stop=True))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            alpha = await client.post(
                "/v1/sessions/acquire",
                headers=auth_headers(),
                json={"project_id": "alpha", "agent_id": "alpha.work", "role": "work", "pid": 123},
            )
            beta = await client.post(
                "/v1/sessions/acquire",
                headers=auth_headers(),
                json={"project_id": "beta", "agent_id": "beta.work", "role": "work", "pid": 456},
            )
            assert alpha.status_code == 200
            assert beta.status_code == 200

            lease_id = alpha.json()["session"]["lease_id"]
            released = await client.post(
                f"/v1/sessions/{lease_id}/release",
                headers=auth_headers(),
                json={"project_id": "alpha", "reason": "done"},
            )
            assert released.status_code == 200
            await asyncio.sleep(0.7)

    asyncio.run(run())

    assert killed == []


def test_broker_rejects_duplicate_active_session_for_same_named_worker():
    client = make_client()
    first = client.post(
        "/v1/sessions/acquire",
        headers=auth_headers(),
        json={"project_id": "test", "agent_id": "test.review", "role": "work", "worker_name": "review"},
    )
    assert first.status_code == 200

    duplicate = client.post(
        "/v1/sessions/acquire",
        headers=auth_headers(),
        json={"project_id": "test", "agent_id": "test.review", "role": "work", "worker_name": "review"},
    )
    assert duplicate.status_code == 409
    assert "Active session already exists" in duplicate.text

    duplicate_name = client.post(
        "/v1/sessions/acquire",
        headers=auth_headers(),
        json={"project_id": "test", "agent_id": "test.other-review", "role": "work", "worker_name": "review"},
    )
    assert duplicate_name.status_code == 409
    assert "worker name: review" in duplicate_name.text


def test_broker_next_requires_matching_session_lease_when_active_session_exists():
    client = make_client()
    acquired = client.post(
        "/v1/sessions/acquire",
        headers=auth_headers(),
        json={"project_id": "test", "agent_id": "test.review", "role": "work", "worker_name": "review"},
    )
    lease_id = acquired.json()["session"]["lease_id"]
    message = {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-review",
        "correlation_id": "req-review",
        "project_id": "test",
        "conversation_id": "test-tasks",
        "task_id": "R001",
        "from_agent": "test.lead",
        "to_agent": "test.review",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 30,
        "delivery": "async",
        "payload": {"mode": "REVIEW", "intent": "review"},
    }
    queued = client.post("/v1/messages/send", headers=auth_headers(), json=message)
    assert queued.status_code == 200

    no_lease = client.get("/v1/agents/test.review/next?wait_seconds=0&project_id=test", headers=auth_headers())
    assert no_lease.status_code == 409
    stale = client.get("/v1/agents/test.review/next?wait_seconds=0&project_id=test&lease_id=lease-stale", headers=auth_headers())
    assert stale.status_code == 409

    delivered = client.get(
        f"/v1/agents/test.review/next?wait_seconds=1&project_id=test&lease_id={lease_id}",
        headers=auth_headers(),
    )
    assert delivered.status_code == 200
    assert delivered.json()["status"] == "message"
    assert delivered.json()["message"]["task_id"] == "R001"


# --- G009 AC-9: FastAPI DTOs live outside broker/main.py and map to typed
#     domain commands while HTTP request/response shapes stay unchanged.


def test_g009_route_dtos_are_extracted_and_map_to_domain_commands():
    from pathlib import Path

    from orchlink.broker.dto import ActivityBody, SessionAcquireBody, SessionHeartbeatBody, SessionReleaseBody
    from orchlink.core.models import SessionAcquire, SessionHeartbeat, SessionRelease, WorkerActivityInput

    main_source = (Path(__file__).resolve().parent.parent / "src" / "orchlink" / "broker" / "main.py").read_text(encoding="utf-8")
    assert "class SessionAcquireBody" not in main_source
    assert "class ActivityBody" not in main_source
    assert "from orchlink.broker.dto import" in main_source

    acquire = SessionAcquireBody(project_id="demo", agent_id="demo.work", role="work", worker_name="work")
    acquire_command = acquire.to_command()
    assert isinstance(acquire_command, SessionAcquire)
    assert acquire_command.project_id == "demo"
    assert acquire_command.worker_name == "work"

    heartbeat_command = SessionHeartbeatBody(ready=True, thinking="high").to_command("lease-1", project_id="demo")
    assert isinstance(heartbeat_command, SessionHeartbeat)
    assert heartbeat_command.lease_id == "lease-1"
    assert heartbeat_command.project_id == "demo"
    assert heartbeat_command.ready is True
    assert heartbeat_command.thinking == "high"

    release_command = SessionReleaseBody(reason="done").to_command("lease-1", project_id="demo")
    assert isinstance(release_command, SessionRelease)
    assert release_command.reason == "done"

    activity_command = ActivityBody(project_id="demo", agent_id="demo.work", detail="alive").to_command()
    assert isinstance(activity_command, WorkerActivityInput)
    assert activity_command.detail == "alive"


def test_g009_session_and_activity_routes_preserve_http_shapes_with_dto_commands():
    import asyncio

    import httpx

    from orchlink.broker.main import create_app
    from orchlink.broker.settings import Settings
    from orchlink.broker.storage.memory import MemoryMessageStore
    from orchlink.core.models import Session

    async def run():
        store = MemoryMessageStore()
        app = create_app(store=store, settings=Settings(api_key="test-key", store_backend="memory"))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            headers = {"X-API-Key": "test-key"}
            acquired = await client.post(
                "/v1/sessions/acquire",
                headers=headers,
                json={"project_id": "demo", "agent_id": "demo.work", "role": "work", "worker_name": "work"},
            )
            assert acquired.status_code == 200
            acquired_body = acquired.json()
            assert acquired_body["status"] == "active"
            lease_id = acquired_body["session"]["lease_id"]
            assert isinstance(store._state.sessions[lease_id], Session)

            heartbeat = await client.post(
                f"/v1/sessions/{lease_id}/heartbeat",
                headers=headers,
                json={"project_id": "demo", "ready": True, "thinking": "high"},
            )
            assert heartbeat.status_code == 200
            assert heartbeat.json()["session"]["thinking"] == "high"

            activity = await client.post(
                "/v1/activity",
                headers=headers,
                json={"project_id": "demo", "agent_id": "demo.work", "detail": "alive"},
            )
            assert activity.status_code == 200
            assert activity.json() == {"status": "recorded", "activity_id": 1}
            assert store._state.activity[0].detail == "alive"

    asyncio.run(run())
