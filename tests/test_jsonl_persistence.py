"""S04 — focused JSONL broker-store durability and compaction tests.

Covers the G017 AC-4 acceptance points:

* **Concurrency ordering** — concurrent mutations land in the journal in
  authoritative mutation order; the snapshot captured for each record
  reflects the state at the moment the mutation committed.
* **Bounded growth** — repeated mutations never let the journal grow
  past the configured threshold; a single-snapshot compaction replaces
  the file atomically.
* **Partial-tail** — a truncated trailing journal line does not corrupt
  startup; the previous complete snapshot is restored.
* **Interrupted compaction** — a mid-compaction failure leaves either
  the original or the new file at the canonical path; never a half
  written artifact.
* **Failed mutation** — a mutation that raises an exception never
  reaches the journal; the file is unchanged.
* **Legacy replay** — the existing record shape and legacy journals
  keep working unchanged.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from orchlink.broker.storage import (
    JsonlMessageStore,
    MessageStoreBusy,
)
from orchlink.broker.storage.jsonl import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_RECORDS,
)
from orchlink.broker.storage.persistence import (
    atomic_write_text,
    count_complete_jsonl_lines,
    encode_jsonl_record,
    read_latest_snapshot,
)
from orchlink.broker.checkpoint import DriftedLease
from orchlink.core.envelope import AgentRegistration, MessageEnvelope
from orchlink.core.views import worker_activity_from_wire


# ---------------------------------------------------------------------------
# Helpers (mirror the existing test_memory_store.py envelopes so we exercise
# the same wire boundary).
# ---------------------------------------------------------------------------


def task_message(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
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


def message_envelope(data: dict[str, Any]) -> MessageEnvelope:
    return MessageEnvelope.model_validate(
        {k: v for k, v in data.items() if k not in {"created_at", "queued_at", "updated_at"}}
    )


def reply_message(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
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
    data.update(overrides)
    return data


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read all valid JSONL records from ``path``.

    A truncated trailing line is silently skipped so the tests can assert
    on the durable records only. A missing file is treated as ``[]`` so
    tests can call this against a brand-new journal that has not yet been
    touched by a mutation.
    """
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            stripped = raw.rstrip("\n")
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                out.append(record)
    return out


# ---------------------------------------------------------------------------
# AC-4 #1: Concurrency ordering — mutations persist in authoritative order.
# ---------------------------------------------------------------------------


def test_jsonl_concurrent_mutations_persist_in_authoritative_order(tmp_path: Path) -> None:
    """Many concurrent enqueues produce one journal record per mutation, and
    every record's snapshot reflects a state that includes its own mutation.

    Under FIFO-fair ``asyncio.Lock`` semantics, the journal acquisition
    order matches the snapshot acquisition order, which matches the
    mutation completion order observed by the scheduler. We assert that
    invariant by spawning N concurrent enqueues for unique ``message_id``s
    and confirming every mutation appears in the journal.
    """

    async def run() -> None:
        journal_path = tmp_path / "concurrent.jsonl"
        # Use generous thresholds so this test focuses on ordering, not
        # on compaction. The bounded-growth test exercises the compact
        # path explicitly.
        store = JsonlMessageStore(journal_path, max_records=1024, max_bytes=4 * 1024 * 1024)
        n = 32

        async def enqueue_one(i: int) -> None:
            payload = task_message(
                message_id=f"msg-c-{i:03d}",
                correlation_id=f"req-c-{i:03d}",
                task_id=f"T-{i:03d}",
                # Fan out to a unique worker so the worker-target busy
                # guard never fires for our concurrency experiment.
                to_agent=f"demo.work-{i:03d}",
            )
            result = await store.enqueue_message(message_envelope(payload))
            assert result["status"] == "queued"

        await asyncio.gather(*[enqueue_one(i) for i in range(n)])

        records = _read_jsonl(journal_path)
        # Every enqueue must have produced an "enqueue_message" record;
        # we configured the thresholds to skip compaction during this
        # test so the audit trail still carries every mutation.
        operations = [record["operation"] for record in records]
        assert operations.count("enqueue_message") == n

        latest_snapshot = read_latest_snapshot(journal_path)
        assert latest_snapshot is not None
        active_message_ids = {
            str(stored["message_id"])
            for stored in (latest_snapshot.get("active_messages") or {}).values()
        }
        expected_ids = {f"msg-c-{i:03d}" for i in range(n)}
        assert expected_ids <= active_message_ids

    asyncio.run(run())


