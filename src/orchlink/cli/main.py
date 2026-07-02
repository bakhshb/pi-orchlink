"""Typer app wiring and entrypoint for the ``orch`` CLI.

Command implementations live in per-group modules under
``orchlink.cli.commands``. This module owns app construction, goal sub-app
mounting, shared project helpers, shared error rendering, and the final
``register_all(app)`` call that mounts every command group.

Some command modules intentionally look up shared call-site dependencies through
this module at runtime. That keeps dependency overrides centralized for tests
and avoids duplicating broker/client setup across command modules. Those shared
names must be bound before ``register_all(app)`` imports the command modules.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import typer
import uvicorn
from rich.console import Console

# Re-exports for tests that patch via ``monkeypatch.setattr(cli_main, "X", ...)``.
# The stdlib modules (time, subprocess, sys) and ``uvicorn`` are imported so
# test patterns like ``cli_main.time.strftime`` and ``cli_main.uvicorn.run``
# keep working — the test rebinds an attribute on the module object, which
# is visible globally because the call site also does module-attribute
# access.
from orchlink.client import (
    ask_worker_sync as project_ask_worker_sync,
    close_talk_sync,
    format_event,
    send_worker_sync,
    say_talk_sync,
    start_talk_sync,
)
from orchlink.client.process import broker_compatible, broker_info
from orchlink.client.sync import (
    broker_get_sync,
    broker_post_sync,
    ensure_broker_running,
    fetch_events_sync,
    fetch_status_sync,
)
from orchlink.cli.commands._helpers import (
    current_project_id,
    job_activity_line,
    next_conversation_id,
    project_query,
    register_project_role_sync,
)
from orchlink.connector.pi_connector import PiConnector
from orchlink.project.config import ProjectConfigError, load_project_config


__all__ = [
    "PROJECT_ROOT",
    "PiConnector",
    "app",
    "auto_refresh_project_skills",
    "broker_compatible",
    "broker_get_sync",
    "broker_info",
    "broker_post_sync",
    "close_talk_sync",
    "conversation_state",
    "current_project_id",
    "discover_project_root",
    "ensure_broker_running",
    "fetch_events_sync",
    "fetch_status_sync",
    "format_event",
    "job_activity_line",
    "load_goal_app",
    "load_project_or_exit",
    "next_conversation_id",
    "print_orch_exception",
    "project_ask_worker_sync",
    "register_project_role_sync",
    "say_talk_sync",
    "send_worker_sync",
    "start_talk_sync",
    "subprocess",
    "sys",
    "time",
    "uvicorn",
]


console = Console()


def discover_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


PROJECT_ROOT = discover_project_root()
app = typer.Typer(help="Local broker and connector for two Pi coding-agent sessions.")


def load_goal_app() -> typer.Typer:
    try:
        from orchlink.goal.cli import goal_app

        return goal_app
    except Exception as exc:
        error_message = str(exc)
        fallback = typer.Typer(help="Goal Mode is unavailable because its module failed to load.")

        @fallback.callback(invoke_without_command=True)
        def goal_unavailable() -> None:
            console.print(f"[Orch] Goal Mode failed to load: {error_message}")
            raise typer.Exit(1)

        return fallback


app.add_typer(load_goal_app(), name="goal")


def print_orch_exception(exc: Exception) -> None:
    """Render broker error responses for the CLI; imported by command modules."""
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            detail = exc.response.json().get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, dict):
            console.print(f"[Orch] {detail.get('message') or detail.get('error') or exc}")
            if detail.get("blocking_id"):
                console.print(
                    f"[Orch] Blocking work: {detail.get('blocking_id')} "
                    f"({detail.get('blocking_kind', 'work')} {detail.get('blocking_status', '')})"
                )
            return
        if detail:
            console.print(f"[Orch] {detail}")
            return
    console.print(f"[Orch] {exc}")


def load_project_or_exit() -> dict[str, Any]:
    try:
        return load_project_config()
    except ProjectConfigError as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc


def auto_refresh_project_skills(config: dict[str, Any]) -> None:
    from orchlink.project.init import refresh_project_skills_if_needed

    refreshed = refresh_project_skills_if_needed(PROJECT_ROOT)
    if refreshed:
        console.print(
            f"[Orch] Refreshed project skills from current templates: {', '.join(refreshed)}"
        )


def conversation_state(config: dict[str, Any], conversation_id: str) -> dict[str, Any] | None:
    body = broker_get_sync(config, f"/v1/jobs?limit=500{project_query(config, '&')}")
    for job in body.get("jobs", []):
        if job.get("conversation_id") == conversation_id:
            return job
    return None


# Mount every command group on the shared Typer app. This MUST be the last
# top-level statement in the file: it triggers ``cli/commands/__init__.py``,
# which imports every command module; each of those command modules does
# ``from orchlink.cli import main as _cli_main`` to look up test-patchable
# symbols on this module's namespace, and those lookups require that every
# re-export above has been bound.
from orchlink.cli.commands import register_all  # noqa: E402  (intentionally last)
register_all(app)


if __name__ == "__main__":
    app()
