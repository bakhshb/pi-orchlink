"""``orch doctor`` and ``orch resume`` — diagnostic commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from rich.console import Console

from orchlink.broker.checkpoint import (
    Checkpoint,
    checkpoint_path,
    load_checkpoint,
    reconcile_checkpoint,
)
from orchlink.cli import main as _cli_main
from orchlink.cli.commands._helpers import (
    current_project_id,
    load_project_or_exit,
    project_query,
)
from orchlink.cli.resume import (
    ActiveTaskSummary,
    ResumeState,
    SessionSummary,
    render_resume_report,
    resume_state_from_checkpoint,
)
from orchlink.project.config import (
    ProjectConfigError,
    broker_store_backend,
    broker_store_path,
    broker_url,
    load_project_config,
    project_root,
)
from orchlink.project.init import project_skill_statuses


console = Console()


def _resume_active_from_status(config: dict[str, Any], body: dict[str, Any]) -> list[ActiveTaskSummary]:
    from orchlink.cli.commands._helpers import filter_jobs, job_id, job_kind

    active: list[ActiveTaskSummary] = []
    for job in filter_jobs(body.get("jobs", []), active=True):
        item_id = job_id(job)
        if item_id == "-":
            continue
        active.append(
            ActiveTaskSummary(
                task_id=item_id,
                kind=job_kind(job),
                state=str(job.get("status") or "UNKNOWN"),
                title=str(job.get("preview") or job.get("last_message_preview") or ""),
            )
        )
    import yaml

    goals_dir = project_root(config) / ".orch" / "goals"
    if goals_dir.is_dir():
        for goal_file in sorted(goals_dir.glob("G*/goal.yaml")):
            try:
                data = yaml.safe_load(goal_file.read_text(encoding="utf-8")) or {}
            except (OSError, yaml.YAMLError):
                continue
            status = str(data.get("status") or "")
            if status not in {"running", "blocked", "gated"}:
                continue
            goal_id = str(data.get("id") or goal_file.parent.name)
            active.append(
                ActiveTaskSummary(
                    task_id=goal_id,
                    kind="goal",
                    state=status,
                    title=str(data.get("title") or ""),
                )
            )
    return active


def _resume_sessions_from_status(body: dict[str, Any]) -> list[SessionSummary]:
    sessions: list[SessionSummary] = []
    for session in body.get("sessions", []):
        role = str(session.get("role") or session.get("agent_id") or "unknown")
        state = str(session.get("status") or "unknown").lower()
        detail = str(session.get("agent_id") or "")
        sessions.append(SessionSummary(role=role, state=state, detail=detail))
    return sessions


def _resume_current_leases_from_status(body: dict[str, Any]) -> dict[str, tuple[int, str]]:
    leases: dict[str, tuple[int, str]] = {}
    for job in body.get("jobs", []):
        lease = job.get("lease") or {}
        task_id = job.get("task_id") or job.get("id")
        if task_id and lease:
            leases[str(task_id)] = (int(lease.get("epoch") or 0), str(lease.get("holder") or ""))
    return leases


def _checkpoint_for_project(checkpoint: Checkpoint, project_id: str) -> Checkpoint:
    """Return only checkpoint leases owned by the current project's agents."""
    holder_prefix = f"{project_id}."
    return Checkpoint(
        version=checkpoint.version,
        last_checkpoint_at=checkpoint.last_checkpoint_at,
        leases=[lease for lease in checkpoint.leases if not lease.holder or lease.holder.startswith(holder_prefix)],
    )


def register_diagnose(app: typer.Typer) -> None:
    """Register doctor and resume on the given Typer app."""

    @app.command(help="Check local Orchlink project setup, broker compatibility, and generated skills.")
    def doctor() -> None:
        from orchlink.cli.main import PROJECT_ROOT

        console.print("Orchlink doctor")
        console.print(f"Package file: {Path(__file__).resolve()}")
        console.print(f"Project root: {PROJECT_ROOT}")

        try:
            config = load_project_config()
        except ProjectConfigError:
            console.print(".orch/project.yaml: missing")
        else:
            connector = _cli_main.PiConnector(config)
            info = _cli_main.broker_info(broker_url(config))
            console.print(f".orch/project.yaml: found ({config.get('_config_path')})")
            console.print(f"Project ID: {current_project_id(config)}")
            console.print(f"Broker URL: {broker_url(config)}")
            console.print(f"Broker store: {broker_store_backend(config)} ({broker_store_path(config)})")
            console.print(f"Broker reachable: {'yes' if info else 'no'}")
            if info:
                console.print(
                    f"Broker version: {info.get('version', 'unknown')} "
                    f"({'compatible' if _cli_main.broker_compatible(info) else 'stale'})"
                )
            console.print("API key configured: yes")
            console.print(
                f"Pi command: {connector.pi_command()} ({'found' if connector.check_available() else 'missing'})"
            )
            statuses = project_skill_statuses(project_root(config))
            stale = False
            missing = False
            for role in ("lead", "work"):
                status_text = statuses[role]
                stale = stale or status_text == "stale"
                missing = missing or status_text == "missing"
                console.print(f"{role}.md: {status_text}")
            reference_statuses = [status for name, status in statuses.items() if name.startswith("references/")]
            if reference_statuses:
                references_current = all(status == "current" for status in reference_statuses)
                stale = stale or any(status == "stale" for status in reference_statuses)
                missing = missing or any(status == "missing" for status in reference_statuses)
                console.print(f"references/: {'current' if references_current else 'stale'}")
            if stale or missing:
                console.print("Project .orch files: stale")
                console.print("Run: orch init --refresh-skills")
            else:
                console.print("Project .orch files: current")

        console.print("CLI symlink: ~/.local/bin/orch -> <orchlink-repo>/.venv/bin/orch")

    @app.command(help="Show a single recovery report: active work, sessions, checkpoint drift, and next action.")
    def resume() -> None:
        config = load_project_or_exit()
        try:
            _cli_main.ensure_broker_running(config)
            body = _cli_main.broker_get_sync(config, f"/v1/status{project_query(config)}")
        except (RuntimeError, httpx.HTTPError) as exc:
            console.print(f"[Orch] {exc}")
            raise typer.Exit(1) from exc

        checkpoint_file = checkpoint_path(project_root(config))
        checkpoint = load_checkpoint(checkpoint_file) if checkpoint_file.is_file() else None
        if checkpoint is not None:
            checkpoint = _checkpoint_for_project(checkpoint, current_project_id(config))
        active = _resume_active_from_status(config, body)
        sessions_state = _resume_sessions_from_status(body)
        if checkpoint is None:
            state = ResumeState(
                mode="normal" if active else "idle",
                active=active,
                sessions=sessions_state,
                checkpoint=None,
            )
        else:
            state = resume_state_from_checkpoint(
                checkpoint,
                reconcile_checkpoint(checkpoint, _resume_current_leases_from_status(body)),
                active=active,
                sessions=sessions_state,
            )
        console.print(render_resume_report(state), end="", markup=False)
