"""Focused tests for the Loop runtime composition module."""

from __future__ import annotations

import asyncio
from pathlib import Path

from orchlink.loop import runtime as loop_runtime
from orchlink.loop.domain.item import MakerResult, WorkerAssignment
from orchlink.loop.ports import BrokerTaskStatus
from orchlink.loop.services.verifier_service import VerifierHandle
from orchlink.project.init import init_project


class FakeWorkerGateway:
    def __init__(self):
        self.dispatched = []
        self.verifier_dispatched = []

    async def dispatch_maker(self, maker_assignment: WorkerAssignment, prompt: str) -> VerifierHandle:
        self.dispatched.append((maker_assignment, prompt))
        return VerifierHandle(task_id=f"real-{maker_assignment.task_id}", worker_name=maker_assignment.worker_name)

    async def dispatch_verifier(self, verifier_assignment: WorkerAssignment, prompt: str) -> VerifierHandle:
        self.verifier_dispatched.append((verifier_assignment, prompt))
        return VerifierHandle(task_id=verifier_assignment.task_id, worker_name=verifier_assignment.worker_name)

    async def await_result(self, handle: VerifierHandle, timeout_seconds: int) -> MakerResult:
        return MakerResult("done")

    async def maker_session_project_dir(self, worker_name: str) -> dict[str, str] | None:
        return {"project_dir": "/tmp/fake-worktree", "lease_id": "lease-1"}


class FakeBroker:
    def __init__(self, status: str = "running"):
        self.status = status
        self.calls: list[str] = []

    def get_task_status(self, task_id: str) -> BrokerTaskStatus | None:
        self.calls.append(task_id)
        return BrokerTaskStatus(status=self.status)

    def get_session_active(self, lease_id: str) -> bool:
        return True


def _project_config(tmp_path: Path) -> dict[str, object]:
    init_project(tmp_path, project_id="demo")
    return {"_project_root": str(tmp_path)}


def test_build_services_wires_service_graph(tmp_path: Path):
    config = _project_config(tmp_path)

    loop_service, triage_service, verifier_service, engine, goal_adapter = loop_runtime.build_services(config)

    assert loop_service is not None
    assert triage_service.loop_service is loop_service
    assert engine.loop_service is loop_service
    assert engine.triage_service is triage_service
    assert engine.verifier_service is verifier_service
    assert engine.goal_service is goal_adapter


def test_build_worker_runtime_with_none_gateway_returns_none():
    gateway, worker_service = loop_runtime.build_worker_runtime({}, gateway=None)

    assert gateway is None
    assert worker_service is None


def test_build_worker_runtime_wires_service_for_gateway():
    fake = FakeWorkerGateway()

    gateway, worker_service = loop_runtime.build_worker_runtime({}, gateway=fake)

    assert gateway is fake
    assert worker_service is not None
    assert worker_service.gateway is fake


def test_build_broker_client_returns_none_when_broker_unreachable(monkeypatch, tmp_path: Path):
    config = _project_config(tmp_path)
    monkeypatch.setattr(loop_runtime, "broker_reachable", lambda _config: False)

    client = loop_runtime.build_broker_client(config)

    assert client is None


def test_build_worker_gateway_returns_none_when_broker_unreachable(monkeypatch, tmp_path: Path):
    config = _project_config(tmp_path)
    monkeypatch.setattr(loop_runtime, "broker_reachable", lambda _config: False)

    gateway = loop_runtime.build_worker_gateway(config)

    assert gateway is None


def test_configure_engine_runtime_wires_adapters(monkeypatch, tmp_path: Path):
    config = _project_config(tmp_path)
    loop_service, _, _, engine, _ = loop_runtime.build_services(config)
    fake_gateway = FakeWorkerGateway()
    fake_broker = FakeBroker()

    loop_runtime.configure_engine_runtime(
        config,
        engine,
        run_checks=True,
        worker_gateway=fake_gateway,
        broker_client=fake_broker,
    )

    assert engine.worker_service is not None
    assert engine.worker_service.gateway is fake_gateway
    assert engine.verifier_service is not None
    assert engine.verifier_service.gateway is fake_gateway
    assert engine.broker_client is fake_broker
    assert engine.config["run_checks"] is True


def test_build_verifier_service_includes_worktree_evidence_collector(tmp_path: Path):
    config = _project_config(tmp_path)
    fake_gateway = FakeWorkerGateway()

    verifier_service = loop_runtime.build_verifier_service(config, fake_gateway)

    assert verifier_service.gateway is fake_gateway
    evidence = verifier_service.evidence_collector.collect(None)
    assert evidence.unavailable_reason == "no worktree provided"


def test_build_project_connectors_falls_back_to_local_git(tmp_path: Path):
    config = {"_project_root": str(tmp_path)}

    connectors = loop_runtime.build_project_connectors(config, tmp_path)

    assert len(connectors) == 1
    assert connectors[0].name == "local_git"


def test_build_goal_evidence_adapter_returns_adapter_for_valid_project(tmp_path: Path):
    config = _project_config(tmp_path)

    adapter = loop_runtime.build_goal_evidence_adapter(config)

    assert adapter is not None


def test_http_loop_broker_client_normalizes_done_snapshot_with_result(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "DONE", "reply": {"payload": {"summary": "ok"}}}

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, path, headers=None):
            calls.append((path, headers, self.kwargs))
            return FakeResponse()

    monkeypatch.setattr(loop_runtime.httpx, "Client", FakeClient)
    client = loop_runtime.HttpLoopBrokerClient({"project_id": "demo", "broker": {"url": "http://broker", "api_key": "k"}})

    snapshot = client.get_task_status("T-1")

    assert snapshot == BrokerTaskStatus(status="done", result="ok")


def test_http_loop_broker_client_extracts_verifier_reply_text(monkeypatch):
    from orchlink.loop import runtime as loop_runtime

    verdict_block = "VERDICT: ACCEPTED\nREASON: accepted\nDETAIL: ok\nVERIFIER_WORKER: review"

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"status": "DONE", "reply": {"payload": {"summary": verdict_block}}}

    class FakeClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, path, headers=None):
            return FakeResponse()

    monkeypatch.setattr(loop_runtime.httpx, "Client", FakeClient)
    client = loop_runtime.HttpLoopBrokerClient({"project_id": "demo", "broker": {"url": "http://broker", "api_key": "k"}})

    snapshot = client.get_task_status("verify:I-1:1")

    assert snapshot.status == "done"
    assert snapshot.result == verdict_block


def test_http_loop_worker_gateway_awaits_maker_result_from_payload(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"reply": {"payload": {"stdout": "maker output"}}}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, path, headers=None):
            calls.append((path, headers, self.kwargs))
            return FakeResponse()

        async def post(self, path, headers=None, json=None):
            calls.append((path, headers, json, self.kwargs))
            return FakeResponse()

    monkeypatch.setattr(loop_runtime.httpx, "AsyncClient", FakeAsyncClient)
    gateway = loop_runtime.HttpLoopWorkerGateway({"project_id": "demo", "broker": {"url": "http://broker", "api_key": "k"}})

    result = asyncio.run(gateway.await_result(VerifierHandle(task_id="T-1", worker_name="maker"), 1))

    assert result.result == "maker output"
