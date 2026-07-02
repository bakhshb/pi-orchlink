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

import httpx

from orchlink.broker.journal import Journal
from orchlink.broker.main import create_app
from orchlink.broker.settings import Settings
from orchlink.broker.storage.memory import MemoryMessageStore
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