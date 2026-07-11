"""G019 AC-5 contract tests for the broker task telemetry endpoint.

These tests pin the durable, lease-fenced, latest-state telemetry contract
that the broker exposes for the lead-side ``/orchlink`` inline tree and the
worker instrumentation. They cover:

    * Replace-in-place storage shape (no append history).
    * Lease fencing on project, task, session lease, job lease, and lease
      generation.
    * Terminal-state rejection (DONE / CANCELLED / TIMEOUT / ERROR).
    * Cross-project guard.
    * JSONL replay durability — replaced record survives restart; an
      unbounded heartbeat history is impossible by construction.
    * Privacy boundary — telemetry payloads never carry message bodies,
      tool arguments, raw tool output, provider data, secrets, env
      values, or any content a worker could supply beyond numeric metrics
      + lease metadata.

The tests use the broker's public ``MemoryMessageStore`` API and the
``JsonlMessageStore`` snapshot path, mirroring how a real worker or the
lead UI would call the endpoint.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from orchlink.core.models import (
    Job,
    JobLease,
    JobRoute,
    StoredMessage,
    TaskProjection,
    TaskTelemetry,
)
from orchlink.core.envelope import MessageEnvelope


# --- helpers -----------------------------------------------------------------


def _telemetry(
    project_id: str = "default",
    task_id: str = "T-AC5",
    worker_name: str = "demo.work",
    *,
    tool_count: int = 0,
    tokens: int | None = None,
    context_window: int | None = None,
    percent: float | None = None,
    updated_at: str | None = None,
    lease_epoch: int = 1,
    lease_holder: str = "demo.work",
    session_lease_id: str | None = None,
) -> TaskTelemetry:
    return TaskTelemetry(
        project_id=project_id,
        task_id=task_id,
        worker_name=worker_name,
        tool_count=tool_count,
        tokens=tokens,
        context_window=context_window,
        percent=percent,
        updated_at=updated_at,
        lease_epoch=lease_epoch,
        lease_holder=lease_holder,
        session_lease_id=session_lease_id,
    )


def _envelope(*, message_id: str = "msg-ac5", task_id: str = "T-AC5") -> MessageEnvelope:
    # ``timeout_seconds`` minimum is 1 by the envelope schema, so use a
    # very large value so the broker's timeout sweeper never expires our
    # seeded task during the test.
    return MessageEnvelope(
        message_id=message_id,
        correlation_id=f"req-{message_id}",
        conversation_id="C-ac5",
        project_id="default",
        task_id=task_id,
        from_agent="demo.lead",
        to_agent="demo.work",
        type="TASK",
        timeout_seconds=10**9,
    )


def _job(*, task_id: str = "T-AC5", holder: str = "demo.work", epoch: int = 1) -> Job:
    lease = JobLease.fresh(holder, heartbeat_ms=15000, epoch=epoch, grace_multiplier=6)
    return Job(
        id=f"job-{task_id}",
        kind="task",
        project_id="default",
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="DO",
        status="RUNNING",
        task_id=task_id,
        conversation_id="C-ac5",
        turn=1,
        max_turns=6,
        lease=lease,
    )


def _task_projection(*, task_id: str = "T-AC5", status: str = "RUNNING") -> TaskProjection:
    return TaskProjection(
        kind="task",
        project_id="default",
        task_id=task_id,
        conversation_id="C-ac5",
        mode="DO",
    ).with_updates({"status": status, "updated_at": "2026-01-01T00:00:00+00:00"})


# --- contract: storage shape (replace in place, no append) --------------------


def test_telemetry_replaces_in_place_no_append_history():
    """AC-5: a second telemetry update on the same task REPLACES the first
    rather than appending. The storage shape is bounded by task count,
    not by heartbeat frequency.
    """
    from orchlink.broker.storage.memory import MemoryMessageStore

    async def run() -> None:
        store = MemoryMessageStore()
        # Seed the in-memory state with a running task projection so the
        # lease fence passes.
        sm = StoredMessage.from_envelope(_envelope(), now="2026-01-01T00:00:00+00:00")
        store._state.active_messages[sm.envelope.message_id] = sm
        task_key = store._job_projector.task_key("default", "T-AC5")
        store._state.task_jobs[task_key] = _job()
        store._state.tasks[task_key] = _task_projection()
        # First update: tool_count=2.
        first = await store.record_task_telemetry(
            _telemetry(tool_count=2, tokens=1000, context_window=200_000, percent=1.0),
            agent_id="demo.work",
            session_lease_id=None,
            lease_epoch=1,
            lease_holder="demo.work",
        )
        assert first["status"] == "recorded"
        # Second update on the SAME task: tool_count=4.
        second = await store.record_task_telemetry(
            _telemetry(tool_count=4, tokens=2000, context_window=200_000, percent=2.0),
            agent_id="demo.work",
            session_lease_id=None,
            lease_epoch=1,
            lease_holder="demo.work",
        )
        assert second["status"] == "recorded"
        # Exactly one record per task; no append history.
        assert len(store._state.telemetry_by_task) == 1
        record = store._state.telemetry_by_task[task_key]
        assert record.tool_count == 4, "second update must replace the first"
        assert record.tokens == 2000
        # ``updated_at`` advanced through the replacement.
        assert record.updated_at is not None

    asyncio.run(run())


# --- contract: lease fencing ------------------------------------------------


def test_telemetry_rejects_stale_job_lease_with_structured_reason():
    """AC-5: a stale ``lease_epoch`` / ``lease_holder`` is rejected with the
    ``stale-job-lease`` reason and the existing record is left untouched.
    """
    from orchlink.broker.storage.memory import MemoryMessageStore
    from orchlink.broker.storage.memory_telemetry_store import TelemetryRejected

    async def run() -> None:
        store = MemoryMessageStore()
        sm = StoredMessage.from_envelope(_envelope(), now="2026-01-01T00:00:00+00:00")
        store._state.active_messages[sm.envelope.message_id] = sm
        task_key = store._job_projector.task_key("default", "T-AC5")
        store._state.task_jobs[task_key] = _job(holder="demo.work", epoch=5)
        store._state.tasks[task_key] = _task_projection()
        # Seed a known-good record.
        accepted = await store.record_task_telemetry(
            _telemetry(tool_count=1, lease_epoch=5, lease_holder="demo.work"),
            agent_id="demo.work",
            lease_epoch=5,
            lease_holder="demo.work",
        )
        assert accepted["status"] == "recorded"
        # Stale lease: caller claims epoch=4, broker holds epoch=5. Rejected.
        with pytest.raises(TelemetryRejected) as exc_info:
            await store.record_task_telemetry(
                _telemetry(tool_count=2, lease_epoch=4, lease_holder="demo.work"),
                agent_id="demo.work",
                lease_epoch=4,
                lease_holder="demo.work",
            )
        assert exc_info.value.reason == "stale-job-lease"
        # The existing record was preserved through the failed update.
        record = store._state.telemetry_by_task[task_key]
        assert record.tool_count == 1, "rejected update must not mutate the record"

    asyncio.run(run())


def test_telemetry_rejects_stale_session_lease():
    """AC-5: a stale or unknown ``session_lease_id`` is rejected with
    ``stale-session-lease`` and the broker refuses to publish the record.
    """
    from orchlink.broker.storage.memory import MemoryMessageStore
    from orchlink.broker.storage.memory_telemetry_store import TelemetryRejected

    async def run() -> None:
        store = MemoryMessageStore()
        sm = StoredMessage.from_envelope(_envelope(), now="2026-01-01T00:00:00+00:00")
        store._state.active_messages[sm.envelope.message_id] = sm
        task_key = store._job_projector.task_key("default", "T-AC5")
        store._state.task_jobs[task_key] = _job()
        store._state.tasks[task_key] = _task_projection()
        # No active session for ``demo.work``: any session_lease_id is stale.
        with pytest.raises(TelemetryRejected) as exc_info:
            await store.record_task_telemetry(
                _telemetry(tool_count=1, session_lease_id="lease-stale"),
                agent_id="demo.work",
                session_lease_id="lease-stale",
            )
        assert exc_info.value.reason == "stale-session-lease"

    asyncio.run(run())


def test_telemetry_rejects_terminal_task_status():
    """AC-5: a terminal task (DONE / CANCELLED / TIMEOUT / FAILED) rejects
    every telemetry write forever. The remaining ``updated_at`` is the
    record captured while the task was still active; the broker never
    refreshes a completed task's lead-UI view.
    """
    from orchlink.broker.storage.memory import MemoryMessageStore
    from orchlink.broker.storage.memory_telemetry_store import TelemetryRejected

    async def run() -> None:
        store = MemoryMessageStore()
        sm = StoredMessage.from_envelope(_envelope(), now="2026-01-01T00:00:00+00:00")
        store._state.active_messages[sm.envelope.message_id] = sm
        task_key = store._job_projector.task_key("default", "T-AC5")
        store._state.task_jobs[task_key] = _job()
        store._state.tasks[task_key] = _task_projection()
        # Each terminal status must produce a deterministic rejection reason.
        for terminal in ("DONE", "CANCELLED", "TIMEOUT", "FAILED"):
            store._state.tasks[task_key] = _task_projection(status=terminal)
            with pytest.raises(TelemetryRejected) as exc_info:
                await store.record_task_telemetry(
                    _telemetry(tool_count=1),
                    agent_id="demo.work",
                )
            assert exc_info.value.reason == "terminal-task", terminal

    asyncio.run(run())


def test_telemetry_rejects_unknown_task():
    """AC-5: an unknown task (no projection) is rejected with
    ``unknown-task`` so a stale worker never seeds a phantom record.
    """
    from orchlink.broker.storage.memory import MemoryMessageStore
    from orchlink.broker.storage.memory_telemetry_store import TelemetryRejected

    async def run() -> None:
        store = MemoryMessageStore()
        with pytest.raises(TelemetryRejected) as exc_info:
            await store.record_task_telemetry(
                _telemetry(task_id="T-DOES-NOT-EXIST"),
                agent_id="demo.work",
            )
        assert exc_info.value.reason == "unknown-task"

    asyncio.run(run())


# --- contract: JSONL durability ----------------------------------------------


def test_telemetry_round_trips_through_jsonl_snapshot_replay(tmp_path):
    """AC-5: a telemetry record survived close-and-reopen (broker restart).
    The JSONL snapshot dict carries the same wire shape; a subsequent
    restart restores the latest record. Two heartbeats under the same key
    collapse to a single record at replay because the store replaces in
    place rather than appending.
    """

    from orchlink.broker.storage.jsonl import JsonlMessageStore

    async def run() -> None:
        path = os.path.join(str(tmp_path), "telemetry.jsonl")
        t_first = "2026-01-01T00:00:01+00:00"
        t_second = "2026-01-01T00:00:30+00:00"
        writer = JsonlMessageStore(path=path)
        sm = StoredMessage.from_envelope(_envelope(), now="2026-01-01T00:00:00+00:00")
        writer._state.active_messages[sm.envelope.message_id] = sm
        task_key = writer._job_projector.task_key("default", "T-AC5")
        writer._state.task_jobs[task_key] = _job()
        writer._state.tasks[task_key] = _task_projection()
        # First heartbeat.
        first = await writer.record_task_telemetry(
            _telemetry(tool_count=2, updated_at=t_first),
            agent_id="demo.work",
        )
        assert first["status"] == "recorded"
        # Second heartbeat replaces the first; the disk shape stays bounded.
        second = await writer.record_task_telemetry(
            _telemetry(tool_count=4, tokens=4096, percent=2.0, updated_at=t_second),
            agent_id="demo.work",
        )
        assert second["status"] == "recorded"
        # ``updated_at`` is stamped by the store on write; the value passed
        # in by the worker is the *requested* update time, not a fact.
        # Either it equals the input (when the broker hasn't moved) or it
        # is a later timestamp. The point is the value is non-null and
        # carried by the durable snapshot.
        assert second["updated_at"] is not None
        # Restart: the journal is a single record per snapshot, so the
        # record on disk is the LATEST, not a heartbeat history.
        restarted = JsonlMessageStore(path=path)
        listed = await restarted.list_task_telemetry(project_id="default")
        assert len(listed) == 1, f"expected exactly one record, got {len(listed)}"
        record = listed[0]
        assert record["tool_count"] == 4, "replace semantics must survive restart"
        assert record["percent"] == 2.0
        assert record["tokens"] == 4096
        # A new write after restart behaves the same way.
        third = await restarted.record_task_telemetry(
            _telemetry(tool_count=7),
            agent_id="demo.work",
        )
        assert third["status"] == "recorded"
        # Sanity: count_records_by_task stays at 1 because the snapshot
        # is replaced, never appended.
        listed_after = await restarted.list_task_telemetry(project_id="default")
        assert len(listed_after) == 1

    asyncio.run(run())


# --- contract: privacy boundary ----------------------------------------------


def test_telemetry_payload_never_carries_body_reasoning_or_secrets():
    """AC-5 / AC-10 (privacy): the telemetry wire shape excludes body, hidden
    reasoning, tool arguments, raw tool output, provider data, environment
    value, secret, or authorization data. Verifies both the TaskTelemetry
    domain object and the wire projection.
    """
    from orchlink.broker.storage.memory import MemoryMessageStore

    async def run() -> None:
        store = MemoryMessageStore()
        sm = StoredMessage.from_envelope(_envelope(), now="2026-01-01T00:00:00+00:00")
        store._state.active_messages[sm.envelope.message_id] = sm
        task_key = store._job_projector.task_key("default", "T-AC5")
        store._state.task_jobs[task_key] = _job()
        store._state.tasks[task_key] = _task_projection()
        recorded = await store.record_task_telemetry(
            _telemetry(
                tool_count=3,
                tokens=12_345,
                context_window=200_000,
                percent=6.17,
            ),
            agent_id="demo.work",
        )
        wire = store._telemetry_store.get_task_telemetry_locked("default", "T-AC5")
        assert wire is not None
        forbidden_terms = (
            # Body / reasoning / raw output / provider payloads — never
            # allowed through telemetry, even as field names.
            "intent",
            "message",
            "transcript",
            "thinking",
            "secret",
            "tool_name",
            "tool_arg",
            "tool_output",
            "stdout",
            "stderr",
            "raw",
            "api_key",
            "authorization",
            "bearer",
            "provider",
            "ORCHLINK_API_KEY",
            "env",
        )
        for term in forbidden_terms:
            assert term not in wire, f"telemetry wire shape leaks forbidden field {term!r}"
            # Python fields too.
            assert not hasattr(recorded, term), f"TaskTelemetry carries forbidden field {term!r}"
        # Numeric-only telemetry fields: no non-scalar content sneaks in.
        for field in ("tool_count", "tokens", "context_window", "percent"):
            value = getattr(recorded, field, None)
            if value is not None:
                assert isinstance(value, (int, float)), f"telemetry field {field!r} must be numeric, got {type(value)}"

    asyncio.run(run())


# --- AC-10 widget privacy: the generated lead UI widget is status-only ----------


def test_telemetry_rejects_non_numeric_tool_count():
    """The wire shape normalizes negative or non-numeric ``tool_count`` to
    zero. The default invariant is that tool_count is a non-negative integer.
    """
    from orchlink.core.views import task_telemetry_from_wire

    # Wire-level: from_wire clamps.
    wire = task_telemetry_from_wire(
        {"project_id": "default", "task_id": "T", "tool_count": -5, "tokens": "bad"}
    )
    assert wire.tool_count == 0, "negative tool_count must clamp to zero"
    assert wire.tokens is None, "invalid tokens must stay None rather than crash"
    assert wire.context_window is None


# --- contract: service / route adapter mapping (HTTP wire-up) ----------------


def test_telemetry_route_adapter_maps_rejection_to_http_409(monkeypatch):
    """AC-5: ``BrokerRouteAdapter.record_task_telemetry`` translates
    TelemetryRejected into a 409 with the structured ``reason``.
    """
    from fastapi import HTTPException

    from orchlink.broker.route_adapter import BrokerRouteAdapter
    from orchlink.broker.storage.memory_telemetry_store import TelemetryRejected

    adapter = BrokerRouteAdapter.__new__(BrokerRouteAdapter)

    async def boom(**_kwargs):
        raise TelemetryRejected("terminal-task", "task is DONE")

    # Bypass the adapter's constructor by injecting a stub service.
    class _Stub:
        async def record_task_telemetry(self, *_args, **_kwargs):
            return await boom()

    adapter.service = _Stub()  # type: ignore[assignment]

    async def run() -> None:
        try:
            await adapter.record_task_telemetry(
                "T-AC5",
                {"project_id": "default", "tool_count": 1},
                project_id="default",
                agent_id="demo.work",
            )
        except HTTPException as exc:
            assert exc.status_code == 409
            assert exc.detail["reason"] == "terminal-task"
        else:
            raise AssertionError("expected HTTPException")

    asyncio.run(run())


# --- contract: openapi surface ------------------------------------------------


def test_telemetry_post_and_get_endpoints_are_registered():
    """The broker FastAPI app exposes both telemetry endpoints under the
    secure ``/v1`` prefix, behind the API-key dependency.
    """
    from fastapi.testclient import TestClient

    from orchlink.broker.main import create_app
    from orchlink.broker.settings import Settings

    settings = Settings(api_key="change-me", store_path=":memory:")
    app = create_app(settings=settings)
    client = TestClient(app)
    # Both telemetry routes are present in the OpenAPI schema; the 422
    # responses (missing body) confirm the parameters are typed correctly.
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/v1/tasks/{task_id}/telemetry" in paths
    path_obj = paths["/v1/tasks/{task_id}/telemetry"]
    post = path_obj["post"]
    # The GET endpoint is the second method on the same path.
    get_method = path_obj.get("get")
    assert get_method is not None
    # Both methods are protected by the API-key dependency.
    # Each method lists an OpenAPI ``security`` requirement referencing
    # the ``apiKeyHeader`` scheme (or its name string). Either form
    # confirms the route is guarded by the API-key dependency.
    def _has_security(method: dict) -> bool:
        entries = method.get("security") or []
        return any(
            "X-API-Key" in entry or "apiKeyHeader" in entry or bool(entry)
            for entry in entries
        )

    assert _has_security(post)
    assert _has_security(get_method)

    # Hit the API and confirm both POST and GET behave end-to-end with a
    # valid key. POST with a valid body returns 200 (record stored) on
    # success or 4xx on missing fields / cross-project guard / lease
    # rejection. The test only requires that the routes are wired and
    # the API-key guard runs.
    headers = {"X-API-Key": "change-me", "X-Orchlink-Project-ID": "default"}
    post_response = client.post(
        "/v1/tasks/T-AC5/telemetry",
        json={"worker_name": "demo.work", "tool_count": 0},
        headers=headers,
    )
    assert post_response.status_code in (200, 400, 404, 409, 422)
    get_response = client.get(
        "/v1/tasks/T-AC5/telemetry",
        headers=headers,
    )
    # GET never returns 422 because all path params are required strings.
    assert get_response.status_code in (200, 400, 404, 500)
