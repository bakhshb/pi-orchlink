"""Loop runtime composition module.

This module owns the construction of concrete Loop adapters, HTTP gateways,
triage connectors, the Goal evidence adapter, and the wired service graph. It is
called from the Typer CLI edge (``orchlink.loop.cli``) and from tests that want
to exercise the runtime with fake ports.

Application service modules live under ``orchlink.loop.services`` and import
only :mod:`orchlink.loop.ports` plus the domain; they never import this module,
any adapter, or the CLI.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from orchlink.goal.runner import GoalEvidenceAdapter
from orchlink.goal.store import GoalStore
from orchlink.loop.adapters.connectors import (
    ConnectorSecretGateway,
    GitHubConnector,
    LinearConnector,
    LocalGitConnector,
)
from orchlink.loop.adapters.state_repo import LoopStateRepo
from orchlink.loop.adapters.worktree_evidence import WorktreeEvidenceCollector
from orchlink.loop.domain.item import MakerResult, WorkerAssignment
from orchlink.loop.ports import (
    BrokerStatusPort,
    BrokerTaskStatus,
    Connector,
    GoalEvidencePort,
    LoopRepository,
    MakerWorktreeResolverPort,
    WorkerGateway,
)
from orchlink.loop.services import (
    LoopEngine,
    LoopService,
    TriageService,
    VerifierService,
    WorkerService,
)
from orchlink.loop.services.verifier_service import VerifierHandle
from orchlink.project.config import broker_api_key, broker_url, project_root

log = logging.getLogger(__name__)


def _current_project_id(config: dict[str, Any]) -> str:
    return str(config.get("project_id") or "default")


def _extract_reply_text(body: dict[str, Any]) -> str:
    """Extract the usable reply/result text from a broker task wire body.

    Worker replies are stored as message envelopes whose payload carries the
    worker's text (summary/stdout/message/result). Mirrors
    ``HttpLoopWorkerGateway.await_result`` so loop recovery consumes the same
    result text shape as the live maker/verifier dispatch path.
    """
    reply = body.get("reply") or body.get("result") or {}
    payload = reply.get("payload") if isinstance(reply, dict) else None
    if isinstance(payload, dict):
        text = payload.get("summary") or payload.get("stdout") or payload.get("message") or payload.get("result")
    else:
        text = body.get("output") or body.get("message")
    return str(text or "")


class HttpLoopBrokerClient:
    """Synchronous HTTP broker snapshot adapter for loop recovery."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.base_url = broker_url(config)
        self.headers = {"X-API-Key": broker_api_key(config)}
        self.project_id = _current_project_id(config)

    def get_task_status(self, task_id: str) -> BrokerTaskStatus | None:
        params = urlencode({"project_id": self.project_id})
        with httpx.Client(base_url=self.base_url, timeout=1.0) as client:
            response = client.get(f"/v1/tasks/{task_id}?{params}", headers=self.headers)
            response.raise_for_status()
            body = response.json()
        if not isinstance(body, dict):
            return None
        status = body.get("status")
        result = _extract_reply_text(body) or None
        if result is None and isinstance(body.get("job"), dict):
            status = status or body["job"].get("status")
        return BrokerTaskStatus(
            status=str(status).lower() if status is not None else "missing",
            result=result,
        )

    def get_session_active(self, lease_id: str) -> bool:
        params = urlencode({"project_id": self.project_id})
        with httpx.Client(base_url=self.base_url, timeout=1.0) as client:
            response = client.get(f"/v1/sessions?{params}", headers=self.headers)
            response.raise_for_status()
        for session in response.json().get("sessions", []):
            if str(session.get("lease_id") or "") == lease_id:
                status = str(session.get("status") or "").lower()
                return status in {"active", "ready", "busy"} or bool(session.get("active"))
        return False


