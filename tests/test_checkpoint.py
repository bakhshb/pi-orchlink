"""AC-3: durable broker checkpoint artifact for Orchlink interruption recovery.

These tests pin the contract documented in orchlink.broker.checkpoint:

- The file is written under ``<project_root>/.orch/run/broker-checkpoint.json``.
- Each entry contains ``task_id``, ``epoch``, ``holder``, ``status``, and an
  ISO-8601 ``updated_at`` timestamp.
- Both ``in_flight`` and ``recently_settled`` statuses are first-class; the
  ``Checkpoint`` keeps them separate via ``in_flight`` / ``recently_settled``
  properties.
- The module is store-backend-agnostic: it does not import either in-memory or
  jsonl store internals, so ``orch resume`` and the broker can rely on the file
  regardless of the active ``store_backend``.
- The file is updated atomically on every ``record_lease`` call.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def test_checkpoint_path_lives_under_project_run_dir():
    from orchlink.broker.checkpoint import checkpoint_path

    project_root = Path("/tmp/orch-checkpoint-test")
    expected = project_root / ".orch" / "run" / "broker-checkpoint.json"
    assert checkpoint_path(project_root) == expected


def test_checkpoint_module_does_not_import_storage_backends():
    """The checkpoint module must be store-backend-agnostic.

    ``orch resume`` and the broker reconcile against this file on every
    restart; if it depended on the in-memory or jsonl storage modules, a
    user running with ``store_backend: jsonl`` would see different
    checkpoint behavior than one running with the default memory store.
    """
    src_path = ROOT / "src" / "orchlink" / "broker" / "checkpoint.py"
    text = src_path.read_text(encoding="utf-8")
    for forbidden in (
        "from orchlink.broker.storage",
        "import orchlink.broker.storage",
    ):
        assert forbidden not in text, f"checkpoint.py must not reference {forbidden!r}"


def test_checkpoint_file_contains_required_fields(tmp_path: Path):
    """Persisted JSON contains task_id, epoch, holder, status, updated_at."""
    from orchlink.broker.checkpoint import record_lease

    project_root = tmp_path / "proj"
    record_lease(project_root, "T001", epoch=1, holder="work-1", status="in_flight")

    path = project_root / ".orch" / "run" / "broker-checkpoint.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert isinstance(raw["last_checkpoint_at"], str) and raw["last_checkpoint_at"]
    assert isinstance(raw["leases"], list)
    assert len(raw["leases"]) == 1
    lease = raw["leases"][0]
    # AC-3 specifically requires task_id, epoch, holder; status and
    # updated_at round-trip the in-flight / recently-settled distinction.
    for required_field in ("task_id", "epoch", "holder", "status", "updated_at"):
        assert required_field in lease, f"missing required field: {required_field}"
    assert lease["task_id"] == "T001"
    assert lease["epoch"] == 1
    assert lease["holder"] == "work-1"
    assert lease["status"] == "in_flight"


def test_checkpoint_records_inflight_and_recently_settled_separately(tmp_path: Path):
    """Both in-flight and recently-settled leases are recorded, distinct."""
    from orchlink.broker.checkpoint import record_lease

    project_root = tmp_path / "proj"
    record_lease(project_root, "T001", epoch=1, holder="work-1", status="in_flight")
    record_lease(project_root, "T002", epoch=2, holder="work-1", status="recently_settled")
    record_lease(project_root, "T003", epoch=3, holder="work-2", status="in_flight")

    from orchlink.broker.checkpoint import load_checkpoint
    cp = load_checkpoint(project_root / ".orch" / "run" / "broker-checkpoint.json")
    in_flight_ids = sorted(lease.task_id for lease in cp.in_flight)
    settled_ids = sorted(lease.task_id for lease in cp.recently_settled)
    assert in_flight_ids == ["T001", "T003"]
    assert settled_ids == ["T002"]


def test_checkpoint_updates_on_lease_change(tmp_path: Path):
    """A second record_lease for the same task replaces the prior entry."""
    from orchlink.broker.checkpoint import load_checkpoint, record_lease

    project_root = tmp_path / "proj"
    record_lease(project_root, "T010", epoch=1, holder="work-1", status="in_flight")

    cp_before = load_checkpoint(
        project_root / ".orch" / "run" / "broker-checkpoint.json"
    )
    assert len(cp_before.leases) == 1
    assert cp_before.leases[0].epoch == 1
    assert cp_before.leases[0].holder == "work-1"

    # Lease change: same task id, new epoch, new holder.
    record_lease(project_root, "T010", epoch=2, holder="work-2", status="in_flight")

    cp_after = load_checkpoint(
        project_root / ".orch" / "run" / "broker-checkpoint.json"
    )
    assert len(cp_after.leases) == 1
    assert cp_after.leases[0].epoch == 2
    assert cp_after.leases[0].holder == "work-2"
    assert cp_after.leases[0].task_id == "T010"


def test_checkpoint_status_transition_in_flight_to_recently_settled(tmp_path: Path):
    """Status transitions are recorded: in_flight -> recently_settled."""
    from orchlink.broker.checkpoint import load_checkpoint, record_lease

    project_root = tmp_path / "proj"
    record_lease(project_root, "T020", epoch=5, holder="work-1", status="in_flight")

    cp1 = load_checkpoint(project_root / ".orch" / "run" / "broker-checkpoint.json")
    assert [lease.task_id for lease in cp1.in_flight] == ["T020"]
    assert cp1.recently_settled == []

    record_lease(project_root, "T020", epoch=5, holder="work-1", status="recently_settled")

    cp2 = load_checkpoint(project_root / ".orch" / "run" / "broker-checkpoint.json")
    assert cp2.in_flight == []
    assert [lease.task_id for lease in cp2.recently_settled] == ["T020"]


def test_checkpoint_atomic_write_does_not_leave_partial_files(tmp_path: Path):
    """Atomic write produces either the previous file or the new file — not half."""
    from orchlink.broker.checkpoint import record_lease

    project_root = tmp_path / "proj"
    path = project_root / ".orch" / "run" / "broker-checkpoint.json"

    record_lease(project_root, "T030", epoch=1, holder="work-1", status="in_flight")
    raw_before = json.loads(path.read_text(encoding="utf-8"))
    assert raw_before["leases"][0]["task_id"] == "T030"

    record_lease(project_root, "T030", epoch=2, holder="work-2", status="in_flight")
    raw_after = json.loads(path.read_text(encoding="utf-8"))
    assert raw_after["leases"][0]["task_id"] == "T030"
    assert raw_after["leases"][0]["epoch"] == 2
    assert raw_after["leases"][0]["holder"] == "work-2"

    # No leftover .tmp files in the run directory.
    leftovers = [
        child for child in path.parent.iterdir()
        if child.name.startswith(path.name + ".") and child.name.endswith(".tmp")
    ]
    assert leftovers == [], f"unexpected tmp files: {leftovers}"


def test_checkpoint_load_handles_missing_and_corrupt_files(tmp_path: Path):
    """Missing or corrupt checkpoint yields an empty Checkpoint, never raises."""
    from orchlink.broker.checkpoint import load_checkpoint

    # Missing file -> empty checkpoint (broker can start clean).
    cp_missing = load_checkpoint(tmp_path / "nonexistent.json")
    assert cp_missing.leases == []
    assert cp_missing.version == 1
    assert isinstance(cp_missing.last_checkpoint_at, str)
    assert cp_missing.last_checkpoint_at  # non-empty

    # Corrupt JSON -> empty checkpoint.
    bad = tmp_path / "broker-checkpoint.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{ this is not json", encoding="utf-8")
    cp_corrupt = load_checkpoint(bad)
    assert cp_corrupt.leases == []
    assert cp_corrupt.version == 1


def test_checkpoint_record_lease_rejects_unknown_status(tmp_path: Path):
    from orchlink.broker.checkpoint import record_lease

    try:
        record_lease(tmp_path, "T040", epoch=1, holder="work-1", status="bogus")  # type: ignore[arg-type]
    except ValueError:
        return
    raise AssertionError("record_lease should reject unknown status values")


async def _broker_request(app, method: str, path: str, **kwargs):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def _broker_post(app, path: str, **kwargs):
    return asyncio.run(_broker_request(app, "POST", path, **kwargs))


def _broker_get(app, path: str, **kwargs):
    return asyncio.run(_broker_request(app, "GET", path, **kwargs))


def _checkpoint_task_message(task_id: str = "T050") -> dict:
    return {
        "protocol": "orch-a2a-v1",
        "message_id": "msg-checkpoint-1",
        "correlation_id": "corr-checkpoint-1",
        "project_id": "test",
        "conversation_id": "test-default",
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
        "payload": {"mode": "REVIEW", "intent": "check"},
    }


def test_broker_writes_checkpoint_on_delivery_and_reply(tmp_path: Path):
    from orchlink.broker.checkpoint import load_checkpoint
    from orchlink.broker.main import create_app
    from orchlink.broker.settings import Settings
    from orchlink.broker.storage.memory import MemoryMessageStore

    store_path = tmp_path / ".orch" / "run" / "orchlink-journal.jsonl"
    app = create_app(
        store=MemoryMessageStore(),
        settings=Settings(api_key="test-key", store_path=str(store_path)),
    )
    auth = {"X-API-Key": "test-key", "X-Orchlink-Project-ID": "test"}
    message = _checkpoint_task_message()

    _broker_post(app, "/v1/agents/register", headers=auth, json={"agent_id": "test.work", "role": "worker", "project_id": "test"})
    _broker_post(app, "/v1/messages/send", headers=auth, json=message)
    delivered = _broker_get(app, "/v1/agents/test.work/next?wait_seconds=1", headers=auth).json()["message"]

    cp = load_checkpoint(tmp_path / ".orch" / "run" / "broker-checkpoint.json")
    assert [(lease.task_id, lease.epoch, lease.holder, lease.status) for lease in cp.in_flight] == [
        ("T050", 1, "test.work", "in_flight")
    ]

    reply = {**message, "message_id": "reply-checkpoint-1", "from_agent": "test.work", "to_agent": "test.lead", "type": "RESULT", "status": "DONE", "requires_reply": False}
    response = _broker_post(
        app,
        "/v1/messages/msg-checkpoint-1/reply",
        headers={**auth, "X-Orchlink-Lease-Epoch": str(delivered["lease"]["epoch"]), "X-Orchlink-Lease-Holder": delivered["lease"]["holder"]},
        json=reply,
    )
    assert response.status_code == 200, response.text
    cp = load_checkpoint(tmp_path / ".orch" / "run" / "broker-checkpoint.json")
    assert cp.in_flight == []
    assert [(lease.task_id, lease.epoch, lease.holder, lease.status) for lease in cp.recently_settled] == [
        ("T050", 1, "test.work", "recently_settled")
    ]


# ---------------------------------------------------------------------------
# AC-4: broker-startup reconciliation between prior checkpoint and current
# in-memory lease view.
# ---------------------------------------------------------------------------


def _build_prior_checkpoint(*pairs: tuple[str, int, str, str]) -> "Checkpoint":  # noqa: F821
    """Build a Checkpoint from ``(task_id, epoch, holder, status)`` rows."""
    from orchlink.broker.checkpoint import Checkpoint, CheckpointLease
    return Checkpoint(
        leases=[
            CheckpointLease(
                task_id=tid,
                epoch=epoch,
                holder=holder,
                status=status,  # type: ignore[arg-type]
                updated_at="2026-07-01T00:00:00+00:00",
            )
            for (tid, epoch, holder, status) in pairs
        ]
    )


def test_broker_reconciles_stale_leases():
    """AC-4: stale-leases reconciliation surfaces drifted leases with previous
    vs. current epoch and the affected task id.
    """
    from orchlink.broker.checkpoint import reconcile_checkpoint

    prior = _build_prior_checkpoint(
        ("T100", 1, "work-1", "in_flight"),  # gone after restart
        ("T101", 2, "work-1", "in_flight"),  # reacquired with a new epoch
        ("T102", 3, "work-1", "in_flight"),  # handed to a different agent
        ("T103", 4, "work-1", "in_flight"),  # healthy: unchanged
        ("T104", 5, "work-1", "recently_settled"),  # history, not drift
    )
    current = {
        "T101": (3, "work-1"),
        "T102": (3, "work-2"),
        "T103": (4, "work-1"),
        # T100 absent (worker gone)
        # T104 absent (already settled)
    }

    drifts = reconcile_checkpoint(prior, current)

    drift_by_task = {d.task_id: d for d in drifts}

    # T100: missing_after_restart -> previous epoch 1, current None.
    assert "T100" in drift_by_task
    assert drift_by_task["T100"].previous_epoch == 1
    assert drift_by_task["T100"].current_epoch is None
    assert drift_by_task["T100"].previous_holder == "work-1"
    assert drift_by_task["T100"].current_holder is None
    assert drift_by_task["T100"].reason == "missing_after_restart"

    # T101: epoch 2 -> 3, holder unchanged.
    assert "T101" in drift_by_task
    assert drift_by_task["T101"].previous_epoch == 2
    assert drift_by_task["T101"].current_epoch == 3
    assert drift_by_task["T101"].previous_holder == "work-1"
    assert drift_by_task["T101"].current_holder == "work-1"
    assert drift_by_task["T101"].reason == "epoch_changed"

    # T102: holder changed at same epoch.
    assert "T102" in drift_by_task
    assert drift_by_task["T102"].previous_holder == "work-1"
    assert drift_by_task["T102"].current_holder == "work-2"
    assert drift_by_task["T102"].reason == "holder_changed"

    # T103: healthy lease, no drift.
    assert "T103" not in drift_by_task

    # T104: previously recently-settled -> not surfaced as drift (history).
    assert "T104" not in drift_by_task


def test_broker_reconcile_with_empty_prior_checkpoint_is_no_drift():
    from orchlink.broker.checkpoint import empty_checkpoint, reconcile_checkpoint

    assert reconcile_checkpoint(empty_checkpoint(), {}) == []
    assert reconcile_checkpoint(
        empty_checkpoint(),
        {"T001": (1, "work-1"), "T002": (2, "work-2")},
    ) == []


def test_broker_reconcile_with_empty_broker_state_marks_all_prior_as_drifted():
    """If the broker remembers nothing (cold restart), every prior in-flight
    lease shows up as missing-after-restart drift.
    """
    from orchlink.broker.checkpoint import reconcile_checkpoint

    prior = _build_prior_checkpoint(
        ("T200", 1, "work-1", "in_flight"),
        ("T201", 2, "work-2", "in_flight"),
        ("T202", 3, "work-1", "recently_settled"),
    )

    drifts = reconcile_checkpoint(prior, {})

    drifted_ids = sorted(d.task_id for d in drifts)
    assert drifted_ids == ["T200", "T201"]
    assert all(d.current_epoch is None for d in drifts)
    assert all(d.current_holder is None for d in drifts)
    assert all(d.reason == "missing_after_restart" for d in drifts)
