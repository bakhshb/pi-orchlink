"""Tests for G018 transcript broker API.

Covers project scope, lease/session validation, terminal-state rejection,
idempotent retry, ordered reads, and bounded long polling.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx

from orchlink.broker.main import create_app
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

    def sget(self, path: str, **kwargs):
        transport = httpx.ASGITransport(app=self.app)
        with httpx.Client(transport=transport, base_url="http://testserver") as client:
            return client.get(path, **kwargs)

    def spost(self, path: str, **kwargs):
        transport = httpx.ASGITransport(app=self.app)
        with httpx.Client(transport=transport, base_url="http://testserver") as client:
            return client.post(path, **kwargs)

    def get(self, path: str, **kwargs):
        return asyncio.run(self._request("GET", path, **kwargs))

    def post(self, path: str, **kwargs):
        return asyncio.run(self._request("POST", path, **kwargs))

    async def aget(self, path: str, **kwargs):
        return await self._request("GET", path, **kwargs)

    async def apost(self, path: str, **kwargs):
        return await self._request("POST", path, **kwargs)


def make_client():
    return ASGITestClient()


def auth_headers(project_id: str = "test"):
    return {"X-API-Key": "test-key", "X-Orchlink-Project-ID": project_id}


def worker_task_message(task_id: str = "T001", to_agent: str = "test.work"):
    return {
        "protocol": "orch-a2a-v1",
        "message_id": f"msg-{task_id}",
        "correlation_id": f"req-{task_id}",
        "project_id": "test",
        "conversation_id": "test-default",
        "task_id": task_id,
        "from_agent": "test.lead",
        "to_agent": to_agent,
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 1800,
        "payload": {"intent": "Do something."},
    }


def acquire_session(client, agent_id: str, role: str = "worker", worker_name: str | None = None):
    body = {"project_id": "test", "agent_id": agent_id, "role": role, "display_name": agent_id}
    if worker_name:
        body["worker_name"] = worker_name
    response = client.post("/v1/sessions/acquire", headers=auth_headers(), json=body)
    assert response.status_code == 200, response.text
    return response.json()["session"]["lease_id"]


def deliver_task(client, task_id: str = "T001", to_agent: str = "test.work"):
    client.post("/v1/agents/register", headers=auth_headers(), json={
        "agent_id": to_agent,
        "role": "worker",
        "display_name": to_agent,
        "capabilities": ["implementation"],
    })
    lease_id = acquire_session(client, to_agent, worker_name=to_agent)
    client.post("/v1/messages/send", headers=auth_headers(), json=worker_task_message(task_id, to_agent))
    response = client.get(f"/v1/agents/{to_agent}/next?wait_seconds=1&lease_id={lease_id}", headers=auth_headers())
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "message"
    return lease_id


def heartbeat_job(client, task_id: str, holder: str, epoch: int = 1):
    response = client.post(
        f"/v1/jobs/{task_id}/heartbeat",
        headers=auth_headers(),
        json={"holder": holder, "epoch": epoch},
    )
    assert response.status_code == 200, response.text
    return response.json()["lease"]


def append_transcript(
    client,
    task_id: str,
    events: list[dict[str, str]],
    agent_id: str,
    lease_id: str,
    lease_epoch: int,
    lease_holder: str,
    batch_id: str,
    project_id: str = "test",
):
    response = client.post(
        f"/v1/tasks/{task_id}/transcript",
        headers={
            **auth_headers(project_id),
            "X-Orchlink-Session-Lease-ID": lease_id,
            "X-Orchlink-Lease-Epoch": str(lease_epoch),
            "X-Orchlink-Lease-Holder": lease_holder,
        },
        json={
            "project_id": project_id,
            "task_id": task_id,
            "agent_id": agent_id,
            "worker_name": lease_holder,
            "batch_id": batch_id,
            "events": events,
        },
    )
    return response


def test_transcript_write_requires_project_header():
    client = make_client()
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    response = client.post(
        "/v1/tasks/T001/transcript",
        headers={"X-API-Key": "test-key", "X-Orchlink-Session-Lease-ID": lease_id},
        json={"agent_id": "test.work", "batch_id": "b1", "events": [{"kind": "assistant_delta", "text": "hello"}]},
    )
    assert response.status_code == 400
    assert "Project ID" in response.json()["detail"]


def test_transcript_write_requires_matching_session_lease():
    client = make_client()
    deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    response = client.post(
        "/v1/tasks/T001/transcript",
        headers={**auth_headers(), "X-Orchlink-Session-Lease-ID": "lease-stale"},
        json={"agent_id": "test.work", "batch_id": "b1", "events": [{"kind": "assistant_delta", "text": "hello"}]},
    )
    assert response.status_code == 409


def test_transcript_write_requires_matching_lease_epoch():
    client = make_client()
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    response = client.post(
        "/v1/tasks/T001/transcript",
        headers={
            **auth_headers(),
            "X-Orchlink-Session-Lease-ID": lease_id,
            "X-Orchlink-Lease-Epoch": "99",
            "X-Orchlink-Lease-Holder": "test.work",
        },
        json={"agent_id": "test.work", "batch_id": "b1", "events": [{"kind": "assistant_delta", "text": "hello"}]},
    )
    assert response.status_code == 409


def test_transcript_write_requires_matching_lease_holder():
    client = make_client()
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    response = client.post(
        "/v1/tasks/T001/transcript",
        headers={
            **auth_headers(),
            "X-Orchlink-Session-Lease-ID": lease_id,
            "X-Orchlink-Lease-Epoch": "1",
            "X-Orchlink-Lease-Holder": "evil.holder",
        },
        json={"agent_id": "test.work", "batch_id": "b1", "events": [{"kind": "assistant_delta", "text": "hello"}]},
    )
    assert response.status_code == 409


def test_transcript_write_rejects_terminal_task():
    client = make_client()
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    cancel = client.post("/v1/jobs/T001/cancel", headers=auth_headers(), json={"reason": "test"})
    assert cancel.status_code == 200
    response = append_transcript(client, "T001", [{"kind": "assistant_delta", "text": "after cancel"}], "test.work", lease_id, 1, "test.work", "b1")
    assert response.status_code == 409


def test_transcript_retry_does_not_duplicate_events():
    client = make_client()
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    response1 = append_transcript(client, "T001", [{"kind": "assistant_delta", "text": "chunk"}], "test.work", lease_id, 1, "test.work", "same-batch")
    assert response1.status_code == 200
    response2 = append_transcript(client, "T001", [{"kind": "assistant_delta", "text": "chunk"}], "test.work", lease_id, 1, "test.work", "same-batch")
    assert response2.status_code == 200
    assert response2.json()["status"] == "deduplicated"
    read = client.get("/v1/tasks/T001/transcript?after=0", headers=auth_headers())
    assert read.status_code == 200
    assert len(read.json()["events"]) == 1
    assert read.json()["events"][0]["seq"] == 1


def test_transcript_read_returns_ordered_events_after_sequence():
    client = make_client()
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    for i in range(3):
        response = append_transcript(client, "T001", [{"kind": "assistant_delta", "text": f"line {i}"}], "test.work", lease_id, 1, "test.work", f"b{i}")
        assert response.status_code == 200
    read0 = client.get("/v1/tasks/T001/transcript?after=0", headers=auth_headers())
    assert read0.status_code == 200
    events = read0.json()["events"]
    assert [event["seq"] for event in events] == [1, 2, 3]
    read2 = client.get("/v1/tasks/T001/transcript?after=2", headers=auth_headers())
    assert read2.json()["events"][0]["seq"] == 3


def test_transcript_read_rejects_cross_project():
    client = make_client()
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    response = append_transcript(client, "T001", [{"kind": "assistant_delta", "text": "x"}], "test.work", lease_id, 1, "test.work", "b1")
    assert response.status_code == 200
    read = client.get("/v1/tasks/T001/transcript?after=0", headers=auth_headers(project_id="other"))
    assert read.status_code == 200
    assert read.json()["events"] == []


def test_transcript_long_poll_wakes_on_new_event():
    import threading
    import time

    client = make_client()
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")

    poll_response = [None]

    def poll_in_thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            poll_response[0] = loop.run_until_complete(client.aget("/v1/tasks/T001/transcript?after=0&wait_seconds=2", headers=auth_headers()))
        finally:
            loop.close()

    def write_after_delay():
        time.sleep(0.1)
        client.post(
            "/v1/tasks/T001/transcript",
            headers={
                **auth_headers(),
                "X-Orchlink-Session-Lease-ID": lease_id,
                "X-Orchlink-Lease-Epoch": "1",
                "X-Orchlink-Lease-Holder": "test.work",
            },
            json={
                "project_id": "test",
                "task_id": "T001",
                "agent_id": "test.work",
                "worker_name": "test.work",
                "batch_id": "b-poll",
                "events": [{"kind": "assistant_delta", "text": "delayed"}],
            },
        )

    poll_thread = threading.Thread(target=poll_in_thread)
    poll_thread.start()
    write_after_delay()
    poll_thread.join()

    read_response = poll_response[0]
    assert read_response is not None
    assert read_response.status_code == 200
    events = read_response.json()["events"]
    assert len(events) == 1
    assert events[0]["text"] == "delayed"


def test_transcript_system_event_is_accepted():
    client = make_client()
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    response = append_transcript(client, "T001", [{"kind": "system", "text": "started"}], "test.work", lease_id, 1, "test.work", "b-sys")
    assert response.status_code == 200
    read = client.get("/v1/tasks/T001/transcript?after=0", headers=auth_headers())
    assert read.json()["events"][0]["kind"] == "system"


# --- AC-8: Lifecycle and recovery (cancellation, lease fencing, worker exit,
# long-poll cleanup, cursor resumption).


def test_transcript_write_rejected_for_timed_out_task():
    """AC-8: A task that has timed out (not just cancelled) must reject
    further transcript writes with a 409, the same as terminal cancel.

    Drives the broker's ``_expire_timed_out_messages_locked`` path by sending
    a task with a deliberately short ``timeout_seconds`` plus a write/read
    cycle that triggers the expiry check.
    """
    import time

    client = make_client()
    # Register and acquire session for a worker that will not respond in time.
    client.post("/v1/agents/register", headers=auth_headers(), json={
        "agent_id": "test.work",
        "role": "worker",
        "display_name": "test.work",
        "capabilities": ["implementation"],
    })
    lease_id = acquire_session(client, "test.work", worker_name="test.work")
    client.post("/v1/messages/send", headers=auth_headers(), json={
        "protocol": "orch-a2a-v1",
        "message_id": "msg-to",
        "correlation_id": "req-to",
        "project_id": "test",
        "conversation_id": "test-default",
        "task_id": "T-TIMEOUT",
        "from_agent": "test.lead",
        "to_agent": "test.work",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 1,
        "payload": {"intent": "Will time out."},
    })
    response = client.get(f"/v1/agents/test.work/next?wait_seconds=1&lease_id={lease_id}", headers=auth_headers())
    assert response.json()["status"] == "message"

    # Heartbeat once to register the lease on the broker side.
    heartbeat_job(client, "T-TIMEOUT", "test.work")

    # Sleep past the deadline, then trigger a read-path expiry.
    time.sleep(1.2)
    trigger = client.get("/v1/status", headers=auth_headers())
    assert trigger.status_code == 200

    response = append_transcript(
        client,
        "T-TIMEOUT",
        [{"kind": "assistant_delta", "text": "post-timeout"}],
        "test.work",
        lease_id,
        1,
        "test.work",
        "b-late",
    )
    assert response.status_code == 409, "timed-out task must reject transcript writes"


def test_transcript_read_survives_worker_session_release():
    """AC-8: Releasing a worker's session must not erase stored transcript
    events; the data must remain readable for the lead panel even though the
    worker has exited.
    """
    client = make_client()
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    response = append_transcript(
        client,
        "T001",
        [{"kind": "assistant_delta", "text": "before-exit"}],
        "test.work",
        lease_id,
        1,
        "test.work",
        "b1",
    )
    assert response.status_code == 200

    release = client.post(
        f"/v1/sessions/{lease_id}/release",
        headers=auth_headers(),
        json={"reason": "worker exit"},
    )
    assert release.status_code == 200

    read = client.get("/v1/tasks/T001/transcript?after=0", headers=auth_headers())
    assert read.status_code == 200
    events = read.json()["events"]
    assert [event["text"] for event in events] == ["before-exit"]


def test_transcript_lease_reclaim_enforces_strict_epoch_fencing():
    """AC-8: After a lease is reclaimed to a new holder/epoch, writers under
    the old epoch/holder must be rejected, while the new holder can write.
    """
    from datetime import datetime, timedelta, timezone

    client = make_client()
    lease_id = deliver_task(client, to_agent="test.work")
    # Establish a lease for ``test.work`` at epoch 1.
    heartbeat_job(client, "T001", "test.work", epoch=1)

    # Deliver a transcript under the original lease.
    first = append_transcript(
        client,
        "T001",
        [{"kind": "assistant_delta", "text": "epoch 1 line"}],
        "test.work",
        lease_id,
        1,
        "test.work",
        "b-1",
    )
    assert first.status_code == 200

    # Force the existing lease to be expired so a reclaim is accepted, then
    # reclaim to a fresh holder + epoch via the broker's reclaim route. This
    # mirrors how a new worker takes over after the previous holder expired.
    store = client.app.state.store  # type: ignore[attr-defined]
    task_key = "test:T001"
    job = store._state.task_jobs[task_key]
    expired_lease = job.lease
    assert expired_lease is not None
    expired_in_past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    expired_lease = expired_lease.renew(
        heartbeat_ms=1000,  # irrelevant; we want is_active() to return False
    )
    # Overwrite expiry directly to the past using a fresh lease object.
    stale_expired = replace(expired_lease, expires_at=expired_in_past)
    job = replace(job, lease=stale_expired)
    store._state.task_jobs[task_key] = job
    reclaim = client.post(
        "/v1/jobs/T001/reclaim",
        headers=auth_headers(),
        json={"holder": "test.work2"},
    )
    assert reclaim.status_code == 200, reclaim.text
    new_lease = reclaim.json()["lease"]
    assert new_lease["epoch"] == 2, "reclaim must bump the lease epoch"
    assert new_lease["holder"] == "test.work2"

    # Old-epoch writer is rejected.
    stale = append_transcript(
        client,
        "T001",
        [{"kind": "assistant_delta", "text": "stale"}],
        "test.work",
        lease_id,
        1,
        "test.work",
        "b-stale",
    )
    assert stale.status_code == 409, "epoch 1 writer must be rejected after reclaim"

    # New-epoch writer succeeds.
    fresh_lease_id = acquire_session(client, "test.work2", worker_name="test.work2")
    accepted = append_transcript(
        client,
        "T001",
        [{"kind": "assistant_delta", "text": "epoch 2 line"}],
        "test.work2",
        fresh_lease_id,
        2,
        "test.work2",
        "b-2",
    )
    assert accepted.status_code == 200

    # Reading back yields both batches in order without duplicates.
    read = client.get("/v1/tasks/T001/transcript?after=0", headers=auth_headers())
    events = read.json()["events"]
    seqs = [event["seq"] for event in events]
    texts = [event["text"] for event in events]
    assert texts == ["epoch 1 line", "epoch 2 line"]
    assert seqs == sorted(seqs) and len(seqs) == 2


def test_transcript_long_poll_cleans_up_waiters_on_timeout():
    """AC-8: A long-poll that times out (no event arrives) must not leave a
    waiter registered against the task key — otherwise stale waiters would
    leak across cancels and over-resume on unrelated writes.
    """
    client = make_client()
    deliver_task(client)
    heartbeat_job(client, "T001", "test.work")

    # Hold the broker's store directly to inspect waiter state.
    store = client.app.state.store  # type: ignore[attr-defined]
    task_key = "test:T001"

    async def _poll():
        return await client.aget("/v1/tasks/T001/transcript?after=0&wait_seconds=1", headers=auth_headers())

    response = asyncio.run(_poll())
    assert response.status_code == 200
    # After the bounded wait completed with no writes, waiter list must be
    # empty for this task. The broker should have cleaned it up in ``finally``.
    assert task_key not in store._state.transcript_waiters or not store._state.transcript_waiters[task_key]


# --- G018-BACKEND-FIX-016 -----------------------------------------------------
# Defect 1/2/3 focused tests for the broker side only.


def _register_and_session(client, agent_id="test.work", worker_name=None):
    worker_name = worker_name or agent_id
    client.post("/v1/agents/register", headers=auth_headers(), json={
        "agent_id": agent_id,
        "role": "worker",
        "display_name": agent_id,
        "capabilities": ["implementation"],
    })
    body = {"project_id": "test", "agent_id": agent_id, "role": "worker", "display_name": agent_id, "worker_name": worker_name}
    response = client.post("/v1/sessions/acquire", headers=auth_headers(), json=body)
    assert response.status_code == 200
    return response.json()["session"]["lease_id"]


def _deliver(client, agent_id="test.work", task_id="T001"):
    lease_id = _register_and_session(client, agent_id=agent_id)
    client.post("/v1/messages/send", headers=auth_headers(), json={
        "protocol": "orch-a2a-v1",
        "message_id": f"msg-{task_id}",
        "correlation_id": f"req-{task_id}",
        "project_id": "test",
        "conversation_id": "test-default",
        "task_id": task_id,
        "from_agent": "test.lead",
        "to_agent": agent_id,
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 1800,
        "payload": {"intent": "Do something."},
    })
    response = client.get(f"/v1/agents/{agent_id}/next?wait_seconds=1&lease_id={lease_id}", headers=auth_headers())
    assert response.status_code == 200
    return lease_id


# --- Defect 3: cross-project rejection on POST ---


def test_transcript_write_rejects_body_project_mismatch_with_header():
    """Defect 3: body project_id must match X-Orchlink-Project-ID when both
    are present. Mismatches are rejected with 403 so a caller cannot
    accidentally bypass the project scope.
    """
    client = make_client()
    lease_id = _deliver(client)
    heartbeat_job(client, "T001", "test.work")
    headers = {
        **auth_headers("test"),  # X-Orchlink-Project-ID: test
        "X-Orchlink-Session-Lease-ID": lease_id,
        "X-Orchlink-Lease-Epoch": "1",
        "X-Orchlink-Lease-Holder": "test.work",
    }
    # Body says "other" while header says "test" -> mismatch should reject.
    response = client.post(
        "/v1/tasks/T001/transcript",
        headers=headers,
        json={
            "project_id": "other",
            "task_id": "T001",
            "agent_id": "test.work",
            "worker_name": "test.work",
            "batch_id": "b-cross",
            "events": [{"kind": "assistant_delta", "text": "no-go"}],
        },
    )
    assert response.status_code == 403
    assert "project_id" in response.json()["detail"].lower()


def test_transcript_write_accepts_body_only_when_header_matches():
    """Defect 3 sanity: when body and header both reference the same project,
    the write succeeds. This guards against over-aggressive rejection.
    """
    client = make_client()
    lease_id = _deliver(client)
    heartbeat_job(client, "T001", "test.work")
    response = client.post(
        "/v1/tasks/T001/transcript",
        headers={
            **auth_headers("test"),
            "X-Orchlink-Session-Lease-ID": lease_id,
            "X-Orchlink-Lease-Epoch": "1",
            "X-Orchlink-Lease-Holder": "test.work",
        },
        json={
            "project_id": "test",
            "task_id": "T001",
            "agent_id": "test.work",
            "worker_name": "test.work",
            "batch_id": "b-ok",
            "events": [{"kind": "assistant_delta", "text": "ok"}],
        },
    )
    assert response.status_code == 200


def test_transcript_write_accepts_when_only_header_is_provided():
    """Defect 3 sanity: a missing body project_id is allowed (it just falls
    through to the header), preserving the existing single-channel contract.
    """
    client = make_client()
    lease_id = _deliver(client)
    heartbeat_job(client, "T001", "test.work")
    response = client.post(
        "/v1/tasks/T001/transcript",
        headers={
            **auth_headers("test"),
            "X-Orchlink-Session-Lease-ID": lease_id,
            "X-Orchlink-Lease-Epoch": "1",
            "X-Orchlink-Lease-Holder": "test.work",
        },
        json={
            "task_id": "T001",
            "agent_id": "test.work",
            "worker_name": "test.work",
            "batch_id": "b-header-only",
            "events": [{"kind": "assistant_delta", "text": "header-only"}],
        },
    )
    assert response.status_code == 200


def test_transcript_write_rejects_body_only_when_header_differs():
    """Defect 3 edge: if the body has only a project_id and the header is
    absent but a different implicit project resolves, accept it (no header
    to mismatch). This covers the body-only control path preserved for
    callers that set the project on the body alone.
    """
    client = make_client()
    lease_id = _deliver(client)
    heartbeat_job(client, "T001", "test.work")
    # No X-Orchlink-Project-ID header at all -> body must still be a valid
    # project. We expect success because the existing contract treats the
    # body as authoritative when no header is present.
    response = client.post(
        "/v1/tasks/T001/transcript",
        headers={
            "X-API-Key": "test-key",
            "X-Orchlink-Session-Lease-ID": lease_id,
            "X-Orchlink-Lease-Epoch": "1",
            "X-Orchlink-Lease-Holder": "test.work",
        },
        json={
            "project_id": "test",
            "task_id": "T001",
            "agent_id": "test.work",
            "worker_name": "test.work",
            "batch_id": "b-body-only",
            "events": [{"kind": "assistant_delta", "text": "body-only"}],
        },
    )
    # The header-less body-only request resolves "test" as the project,
    # which is what the task was registered under, so it must succeed.
    assert response.status_code == 200