class HttpLoopWorkerGateway(WorkerGateway, MakerWorktreeResolverPort):
    """Async HTTP worker gateway for maker/verifier dispatch and worktree resolution."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.base_url = broker_url(config)
        self.headers = {"X-API-Key": broker_api_key(config)}
        self.project_id = _current_project_id(config)

    async def dispatch_maker(self, maker_assignment: WorkerAssignment, prompt: str) -> VerifierHandle:
        if maker_assignment.task_id is None:
            from orchlink.loop.services.verifier_service import VerifierDispatchError

            raise VerifierDispatchError("maker assignment is missing a task id")
        task_id = maker_assignment.task_id
        if task_id.startswith("reserved:"):
            task_id = f"loop:maker:{task_id.removeprefix('reserved:')}"
        return await self._dispatch(maker_assignment.worker_name, task_id, prompt)

    async def dispatch_verifier(self, verifier_assignment: WorkerAssignment, prompt: str) -> VerifierHandle:
        if verifier_assignment.task_id is None:
            from orchlink.loop.services.verifier_service import VerifierDispatchError

            raise VerifierDispatchError("verifier assignment is missing a task id")
        return await self._dispatch(verifier_assignment.worker_name, verifier_assignment.task_id, prompt)

    async def _dispatch(self, worker_name: str, task_id: str, prompt: str) -> VerifierHandle:
        from orchlink.client.ask import build_task_envelope
        from orchlink.core.envelope import envelope_to_dict

        envelope = build_task_envelope(
            config=self.config,
            worker=worker_name,
            task_id=task_id,
            message=prompt,
            timeout_seconds=1800,
            delivery="async",
        )
        async with httpx.AsyncClient(base_url=self.base_url, timeout=None) as client:
            response = await client.post("/v1/messages/send", headers=self.headers, json=envelope_to_dict(envelope))
            response.raise_for_status()
        return VerifierHandle(task_id=task_id, worker_name=worker_name)

    async def await_result(self, handle: VerifierHandle, timeout_seconds: int) -> "MakerResult":
        params = urlencode({"timeout_seconds": str(timeout_seconds), "project_id": self.project_id})
        async with httpx.AsyncClient(base_url=self.base_url, timeout=None) as client:
            response = await client.get(f"/v1/tasks/{handle.task_id}/wait?{params}", headers=self.headers)
            response.raise_for_status()
        body = response.json()
        return MakerResult(_extract_reply_text(body))

    async def maker_session_project_dir(self, worker_name: str) -> dict[str, Any] | None:
        params = urlencode({"project_id": self.project_id, "active": "true"})
        async with httpx.AsyncClient(base_url=self.base_url, timeout=2.0) as client:
            response = await client.get(f"/v1/sessions?{params}", headers=self.headers)
            response.raise_for_status()
        for session in response.json().get("sessions", []):
            if str(session.get("role") or "") != "work":
                continue
            if str(session.get("worker_name") or session.get("agent_id") or "") != worker_name:
                continue
            if not bool(session.get("ready", False)):
                continue
            return session
        return None


def build_repo(config: dict[str, Any]) -> LoopRepository:
    return LoopStateRepo(project_root(config))


def build_goal_evidence_adapter(config: dict[str, Any]) -> GoalEvidencePort | None:
    try:
        return GoalEvidenceAdapter(GoalStore(config))
    except Exception:
        log.debug("goal evidence adapter unavailable", exc_info=True)
        return None


def build_project_connectors(
    config: dict[str, Any] | None,
    project_dir: Path | str,
    *,
    secrets: object | None = None,
    github_http_client: object | None = None,
    linear_http_client: object | None = None,
) -> list[Connector]:
    """Build read-only project connectors for CLI triage.

    Missing connector config falls back to local git. Invalid configured
    connectors are skipped rather than failing foreground watch.
    """
    raw_configs = _configured_connector_dicts(config or {})
    if not raw_configs:
        return [LocalGitConnector(Path(project_dir))]

    secret_gateway = secrets or ConnectorSecretGateway()
    connectors: list[Connector] = []
    for raw in raw_configs:
        name = str(raw.get("name") or "").strip().lower()
        try:
            if name == "github":
                connectors.append(GitHubConnector(raw, secrets=secret_gateway, http_client=github_http_client))
            elif name == "linear":
                connectors.append(LinearConnector(raw, secrets=secret_gateway, http_client=linear_http_client))
        except Exception as exc:
            log.warning("loop triage connector %s config skipped: %s", name or "unknown", exc)
    return connectors


def _configured_connector_dicts(config: dict[str, Any]) -> list[dict[str, Any]]:
    loop_config = config.get("loop") if isinstance(config.get("loop"), dict) else {}
    raw = loop_config.get("connectors") if isinstance(loop_config, dict) else None
    if raw is None:
        raw = config.get("connectors")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [dict(item) for item in raw if isinstance(item, dict) and item.get("name") in {"github", "linear"}]
    if isinstance(raw, dict):
        configs: list[dict[str, Any]] = []
        for name in ("github", "linear"):
            value = raw.get(name)
            if value is None or value is False:
                continue
            if isinstance(value, dict):
                data = dict(value)
            else:
                data = {}
            data.setdefault("name", name)
            configs.append(data)
        return configs
    return []


def build_services(
    config: dict[str, Any],
) -> tuple[LoopService, TriageService, VerifierService, LoopEngine, GoalEvidencePort | None]:
    loop_service = LoopService(config, build_repo(config))
    triage_service = TriageService(
        config,
        loop_service,
        build_project_connectors(config, project_root(config)),
    )
    verifier_service = VerifierService(config)
    goal_adapter = build_goal_evidence_adapter(config)
    engine = LoopEngine(
        config,
        loop_service,
        triage_service=triage_service,
        verifier_service=verifier_service,
        broker_client=None,
        goal_service=goal_adapter,
    )
    return loop_service, triage_service, verifier_service, engine, goal_adapter


def broker_reachable(config: dict[str, Any]) -> bool:
    try:
        with httpx.Client(base_url=broker_url(config), timeout=0.2) as http_client:
            response = http_client.get("/v1/status", headers={"X-API-Key": broker_api_key(config)})
            response.raise_for_status()
    except Exception:
        return False
    return True


def build_verifier_service(config: dict[str, Any], gateway: WorkerGateway) -> VerifierService:
    return VerifierService(
        config,
        gateway=gateway,
        evidence_collector=WorktreeEvidenceCollector(),
    )


def build_broker_client(config: dict[str, Any]) -> BrokerStatusPort | None:
    """Return a tiny broker adapter, or None if the broker is unreachable.

    The adapter reads existing HTTP endpoints only (/v1/jobs and /v1/sessions).
    Failure to contact the broker is conservative: callers get None and the loop
    engine follows its no-broker recovery/blocking behavior. This keeps runtime
    broker construction failures as broker_unavailable notes, not tracebacks.
    """
    return HttpLoopBrokerClient(config) if broker_reachable(config) else None


def build_worker_gateway(config: dict[str, Any]) -> WorkerGateway | None:
    return HttpLoopWorkerGateway(config) if broker_reachable(config) else None


def build_worker_runtime(
    config: dict[str, Any],
    *,
    gateway: WorkerGateway | None = None,
) -> tuple[WorkerGateway | None, WorkerService | None]:
    """Build foreground worker dispatch from an existing gateway.

    If ``gateway`` is None the runtime intentionally leaves worker dispatch
    unwired so the engine follows its maker_unavailable path. Callers that need
    a real gateway must build it first with :func:`build_worker_gateway`.
    """
    if gateway is None:
        return None, None
    try:
        return gateway, WorkerService(config, gateway)
    except Exception:
        return None, None


def configure_engine_runtime(
    config: dict[str, Any],
    engine: LoopEngine,
    *,
    run_checks: bool,
    worker_gateway: WorkerGateway | None = None,
    worker_service: WorkerService | None = None,
    broker_client: BrokerStatusPort | None = None,
) -> None:
    """Wire runtime adapters into an already-built engine."""
    engine.config["run_checks"] = run_checks
    gateway, built_worker_service = build_worker_runtime(config, gateway=worker_gateway)
    if worker_service is not None:
        engine.worker_service = worker_service
    elif built_worker_service is not None:
        engine.worker_service = built_worker_service
    if gateway is not None:
        engine.verifier_service = VerifierService(
            config,
            gateway=gateway,
            evidence_collector=WorktreeEvidenceCollector(),
        )
    engine.broker_client = broker_client if broker_client is not None else build_broker_client(config)