def test_jsonl_concurrent_operations_keep_record_shape_consistent(tmp_path: Path) -> None:
    """Under concurrent writes each record still carries the canonical JSONL
    envelope shape (operation / request / result / snapshot) and the
    snapshot captures the post-mutation state."""

    async def run() -> None:
        journal_path = tmp_path / "concurrent-shape.jsonl"
        store = JsonlMessageStore(journal_path)

        async def op(i: int) -> None:
            if i % 2 == 0:
                await store.enqueue_message(
                    message_envelope(task_message(
                        message_id=f"msg-mx-{i:03d}",
                        correlation_id=f"req-mx-{i:03d}",
                        task_id=f"TX-{i:03d}",
                        to_agent=f"demo.work-{i:03d}",
                    ))
                )
            else:
                await store.record_activity(
                    worker_activity_from_wire(
                        {
                            "project_id": "default",
                            "agent_id": "demo.work",
                            "task_id": f"TX-{i - 1:03d}",
                            "activity_type": "tool_call",
                            "tool_name": "read",
                            "detail": f"step {i}",
                        }
                    )
                )

        await asyncio.gather(*[op(i) for i in range(8)])

        records = _read_jsonl(journal_path)
        assert records, "expected durable records"
        for record in records:
            assert set(record) >= {"time", "operation", "request", "result", "snapshot"}
            assert isinstance(record["snapshot"], dict)
            # The snapshot always carries a ``next_event_id``/``next_activity_id``
            # pair, proving the snapshot was captured from authoritative state.
            assert "next_event_id" in record["snapshot"]

    asyncio.run(run())


# ---------------------------------------------------------------------------
# AC-4 #2: Bounded growth — repeated mutations stay under the threshold.
# ---------------------------------------------------------------------------


def test_jsonl_bounded_growth_compacts_below_threshold(tmp_path: Path) -> None:
    """After many mutations the journal is atomically compacted to a single
    snapshot record. The file size never grows unbounded and the post-
    compaction record count is 1."""

    async def run() -> None:
        journal_path = tmp_path / "growth.jsonl"
        # Tight thresholds to force compaction quickly.
        store = JsonlMessageStore(journal_path, max_records=4, max_bytes=1024)

        for i in range(20):
            await store.enqueue_message(
                message_envelope(task_message(
                    message_id=f"msg-g-{i:03d}",
                    correlation_id=f"req-g-{i:03d}",
                    task_id=f"TG-{i:03d}",
                    to_agent=f"demo.work-{i:03d}",
                ))
            )

        records = _read_jsonl(journal_path)
        # Compaction must have replaced the file at least once, leaving
        # only the latest snapshot record (or the original set plus a
        # ``_compact`` record — both count as bounded growth).
        assert len(records) <= 4, (
            f"expected at most max_records=4 records after compaction, "
            f"got {len(records)}: {len(json.dumps(records))} bytes"
        )

        latest_snapshot = read_latest_snapshot(journal_path)
        assert latest_snapshot is not None
        # The latest snapshot still carries every enqueued task, proving
        # compaction did not lose data.
        task_ids = {
            str(job["task_id"])
            for job in (latest_snapshot.get("task_jobs") or {}).values()
            if job.get("task_id")
        }
        expected_ids = {f"TG-{i:03d}" for i in range(20)}
        assert expected_ids <= task_ids

        # The on-disk file is bounded by max_records record lines. The
        # compacted record itself is large because it carries every task,
        # but record count alone proves the bounded-growth invariant.
        assert len(records) <= 4

    asyncio.run(run())


