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
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

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


def test_broker_checkpoint_startup_uses_service_boundary_not_private_state():
    main_text = (ROOT / "src" / "orchlink" / "broker" / "main.py").read_text(encoding="utf-8")
    service_text = (ROOT / "src" / "orchlink" / "broker" / "service.py").read_text(encoding="utf-8")
    assert "store._state" not in main_text
    assert "getattr(store, \"_state\"" not in main_text
    assert "load_checkpoint" not in main_text
    assert "record_lease" not in main_text
    assert "service_obj.startup_reconcile_checkpoint()" in main_text
    assert "self.store.current_job_leases()" in service_text
    assert "self.store.append_checkpoint_drifts(" in service_text


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


def test_checkpoint_ordering_rejects_stale_delivery_after_settlement(tmp_path: Path):
    from orchlink.broker.checkpoint import load_checkpoint, record_lease

    project_root = tmp_path / "proj"
    record_lease(project_root, "T021", epoch=1, holder="work-1", status="in_flight")
    record_lease(project_root, "T021", epoch=1, holder="work-1", status="recently_settled")

    # A delayed delivery checkpoint for the same epoch must not resurrect the
    # task as in-flight after cancellation/reply settlement won the race.
    record_lease(project_root, "T021", epoch=1, holder="work-1", status="in_flight")

    cp = load_checkpoint(project_root / ".orch" / "run" / "broker-checkpoint.json")
    assert cp.in_flight == []
    assert [(lease.task_id, lease.epoch, lease.holder, lease.status) for lease in cp.recently_settled] == [
        ("T021", 1, "work-1", "recently_settled")
    ]


def test_checkpoint_higher_epoch_reclaim_overrides_prior_settlement(tmp_path: Path):
    from orchlink.broker.checkpoint import load_checkpoint, record_lease

    project_root = tmp_path / "proj"
    record_lease(project_root, "T022", epoch=1, holder="work-1", status="recently_settled")
    record_lease(project_root, "T022", epoch=2, holder="work-2", status="in_flight")

    cp = load_checkpoint(project_root / ".orch" / "run" / "broker-checkpoint.json")
    assert [(lease.task_id, lease.epoch, lease.holder, lease.status) for lease in cp.in_flight] == [
        ("T022", 2, "work-2", "in_flight")
    ]
    assert cp.recently_settled == []


