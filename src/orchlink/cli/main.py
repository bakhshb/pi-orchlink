"""Typer app wiring and entrypoint for the ``orch`` CLI.

Command implementations live in per-group modules under
``orchlink.cli.commands``. This module owns app construction, goal sub-app
mounting, shared project helpers, shared error rendering, and the final
``register_all(app)`` call that mounts every command group.

Command modules import their shared helpers from ``cli.commands._helpers`` and
look up broker/client call sites here only when those are part of the CLI app
wiring.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx
import typer
import uvicorn
from rich.console import Console

# The stdlib modules (time, subprocess, sys) and ``uvicorn`` are imported at
# module scope because command implementations intentionally access them through
# the CLI app module.
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
    register_project_role_sync,
)
from orchlink.connector.pi_connector import PiConnector


__all__ = [
    "PROJECT_ROOT",
    "PiConnector",
    "app",
    "broker_compatible",
    "broker_get_sync",
    "broker_info",
    "broker_post_sync",
    "close_talk_sync",
    "current_project_id",
    "discover_project_root",
    "ensure_broker_running",
    "fetch_events_sync",
    "fetch_status_sync",
    "format_event",
    "job_activity_line",
    "load_goal_app",
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
app = typer.Typer(help="Local broker and connector for Pi lead and named worker sessions.")


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


# Mount every command group on the shared Typer app. This MUST be the last
# top-level statement in the file: command modules import this app module for
# broker/client call sites, so those names must be bound first.
from orchlink.cli.commands import register_all  # noqa: E402  (intentionally last)
register_all(app)


if __name__ == "__main__":
    app()