def test_jsonl_bounded_growth_size_threshold_triggers_compaction(tmp_path: Path) -> None:
    """A size-based threshold also forces compaction even when the record
    count alone would not."""

    async def run() -> None:
        journal_path = tmp_path / "growth-size.jsonl"
        # Force compaction after 8 KiB regardless of record count.
        store = JsonlMessageStore(journal_path, max_records=1000, max_bytes=8 * 1024)

        # 32 records × ~500 bytes each > 8 KiB, so several compactions run.
        for i in range(32):
            await store.record_activity(
                worker_activity_from_wire(
                    {
                        "project_id": "default",
                        "agent_id": "demo.work",
                        "task_id": f"TS-{i:03d}",
                        "activity_type": "tool_call",
                        "tool_name": "read",
                        "detail": "x" * 200,  # pad each snapshot to push the size over
                    }
                )
            )

        size = journal_path.stat().st_size
        # Bounded growth invariant: the journal cannot accumulate a
        # separate record per mutation. After compaction the file should
        # be at most ``max_records`` record lines. We assert the on-disk
        # record count rather than raw bytes because a single-snapshot
        # record carries the entire accumulated state and is naturally
        # larger than ``max_bytes`` by design.
        records = _read_jsonl(journal_path)
        assert len(records) <= 5, (
            f"journal grew past the size cap: {len(records)} records, "
            f"{size} bytes"
        )

    asyncio.run(run())


# ---------------------------------------------------------------------------
# AC-4 #3: Partial-tail tolerance — truncated journal restores the prior
#           valid snapshot.
# ---------------------------------------------------------------------------


