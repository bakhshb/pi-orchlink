from fastapi.testclient import TestClient

from orchlink.broker.main import create_app
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
    assert response.json() == {"status": "ok", "service": "orchlink", "version": "0.1.0"}


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
            "agent_id": "worker-backend",
            "role": "worker",
            "display_name": "Backend Worker",
            "capabilities": ["backend", "tests"],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "registered", "agent_id": "worker-backend"}


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


def test_get_next_returns_empty_when_no_message_arrives():
    client = make_client()

    response = client.get(
        "/v1/agents/worker-backend/next?wait_seconds=0",
        headers=auth_headers(),
    )

    assert response.status_code == 200
    assert response.json() == {"status": "empty"}


def test_status_reports_basic_counts():
    client = make_client()
    client.post(
        "/v1/agents/register",
        headers=auth_headers(),
        json={
            "agent_id": "orchestrator",
            "role": "orchestrator",
            "display_name": "Orchestrator",
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
