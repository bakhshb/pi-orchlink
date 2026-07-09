"""Typer CLI for Loop Mode."""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
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
from orchlink.loop.adapters.state_repo import LoopStateRepo
from orchlink.loop.domain.errors import BudgetExhausted, IllegalTransition, VerifierMismatch
from orchlink.loop.domain.item import LoopItem, LoopItemState, MakerResult, WorkerAssignment
from orchlink.loop.domain.verdict import ReasonCode
from orchlink.loop.domain.worktree import Worktree
from orchlink.loop.services import LoopEngine, LoopService, TriageService, VerifierService, WorkerService
from orchlink.loop.services.triage_service import build_project_connectors
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

    async def dispatch_maker(self, maker_assignment: WorkerAssignment, prompt: str) -> VerifierHandle:
        if maker_assignment.task_id is None:
            raise VerifierDispatchError("maker assignment is missing a task id")
        task_id = maker_assignment.task_id
        if task_id.startswith("reserved:"):
            task_id = f"loop:maker:{task_id.removeprefix('reserved:')}"
        return await self._dispatch(maker_assignment.worker_name, task_id, prompt)

    async def dispatch_verifier(self, verifier_assignment: WorkerAssignment, prompt: str) -> VerifierHandle:
        if verifier_assignment.task_id is None:
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
    triage_service = TriageService(config, loop_service, build_project_connectors(config, project_root(config)))
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


def _build_worker_runtime(config: dict[str, Any]) -> tuple[LoopWorkerGateway | None, WorkerService | None]:
    """Build foreground worker dispatch, falling back to no WorkerService.

    Broker or gateway construction failures are intentionally non-fatal: watch
    then follows LoopEngine's existing broker_unavailable/maker_unavailable
    paths instead of crashing during composition.
    """
    try:
        gateway = _build_worker_gateway(config)
    except Exception:
        return None, None
    if gateway is None:
        return None, None
    return gateway, WorkerService(config, gateway)


def _configure_engine_runtime(config: dict[str, Any], engine: LoopEngine, *, run_checks: bool) -> None:
    engine.config["run_checks"] = run_checks
    gateway, worker_service = _build_worker_runtime(config)
    if worker_service is not None:
        engine.worker_service = worker_service
    if gateway is not None:
        engine.verifier_service = VerifierService(config, gateway=gateway)
    engine.broker_client = _build_broker_client(config)


def _print_run_summary(summary) -> None:
    console.print(
        f"[Orch] RunSummary steps={summary.steps} ticks={summary.ticks} dispatched={summary.items_dispatched} "
        f"verified={summary.items_verified} blocked={summary.items_blocked} done={summary.items_done}"
    )
    for note in summary.notes:
        console.print(f"- {note}")
    for error in summary.errors:
        console.print(f"ERROR: {error}")


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


@loop_app.command(help="Run one bounded loop invocation and exit.")
def tick(
    run_checks: Annotated[bool, typer.Option("--run-checks", help="Run configured objective checks before verifier dispatch.")] = False,
    max_steps: Annotated[int, typer.Option("--max-steps", min=1, help="Maximum foreground ticks.")] = 1,
    allow_active_attempts: Annotated[bool, typer.Option("--allow-active-attempts", help="Continue even if active attempts already exist.")] = False,
) -> None:
    config = _project_config()
    try:
        _, _, _, engine, _ = _build_services(config)
        _configure_engine_runtime(config, engine, run_checks=run_checks)
        summary = engine.run(max_steps=max_steps, interval_seconds=0, allow_active_attempts=allow_active_attempts)
    except Exception as exc:
        console.print(f"ERROR: {exc}")
        raise typer.Exit(1) from exc
    _print_run_summary(summary)
    if summary.errors:
        raise typer.Exit(1)


_SCHEDULE_TAG = "# orchlink-loop"
_SYSTEMD_ENV = "ORCHLINK_LOOP_SYSTEMD_DIR"