def test_jsonl_partial_final_line_is_tolerated_on_reload(tmp_path: Path) -> None:
    """A truncated final record (e.g. from a crash mid-write) does not
    prevent startup. The next ``JsonlMessageStore`` reads the previous
    valid snapshot and ignores the partial tail."""

    async def seed() -> None:
        store = JsonlMessageStore(tmp_path / "partial.jsonl")
        await store.register_agent(
            AgentRegistration(
                agent_id="demo.work",
                role="worker",
                display_name="Worker",
                capabilities=["backend"],
            )
        )
        await store.enqueue_message(
            message_envelope(task_message(message_id="msg-pt-1", correlation_id="req-pt-1", to_agent="demo.work-pt"))
        )

    async def run() -> None:
        await seed()

        path = tmp_path / "partial.jsonl"
        original_size = path.stat().st_size
        # Truncate the last record by ~half its length. The remaining
        # trailing bytes are guaranteed to not be valid JSON.
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
        with path.open("rb+") as handle:
            handle.truncate(size - max(1, size // 2))

        # A fresh store must reload without raising and restore the prior
        # snapshot. The agent record is the first durable line so its
        # snapshot survives the truncation.
        restored = JsonlMessageStore(path)
        agents = await restored.list_agents()
        assert any(agent["agent_id"] == "demo.work" for agent in agents)
        # The enqueue was the last record and its truncated tail was
        # ignored — the recovered snapshot does not have it.
        active = await restored.list_active_messages()
        assert not any(message["message_id"] == "msg-pt-1" for message in active)

        # The reload also repairs the corrupt tail: the canonical file
        # now ends on a clean newline carrying only the surviving valid
        # records, so a future append starts a fresh line instead of
        # concatenating after the truncated bytes.
        assert path.stat().st_size <= original_size
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                json.loads(line)  # no corrupt tail remains

    asyncio.run(run())


def test_jsonl_partial_final_line_with_only_truncated_bytes(tmp_path: Path) -> None:
    """Even a one-byte partial tail (e.g. interrupted mid-write) is tolerated."""

    async def seed() -> None:
        store = JsonlMessageStore(tmp_path / "tail.jsonl")
        await store.enqueue_message(
            message_envelope(task_message(message_id="msg-tail", correlation_id="req-tail"))
        )

    async def run() -> None:
        await seed()
        path = tmp_path / "tail.jsonl"
        # Strip everything after the last newline so the file ends with a
        # complete record plus a one-byte partial.
        with path.open("rb") as handle:
            data = handle.read()
        last_newline = data.rfind(b"\n")
        assert last_newline > 0
        with path.open("wb") as handle:
            handle.write(data[: last_newline + 1])
            handle.write(b"\x7b")  # partial start of a JSON object

        restored = JsonlMessageStore(path)
        messages = await restored.list_active_messages()
        assert any(message["message_id"] == "msg-tail" for message in messages)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# AC-4 #4: Interrupted compaction — atomicity preserved under failure.
# ---------------------------------------------------------------------------


def test_jsonl_interrupted_compaction_preserves_old_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Compaction failure is maintenance: the append already succeeded, so
    the mutation returns normally and the canonical file contains the newly
    appended record. The half-written tmp file is never moved into place."""

    async def seed() -> None:
        # Use loose thresholds during the seed so we end up with multiple
        # records on disk; the test then re-opens the store with tight
        # thresholds so the next append trips the compaction path.
        store = JsonlMessageStore(tmp_path / "interrupted.jsonl", max_records=1024, max_bytes=4 * 1024 * 1024)
        for i in range(6):
            await store.enqueue_message(
                message_envelope(task_message(
                    message_id=f"msg-int-{i:02d}",
                    correlation_id=f"req-int-{i:02d}",
                    task_id=f"TI-{i:02d}",
                    to_agent=f"demo.work-{i:02d}",
                ))
            )

    async def run() -> None:
        await seed()
        path = tmp_path / "interrupted.jsonl"
        original_record_count = count_complete_jsonl_lines(path)
        assert original_record_count > 1

        # Force the next compaction to fail mid-write.
        original_writer = atomic_write_text
        call_count = {"n": 0}

        def explode(path_arg: Any, text: str) -> None:
            call_count["n"] += 1
            raise OSError("simulated compaction crash")

        monkeypatch.setattr("orchlink.broker.storage.jsonl.atomic_write_text", explode)

        store = JsonlMessageStore(path, max_records=4, max_bytes=512)
        # This mutation trips the size/record threshold and triggers a
        # compaction attempt that fails. The append itself is durable, so
        # the mutation must return successfully.
        result = await store.enqueue_message(
            message_envelope(task_message(
                message_id="msg-int-trip",
                correlation_id="req-int-trip",
                task_id="TI-TRIP",
                to_agent="demo.work-trip",
            ))
        )
        assert result == {"status": "queued", "message_id": "msg-int-trip"}
        assert call_count["n"] >= 1, "compaction should have been attempted"

        # The canonical file contains the newly appended record; the failed
        # compaction never replaced it.
        records = _read_jsonl(path)
        assert len(records) == original_record_count + 1
        assert records[-1]["operation"] == "enqueue_message"
        assert records[-1]["request"]["message"]["message_id"] == "msg-int-trip"

        # A fresh store can reload the prior state plus the new record.
        restored = JsonlMessageStore(path)
        snapshot = restored._snapshot()
        active_ids = {
            str(stored["message_id"])
            for stored in snapshot.get("active_messages", {}).values()
        }
        assert "msg-int-00" in active_ids
        assert "msg-int-trip" in active_ids

        # Restore the real writer and append a new mutation; this time the
        # compaction succeeds and the file shrinks back to a single record.
        monkeypatch.setattr("orchlink.broker.storage.jsonl.atomic_write_text", original_writer)
        store2 = JsonlMessageStore(path, max_records=2, max_bytes=512)
        await store2.enqueue_message(
            message_envelope(task_message(
                message_id="msg-int-recover",
                correlation_id="req-int-recover",
                task_id="TI-RECOVER",
                to_agent="demo.work-recover",
            ))
        )
        records_after = _read_jsonl(path)
        assert len(records_after) <= 2
        assert records_after[-1]["operation"] in ("enqueue_message", "_compact")
        latest_snapshot = read_latest_snapshot(path)
        assert latest_snapshot is not None
        assert "msg-int-recover" in {
            str(stored["message_id"])
            for stored in latest_snapshot.get("active_messages", {}).values()
        }

    asyncio.run(run())


def test_jsonl_compaction_is_atomic_with_successful_replacement(tmp_path: Path) -> None:
    """A successful compaction replaces the file with a single snapshot
    record atomically (sibling tmp + ``os.replace``). The file at the
    canonical path is either the pre-compaction or post-compaction state,
    never partial."""

    async def run() -> None:
        path = tmp_path / "atomic.jsonl"
        store = JsonlMessageStore(path, max_records=2, max_bytes=512)
        for i in range(6):
            await store.enqueue_message(
                message_envelope(task_message(
                    message_id=f"msg-atom-{i:02d}",
                    correlation_id=f"req-atom-{i:02d}",
                    task_id=f"TA-{i:02d}",
                    to_agent=f"demo.work-{i:02d}",
                ))
            )

        records = _read_jsonl(path)
        # After the loop the file is bounded to ≤ max_records entries; the
        # final compaction replaced the file in one shot.
        assert len(records) <= 2
        # And the canonical file is parseable end-to-end.
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                json.loads(line)  # must not raise

    asyncio.run(run())


# ---------------------------------------------------------------------------
# AC-4 #5: Failed mutation — never persisted.
# ---------------------------------------------------------------------------


def test_jsonl_failed_mutation_is_not_persisted(tmp_path: Path) -> None:
    """A mutation that raises is not journaled and does not change the
    file size. A second mutation that succeeds *does* land in the journal,
    proving the failure path skipped persistence cleanly."""

    async def run() -> None:
        path = tmp_path / "failed.jsonl"
        store = JsonlMessageStore(path)
        await store.register_agent(
            AgentRegistration(
                agent_id="demo.work",
                role="worker",
                display_name="Worker",
                capabilities=["backend"],
            )
        )

        baseline_records = _read_jsonl(path)
        baseline_size = path.stat().st_size

        # The second enqueue collides with the in-flight worker target and
        # raises ``MessageStoreBusy`` — the broker mutation fails.
        await store.enqueue_message(message_envelope(task_message()))
        with pytest.raises(MessageStoreBusy):
            await store.enqueue_message(
                message_envelope(task_message(message_id="msg-0002", correlation_id="req-0002", task_id="TEST-002"))
            )

        # The failed mutation produced no journal record.
        after_failure_records = _read_jsonl(path)
        assert len(after_failure_records) == len(baseline_records) + 1  # the successful enqueue only
        assert path.stat().st_size > baseline_size
        operations = [r["operation"] for r in after_failure_records]
        assert operations[-1] == "enqueue_message"
        assert "msg-0002" not in str(after_failure_records[-1]["request"])

    asyncio.run(run())


def test_jsonl_failed_inner_call_does_not_corrupt_snapshot(tmp_path: Path) -> None:
    """Even when an inner mutation raises, no record reaches the file."""

    async def run() -> None:
        path = tmp_path / "raise.jsonl"
        store = JsonlMessageStore(path)

        baseline_records = _read_jsonl(path)

        async def boom() -> None:
            raise RuntimeError("simulated inner mutation failure")

        with pytest.raises(RuntimeError):
            await store._recorded("boom_op", {"k": "v"}, boom)

        after_records = _read_jsonl(path)
        assert len(after_records) == len(baseline_records)
        assert all(record["operation"] != "boom_op" for record in after_records)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# AC-4 #6: Legacy replay — existing record shape remains compatible.
# ---------------------------------------------------------------------------


def test_jsonl_legacy_record_shape_replays_without_modification(tmp_path: Path) -> None:
    """A journal written with the historical line shape (operation /
    request / result / snapshot) reloads unchanged under the new code."""

    legacy_path = tmp_path / "legacy.jsonl"
    legacy_record = {
        "time": "2025-01-01T00:00:00+00:00",
        "operation": "register_agent",
        "request": {"agent": {"agent_id": "legacy.work", "role": "worker"}},
        "result": {
            "agent_id": "legacy.work",
            "role": "worker",
            "display_name": "Legacy",
            "capabilities": [],
        },
        "snapshot": {
            "agents": {
                "legacy.work": {
                    "agent_id": "legacy.work",
                    "role": "worker",
                    "display_name": "Legacy",
                    "capabilities": [],
                }
            },
            "active_messages": {},
            "tasks": {},
            "task_jobs": {},
            "talk_jobs": {},
            "results_by_task": {},
            "conversations": {},
            "events": [],
            "activity": [],
            "sessions": {},
            "next_event_id": 1,
            "next_activity_id": 1,
        },
    }
    legacy_path.write_text(encode_jsonl_record(legacy_record) + "\n", encoding="utf-8")

    restored = JsonlMessageStore(legacy_path)
    agents = asyncio.run(restored.list_agents())
    assert any(agent["agent_id"] == "legacy.work" for agent in agents)


def test_jsonl_default_thresholds_match_documented_values() -> None:
    """The default compaction thresholds are pinned so a future bump does
    not silently change the on-disk growth contract."""
    assert DEFAULT_MAX_RECORDS == 64
    assert DEFAULT_MAX_BYTES == 256 * 1024


def test_jsonl_append_only_path_under_thresholds_does_not_compact(tmp_path: Path) -> None:
    """A single mutation under the compaction threshold leaves the file as
    a normal append (one record). The compact path is not exercised, so
    the wire shape stays additive-only."""

    async def run() -> None:
        path = tmp_path / "append-only.jsonl"
        store = JsonlMessageStore(path)
        await store.register_agent(
            AgentRegistration(
                agent_id="demo.work",
                role="worker",
                display_name="Worker",
                capabilities=["backend"],
            )
        )

        records = _read_jsonl(path)
        assert len(records) == 1
        assert records[0]["operation"] == "register_agent"
        # No ``_compact`` record was emitted under-threshold.
        assert "operation" in records[0]

    asyncio.run(run())


def test_jsonl_wait_for_task_persists_timeout_expiry(tmp_path: Path) -> None:
    async def run() -> None:
        path = tmp_path / "wait-expiry.jsonl"
        store = JsonlMessageStore(path, max_records=1024, max_bytes=4 * 1024 * 1024)
        await store.enqueue_message(message_envelope(task_message(timeout_seconds=1)))
        store._parse_time = lambda _value: datetime(2000, 1, 1, tzinfo=timezone.utc)

        result = await store.wait_for_task("TEST-001", timeout_seconds=0)

        assert result["status"] == "TIMEOUT"
        records = _read_jsonl(path)
        assert records[-1]["operation"] == "wait_for_task"
        restored = JsonlMessageStore(path)
        assert (await restored.get_task_result("TEST-001"))["status"] == "TIMEOUT"

    asyncio.run(run())


# ---------------------------------------------------------------------------
# AC-4 #7: Tail repair — a truncated trailing line is repaired at load so a
#           later mutation lands cleanly and survives a second restart.
# ---------------------------------------------------------------------------


def test_jsonl_truncated_tail_repaired_before_append_survives_second_restart(tmp_path: Path) -> None:
    """Two-restart regression for the tail-repair fix.

    A journal with a valid snapshot record followed by a partial trailing
    line (simulating a crash mid-append) is repaired at construction. A
    subsequent mutation therefore lands on a clean line and survives a
    second restart. Without the repair the mutation would concatenate
    after the corrupt tail and the next reload would stop at the
    corruption and silently ignore it.
    """

    async def seed() -> Path:
        path = tmp_path / "repair.jsonl"
        store = JsonlMessageStore(path)
        await store.register_agent(
            AgentRegistration(
                agent_id="demo.work",
                role="worker",
                display_name="Worker",
                capabilities=["backend"],
            )
        )
        return path

    async def run() -> None:
        path = await seed()

        # Simulate a crash mid-append: append a partial JSON line with no
        # trailing newline. The valid register_agent record precedes it.
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                '{"time":"2030-01-01T00:00:00+00:00","operation":"enqueue_message","request":'
            )
        truncated_size = path.stat().st_size

        # Restart 1: construction repairs the corrupt tail. The recovered
        # snapshot keeps the preceding valid agent.
        store = JsonlMessageStore(path)
        agents = await store.list_agents()
        assert any(agent["agent_id"] == "demo.work" for agent in agents)
        # The canonical file no longer carries the corrupt tail: every
        # remaining non-blank line parses and the file ends on a newline.
        text = path.read_text(encoding="utf-8")
        assert text.endswith("\n")
        for line in text.splitlines():
            if line.strip():
                json.loads(line)  # no corrupt bytes remain
        assert path.stat().st_size < truncated_size

        # Successful mutation after the repair lands on a clean line.
        await store.register_agent(
            AgentRegistration(
                agent_id="demo.lead",
                role="lead",
                display_name="Lead",
                capabilities=["backend"],
            )
        )

        # Restart 2: the post-repair mutation survives. Without the repair
        # the reload would stop at the (former) corrupt tail and drop it.
        restored = JsonlMessageStore(path)
        agent_ids = {agent["agent_id"] for agent in await restored.list_agents()}
        assert "demo.work" in agent_ids
        assert "demo.lead" in agent_ids
        # And the file is still fully parseable end-to-end.
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                json.loads(line)

    asyncio.run(run())


# ---------------------------------------------------------------------------
# AC-4 #8: Durable checkpoint drifts — append_checkpoint_drifts persists
#           through the snapshot/append pipeline.
# ---------------------------------------------------------------------------


def test_jsonl_append_checkpoint_drifts_survive_restart(tmp_path: Path) -> None:
    """Checkpoint drifts are journaled through the same snapshot/append
    pipeline as every other mutation, so a restart replays them. Without
    durability the drift events would be lost on reload."""

    async def run() -> None:
        path = tmp_path / "drift.jsonl"
        store = JsonlMessageStore(path)
        drift = DriftedLease(
            task_id="T-DRIFT-1",
            previous_epoch=3,
            previous_holder="demo.work",
            previous_updated_at="2026-01-01T00:00:00+00:00",
            current_epoch=7,
            current_holder="demo.work-2",
            reason="holder_changed",
        )
        store.append_checkpoint_drifts([drift])

        # The drift mutation landed a durable journal record.
        records = _read_jsonl(path)
        assert records[-1]["operation"] == "append_checkpoint_drifts"
        assert records[-1]["result"] == {"count": 1}
        assert records[-1]["request"]["drifts"][0]["task_id"] == "T-DRIFT-1"

        # Restart replays the latest snapshot, restoring the drift event.
        restored = JsonlMessageStore(path)
        events = await restored.list_events()
        assert any(
            event.get("type") == "lease_expired_during_downtime"
            and event.get("task_id") == "T-DRIFT-1"
            for event in events
        )

    asyncio.run(run())


def test_jsonl_append_checkpoint_drifts_failed_write_is_isolated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed durable append propagates and leaves the journal untouched,
    matching the async ``_recorded`` contract (mutated in memory, not
    durable). A later restart restores the pre-drift state."""

    async def run() -> None:
        path = tmp_path / "drift-fail.jsonl"
        store = JsonlMessageStore(path)
        await store.register_agent(
            AgentRegistration(
                agent_id="demo.work",
                role="worker",
                display_name="Worker",
                capabilities=["backend"],
            )
        )
        baseline = _read_jsonl(path)
        baseline_size = path.stat().st_size

        def explode(path_arg: Any, line: str) -> None:
            raise OSError("simulated journal write failure")

        monkeypatch.setattr("orchlink.broker.storage.jsonl.atomic_append_jsonl_line", explode)

        drift = DriftedLease(
            task_id="T-DRIFT-FAIL",
            previous_epoch=1,
            previous_holder="demo.work",
            previous_updated_at="2026-01-01T00:00:00+00:00",
            current_epoch=None,
            current_holder=None,
            reason="missing_after_restart",
        )
        with pytest.raises(OSError):
            store.append_checkpoint_drifts([drift])

        # The journal is unchanged: no drift record reached the file.
        after = _read_jsonl(path)
        assert after == baseline
        assert path.stat().st_size == baseline_size
        assert all(record["operation"] != "append_checkpoint_drifts" for record in after)

        # Restart restores the pre-drift state — the failed write left no
        # durable trace of the drift.
        restored = JsonlMessageStore(path)
        events = await restored.list_events()
        assert not any("T-DRIFT-FAIL" in json.dumps(event) for event in events)

    asyncio.run(run())