"""M3 job lease + epoch reliability tests.

Covers: lease acquired on dispatch, heartbeat renewal only for current
holder+epoch, stale heartbeat -> 409, stale reply -> 409 (backward-compatible
when lease headers absent), reclaim after expiry with epoch increment, reclaim
idempotency, reclaim rejection when not expired by a different holder, and
terminal transition clearing the lease.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import httpx

from orchlink.broker.main import create_app
from orchlink.broker.settings import Settings
from orchlink.broker.storage.memory import MemoryMessageStore


AUTH = {"X-API-Key": "test-key"}


class ASGITestClient:
    def __init__(self) -> None:
        self.app = create_app(store=MemoryMessageStore(), settings=Settings(api_key="test-key"))

    async def _request(self, method: str, path: str, **kwargs):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)

    def get(self, path: str, **kwargs):
        return asyncio.run(self._request("GET", path, **kwargs))

    def post(self, path: str, **kwargs):
        return asyncio.run(self._request("POST", path, **kwargs))

    @property
    def store(self):
        return self.app.state.store


def _task_message(message_id: str = "msg-0001", task_id: str = "T001", project_id: str = "test") -> dict:
    return {
        "protocol": "orch-a2a-v1",
        "message_id": message_id,
        "correlation_id": f"req-{message_id}",
        "project_id": project_id,
        "conversation_id": f"{project_id}-default",
        "task_id": task_id,
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


def _reply(message: dict, message_id: str = "reply-0001") -> dict:
    return {
        **message,
        "message_id": message_id,
        "from_agent": "test.work",
        "to_agent": "test.lead",
        "type": "RESULT",
        "status": "DONE",
        "turn": 2,
        "requires_reply": False,
        "payload": {"mode": "PLAN", "summary": "Done."},
    }


def _deliver(client: ASGITestClient, task_id: str = "T001", message_id: str = "msg-0001") -> dict:
    """Register, send, and deliver a task; return the delivered message."""
    client.post(
        "/v1/agents/register",
        headers={**AUTH, "X-Orchlink-Project-ID": "test"},
        json={"agent_id": "test.work", "role": "worker", "display_name": "Worker", "project_id": "test", "capabilities": ["tests"]},
    )
    client.post("/v1/messages/send", headers={**AUTH, "X-Orchlink-Project-ID": "test"}, json=_task_message(message_id, task_id))
    next_response = client.get("/v1/agents/test.work/next?wait_seconds=1", headers={**AUTH, "X-Orchlink-Project-ID": "test"})
    return next_response.json()["message"]


def _job(client: ASGITestClient, task_id: str = "T001", project_id: str = "test"):
    return client.store._state.task_jobs[f"{project_id}:{task_id}"]


def _expire_lease(client: ASGITestClient, task_id: str = "T001", project_id: str = "test") -> None:
    """Force the job lease into the past so reclaim treats it as expired."""
    key = f"{project_id}:{task_id}"
    job = client.store._state.task_jobs[key]
    past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    client.store._state.task_jobs[key] = replace(job, lease={**job.lease, "expires_at": past})


def test_lease_acquired_on_dispatch_with_epoch_one():
    client = ASGITestClient()
    delivered = _deliver(client)

    job = _job(client)
    assert job.lease is not None
    assert job.lease["holder"] == "test.work"
    assert job.lease["epoch"] == 1
    assert job.lease["heartbeat_ms"] >= 1000
    # The delivered message carries the lease so the worker can learn its epoch.
    assert delivered["lease"]["epoch"] == 1
    assert delivered["lease"]["holder"] == "test.work"


def test_heartbeat_renews_for_current_holder_and_epoch():
    client = ASGITestClient()
    _deliver(client)
    before = _job(client).lease["expires_at"]

    response = client.post(
        "/v1/jobs/T001/heartbeat",
        headers={**AUTH, "X-Orchlink-Project-ID": "test"},
        json={"holder": "test.work", "epoch": 1, "project_id": "test"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "renewed"
    assert body["lease"]["epoch"] == 1
    assert body["lease"]["expires_at"] >= before


def test_stale_heartbeat_with_wrong_epoch_is_409():
    client = ASGITestClient()
    _deliver(client)

    response = client.post(
        "/v1/jobs/T001/heartbeat",
        headers={**AUTH, "X-Orchlink-Project-ID": "test"},
        json={"holder": "test.work", "epoch": 999, "project_id": "test"},
    )
    assert response.status_code == 409
    # No state change: epoch unchanged.
    assert _job(client).lease["epoch"] == 1


def test_stale_heartbeat_with_wrong_holder_is_409():
    client = ASGITestClient()
    _deliver(client)

    response = client.post(
        "/v1/jobs/T001/heartbeat",
        headers={**AUTH, "X-Orchlink-Project-ID": "test"},
        json={"holder": "someone-else", "epoch": 1, "project_id": "test"},
    )
    assert response.status_code == 409


def test_stale_reply_with_mismatched_epoch_is_409():
    client = ASGITestClient()
    message = _task_message()
    _deliver(client)

    reply = _reply(message)
    response = client.post(
        "/v1/messages/msg-0001/reply",
        headers={**AUTH, "X-Orchlink-Project-ID": "test", "X-Orchlink-Lease-Epoch": "999", "X-Orchlink-Lease-Holder": "test.work"},
        json=reply,
    )
    assert response.status_code == 409
    # The job is still DELIVERED (no state change from the rejected reply).
    assert _job(client).status == "DELIVERED"


def test_reply_with_current_lease_headers_is_accepted():
    client = ASGITestClient()
    message = _task_message()
    delivered = _deliver(client)

    lease = delivered["lease"]
    response = client.post(
        "/v1/messages/msg-0001/reply",
        headers={
            **AUTH,
            "X-Orchlink-Project-ID": "test",
            "X-Orchlink-Lease-Epoch": str(lease["epoch"]),
            "X-Orchlink-Lease-Holder": lease["holder"],
        },
        json=_reply(message),
    )

    assert response.status_code == 200
    assert _job(client).status == "DONE"
    assert _job(client).lease is None


def test_reply_with_invalid_lease_epoch_header_is_400():
    client = ASGITestClient()
    message = _task_message()
    _deliver(client)

    response = client.post(
        "/v1/messages/msg-0001/reply",
        headers={**AUTH, "X-Orchlink-Project-ID": "test", "X-Orchlink-Lease-Epoch": "not-an-int", "X-Orchlink-Lease-Holder": "test.work"},
        json=_reply(message),
    )

    assert response.status_code == 400
    assert _job(client).status == "DELIVERED"


def test_reply_without_lease_headers_is_backward_compatible():
    client = ASGITestClient()
    message = _task_message()
    _deliver(client)

    reply = _reply(message)
    response = client.post(
        "/v1/messages/msg-0001/reply",
        headers={**AUTH, "X-Orchlink-Project-ID": "test"},
        json=reply,
    )
    assert response.status_code == 200
    # Terminal transition cleared the lease.
    assert _job(client).lease is None
    assert _job(client).status == "DONE"


def test_reclaim_after_expiry_increments_epoch_and_reassigns_holder():
    client = ASGITestClient()
    _deliver(client)
    assert _job(client).lease["epoch"] == 1

    _expire_lease(client)
    response = client.post(
        "/v1/jobs/T001/reclaim",
        headers={**AUTH, "X-Orchlink-Project-ID": "test"},
        json={"holder": "test.work-recovered", "project_id": "test"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reclaimed"] is True
    assert body["lease"]["epoch"] == 2
    assert body["lease"]["holder"] == "test.work-recovered"
    assert _job(client).status == "RUNNING"

    # The old holder's heartbeat is now stale (409).
    stale = client.post(
        "/v1/jobs/T001/heartbeat",
        headers={**AUTH, "X-Orchlink-Project-ID": "test"},
        json={"holder": "test.work", "epoch": 1, "project_id": "test"},
    )
    assert stale.status_code == 409
    # The new holder can renew.
    ok = client.post(
        "/v1/jobs/T001/heartbeat",
        headers={**AUTH, "X-Orchlink-Project-ID": "test"},
        json={"holder": "test.work-recovered", "epoch": 2, "project_id": "test"},
    )
    assert ok.status_code == 200


def test_reclaim_is_idempotent_for_same_holder_when_not_expired():
    client = ASGITestClient()
    _deliver(client)
    before = _job(client).lease["epoch"]

    response = client.post(
        "/v1/jobs/T001/reclaim",
        headers={**AUTH, "X-Orchlink-Project-ID": "test"},
        json={"holder": "test.work", "project_id": "test"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["reclaimed"] is False
    assert body["lease"]["epoch"] == before  # unchanged


def test_reclaim_rejects_different_holder_when_not_expired():
    client = ASGITestClient()
    _deliver(client)

    response = client.post(
        "/v1/jobs/T001/reclaim",
        headers={**AUTH, "X-Orchlink-Project-ID": "test"},
        json={"holder": "someone-else", "project_id": "test"},
    )
    assert response.status_code == 409
    assert _job(client).lease["epoch"] == 1


def test_reclaim_unknown_job_is_404():
    client = ASGITestClient()
    response = client.post(
        "/v1/jobs/NOPE/reclaim",
        headers={**AUTH, "X-Orchlink-Project-ID": "test"},
        json={"holder": "x", "project_id": "test"},
    )
    assert response.status_code == 404


def test_terminal_transition_clears_lease_on_cancel():
    client = ASGITestClient()
    _deliver(client)
    assert _job(client).lease is not None

    response = client.post(
        "/v1/jobs/T001/cancel",
        headers={**AUTH, "X-Orchlink-Project-ID": "test"},
        json={"reason": "manual", "project_id": "test"},
    )
    assert response.status_code == 200
    assert _job(client).status == "CANCELLED"
    assert _job(client).lease is None


def test_heartbeat_on_terminal_job_is_409():
    client = ASGITestClient()
    _deliver(client)
    client.post("/v1/jobs/T001/cancel", headers={**AUTH, "X-Orchlink-Project-ID": "test"}, json={"reason": "x", "project_id": "test"})

    response = client.post(
        "/v1/jobs/T001/heartbeat",
        headers={**AUTH, "X-Orchlink-Project-ID": "test"},
        json={"holder": "test.work", "epoch": 1, "project_id": "test"},
    )
    assert response.status_code == 409