def test_checkpoint_serializes_updates_without_losing_other_tasks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import orchlink.broker.checkpoint as checkpoint

    original_atomic_write = checkpoint._atomic_write_text

    def slow_atomic_write(path: Path, text: str) -> None:
        time.sleep(0.02)
        original_atomic_write(path, text)

    monkeypatch.setattr(checkpoint, "_atomic_write_text", slow_atomic_write)
    project_root = tmp_path / "proj"
    barrier = threading.Barrier(6)

    def record(index: int) -> None:
        barrier.wait(timeout=2)
        checkpoint.record_lease(project_root, f"T-lost-{index}", epoch=1, holder=f"work-{index}", status="in_flight")

    threads = [threading.Thread(target=record, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()

    cp = checkpoint.load_checkpoint(project_root / ".orch" / "run" / "broker-checkpoint.json")
    assert sorted(lease.task_id for lease in cp.in_flight) == [f"T-lost-{index}" for index in range(6)]


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


def test_delivery_checkpoint_cannot_overwrite_concurrent_cancel(tmp_path: Path):
    """A long-poll delivery cannot write a stale in_flight after cancel wins.

    The worker poll is gated outside the store mutation lock so that a
    concurrent cancel can settle the task before delivery completes. With no
    prior checkpoint, the only acceptable outcome is that the task is not
    resurrected as in_flight.
    """
    from orchlink.broker.checkpoint import load_checkpoint
    from orchlink.broker.service import BrokerService
    from orchlink.broker.storage.memory import MemoryMessageStore
    from orchlink.core.envelope import AgentRegistration, MessageEnvelope

    class GatedStore(MemoryMessageStore):
        def __init__(self) -> None:
            super().__init__()
            self.poll_started = asyncio.Event()
            self.poll_release = asyncio.Event()

        async def get_next_message(
            self,
            agent_id: str,
            wait_seconds: int,
            lease_id: str | None = None,
            project_id: str | None = None,
            on_delivered: Any = None,
        ) -> dict[str, Any] | None:
            # Signal that the poll has reached the wait phase, then block
            # without holding the store mutation lock.
            self.poll_started.set()
            await self.poll_release.wait()
            # Once released, resolve the real delivery immediately so the
            # test does not wait on the empty inbox again.
            return await super().get_next_message(
                agent_id,
                0,
                lease_id=lease_id,
                project_id=project_id,
                on_delivered=on_delivered,
            )

    async def run() -> None:
        store = GatedStore()
        service = BrokerService(store, project_root=tmp_path)
        await service.register_agent(
            AgentRegistration(
                agent_id="test.work",
                role="worker",
                display_name="Worker",
                project_id="test",
            )
        )
        message = MessageEnvelope(**_checkpoint_task_message("T-race"))
        await service.enqueue_message(message)

        # Start the long-poll. The gated store is suspended in the wait
        # phase without holding any lock.
        poll = asyncio.create_task(service.get_next_message("test.work", wait_seconds=30))
        await asyncio.wait_for(store.poll_started.wait(), timeout=1)

        # Cancel must complete while the poll is still waiting, without
        # blocking behind the checkpoint lock.
        await asyncio.wait_for(
            service.cancel_work("T-race", reason="operator cancel"),
            timeout=1,
        )
        store.poll_release.set()
        assert await asyncio.wait_for(poll, timeout=1) is None

        # The task was settled before the delivery callback could run.
        task = await service.get_task_result("T-race", project_id="test")
        assert task["status"] == "CANCELLED"

    asyncio.run(run())

    cp = load_checkpoint(tmp_path / ".orch" / "run" / "broker-checkpoint.json")
    assert cp.in_flight == []
    assert "T-race" not in {lease.task_id for lease in cp.leases}


def test_jsonl_delivery_is_durable_before_checkpoint_callback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """JSONL delivery lands durably before BrokerService records in-flight."""
    from orchlink.broker.checkpoint import load_checkpoint
    from orchlink.broker.service import BrokerService
    from orchlink.broker.storage.jsonl import JsonlMessageStore
    from orchlink.core.envelope import AgentRegistration, MessageEnvelope

    journal_path = tmp_path / ".orch" / "run" / "broker.jsonl"
    observed: dict[str, str] = {}

    async def run() -> None:
        store = JsonlMessageStore(journal_path)
        service = BrokerService(store, project_root=tmp_path)
        await service.register_agent(
            AgentRegistration(
                agent_id="test.work",
                role="worker",
                display_name="Worker",
                project_id="test",
            )
        )
        await service.enqueue_message(MessageEnvelope(**_checkpoint_task_message("T-jsonl-order")))
        original_callback = service._on_message_delivered

        def assert_durable_then_checkpoint(delivered: dict[str, Any]) -> None:
            last_record = json.loads(journal_path.read_text(encoding="utf-8").splitlines()[-1])
            observed["operation"] = str(last_record.get("operation"))
            message_id = str(delivered["message_id"])
            observed["status"] = str(last_record["snapshot"]["active_messages"][message_id]["status"])
            original_callback(delivered)

        monkeypatch.setattr(service, "_on_message_delivered", assert_durable_then_checkpoint)
        delivered = await service.get_next_message("test.work", wait_seconds=1)
        assert delivered is not None

    asyncio.run(run())

    assert observed == {"operation": "get_next_message", "status": "DELIVERED"}
    checkpoint = load_checkpoint(tmp_path / ".orch" / "run" / "broker-checkpoint.json")
    assert [(lease.task_id, lease.status) for lease in checkpoint.in_flight] == [
        ("T-jsonl-order", "in_flight")
    ]


def test_on_delivered_failure_is_non_fatal_and_does_not_roll_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """If the in_flight checkpoint write fails during delivery, the message
    is still delivered and no exception escapes."""
    from orchlink.broker.checkpoint import load_checkpoint
    from orchlink.broker.service import BrokerService
    from orchlink.broker.storage.memory import MemoryMessageStore
    from orchlink.core.envelope import AgentRegistration, MessageEnvelope

    def fail_record_lease(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("orchlink.broker.service.record_lease", fail_record_lease)

    async def run() -> None:
        store = MemoryMessageStore()
        service = BrokerService(store, project_root=tmp_path)
        await service.register_agent(
            AgentRegistration(
                agent_id="test.work",
                role="worker",
                display_name="Worker",
                project_id="test",
            )
        )
        message = MessageEnvelope(**_checkpoint_task_message("T-fail"))
        await service.enqueue_message(message)
        delivered = await service.get_next_message("test.work", wait_seconds=1)
        assert delivered is not None
        assert delivered["task_id"] == "T-fail"
        assert delivered["status"] == "DELIVERED"

        # The store-side delivery was not rolled back.
        task = await service.get_task_result("T-fail", project_id="test")
        assert task["status"] == "DELIVERED"

    asyncio.run(run())

    # No checkpoint write succeeded, so there is no in_flight record.
    cp = load_checkpoint(tmp_path / ".orch" / "run" / "broker-checkpoint.json")
    assert cp.in_flight == []


def test_session_release_records_settled_work_in_checkpoint(tmp_path: Path):
    from orchlink.broker.checkpoint import load_checkpoint
    from orchlink.broker.main import create_app
    from orchlink.broker.settings import Settings
    from orchlink.broker.storage.memory import MemoryMessageStore

    store_path = tmp_path / ".orch" / "run" / "orchlink-journal.jsonl"
    app = create_app(
        store=MemoryMessageStore(require_peer_sessions=True),
        settings=Settings(api_key="test-key", require_peer_sessions=True, store_path=str(store_path)),
    )
    auth = {"X-API-Key": "test-key", "X-Orchlink-Project-ID": "test"}
    message = _checkpoint_task_message("T-release")

    session = _broker_post(app, "/v1/sessions/acquire", headers=auth, json={"agent_id": "test.work", "role": "worker", "project_id": "test"}).json()["session"]
    _broker_post(app, "/v1/messages/send", headers=auth, json=message)
    _broker_get(app, f"/v1/agents/test.work/next?wait_seconds=1&lease_id={session['lease_id']}", headers=auth)

    response = _broker_post(app, f"/v1/sessions/{session['lease_id']}/release", headers=auth, json={"project_id": "test", "reason": "worker exited"})
    assert response.status_code == 200, response.text
    assert response.json()["session"]["settled_work"] == ["T-release"]

    cp = load_checkpoint(tmp_path / ".orch" / "run" / "broker-checkpoint.json")
    assert cp.in_flight == []
    assert [(lease.task_id, lease.epoch, lease.holder, lease.status) for lease in cp.recently_settled] == [
        ("T-release", 1, "test.work", "recently_settled")
    ]


def test_session_expiry_records_settled_work_in_checkpoint(tmp_path: Path):
    from orchlink.broker.checkpoint import load_checkpoint
    from orchlink.broker.service import BrokerService
    from orchlink.broker.storage.memory import MemoryMessageStore
    from orchlink.core.envelope import MessageEnvelope
    from orchlink.core.models import SessionAcquire

    async def run() -> None:
        store = MemoryMessageStore(require_peer_sessions=True, session_grace_seconds=1)
        service = BrokerService(store, project_root=tmp_path)
        session = await service.acquire_session(SessionAcquire(agent_id="test.work", role="worker", project_id="test", lease_grace_seconds=1))
        message = MessageEnvelope(**_checkpoint_task_message("T-expire"))
        await service.enqueue_message(message)
        delivered = await service.get_next_message("test.work", wait_seconds=1, lease_id=session["lease_id"], project_id="test")
        assert delivered is not None
        await asyncio.sleep(1.05)
        expired = await service.expire_sessions()
        assert len(expired) == 1
        assert expired[0].settled_work == ["T-expire"]

    asyncio.run(run())

    cp = load_checkpoint(tmp_path / ".orch" / "run" / "broker-checkpoint.json")
    assert cp.in_flight == []
    assert [(lease.task_id, lease.epoch, lease.holder, lease.status) for lease in cp.recently_settled] == [
        ("T-expire", 1, "test.work", "recently_settled")
    ]


def test_checkpoint_write_failures_are_non_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from orchlink.broker.service import BrokerService
    from orchlink.broker.storage.memory import MemoryMessageStore

    def fail_record(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("orchlink.broker.service.record_lease", fail_record)
    service = BrokerService(MemoryMessageStore(), project_root=tmp_path)
    service.record_in_flight("T-nonfatal", {"epoch": 1, "holder": "work-1"})
    service.record_recently_settled("T-nonfatal", {"epoch": 1, "holder": "work-1"})


def test_create_app_is_checkpoint_pure_until_lifespan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from orchlink.broker.main import create_app
    from orchlink.broker.settings import Settings
    from orchlink.broker.storage.memory import MemoryMessageStore

    calls = 0

    def count_load(path):
        nonlocal calls
        calls += 1
        from orchlink.broker.checkpoint import empty_checkpoint
        return empty_checkpoint()

    monkeypatch.setattr("orchlink.broker.service.load_checkpoint", count_load)
    app = create_app(
        store=MemoryMessageStore(),
        settings=Settings(api_key="test-key", store_path=str(tmp_path / ".orch" / "run" / "orchlink-journal.jsonl")),
    )
    assert calls == 0

    async def run() -> None:
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(run())
    assert calls == 1


def test_lifespan_reconciles_checkpoint_and_surfaces_drift(tmp_path: Path):
    from orchlink.broker.checkpoint import record_lease
    from orchlink.broker.main import create_app
    from orchlink.broker.settings import Settings
    from orchlink.broker.storage.memory import MemoryMessageStore

    record_lease(tmp_path, "T-lifespan", epoch=7, holder="work-lost", status="in_flight")
    store = MemoryMessageStore()
    app = create_app(
        store=store,
        settings=Settings(api_key="test-key", store_path=str(tmp_path / ".orch" / "run" / "orchlink-journal.jsonl")),
    )

    async def run() -> None:
        async with app.router.lifespan_context(app):
            pass
        events = await store.list_events()
        assert app.state.drifted_leases[0].task_id == "T-lifespan"
        assert app.state.drifted_leases[0].reason == "missing_after_restart"
        assert events[0]["type"] == "lease_expired_during_downtime"

    asyncio.run(run())


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