def _parse_schedule_interval(value: str) -> tuple[str, str]:
    normalized = str(value or "").strip().lower()
    if normalized == "30m":
        return "*/30 * * * *", "*:0/30:00"
    if normalized == "1h":
        return "0 * * * *", "hourly"
    if normalized == "6h":
        return "0 */6 * * *", "*-*-* 0/6:00:00"
    if normalized == "daily":
        return "0 0 * * *", "daily"
    raise ValueError("invalid schedule interval; use 30m, 1h, 6h, or daily")


def _tick_command(config: dict[str, Any], *, max_steps: int, run_checks: bool) -> str:
    args = f"orch loop tick --max-steps {max_steps}"
    if run_checks:
        args += " --run-checks"
    return f"cd {shlex.quote(str(project_root(config)))} && {args}"


def _crontab_line(config: dict[str, Any], *, every: str, max_steps: int, run_checks: bool) -> str:
    cron, _ = _parse_schedule_interval(every)
    return f"{cron} {_tick_command(config, max_steps=max_steps, run_checks=run_checks)}"


def _read_crontab() -> str:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=False)  # noqa: S603 - user crontab CLI.
    return result.stdout if result.returncode == 0 else ""


def _write_crontab(text: str) -> None:
    subprocess.run(["crontab", "-"], input=text, text=True, check=True)  # noqa: S603 - user crontab CLI.


def _crontab_lines_without_schedule(current: str) -> list[str]:
    lines: list[str] = []
    in_schedule_block = False
    for existing in current.splitlines():
        if _SCHEDULE_TAG in existing:
            lowered = existing.lower()
            if "begin" in lowered:
                in_schedule_block = True
            elif "end" in lowered:
                in_schedule_block = False
            continue
        if not in_schedule_block:
            lines.append(existing)
    return lines


def _replace_schedule_line(current: str, line: str | None) -> str:
    lines = _crontab_lines_without_schedule(current)
    if line is not None:
        lines.append(f"{line} {_SCHEDULE_TAG}")
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def _systemd_dir() -> Path:
    override = os.environ.get(_SYSTEMD_ENV)
    return Path(override).expanduser() if override else Path("~/.config/systemd/user").expanduser()


