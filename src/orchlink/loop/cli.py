"""Typer CLI for Loop Mode."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any, Protocol
from urllib.parse import urlencode

import httpx
import typer
from rich.panel import Panel
from rich.pretty import Pretty
from rich.table import Table

from orchlink.cli.commands._helpers import console, current_project_id, load_project_or_exit
from orchlink.goal.runner import GoalEvidenceAdapter
from orchlink.goal.store import GoalStore
from orchlink.loop.adapters.connectors import LocalGitConnector
from orchlink.loop.adapters.state_repo import LoopStateRepo
from orchlink.loop.domain.errors import BudgetExhausted, IllegalTransition, VerifierMismatch
from orchlink.loop.domain.item import LoopItem, LoopItemState, MakerResult, WorkerAssignment
from orchlink.loop.domain.verdict import ReasonCode
from orchlink.loop.domain.worktree import Worktree
from orchlink.loop.services import LoopEngine, LoopService, TriageService, VerifierService
from orchlink.loop.services.verifier_service import (
    VerdictParseError,
    VerifierDispatchError,
    VerifierHandle,
    VerifierTimeoutError,
    WorkerGatewayUnavailable,
)
from orchlink.project.config import broker_api_key, broker_url, project_root


loop_app = typer.Typer(help="Loop Mode item lifecycle commands.")
ACTIVE_STATES = {
    LoopItemState.DISPATCHING,
    LoopItemState.RUNNING,
    LoopItemState.AWAITING_VERDICT,
    LoopItemState.VERIFYING,
}


class LoopBrokerClient(Protocol):
    def get_task_status(self, task_id: str) -> str | None:
        ...

    def get_session_active(self, lease_id: str) -> bool:
        ...


class LoopWorkerGateway:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.base_url = broker_url(config)
        self.headers = {"X-API-Key": broker_api_key(config)}
        self.project_id = current_project_id(config)

    async def dispatch_verifier(self, verifier_assignment: WorkerAssignment, prompt: str) -> VerifierHandle:
        from orchlink.client.ask import build_task_envelope
        from orchlink.core.envelope import envelope_to_dict

        if verifier_assignment.task_id is None:
            raise VerifierDispatchError("verifier assignment is missing a task id")
        envelope = build_task_envelope(
            config=self.config,
            worker=verifier_assignment.worker_name,
            task_id=verifier_assignment.task_id,
            message=prompt,
            timeout_seconds=1800,
            delivery="async",
        )
        async with httpx.AsyncClient(base_url=self.base_url, timeout=None) as client:
            response = await client.post("/v1/messages/send", headers=self.headers, json=envelope_to_dict(envelope))
            response.raise_for_status()
        return VerifierHandle(task_id=verifier_assignment.task_id, worker_name=verifier_assignment.worker_name)

    async def await_result(self, handle: VerifierHandle, timeout_seconds: int) -> MakerResult:
        params = urlencode({"timeout_seconds": str(timeout_seconds), "project_id": self.project_id})
        async with httpx.AsyncClient(base_url=self.base_url, timeout=None) as client:
            response = await client.get(f"/v1/tasks/{handle.task_id}/wait?{params}", headers=self.headers)
            response.raise_for_status()
        body = response.json()
        reply = body.get("reply") or body.get("result") or {}
        payload = reply.get("payload") if isinstance(reply, dict) else None
        if isinstance(payload, dict):
            text = payload.get("summary") or payload.get("stdout") or payload.get("message") or payload.get("result")
        else:
            text = body.get("output") or body.get("message")
        return MakerResult(str(text or ""))


class HttpLoopBrokerClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.base_url = broker_url(config)
        self.headers = {"X-API-Key": broker_api_key(config)}
        self.project_id = current_project_id(config)

    def get_task_status(self, task_id: str) -> str | None:
        params = urlencode({"limit": "500", "project_id": self.project_id})
        with httpx.Client(base_url=self.base_url, timeout=1.0) as client:
            response = client.get(f"/v1/jobs?{params}", headers=self.headers)
            response.raise_for_status()
        for job in response.json().get("jobs", []):
            if str(job.get("task_id") or job.get("id") or job.get("item_id") or "") == task_id:
                status = job.get("status")
                return str(status).lower() if status is not None else None
        return None

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


def register_loop(app: typer.Typer) -> None:
    app.add_typer(loop_app, name="loop")


def _project_config() -> dict[str, Any]:
    return load_project_or_exit()


def _repo(config: dict[str, Any]) -> LoopStateRepo:
    return LoopStateRepo(project_root(config))


def _build_services(config: dict[str, Any]) -> tuple[LoopService, TriageService, VerifierService, LoopEngine, GoalEvidenceAdapter | None]:
    loop_service = LoopService(config, _repo(config))
    triage_service = TriageService(config, loop_service, [LocalGitConnector(project_root(config))])
    verifier_service = VerifierService(config)
    try:
        goal_adapter: GoalEvidenceAdapter | None = GoalEvidenceAdapter(GoalStore(config))
    except Exception:
        goal_adapter = None
    engine = LoopEngine(
        config,
        loop_service,
        triage_service=triage_service,
        verifier_service=verifier_service,
        broker_client=None,
        goal_service=goal_adapter,
    )
    return loop_service, triage_service, verifier_service, engine, goal_adapter


def _broker_reachable(config: dict[str, Any]) -> bool:
    try:
        with httpx.Client(base_url=broker_url(config), timeout=0.2) as http_client:
            response = http_client.get("/v1/status", headers={"X-API-Key": broker_api_key(config)})
            response.raise_for_status()
    except Exception:
        return False
    return True


def _build_broker_client(config: dict[str, Any]) -> object | None:
    """Return a tiny broker adapter, or None if the broker is unreachable.

    The adapter reads existing HTTP endpoints only (/v1/jobs and /v1/sessions).
    Failure to contact the broker is conservative: callers get None and the loop
    engine follows its no-broker recovery/blocking behavior. This keeps runtime
    broker construction failures as broker_unavailable notes, not tracebacks.
    """
    return HttpLoopBrokerClient(config) if _broker_reachable(config) else None


def _build_worker_gateway(config: dict[str, Any]) -> LoopWorkerGateway | None:
    return LoopWorkerGateway(config) if _broker_reachable(config) else None


def _error(message: str) -> None:
    console.print(f"[Orch] {message}")
    raise typer.Exit(1)


def _item_or_exit(service: LoopService, item_id: str) -> LoopItem:
    item = service.get(item_id)
    if item is None:
        _error(f"Loop item not found: {item_id}")
    return item


def _latest_maker(item: LoopItem) -> str:
    if not item.attempts:
        return "-"
    return item.attempts[-1].maker.worker_name or "-"


def _worktree(item: LoopItem) -> str:
    return item.worktree.path if item.worktree is not None else "-"


def _updated(item: LoopItem) -> str:
    return item.updated_at.isoformat() if item.updated_at is not None else "-"


@loop_app.command("ls", help="List loop items.")
def list_items() -> None:
    config = _project_config()
    service, _, _, _, _ = _build_services(config)
    table = Table(title="Loop Items")
    for column in ["ID", "STATE", "TITLE", "MAKER", "WORKTREE", "UPDATED"]:
        table.add_column(column)
    for item in sorted(service.ls(), key=lambda candidate: candidate.item_id):
        table.add_row(item.item_id, item.state.value, item.title or "-", _latest_maker(item), _worktree(item), _updated(item))
    console.print(table)


@loop_app.command("show", help="Show one loop item.")
def show(item_id: str) -> None:
    config = _project_config()
    service, _, _, _, _ = _build_services(config)
    item = _item_or_exit(service, item_id)
    console.print(Panel.fit(f"{item.item_id} · {item.state.value}\n{item.title or '-'}", title="Loop Item"))
    summary = Table(title="Summary")
    summary.add_column("FIELD")
    summary.add_column("VALUE")
    summary.add_row("source", item.source or "-")
    summary.add_row("goal_id", item.goal_id or "-")
    summary.add_row("worktree", _worktree(item))
    summary.add_row("blocker", item.blocker or "-")
    summary.add_row("updated", _updated(item))
    console.print(summary)
    if not item.attempts:
        console.print("No attempts.")
        return
    attempts = Table(title="Attempts")
    for column in ["NO", "MAKER", "MAKER TASK", "VERIFIER", "VERDICT"]:
        attempts.add_column(column)
    for attempt in item.attempts:
        attempts.add_row(
            str(attempt.number),
            attempt.maker.worker_name,
            attempt.maker.task_id or "-",
            attempt.verifier.worker_name if attempt.verifier else "-",
            attempt.verdict.verdict.value if attempt.verdict else "-",
        )
    console.print(attempts)
    console.print(Pretty(item))


@loop_app.command("next", help="Reserve and mark-dispatch a ready item.")
def next_item(
    item_id: str,
    maker: Annotated[str, typer.Option("--maker", help="Maker worker name.")],
    worktree: Annotated[Path | None, typer.Option("--worktree", help="Worktree path for the maker.")] = None,
) -> None:
    config = _project_config()
    service, _, _, _, _ = _build_services(config)
    item = _item_or_exit(service, item_id)
    if item.state is not LoopItemState.READY:
        _error(f"Loop item {item_id} is {item.state.value}; next requires ready.")
    try:
        reservation = service.next_item(item_id, maker_worker=maker, worktree=Worktree(str(worktree)) if worktree else item.worktree)
        dispatched = service.mark_dispatched(item_id, attempt_no=reservation.attempt.number, task_id=f"cli:{item_id}:{reservation.attempt.number}")
    except (IllegalTransition, BudgetExhausted, ValueError) as exc:
        _error(str(exc))
    console.print(f"[Orch] Reserved attempt {reservation.attempt.number} for {item_id}; state={dispatched.state.value}")


@loop_app.command(help="Move a triaged/rejected/blocked item to ready.")
def ready(item_id: str) -> None:
    config = _project_config()
    service, _, _, _, _ = _build_services(config)
    _item_or_exit(service, item_id)
    try:
        updated = service.ready(item_id)
    except (IllegalTransition, BudgetExhausted) as exc:
        _error(str(exc))
    console.print(f"[Orch] {item_id} state={updated.state.value}")


@loop_app.command(help="Collect a maker result for a running item.")
def collect(
    item_id: str,
    task_id: Annotated[str, typer.Option("--task-id", help="Maker task id to collect.")],
    result: Annotated[str, typer.Option("--result", help="Maker result text.")] = "Maker result collected by loop CLI.",
) -> None:
    config = _project_config()
    service, _, _, _, _ = _build_services(config)
    item = _item_or_exit(service, item_id)
    if item.state is not LoopItemState.RUNNING:
        _error(f"Loop item {item_id} is {item.state.value}; collect requires running.")
    attempt = item.attempts[-1]
    if attempt.maker.task_id and attempt.maker.task_id != task_id:
        _error(f"Task id mismatch for {item_id}: expected {attempt.maker.task_id}")
    try:
        updated = service.collect_maker_result(item_id, attempt_no=attempt.number, result=MakerResult(result))
    except IllegalTransition as exc:
        _error(str(exc))
    console.print(f"[Orch] {item_id} state={updated.state.value}")


@loop_app.command(help="Reserve verification, dispatch verifier, and apply verdict.")
def verify(
    item_id: str,
    verifier: Annotated[str, typer.Option("--verifier", help="Verifier worker name.")],
    allow_same_worker: Annotated[bool, typer.Option("--allow-same-worker", help="Allow maker and verifier to be the same worker.")] = False,
) -> None:
    config = _project_config()
    service, _, _, _, _ = _build_services(config)
    item = _item_or_exit(service, item_id)
    if item.state is not LoopItemState.AWAITING_VERDICT:
        _error(f"Loop item {item_id} is {item.state.value}; verify requires awaiting_verdict.")
    gateway = _build_worker_gateway(config)
    if gateway is None:
        _error("no verifier worker gateway available; broker is unreachable")
    verifier_service = VerifierService(config, gateway=gateway)
    attempt = item.attempts[-1]
    try:
        verifier_service.validate_separation(attempt.maker.worker_name, verifier, allow_same_worker=allow_same_worker)
        reservation = service.reserve_verification(item_id, attempt_no=attempt.number, verifier_worker=verifier)
        verdict = asyncio.run(verifier_service.dispatch_and_collect(reservation.item, reservation.attempt, worktree=reservation.item.worktree))
        applied = service.apply_verdict(item_id, attempt_no=attempt.number, verdict=verdict, allow_same_worker=allow_same_worker)
    except VerifierDispatchError as exc:
        _error(f"verifier dispatch failed: {exc}")
    except VerifierTimeoutError:
        _error("verifier timed out")
    except VerdictParseError:
        _error("verifier produced an unparseable verdict")
    except ValueError as exc:
        _error(f"verdict validation failed: {exc}")
    except (VerifierMismatch, IllegalTransition, WorkerGatewayUnavailable, RuntimeError) as exc:
        _error(str(exc))
    console.print(f"[Orch] {item_id} state={applied.item.state.value} verdict={verdict.verdict.value}")


@loop_app.command(help="Cancel a loop item.")
def cancel(
    item_id: str,
    reason: Annotated[str, typer.Option("--reason", help="Cancellation reason.")],
) -> None:
    config = _project_config()
    service, _, _, _, _ = _build_services(config)
    _item_or_exit(service, item_id)
    try:
        updated = service.cancel(item_id, reason=reason)
    except IllegalTransition as exc:
        _error(str(exc))
    console.print(f"[Orch] {item_id} state={updated.state.value} reason={updated.cancellation_reason}")


@loop_app.command(help="Recover stale active loop items conservatively.")
def recover() -> None:
    config = _project_config()
    service, _, _, _, _ = _build_services(config)
    report = service.recover(broker_client=None)
    console.print(
        f"[Orch] recovered changed={report.items_changed} blocked={report.items_blocked} resumed={report.items_resumed}"
    )
    for note in report.notes:
        console.print(f"- {note}")


@loop_app.command(help="Run the foreground loop engine.")
def watch(
    interval: Annotated[float, typer.Option("--interval", min=0.0, help="Seconds between loop ticks.")] = 5.0,
    max_steps: Annotated[int, typer.Option("--max-steps", min=1, help="Maximum foreground ticks.")] = 10,
    allow_active_attempts: Annotated[bool, typer.Option("--allow-active-attempts", help="Continue even if active attempts already exist.")] = False,
) -> None:
    config = _project_config()
    service, _, _, engine, _ = _build_services(config)
    engine.broker_client = _build_broker_client(config)
    if not allow_active_attempts and any(item.state in ACTIVE_STATES for item in service.ls()):
        _error("Active loop attempts exist; pass --allow-active-attempts to continue.")
    summary = engine.run(max_steps=max_steps, interval_seconds=interval, allow_active_attempts=allow_active_attempts)
    console.print(
        f"[Orch] RunSummary steps={summary.steps} ticks={summary.ticks} dispatched={summary.items_dispatched} "
        f"verified={summary.items_verified} blocked={summary.items_blocked} done={summary.items_done}"
    )
    for note in summary.notes:
        console.print(f"- {note}")
    for error in summary.errors:
        console.print(f"ERROR: {error}")


__all__ = ["loop_app", "register_loop"]
