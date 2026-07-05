"""M1 audit journal tests.

Covers: append + query, project scoping, since/limit cursor, JSONL file
persistence, the GET/POST /v1/journal endpoint, representative broker
transition journaling via the store event-log hook, and representative Goal
Mode transition journaling via GoalStore.append_history.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from orchlink.broker.journal import Journal
from orchlink.broker.main import create_app
from orchlink.broker.settings import Settings
from orchlink.broker.storage.memory import MemoryMessageStore
from orchlink.core.envelope import MessageEnvelope
from orchlink.core.views import session_acquire_from_wire, session_heartbeat_from_wire
from orchlink.goal.store import GoalStore
from orchlink.project.init import init_project


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
    def journal(self):
        return self.app.state.store.journal


def _task_message(message_id: str, task_id: str, project_id: str = "test") -> dict:
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


def _message_envelope(data: dict[str, Any]) -> MessageEnvelope:
    return MessageEnvelope.model_validate({k: v for k, v in data.items() if k not in {"created_at", "queued_at", "updated_at"}})


def _session_acquire(data: dict[str, Any]):
    return session_acquire_from_wire(data)


def _session_heartbeat(lease_id: str, data: dict[str, Any], *, project_id: str | None = None):
    return session_heartbeat_from_wire(lease_id, project_id=project_id, heartbeat=data)


def _reply(message: dict, message_id: str) -> dict:
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


def test_journal_append_and_query_in_memory():
    journal = Journal()
    journal.append(project_id="p1", actor="a", action="job.created", target_type="job", target_id="T1", after="CREATED")
    journal.append(project_id="p1", actor="a", action="job.dispatched", target_type="job", target_id="T1", after="RUNNING")

    entries = journal.query()
    assert [e.action for e in entries] == ["job.created", "job.dispatched"]
    assert entries[0].seq == 1
    assert entries[1].seq == 2
    assert journal.last_seq() == 2


def test_journal_project_scoping():
    journal = Journal()
    journal.append(project_id="alpha", actor="a", action="job.created", target_id="T1")
    journal.append(project_id="beta", actor="a", action="job.created", target_id="T2")

    alpha = journal.query(project_id="alpha")
    beta = journal.query(project_id="beta")
    assert [e.target_id for e in alpha] == ["T1"]
    assert [e.target_id for e in beta] == ["T2"]


def test_journal_since_and_limit_cursor():
    journal = Journal()
    for index in range(5):
        journal.append(project_id="p", actor="a", action="job.created", target_id=f"T{index}")

    # since cursor returns only newer entries, oldest-first.
    after_two = journal.query(since=2)
    assert [e.seq for e in after_two] == [3, 4, 5]

    # limit caps the count, keeping the oldest of the window.
    limited = journal.query(since=0, limit=2)
    assert [e.seq for e in limited] == [1, 2]

    # since beyond the last entry returns nothing.
    assert journal.query(since=5) == []


def test_journal_jsonl_file_persistence(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    journal = Journal(path=path)
    journal.append(project_id="p1", actor="a", action="job.created", target_id="T1", after="CREATED")
    journal.append(project_id="p1", actor="a", action="job.replied", target_id="T1", after="DONE")

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["action"] == "job.created"
    assert first["seq"] == 1
    assert first["target_id"] == "T1"
    # Append-only: a failed persistence must not corrupt prior lines (simulated
    # by confirming both entries are intact and ordered).
    assert json.loads(lines[1])["action"] == "job.replied"


def test_journal_maps_lease_events_to_v1_action_vocabulary():
    journal = Journal()

    journal.record_broker_event({"type": "job_heartbeat", "project_id": "p", "task_id": "T1", "status": "RUNNING"})
    journal.record_broker_event({"type": "job_reclaimed", "project_id": "p", "task_id": "T1", "status": "RUNNING"})

    assert [entry.action for entry in journal.query(project_id="p")] == ["job.heartbeat", "job.reclaimed"]


def test_journal_endpoint_get_returns_empty_when_no_entries():
    client = ASGITestClient()
    response = client.get("/v1/journal", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["entries"] == []
    assert body["last_seq"] == 0


def test_journal_endpoint_post_then_get():
    client = ASGITestClient()
    post_response = client.post(
        "/v1/journal",
        headers=AUTH,
        json={
            "project_id": "demo",
            "action": "goal.started",
            "target_type": "goal",
            "target_id": "G001",
            "after": "draft",
            "meta": {"event_type": "created"},
        },
    )
    assert post_response.status_code == 200
    assert post_response.json()["status"] == "recorded"
    seq = post_response.json()["seq"]
    assert seq >= 1

    get_response = client.get("/v1/journal", headers=AUTH)
    assert get_response.status_code == 200
    entries = get_response.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["action"] == "goal.started"
    assert entries[0]["target_id"] == "G001"
    assert entries[0]["project_id"] == "demo"
    assert get_response.json()["last_seq"] == seq


def test_journal_endpoint_project_scoping():
    client = ASGITestClient()
    client.post("/v1/journal", headers={**AUTH, "X-Orchlink-Project-ID": "alpha"}, json={"action": "goal.started", "target_id": "G001"})
    client.post("/v1/journal", headers={**AUTH, "X-Orchlink-Project-ID": "beta"}, json={"action": "goal.started", "target_id": "G002"})

    alpha = client.get("/v1/journal?project_id=alpha", headers=AUTH).json()["entries"]
    beta = client.get("/v1/journal?project_id=beta", headers=AUTH).json()["entries"]
    assert [e["target_id"] for e in alpha] == ["G001"]
    assert [e["target_id"] for e in beta] == ["G002"]


def test_broker_transitions_are_journaled():
    """Representative broker state transitions mirror into the audit journal."""
    client = ASGITestClient()
    client.post(
        "/v1/agents/register",
        headers={**AUTH, "X-Orchlink-Project-ID": "test"},
        json={"agent_id": "test.work", "role": "worker", "display_name": "Worker", "project_id": "test", "capabilities": ["tests"]},
    )
    message = _task_message("msg-0001", "T001", project_id="test")
    client.post("/v1/messages/send", headers={**AUTH, "X-Orchlink-Project-ID": "test"}, json=message)
    client.get("/v1/agents/test.work/next?wait_seconds=1", headers={**AUTH, "X-Orchlink-Project-ID": "test"})
    client.post("/v1/messages/msg-0001/reply", headers={**AUTH, "X-Orchlink-Project-ID": "test"}, json=_reply(message, "reply-0001"))

    entries = client.journal.query(project_id="test")
    actions = [entry.action for entry in entries]
    assert "session.registered" in actions
    assert "job.created" in actions
    assert "job.dispatched" in actions
    assert "job.replied" in actions

    # The replied entry carries the task id and the new status.
    replied = next(entry for entry in entries if entry.action == "job.replied")
    assert replied.target_type == "job"
    assert replied.target_id == "T001"
    assert replied.after == "DONE"


def test_goal_transitions_are_journaled(tmp_path: Path, monkeypatch):
    """Representative Goal Mode transitions POST to the broker audit journal."""
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    config = {"project_id": "demo", "broker": {"url": "http://127.0.0.1:8787", "api_key": "change-me"}}

    captured: list[dict] = []

    def fake_post(config, goal_id, action, before, after, meta=None):
        captured.append({"goal_id": goal_id, "action": action, "after": after})

    monkeypatch.setattr("orchlink.goal.store.journal_goal_transition", fake_post)

    store = GoalStore(config)

    # goal.started
    goal = store.create_goal("Smoke goal", "text", "inline source")
    # goal.gated (combined approval)
    store.approve_combined_gate(goal.id)
    # goal.done
    store.set_status(goal.id, "done", "verified_done", {"steps": 1})

    # goal.cancelled on a separate goal
    goal2 = store.create_goal("Cancel smoke", "text", "inline source")
    store.cancel(goal2.id, reason="manual smoke")

    actions = [(c["action"], c["after"]) for c in captured]
    assert ("goal.started", "draft") in actions
    assert ("goal.gated", "approved") in actions
    assert ("goal.done", "done") in actions
    assert ("goal.cancelled", "cancelled") in actions


def test_goal_journaling_failure_is_swallowed(tmp_path: Path, monkeypatch):
    """A journal outage must never block a goal operation."""
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    config = {"project_id": "demo"}

    def raising_post(*args, **kwargs):
        raise RuntimeError("broker unreachable")

    monkeypatch.setattr("orchlink.goal.store.journal_goal_transition", raising_post)
    store = GoalStore(config)

    # append_history must complete despite the journal raising.
    goal = store.create_goal("Resilience smoke", "text", "inline source")
    approved = store.approve_combined_gate(goal.id)

    assert approved.ac_gate == "approved"

def test_jsonl_session_domain_object_round_trip(tmp_path: Path):
    """JsonlMessageStore must persist sessions as dicts but restore them as Session.

    AC-4: JSONL persistence writes sessions as dictionary snapshots and restores
    them as Session objects in memory without changing the on-disk session
    field set.
    """
    from orchlink.broker.storage.jsonl import JsonlMessageStore
    from orchlink.core.models import Session
    from orchlink.core.views import session_to_wire

    async def run():
        snap_path = tmp_path / "snap.jsonl"
        store = JsonlMessageStore(path=str(snap_path))
        wire = await store.acquire_session(
            _session_acquire({
                "project_id": "demo",
                "agent_id": "demo.work",
                "role": "work",
                "pid": 123,
                "backend": "rpc-supervisor",
                "model": "openai/codex-max",
                "thinking": "medium",
                "ready": True,
            })
        )
        lease_id = wire["lease_id"]

        # In-memory state must be a Session.
        in_memory = store._state.sessions[lease_id]
        assert isinstance(in_memory, Session), type(in_memory).__name__

        # On-disk shape must be JSON-serializable dict with the session fields.
        with snap_path.open("r", encoding="utf-8") as f:
            for line in f:
                if '"sessions"' in line:
                    record = json.loads(line)
                    on_disk = record["snapshot"]["sessions"][lease_id]
                    assert isinstance(on_disk, dict), type(on_disk).__name__
                    assert set(on_disk.keys()) == set(session_to_wire(in_memory).keys())
                    # Every value must be JSON-serializable.
                    json.dumps(on_disk)
                    break

        # Round-trip: a fresh store rebuilt from the snapshot file must restore
        # Sessions in memory.
        restored_store = JsonlMessageStore(path=str(snap_path))
        restored = restored_store._state.sessions[lease_id]
        assert isinstance(restored, Session), type(restored).__name__
        assert restored.lease_id == lease_id
        assert restored.agent_id == "demo.work"
        assert restored.backend == "rpc-supervisor"
        assert restored.ready is True

    asyncio.run(run())


def test_jsonl_session_domain_object_full_lifecycle_round_trip(tmp_path: Path):
    """Full acquire->heartbeat->release JSONL round-trip keeps Session in memory
    and a stable on-disk dict shape with every lifecycle field present.

    AC-4 hardening: exercise every lifecycle path and confirm the on-disk
    session dict has all 24 Session fields (including release-time fields:
    settled_work, ended_at, ended_reason, ready=False) and that reload
    reproduces a Session whose attributes match the released state.
    """
    from orchlink.broker.storage.jsonl import JsonlMessageStore
    from orchlink.core.models import Session
    from orchlink.core.views import session_to_wire

    async def run():
        snap_path = tmp_path / "lifecycle.jsonl"
        store = JsonlMessageStore(path=str(snap_path))
        wire = await store.acquire_session(
            _session_acquire({
                "project_id": "demo",
                "agent_id": "demo.work",
                "role": "work",
                "pid": 123,
                "backend": "rpc-supervisor",
                "model": "openai/codex-max",
                "thinking": "medium",
                "supervisor_pid": 999,
            })
        )
        lease_id = wire["lease_id"]

        # heartbeat with readiness and metadata updates
        await store.heartbeat_session(
            lease_id,
            project_id="demo",
            heartbeat=_session_heartbeat(
                lease_id,
                {
                    "ready": True,
                    "runtime_mode": "rpc",
                    "backend": "rpc-supervisor",
                    "model": "openai/codex-max",
                    "thinking": "xhigh",
                    "pi_pid": 456,
                    "worker_name": "work",
                },
                project_id="demo",
            ),
        )
        # release
        await store.release_session(lease_id, "worker exited", project_id="demo")

        # ---- On-disk inspection ----
        expected_keys = {
            "lease_id", "project_id", "agent_id", "role", "worker_name",
            "status", "pid", "session_id", "created_at", "updated_at",
            "last_heartbeat_at", "ended_at", "ended_reason",
            "lease_grace_seconds", "ready", "ready_at",
            "last_ready_heartbeat_at", "runtime_mode", "backend", "model",
            "thinking", "supervisor_pid", "pi_pid", "settled_work",
        }

        latest_session_dict: dict[str, Any] | None = None
        with snap_path.open("r", encoding="utf-8") as f:
            for line in f:
                if '"sessions"' not in line:
                    continue
                record = json.loads(line)
                sess_map = record["snapshot"]["sessions"]
                if lease_id not in sess_map:
                    continue
                latest_session_dict = sess_map[lease_id]

        assert latest_session_dict is not None
        assert isinstance(latest_session_dict, dict)
        # On-disk fields match the canonical Session wire key set exactly.
        assert set(latest_session_dict.keys()) == expected_keys
        # Status field reflects release.
        assert latest_session_dict["status"] == "RELEASED"
        assert latest_session_dict["ended_reason"] == "worker exited"
        assert latest_session_dict["ready"] is False
        # Release-time settled_work is empty (no enqueued task), but the key is present.
        assert latest_session_dict["settled_work"] == []
        # Metadata updates from heartbeat are present on disk.
        assert latest_session_dict["thinking"] == "xhigh"
        assert latest_session_dict["pi_pid"] == 456
        assert latest_session_dict["ready_at"] is not None
        assert latest_session_dict["last_ready_heartbeat_at"] is not None
        # Strict JSON round-trip (no default=str fallback).
        json.dumps(latest_session_dict)

        # ---- Fresh-store reload ----
        restored_store = JsonlMessageStore(path=str(snap_path))
        assert lease_id in restored_store._state.sessions
        restored = restored_store._state.sessions[lease_id]
        assert isinstance(restored, Session)
        # Every release-time attribute survived the round-trip.
        assert restored.status == "RELEASED"
        assert restored.ended_at is not None
        assert restored.ended_reason == "worker exited"
        assert restored.ready is False
        assert restored.settled_work == []
        # Metadata updates survive.
        assert restored.thinking == "xhigh"
        assert restored.pi_pid == 456
        assert restored.ready is True or restored.ready is False  # session_to_wire coerces
        # Public API keeps returning wire dicts with the same shape.
        listed = await restored_store.list_sessions(project_id="demo")
        assert listed, "list_sessions must surface the restored Session"
        row = listed[0]
        assert set(row.keys()) == expected_keys
        assert row["status"] == "RELEASED"
        assert row["lease_id"] == lease_id

        # Wire form from the restored Session matches what we persisted.
        assert session_to_wire(restored) == latest_session_dict

    asyncio.run(run())


# --- G004 AC-5: JSONL snapshots store active messages as dicts and restore them as StoredMessage ---


def test_jsonl_message_envelope_request_is_recorded_as_wire_dict(tmp_path: Path):
    """Typed MessageEnvelope inputs are normalized before JSONL journaling.

    Broker routes now pass validated envelopes to storage. The JSONL operation
    record must still contain JSON-shaped request data, not Pydantic reprs.
    """
    import asyncio
    import json

    from orchlink.broker.storage.jsonl import JsonlMessageStore
    from orchlink.core.envelope import MessageEnvelope

    async def run():
        snap_path = tmp_path / "typed-envelope-request.jsonl"
        store = JsonlMessageStore(path=str(snap_path))
        envelope = MessageEnvelope.model_validate(
            _task_message("msg-envelope-jsonl", "TEST-ENVELOPE", project_id="default")
        )

        await store.enqueue_message(envelope)

        first = json.loads(snap_path.read_text(encoding="utf-8").splitlines()[0])
        message = first["request"]["message"]
        assert isinstance(message, dict)
        assert message["message_id"] == "msg-envelope-jsonl"
        assert message["task_id"] == "TEST-ENVELOPE"
        json.dumps(message)

    asyncio.run(run())


def test_broker_jsonl_typed_envelope_request_is_journaled_and_restored(tmp_path: Path):
    """Broker HTTP validates JSON into MessageEnvelope, passes it to JSONL
    storage, journals a JSON dict request, and reloads as StoredMessage."""
    from orchlink.broker.storage.jsonl import JsonlMessageStore
    from orchlink.core.envelope import MessageEnvelope
    from orchlink.core.models import StoredMessage

    async def run():
        snap_path = tmp_path / "broker-jsonl-typed.jsonl"
        store = JsonlMessageStore(path=str(snap_path))
        app = create_app(store=store, settings=Settings(api_key="test-key"))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/v1/messages/send",
                headers={**AUTH, "X-Orchlink-Project-ID": "default"},
                json=_task_message("msg-http-jsonl", "TEST-HTTP", project_id="default"),
            )
            assert response.status_code == 200
            next_response = await client.get(
                "/v1/agents/test.work/next?wait_seconds=1",
                headers={**AUTH, "X-Orchlink-Project-ID": "default"},
            )
            assert next_response.status_code == 200
            assert next_response.json()["message"]["status"] == "DELIVERED"

        first = json.loads(snap_path.read_text(encoding="utf-8").splitlines()[0])
        request_message = first["request"]["message"]
        assert isinstance(request_message, dict)
        assert request_message["message_id"] == "msg-http-jsonl"
        json.dumps(request_message)

        snapshot_message = first["snapshot"]["active_messages"]["msg-http-jsonl"]
        assert isinstance(snapshot_message, dict)
        assert snapshot_message["status"] == "QUEUED"

        restored_store = JsonlMessageStore(path=str(snap_path))
        restored = restored_store._state.active_messages["msg-http-jsonl"]
        assert isinstance(restored, StoredMessage)
        assert isinstance(restored.envelope, MessageEnvelope)

    asyncio.run(run())


def test_jsonl_typed_reply_and_close_requests_are_journaled_as_dicts(tmp_path: Path):
    """Typed save_reply / close_conversation inputs are normalized before JSONL."""
    from orchlink.broker.storage.jsonl import JsonlMessageStore
    from orchlink.core.envelope import MessageEnvelope

    async def run():
        snap_path = tmp_path / "typed-reply-close.jsonl"
        store = JsonlMessageStore(path=str(snap_path))

        task = _task_message("msg-jsonl-reply", "TEST-REPLY", project_id="default")
        await store.enqueue_message(MessageEnvelope.model_validate(task))
        await store.get_next_message("test.work", wait_seconds=0.1, project_id="default")
        await store.save_reply(
            "msg-jsonl-reply",
            MessageEnvelope.model_validate(_reply(task, "reply-jsonl")),
        )

        chat = {
            **_task_message("chat-jsonl", "", project_id="default"),
            "conversation_id": "conv-jsonl",
            "task_id": None,
            "type": "CHAT_START",
            "delivery": "conversation",
            "payload": {"mode": "TALK", "topic": "jsonl"},
        }
        await store.enqueue_message(MessageEnvelope.model_validate(chat))
        close = {
            **chat,
            "message_id": "close-jsonl",
            "correlation_id": "req-close-jsonl",
            "type": "CHAT_CLOSE",
            "requires_reply": False,
            "payload": {"mode": "TALK", "message": "close"},
        }
        await store.close_conversation("conv-jsonl", MessageEnvelope.model_validate(close))

        records = [json.loads(line) for line in snap_path.read_text(encoding="utf-8").splitlines()]
        by_operation = {record["operation"]: record for record in records}
        reply_request = by_operation["save_reply"]["request"]["reply"]
        close_request = by_operation["close_conversation"]["request"]["message"]
        assert isinstance(reply_request, dict)
        assert reply_request["message_id"] == "reply-jsonl"
        assert isinstance(close_request, dict)
        assert close_request["message_id"] == "close-jsonl"
        json.dumps(reply_request)
        json.dumps(close_request)

    asyncio.run(run())


def test_jsonl_stored_message_domain_object_round_trip(tmp_path: Path):
    """JsonlMessageStore must persist active messages as dicts but restore them
    as `StoredMessage` objects. On-disk field set matches the broker wire
    shape; in-memory state is a domain record carrying a `MessageEnvelope`.

    AC-5: JSONL snapshots write active messages as dictionaries with the
    existing on-disk field set and restore them as `StoredMessage` objects in
    memory.
    """
    import asyncio
    import json

    from orchlink.broker.storage.jsonl import JsonlMessageStore
    from orchlink.core.envelope import MessageEnvelope
    from orchlink.core.models import StoredMessage

    async def run():
        snap_path = tmp_path / "stored-message-snap.jsonl"
        store = JsonlMessageStore(path=str(snap_path))
        wire_message = _task_message("msg-0001", "TEST-001", project_id="default")

        original = await store.enqueue_message(_message_envelope(wire_message))
        assert original == {"status": "queued", "message_id": "msg-0001"}

        # In-memory: StoredMessage carrying a validated MessageEnvelope.
        in_memory = store._state.active_messages["msg-0001"]
        assert isinstance(in_memory, StoredMessage), type(in_memory).__name__
        assert isinstance(in_memory.envelope, MessageEnvelope)
        assert in_memory.status == "QUEUED"

        # On-disk: a JSON-serializable dict with the prior field set.
        with snap_path.open("r", encoding="utf-8") as file:
            for line in file:
                if '"active_messages"' in line:
                    record = json.loads(line)
                    on_disk = record["snapshot"]["active_messages"]["msg-0001"]
                    assert isinstance(on_disk, dict), type(on_disk).__name__
                    assert set(on_disk.keys()) == set(in_memory.to_wire_dict().keys())
                    json.dumps(on_disk)  # serializable
                    # Crucial fields preserved.
                    assert on_disk["message_id"] == "msg-0001"
                    assert on_disk["to_agent"] == "test.work"
                    assert on_disk["status"] == "QUEUED"
                    assert on_disk["created_at"]
                    break
            else:
                raise AssertionError("No snapshot with active_messages found in journal file")

        # Round-trip: a fresh store from the snapshot file restores StoredMessage.
        restored_store = JsonlMessageStore(path=str(snap_path))
        restored = restored_store._state.active_messages.get("msg-0001")
        assert isinstance(restored, StoredMessage), type(restored).__name__
        assert isinstance(restored.envelope, MessageEnvelope)
        assert restored.envelope.message_id == "msg-0001"
        # Broker lifecycle metadata round-trips through the snapshot.
        assert restored.status == "QUEUED"
        assert restored.created_at == in_memory.created_at
        assert restored.queued_at == in_memory.queued_at
        assert restored.updated_at == in_memory.updated_at

    asyncio.run(run())


def test_jsonl_stored_message_domain_object_records_status_transitions(tmp_path: Path):
    """JSONL snapshots persist the broker lifecycle (`status`, `updated_at`)
    of the enqueue path, and reload restores the `StoredMessage` with the
    matching fields.

    AC-5: JSONL snapshots are stable across enqueue while keeping in-memory
    state as `StoredMessage` objects. Subsequent transitions (cancel/timeout)
    flow through `StoredMessage.with_status` so the next snapshot would record
    them too.
    """
    import asyncio

    from orchlink.broker.storage.jsonl import JsonlMessageStore
    from orchlink.core.models import StoredMessage

    async def run():
        snap_path = tmp_path / "transitions.jsonl"
        store = JsonlMessageStore(path=str(snap_path))
        wire_message = _task_message("msg-0001", "TEST-001", project_id="default")

        await store.enqueue_message(_message_envelope(wire_message))

        # Cancel flows through StoredMessage.with_status and updates the snapshot.
        await store.cancel_work("msg-0001", reason="abandoned", project_id="default")

        # The in-memory record reflects the cancel transition.
        snapshot = store._state.active_messages["msg-0001"]
        assert isinstance(snapshot, StoredMessage)
        assert snapshot.status == "CANCELLED"

        # Reload from disk must restore a StoredMessage carrying the post-
        # cancel status.
        restored_store = JsonlMessageStore(path=str(snap_path))
        restored = restored_store._state.active_messages["msg-0001"]
        assert isinstance(restored, StoredMessage)
        assert restored.status == "CANCELLED"
        # And surface the broker-facing dict shape.
        listed = await restored_store.list_active_messages()
        assert listed[0]["status"] == "CANCELLED"
        assert listed[0]["message_id"] == "msg-0001"

    asyncio.run(run())


def test_jsonl_stored_message_domain_object_on_disk_field_set_is_stable(tmp_path: Path):
    """The on-disk snapshot of an active message uses the same field set as
    the prior JSONL implementation (envelope fields plus broker lifecycle
    metadata) — no StoredMessage internals (envelope-only fields) leak into
    the snapshot.

    AC-5: JSONL on-disk active-message shape is stable.
    """
    import asyncio

    from orchlink.broker.storage.jsonl import JsonlMessageStore

    async def run():
        snap_path = tmp_path / "stable-shape.jsonl"
        store = JsonlMessageStore(path=str(snap_path))
        wire_message = _task_message("msg-0001", "TEST-001", project_id="default")
        await store.enqueue_message(_message_envelope(wire_message))

        with snap_path.open("r", encoding="utf-8") as file:
            line = file.readlines()[-1]
        import json as _json

        record = _json.loads(line)
        on_disk = record["snapshot"]["active_messages"]["msg-0001"]

        expected_keys = {
            "protocol", "message_id", "correlation_id", "project_id",
            "conversation_id", "task_id", "from_agent", "to_agent", "type",
            "status", "turn", "max_turns", "requires_reply", "timeout_seconds",
            "delivery", "payload", "meta",
            "created_at", "queued_at", "updated_at",
        }
        actual_keys = set(on_disk.keys())
        # On-disk keys are exactly the expected envelope + broker fields.
        assert expected_keys <= actual_keys, f"missing on-disk keys: {expected_keys - actual_keys}"
        # No extra keys beyond the envelope+broker set are written (StoredMessage
        # internals stay in memory).
        forbidden = actual_keys - expected_keys
        assert not forbidden, f"unexpected on-disk keys: {forbidden}"

    asyncio.run(run())
