"""``orch lead``, ``orch work``, ``orch stop`` — Pi session commands."""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from rich.console import Console

# ``main`` is the cli/main module; we look up the test-patched symbols on its
# namespace at call time so existing ``monkeypatch.setattr(cli_main, "X", ...)``
# patterns keep working. See ``orchlink/cli/main.py`` for the full rationale.
from orchlink.cli import main as _cli_main

from orchlink.cli.commands._helpers import auto_refresh_project_skills, load_project_or_exit
from orchlink.client.process import broker_pid_path
from orchlink.connector.pi_connector import PiConnectorError
from orchlink.project.config import role_agent_id, save_project_config


console = Console()


def with_new_pi_session(config: dict[str, Any], role: str) -> tuple[dict[str, Any], str]:
    """Reset the saved ``lead``/``work`` session id so the next Pi process starts fresh."""
    # ``time.strftime`` is module-attribute access on the stdlib ``time`` module;
    # ``cli/main.py`` re-imports ``time`` so the test pattern
    # ``cli_main.time.strftime`` rebinds globally.
    session_id = f"{role}-{time.strftime('%Y%m%d-%H%M%S')}"
    updated = dict(config)
    updated[role] = dict(config.get(role) or {})
    updated[role]["session_id"] = session_id
    save_project_config(updated)
    return updated, session_id


def stop_pid_file(path: Path, label: str) -> None:
    """Stop the background process recorded in ``path`` (a PID file)."""
    if not path.is_file():
        console.print(f"[Orch] No {label} PID file found for this project.")
        return
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        path.unlink(missing_ok=True)
        console.print(f"[Orch] Removed invalid {label} PID file.")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        console.print(f"[Orch] {label} process was not running.")
    else:
        console.print(f"[Orch] Stopped {label} PID {pid}")
    path.unlink(missing_ok=True)


def register_lead(app: typer.Typer) -> None:
    """Register the lead/work/stop commands on the given Typer app."""

    @app.command(help="Start or reopen the visible Pi lead session for this project.")
    def lead(
        new: Annotated[
            bool,
            typer.Option("--new", help="Start a new Pi lead session instead of reopening the saved lead session."),
        ] = False,
    ) -> None:
        config = load_project_or_exit()
        try:
            auto_refresh_project_skills(config)
            _cli_main.ensure_broker_running(config)
            console.print("[Orch] Broker online")
            _cli_main.register_project_role_sync(config, "lead")
            console.print(f"[Orch] Registered: {role_agent_id(config, 'lead')}")
            if new:
                config, session_id = with_new_pi_session(config, "lead")
                console.print(f"[Orch] New Pi lead session: {session_id}")
            console.print("[Orch] Worker available: work")
            console.print("[Orch] Starting Pi lead session...")
            console.print("[Orch] Lead will listen for worker replies and talk messages.")
            exit_code = _cli_main.PiConnector(config).run_lead()
        except (RuntimeError, PiConnectorError, httpx.HTTPError) as exc:
            console.print(f"[Orch] {exc}")
            raise typer.Exit(1) from exc
        raise typer.Exit(exit_code)

    @app.command(help="Start or reopen the visible Pi worker session for this project.")
    def work(
        new: Annotated[
            bool,
            typer.Option("--new", help="Start a new Pi worker session instead of reopening the saved worker session."),
        ] = False,
    ) -> None:
        config = load_project_or_exit()
        try:
            auto_refresh_project_skills(config)
            _cli_main.ensure_broker_running(config)
            console.print("[Orch] Broker online")
            _cli_main.register_project_role_sync(config, "worker")
            console.print(f"[Orch] Registered: {role_agent_id(config, 'work')}")
            if new:
                config, session_id = with_new_pi_session(config, "work")
                console.print(f"[Orch] New Pi worker session: {session_id}")

            connector = _cli_main.PiConnector(config)
            if not connector.check_available():
                raise PiConnectorError(f"Pi command not found: {connector.pi_command()}")
            console.print("[Orch] Starting Pi worker session...")
            console.print("[Orch] Tasks and talk turns will be posted directly into this Pi chat.")
            exit_code = connector.run_work()
        except (RuntimeError, PiConnectorError, httpx.HTTPError) as exc:
            console.print(f"[Orch] {exc}")
            raise typer.Exit(1) from exc
        raise typer.Exit(exit_code)

    @app.command(help="Stop the background broker process for this project.")
    def stop() -> None:
        config = load_project_or_exit()
        stop_pid_file(broker_pid_path(config), "broker")