def _systemd_text(config: dict[str, Any], *, every: str, max_steps: int, run_checks: bool) -> tuple[str, str]:
    _, calendar = _parse_schedule_interval(every)
    command = f"orch loop tick --max-steps {max_steps}" + (" --run-checks" if run_checks else "")
    service = "\n".join(
        [
            "[Unit]",
            "Description=Orchlink loop tick",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={project_root(config)}",
            f"ExecStart={command}",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )
    timer = "\n".join(
        [
            "[Unit]",
            "Description=Run Orchlink loop tick on schedule",
            "",
            "[Timer]",
            f"OnCalendar={calendar}",
            "Persistent=true",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )
    return service, timer


def _show_crontab_schedule() -> None:
    installed = [line for line in _read_crontab().splitlines() if _SCHEDULE_TAG in line]
    if not installed:
        console.print("No schedule installed.")
        return
    for line in installed:
        console.print(line)


def _install_crontab_schedule(line: str) -> None:
    _write_crontab(_replace_schedule_line(_read_crontab(), line))
    console.print(line)


def _remove_crontab_schedule() -> None:
    current = _read_crontab()
    updated = _replace_schedule_line(current, None)
    if updated == (current if current.endswith("\n") or not current else current + "\n"):
        console.print("No schedule installed.")
    else:
        _write_crontab(updated)
        console.print("Removed orchlink loop schedule.")


def _show_systemd_schedule() -> None:
    directory = _systemd_dir()
    service_path = directory / "orchlink-loop.service"
    timer_path = directory / "orchlink-loop.timer"
    if not service_path.exists() and not timer_path.exists():
        console.print("No schedule installed.")
        return
    if service_path.exists():
        console.print(service_path.read_text(encoding="utf-8"))
    if timer_path.exists():
        console.print(timer_path.read_text(encoding="utf-8"))


def _install_systemd_schedule(config: dict[str, Any], *, every: str, max_steps: int, run_checks: bool) -> None:
    service, timer = _systemd_text(config, every=every, max_steps=max_steps, run_checks=run_checks)
    directory = _systemd_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "orchlink-loop.service").write_text(service, encoding="utf-8")
    (directory / "orchlink-loop.timer").write_text(timer, encoding="utf-8")
    console.print("systemctl --user daemon-reload && systemctl --user enable --now orchlink-loop.timer")


def _remove_systemd_schedule() -> None:
    directory = _systemd_dir()
    service_path = directory / "orchlink-loop.service"
    timer_path = directory / "orchlink-loop.timer"
    existed = service_path.exists() or timer_path.exists()
    service_path.unlink(missing_ok=True)
    timer_path.unlink(missing_ok=True)
    if not existed:
        console.print("No schedule installed.")
        return
    console.print("systemctl --user disable --now orchlink-loop.timer")


@loop_app.command(help="Print or install a discrete orch loop tick schedule.")
def schedule(
    every: Annotated[str | None, typer.Option("--every", help="Interval: 30m, 1h, 6h, or daily.")] = None,
    max_steps: Annotated[int, typer.Option("--max-steps", min=1, help="Max steps passed to orch loop tick.")] = 1,
    run_checks: Annotated[bool, typer.Option("--run-checks", help="Include --run-checks in scheduled ticks.")] = False,
    systemd: Annotated[bool, typer.Option("--systemd", help="Use a systemd user timer instead of crontab.")] = False,
    install: Annotated[bool, typer.Option("--install", help="Install the schedule explicitly.")] = False,
    show: Annotated[bool, typer.Option("--show", help="Show the installed schedule.")] = False,
    remove: Annotated[bool, typer.Option("--remove", help="Remove the installed schedule.")] = False,
) -> None:
    config = _project_config()
    try:
        if sum(bool(flag) for flag in (install, show, remove)) > 1:
            _error("Use only one of --install, --show, or --remove.")
        if show:
            _show_systemd_schedule() if systemd else _show_crontab_schedule()
            return
        if remove:
            _remove_systemd_schedule() if systemd else _remove_crontab_schedule()
            return
        if every is None:
            _error("--every is required unless --show or --remove is used.")
        if systemd:
            if install:
                _install_systemd_schedule(config, every=every, max_steps=max_steps, run_checks=run_checks)
            else:
                service, timer = _systemd_text(config, every=every, max_steps=max_steps, run_checks=run_checks)
                console.print(service)
                console.print(timer)
            return
        line = _crontab_line(config, every=every, max_steps=max_steps, run_checks=run_checks)
        if install:
            _install_crontab_schedule(line)
        else:
            console.print(line)
    except ValueError as exc:
        _error(str(exc))


@loop_app.command(help="Run the foreground loop engine.")
def watch(
    interval: Annotated[float, typer.Option("--interval", min=0.0, help="Seconds between loop ticks.")] = 5.0,
    max_steps: Annotated[int, typer.Option("--max-steps", min=1, help="Maximum foreground ticks.")] = 10,
    allow_active_attempts: Annotated[bool, typer.Option("--allow-active-attempts", help="Continue even if active attempts already exist.")] = False,
    run_checks: Annotated[bool, typer.Option("--run-checks", help="Run configured objective checks before verifier dispatch.")] = False,
) -> None:
    config = _project_config()
    service, _, _, engine, _ = _build_services(config)
    _configure_engine_runtime(config, engine, run_checks=run_checks)
    if not allow_active_attempts and any(item.state in ACTIVE_STATES for item in service.ls()):
        _error("Active loop attempts exist; pass --allow-active-attempts to continue.")
    summary = engine.run(max_steps=max_steps, interval_seconds=interval, allow_active_attempts=allow_active_attempts)
    _print_run_summary(summary)


__all__ = ["loop_app", "register_loop"]
