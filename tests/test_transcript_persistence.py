"""Tests for G018 transcript JSONL persistence (AC-5).

Covers dedicated transcript journal, bounded retention, truncation marker,
restart replay, atomic compaction, and corrupt-tail recovery.
"""

from __future__ import annotations

import asyncio
import json as _json

import httpx

from orchlink.broker.main import create_app
from orchlink.broker.settings import Settings
from orchlink.broker.storage.jsonl import JsonlMessageStore


class ASGITestClient:
    def __init__(self, store: JsonlMessageStore) -> None:
        self.app = create_app(store=store, settings=Settings(api_key="test-key"))

    async def _request(self, method: str, path: str, **kwargs):
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, path, **kwargs)

    def get(self, path: str, **kwargs):
        return asyncio.run(self._request("GET", path, **kwargs))

    def post(self, path: str, **kwargs):
        return asyncio.run(self._request("POST", path, **kwargs))


def auth_headers(project_id: str = "test"):
    return {"X-API-Key": "test-key", "X-Orchlink-Project-ID": project_id}


def acquire_session(client, agent_id: str, worker_name: str):
    response = client.post("/v1/sessions/acquire", headers=auth_headers(), json={
        "project_id": "test", "agent_id": agent_id, "role": "worker", "display_name": agent_id,
        "worker_name": worker_name,
    })
    assert response.status_code == 200, response.text
    return response.json()["session"]["lease_id"]


def deliver_task(client, task_id: str = "T001", to_agent: str = "test.work"):
    client.post("/v1/agents/register", headers=auth_headers(), json={
        "agent_id": to_agent, "role": "worker", "display_name": to_agent, "capabilities": ["implementation"],
    })
    lease_id = acquire_session(client, to_agent, worker_name=to_agent)
    client.post("/v1/messages/send", headers=auth_headers(), json={
        "protocol": "orch-a2a-v1", "message_id": f"msg-{task_id}", "correlation_id": f"req-{task_id}",
        "project_id": "test", "conversation_id": "test-default", "task_id": task_id,
        "from_agent": "test.lead", "to_agent": to_agent, "type": "TASK", "status": "PENDING",
        "turn": 1, "max_turns": 6, "requires_reply": True, "timeout_seconds": 1800,
        "payload": {"intent": "Do something."},
    })
    response = client.get(f"/v1/agents/{to_agent}/next?wait_seconds=1&lease_id={lease_id}", headers=auth_headers())
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "message"
    return lease_id


def heartbeat_job(client, task_id: str, holder: str, epoch: int = 1):
    response = client.post(f"/v1/jobs/{task_id}/heartbeat", headers=auth_headers(), json={"holder": holder, "epoch": epoch})
    assert response.status_code == 200, response.text


def append_transcript(client, task_id, events, lease_id, batch_id, holder="test.work", agent_id="test.work"):
    response = client.post(
        f"/v1/tasks/{task_id}/transcript",
        headers={**auth_headers(), "X-Orchlink-Session-Lease-ID": lease_id, "X-Orchlink-Lease-Epoch": "1", "X-Orchlink-Lease-Holder": holder},
        json={"project_id": "test", "task_id": task_id, "agent_id": agent_id, "worker_name": holder, "batch_id": batch_id, "events": events},
    )
    assert response.status_code == 200, response.text


def test_transcript_journal_is_separate_file(tmp_path):
    journal = tmp_path / "journal.jsonl"
    transcript_journal = tmp_path / "journal.transcript.jsonl"
    store = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
    client = ASGITestClient(store)
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    append_transcript(client, "T001", [{"kind": "assistant_delta", "text": "hello"}], lease_id, "b1")
    assert transcript_journal.is_file()
    assert journal.stat().st_size > 0


def test_transcript_events_survive_restart(tmp_path):
    journal = tmp_path / "journal.jsonl"
    store = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
    client = ASGITestClient(store)
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    append_transcript(client, "T001", [{"kind": "assistant_delta", "text": "survive"}], lease_id, "b1")

    restored = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
    restored_client = ASGITestClient(restored)
    read = restored_client.get("/v1/tasks/T001/transcript?after=0", headers=auth_headers())
    assert read.status_code == 200
    events = read.json()["events"]
    assert len(events) == 1
    assert events[0]["text"] == "survive"


def test_transcript_truncation_marker_on_retention(tmp_path):
    journal = tmp_path / "journal.jsonl"
    store = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
    client = ASGITestClient(store)
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    for i in range(1005):
        append_transcript(client, "T001", [{"kind": "assistant_delta", "text": f"line {i}"}], lease_id, f"b{i}")

    read = client.get("/v1/tasks/T001/transcript?after=0&limit=1000", headers=auth_headers())
    events = read.json()["events"]
    assert events[0]["kind"] == "system"
    assert "dropped" in events[0]["text"].lower() or "truncated" in events[0]["text"].lower()
    assert len(events) == 1001


