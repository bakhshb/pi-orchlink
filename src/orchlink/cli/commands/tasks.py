"""``orch send`` — task-submission command."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from rich.console import Console

from orchlink.cli import main as _cli_main
from orchlink.cli.commands._helpers import load_project_or_exit
from orchlink.cli.message_input import resolve_message_option


console = Console()
_stderr_console = Console(stderr=True)


def _emit_async_handle(payload: dict[str, Any]) -> None:
    """Emit the canonical async tracking handle to stdout as a single JSON line.

    Shape is stable for machine callers (notably the Pi ``delegate_worker``
    tool that uses this mode to bypass the lead terminal entirely). The
    canonical envelope construction is owned by ``orchlink.client.messages``
    and reused unchanged — only the output representation is shaped here.
    """
    handle = {
        "worker": str(payload.get("to_agent") or payload.get("worker") or ""),
        "task_id": str(payload.get("task_id") or ""),
        "correlation_id": str(payload.get("correlation_id") or ""),
        "conversation_id": str(payload.get("conversation_id") or ""),
        "status": str(payload.get("status") or "PENDING"),
        "accepted_at": datetime.now(timezone.utc).isoformat(),
    }
    sys.stdout.write(json.dumps(handle, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _print_task_body(body: dict[str, Any]) -> None:
    task_id = str(body.get("task_id") or "")
    status_text = str(body.get("status") or "UNKNOWN")
    if status_text == "WAIT_TIMEOUT":
        console.print(
            f"[Orch] Wait for task {task_id}: timed out, but the task is still pending unless cancelled or task timeout expires."
        )
        if body.get("error"):
            console.print(str(body["error"]))
        return
    console.print(f"[Orch] Task {task_id}: {status_text}")
    reply = body.get("reply") or {}
    if reply:
        console.print(f"[Orch] Type: {reply.get('type', 'RESULT')}")
        payload = reply.get("payload") or {}
        summary = str(payload.get("summary") or payload.get("stdout") or "").strip()
        stderr = str(payload.get("stderr") or "").strip()
        if summary:
            console.print(summary)
        if stderr:
            console.print("[Orch] Stderr:")
            console.print(stderr)
    elif body.get("job"):
        job = body["job"]
        console.print(f"[Orch] Route: {job.get('from_agent', '-')} → {job.get('to_agent', '-')}")
        activity = job_activity_line_for(job)
        if activity:
            console.print(f"[Orch] Last worker activity: {activity}")
        preview = str(job.get("preview") or "").strip()
        if preview:
            console.print(preview)
    elif body.get("error"):
        console.print(str(body["error"]))


def job_activity_line_for(job: dict[str, Any]) -> str:
    """Render the ``last activity: ...`` line used by jobs result/live output.

    Inlined rather than imported to keep this module free of cross-group
    dependencies — the helpers module's ``job_activity_line`` has the same
    semantics.
    """
    from orchlink.cli.commands._helpers import job_activity_line as _jal

    return _jal(job)


def print_async_guidance(worker_id: str, task_id: str) -> None:
    console.print(f"[Orch] Sent {task_id} to {worker_id}.")
    console.print("[Orch] Async mode: keep the task ID and continue only on non-conflicting lead-owned work.")
    console.print("[Orch] Check active work: orch jobs --active")
    console.print(f"[Orch] Check activity if needed: orch jobs --live {task_id}")
    console.print(f"[Orch] Read result when ready: orch jobs --result {task_id}")
    console.print(f"[Orch] Block only if this now gates you: orch jobs --wait {task_id}")
    console.print(
        f"[Orch] Closeout: before a human-facing completion/decision, read {task_id} "
        "or report it pending with blocking status and retrieval command."
    )


def _run_send_command(
    config: dict[str, Any],
    worker_id: str,
    task_id: str,
    message: str,
    message_file: Path | None,
    edit: bool,
    timeout: int,
    wait: bool,
    thinking: str | None,
    async_json: bool,
    foreground_json: bool,
) -> None:
    """Canonical task submission used by ``orch send``."""
    from orchlink.cli.main import print_orch_exception  # late import

    if foreground_json and (not async_json or wait):
        _stderr_console.print("[Orch] --foreground-json requires --async-json and --no-wait.")
        raise typer.Exit(2)

    try:
        message = resolve_message_option(message, message_file, edit, config, task_id, worker_id, "task")
    except (RuntimeError, httpx.HTTPError, ValueError) as exc:
        print_orch_exception(exc)
        raise typer.Exit(1) from exc

    try:
        _cli_main.ensure_broker_running(config)
        response = _cli_main.send_worker_sync(
            config=config,
            worker=worker_id,
            task_id=task_id,
            message=message,
            timeout_seconds=timeout,
            wait=wait,
            thinking=thinking,
            delivery="blocking" if foreground_json else None,
        )
    except (RuntimeError, httpx.HTTPError, ValueError) as exc:
        print_orch_exception(exc)
        raise typer.Exit(1) from exc

    if async_json:
        # Machine-readable mode: single-line handle to stdout, no human
        # guidance. Errors go to stderr so a JSON consumer can parse stdout
        # without spurious noise. ``wait`` mode still goes through Typer's
        # console so additivity is preserved.
        if not wait:
            _emit_async_handle(response)
        else:
            # Surface both: the canonical sync reply AND the handle. Machine
            # callers that only care about the handle can split on the last
            # JSON line.
            _emit_async_handle(response)
            console.print_json(json.dumps(response))
        return
    if wait:
        console.print_json(json.dumps(response))
    else:
        print_async_guidance(worker_id, task_id)


def register_send(app: typer.Typer) -> None:
    """Register send on the given Typer app."""

    @app.command(help="Send a task to work; default async, use --wait for blocking review/decision tasks.")
    def send(
        worker_id: str,
        task_id: Annotated[
            str,
            typer.Option("--task", "--task-id", "-t", help="Exact task ID to assign, such as T002."),
        ],
        message: Annotated[
            str,
            typer.Option("--msg", "--message", "-m", help="Task prompt for the worker. Use - to read from stdin."),
        ] = "",
        message_file: Annotated[
            Path | None,
            typer.Option("--message-file", "-F", help="Read the task prompt from a UTF-8 file. Use - for stdin."),
        ] = None,
        edit: Annotated[
            bool,
            typer.Option("--edit", "-e", help="Open VISUAL/EDITOR to write the task prompt."),
        ] = False,
        timeout: Annotated[
            int,
            typer.Option("--timeout", help="Task timeout in seconds."),
        ] = 1800,
        wait: Annotated[
            bool,
            typer.Option("--wait/--no-wait", help="Wait in this shell for the reply."),
        ] = False,
        thinking: Annotated[
            str | None,
            typer.Option("--thinking", help="Override worker thinking for this task: off, minimal, low, medium, high, xhigh."),
        ] = None,
        async_json: Annotated[
            bool,
            typer.Option(
                "--async-json",
                help=(
                    "Emit a stable single-line JSON tracking handle to stdout and suppress "
                    "human-readable guidance. Used by the Pi delegate_worker tool to reuse "
                    "the canonical Python envelope builder without duplicating it."
                ),
            ),
        ] = False,
        foreground_json: Annotated[
            bool,
            typer.Option(
                "--foreground-json",
                help=(
                    "Machine integration mode: submit with blocking delivery but return the "
                    "JSON handle immediately so a native UI can stream broker progress."
                ),
                hidden=True,
            ),
        ] = False,
    ) -> None:
        config = load_project_or_exit()
        _run_send_command(
            config,
            worker_id,
            task_id,
            message,
            message_file,
            edit,
            timeout,
            wait,
            thinking,
            async_json,
            foreground_json,
        )
