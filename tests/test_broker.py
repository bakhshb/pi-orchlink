from fastapi.testclient import TestClient

from orchlink.broker.main import BROKER_CAPABILITIES, VERSION, create_app
from orchlink.broker.settings import Settings
from orchlink.broker.storage.memory import MemoryMessageStore


def make_client():
    app = create_app(
        store=MemoryMessageStore(),
        settings=Settings(api_key="test-key"),
    )
    return TestClient(app)


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