def test_transcript_retention_is_bounded(tmp_path):
    journal = tmp_path / "journal.jsonl"
    store = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
    client = ASGITestClient(store)
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    for i in range(2000):
        append_transcript(client, "T001", [{"kind": "assistant_delta", "text": f"line {i}"}], lease_id, f"b{i}")

    read = client.get("/v1/tasks/T001/transcript?after=0&limit=1000", headers=auth_headers())
    events = read.json()["events"]
    # marker + retained events capped at 1000
    assert len(events) == 1001
    assert events[0]["kind"] == "system"


def test_transcript_compaction_preserves_next_sequence(tmp_path):
    journal = tmp_path / "journal.jsonl"
    store = JsonlMessageStore(journal, max_records=4, max_bytes=512)
    client = ASGITestClient(store)
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    for i in range(10):
        append_transcript(client, "T001", [{"kind": "assistant_delta", "text": f"c {i}"}], lease_id, f"bc{i}")

    restored = JsonlMessageStore(journal, max_records=4, max_bytes=512)
    restored_client = ASGITestClient(restored)
    read = restored_client.get("/v1/tasks/T001/transcript?after=0", headers=auth_headers())
    events = read.json()["events"]
    assert [event["seq"] for event in events] == list(range(1, 11))
    assert read.json()["next_seq"] == 11


def test_transcript_corrupt_tail_is_repaired_and_survives_second_restart(tmp_path):
    journal = tmp_path / "journal.jsonl"
    transcript_journal = tmp_path / "journal.transcript.jsonl"
    store = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
    client = ASGITestClient(store)
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    append_transcript(client, "T001", [{"kind": "assistant_delta", "text": "before corrupt"}], lease_id, "b1")
    with transcript_journal.open("a") as fp:
        fp.write("PARTIAL_JSON{")

    restored = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
    restored_client = ASGITestClient(restored)
    read = restored_client.get("/v1/tasks/T001/transcript?after=0", headers=auth_headers())
    assert len(read.json()["events"]) == 1
    assert read.json()["events"][0]["text"] == "before corrupt"

    second = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
    second_client = ASGITestClient(second)
    read2 = second_client.get("/v1/tasks/T001/transcript?after=0", headers=auth_headers())
    assert len(read2.json()["events"]) == 1


def test_transcript_per_task_isolation(tmp_path):
    journal = tmp_path / "journal.jsonl"
    store = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
    client = ASGITestClient(store)
    lease_a = deliver_task(client, task_id="TA", to_agent="test.work")
    lease_b = deliver_task(client, task_id="TB", to_agent="test.review")
    heartbeat_job(client, "TA", "test.work")
    heartbeat_job(client, "TB", "test.review")
    append_transcript(client, "TA", [{"kind": "assistant_delta", "text": "A"}], lease_a, "ba")
    append_transcript(client, "TB", [{"kind": "assistant_delta", "text": "B"}], lease_b, "bb", holder="test.review", agent_id="test.review")

    restored = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
    restored_client = ASGITestClient(restored)
    read_a = restored_client.get("/v1/tasks/TA/transcript?after=0", headers=auth_headers())
    read_b = restored_client.get("/v1/tasks/TB/transcript?after=0", headers=auth_headers())
    assert read_a.json()["events"][0]["text"] == "A"
    assert read_b.json()["events"][0]["text"] == "B"


def test_transcript_cursor_resumes_after_broker_restart(tmp_path):
    """AC-8: A saved lead-panel cursor must continue to function as `after`
    after the broker restarts. This covers the panel switching away/back
    across a broker restart without duplicating or skipping events.
    """
    journal = tmp_path / "journal.jsonl"
    store = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
    client = ASGITestClient(store)
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    for i in range(5):
        append_transcript(
            client,
            "T001",
            [{"kind": "assistant_delta", "text": f"line {i}"}],
            lease_id,
            f"b{i}",
        )
    # The panel would have stored a cursor after reading through it. We
    # simulate that here by picking a non-zero cursor value rather than
    # letting the new broker see ``after=0`` on first contact.
    cursor = 2

    # Restart the broker on the same JSONL path.
    restored = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
    restored_client = ASGITestClient(restored)

    # Resume with the stored cursor: only events strictly after the cursor
    # must come back, in order, with no duplicates or skips.
    resumed = restored_client.get(f"/v1/tasks/T001/transcript?after={cursor}", headers=auth_headers())
    assert resumed.status_code == 200
    events = resumed.json()["events"]
    seqs = [event["seq"] for event in events]
    assert seqs == list(range(cursor + 1, 6))  # 3, 4, 5
    assert [event["text"] for event in events] == ["line 2", "line 3", "line 4"]


# --- G018-BACKEND-FIX-016 -----------------------------------------------------
# Defect 1 (main snapshot isolation) and Defect 2 (bounded retention by
# event count AND byte size, watermark advances across truncations, restart
# preserves).


def test_main_journal_snapshot_omits_transcript_state(tmp_path):
    """Defect 1: ``_snapshot()`` of the main JSONL store must not carry any
    transcript fields. Replay must come from the adjacent transcript journal.
    """
    journal = tmp_path / "journal.jsonl"
    store = JsonlMessageStore(journal, max_records=4, max_bytes=512)
    client = ASGITestClient(store)
    lease_id = deliver_task(client)
    heartbeat_job(client, "T001", "test.work")
    append_transcript(client, "T001", [{"kind": "assistant_delta", "text": "x"}], lease_id, "b1")

    raw = journal.read_text()
    assert raw, "main journal should not be empty after writes"

    # Walk every line; none may include a transcript-related key.
    transcript_keys = {
        "transcripts",
        "transcript_next_seq",
        "transcript_batch_ids",
        "transcript_truncated_before",
    }
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = _json.loads(stripped)
        except _json.JSONDecodeError:
            continue
        snapshot = record.get("snapshot") if isinstance(record, dict) else None
        if not isinstance(snapshot, dict):
            continue
        for key in transcript_keys:
            assert key not in snapshot, f"main snapshot leaked transcript field {key!r}: {snapshot!r}"

    # The transcript journal, by contrast, does carry transcript state.
    transcript_journal = tmp_path / "journal.transcript.jsonl"
    assert transcript_journal.is_file()
    t_lines = transcript_journal.read_text().splitlines()
    parsed = [_json.loads(line) for line in t_lines if line.strip()]
    assert any(
        "transcripts" in (snap := (r.get("snapshot") or {})) and snap["transcripts"]
        for r in parsed
    ), "transcript journal should still carry transcripts"


def test_transcript_retention_drops_oldest_when_byte_cap_exceeded(tmp_path):
    """Defect 2: bounded retention drops oldest events when the total
    UTF-8 byte size of retained events exceeds MAX_TRANSCRIPT_BYTES_PER_TASK,
    even when the per-task event count is well below MAX_TRANSCRIPT_EVENTS_PER_TASK.
    """
    from orchlink.broker.storage.memory_transcript_store import (
        MAX_TRANSCRIPT_BYTES_PER_TASK,
        set_retention_limits,
        reset_retention_limits,
    )

    # Force the byte cap to a tiny value so the test stays small but still
    # proves the cap drives truncation independent of the count cap.
    set_retention_limits(events=10_000, bytes_limit=2048)
    try:
        journal = tmp_path / "journal.jsonl"
        # max_records / max_bytes govern only the MAIN journal's compaction,
        # not transcript retention (which uses MAX_TRANSCRIPT_*).
        store = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
        client = ASGITestClient(store)
        lease_id = deliver_task(client, task_id="T-OVERSIZE", to_agent="oversize.work")
        # Each event's text is ~200 bytes; with 20 events we are at
        # ~4000 bytes payload + overhead, well above the 2 KiB byte cap.
        for i in range(20):
            append_transcript(
                client,
                "T-OVERSIZE",
                [{"kind": "assistant_delta", "text": f"oversize line number {i:04d} with extra padding"}],
                lease_id,
                f"b-{i}",
                holder="oversize.work",
                agent_id="oversize.work",
            )

        # Inspect the in-memory transcript state directly: the per-task
        # buffer must be smaller than 20 events (truncation kicked in).
        ts_store = store._transcript_store  # type: ignore[attr-defined]
        kept = ts_store._state.transcripts["test:T-OVERSIZE"]
        assert len(kept) < 20, f"expected byte cap to drop oldest, got {len(kept)} events"
        # Sanity: keep limit is well above the default so the byte cap alone
        # drove the drop (not the event count).
        assert MAX_TRANSCRIPT_BYTES_PER_TASK == 256 * 1024
    finally:
        reset_retention_limits()


def test_transcript_retention_watermark_advances_across_repeated_truncations(tmp_path):
    """Defect 2: when subsequent appends trigger additional truncation (e.g.,
    a second byte-cap hit), the watermark advances monotonically and
    cannot regress. Replay across a broker restart must preserve that.
    """
    from orchlink.broker.storage.memory_transcript_store import (
        set_retention_limits,
        reset_retention_limits,
    )

    set_retention_limits(events=10_000, bytes_limit=1024)
    try:
        journal = tmp_path / "journal.jsonl"
        store = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
        client = ASGITestClient(store)
        lease_id = deliver_task(client, task_id="T-WATERMARK", to_agent="watermark.work")
        # Round 1: drop enough to bring payload under 1 KiB.
        for i in range(8):
            append_transcript(
                client,
                "T-WATERMARK",
                [{"kind": "assistant_delta", "text": f"round one payload fill {i:02d} extra bytes to push past 1 KiB cap easily"}],
                lease_id,
                f"r1-{i}",
                holder="watermark.work",
                agent_id="watermark.work",
            )
        ts_store = store._transcript_store  # type: ignore[attr-defined]
        first_watermark = ts_store._state.transcript_truncated_before.get("test:T-WATERMARK")
        assert first_watermark is not None, "truncation should set a watermark"

        first_kept = [e.seq for e in ts_store._state.transcripts["test:T-WATERMARK"]]
        assert first_kept == sorted(first_kept) and len(first_kept) > 0

        # Round 2: more writes exceed the byte cap again; the first-retained
        # sequence watermark must advance as additional history is dropped.
        for i in range(8):
            append_transcript(
                client,
                "T-WATERMARK",
                [{"kind": "assistant_delta", "text": f"round two payload fill {i:02d} extra bytes pushed past cap again"}],
                lease_id,
                f"r2-{i}",
                holder="watermark.work",
                agent_id="watermark.work",
            )
        second_watermark = ts_store._state.transcript_truncated_before["test:T-WATERMARK"]
        assert second_watermark is not None
        assert second_watermark > first_watermark, (
            f"watermark did not advance: {first_watermark} -> {second_watermark}; "
            f"the per-task retained seq window must move forward"
        )

        # Round 3: simulate a broker restart; the watermark must persist.
        restored = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
        restored_client = ASGITestClient(restored)
        # A second store instance is created; its in-memory watermark comes
        # ONLY from the transcript journal, never the main snapshot.
        ts_after_restart = restored._transcript_store._state  # type: ignore[attr-defined]
        # The transcript journal is replayed on construction, so the
        # post-truncation watermark must match what we held in memory.
        assert ts_after_restart.transcript_truncated_before.get("test:T-WATERMARK") == second_watermark
        # And a fresh read still surfaces no events past the watermark
        # unless we explicitly paginate.
        read = restored_client.get(
            "/v1/tasks/T-WATERMARK/transcript?after=0&limit=1000",
            headers=auth_headers(),
        )
        events = read.json()["events"]
        assert events[0]["kind"] == "system"
        assert "dropped" in events[0]["text"].lower() or "truncated" in events[0]["text"].lower()
        assert int(events[0]["seq"]) == second_watermark
    finally:
        reset_retention_limits()


def test_transcript_state_does_not_leak_through_main_journal_restart(tmp_path):
    """Defect 1 (replay hardening): even if a corrupt legacy main journal
    accidentally contains transcript-shaped fields, replaying it must NOT
    resurrect transcript events. The transcript journal is the only source.
    """
    journal = tmp_path / "journal.jsonl"
    # Seed a legacy main journal that has transcript-shaped snapshot fields.
    legacy_main = {
        "time": "2020-01-01T00:00:00+00:00",
        "operation": "_compact",
        "request": {},
        "result": {"compacted_from": 1, "compacted_size": 1},
        "snapshot": {
            "next_event_id": 1,
            "next_activity_id": 1,
            "transcripts": {"test:FAKE": [{
                "seq": 1, "time": "t", "project_id": "test", "task_id": "FAKE",
                "agent_id": "x", "worker_name": "x", "kind": "assistant_delta",
                "text": "STALE-FAKE", "tool_name": None,
            }]},
            "transcript_next_seq": {"test:FAKE": 99},
            "transcript_batch_ids": {"test:FAKE": ["FAKE"]},
            "transcript_truncated_before": {"test:FAKE": 1},
        },
    }
    journal.write_text(_json.dumps(legacy_main) + "\n")

    # Transcript journal is empty -> no transcript state should appear.
    store = JsonlMessageStore(journal, max_records=1024, max_bytes=4 * 1024 * 1024)
    assert store._state.transcripts == {}, (
        f"main journal leaked transcript state: {store._state.transcripts}"
    )
    assert store._state.transcript_next_seq == {}
    assert store._state.transcript_batch_ids == {}
    assert store._state.transcript_truncated_before == {}
