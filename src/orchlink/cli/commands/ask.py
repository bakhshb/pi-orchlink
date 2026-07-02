"""``orch ask``, ``orch send`` — task-submission commands."""

from __future__ import annotations

import json
from typing import Annotated, Any

import httpx
import typer

from rich.console import Console

from orchlink.cli import main as _cli_main
from orchlink.cli.commands._helpers import load_project_or_exit
from orchlink.client import infer_task_mode


console = Console()


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
    """Render the ``last activity: ...`` line used by ``orch task`` and ``get``.

    Inlined rather than imported to keep this module free of cross-group
    dependencies — the helpers module's ``job_activity_line`` has the same
    semantics.
    """
    from orchlink.cli.commands._helpers import job_activity_line as _jal

    return _jal(job)


def print_async_guidance(config: dict[str, Any], worker_id: str, task_id: str) -> None:
    console.print(f"[Orch] Sent {task_id} to {worker_id}.")
    console.print("[Orch] Async mode: worker scope is pending.")
    console.print("[Orch] Check status: orch jobs")
    console.print(f"[Orch] Wait: orch wait {task_id}")
    console.print(f"[Orch] Read result: orch get {task_id}")


def register_ask(app: typer.Typer) -> None:
    """Register ask and send on the given Typer app."""

    @app.command(help="Send a task to work and wait by default; use for reviews and decisions.")
    def ask(
        worker_id: str,
        task_id: Annotated[
            str,
            typer.Option("--task", "--task-id", "-t", help="Exact task ID to assign, such as T001."),
        ],
        message: Annotated[
            str,
            typer.Option("--msg", "--message", "-m", help="Task prompt for the worker."),
        ],
        timeout: Annotated[
            int,
            typer.Option("--timeout", help="Seconds to wait for the worker reply."),
        ] = 1800,
        wait: Annotated[
            bool,
            typer.Option(
                "--wait/--no-wait",
                help="Wait in this shell for the reply. Use orch send for async tasks.",
            ),
        ] = True,
    ) -> None:
        from orchlink.cli.main import print_orch_exception  # late import

        config = load_project_or_exit()
        try:
            _cli_main.ensure_broker_running(config)
            response = _cli_main.project_ask_worker_sync(
                config=config,
                worker=worker_id,
                task_id=task_id,
                message=message,
                timeout_seconds=timeout,
                wait=wait,
            )
        except (RuntimeError, httpx.HTTPError) as exc:
            print_orch_exception(exc)
            raise typer.Exit(1) from exc
        if not wait:
            print_async_guidance(config, worker_id, task_id)
        console.print_json(json.dumps(response))

    @app.command(help="Send an async task to work when the lead can continue on another scope.")
    def send(
        worker_id: str,
        task_id: Annotated[
            str,
            typer.Option("--task", "--task-id", "-t", help="Exact task ID to assign, such as T002."),
        ],
        message: Annotated[
            str,
            typer.Option("--msg", "--message", "-m", help="Task prompt for the worker."),
        ],
        timeout: Annotated[
            int,
            typer.Option("--timeout", help="Task timeout in seconds."),
        ] = 1800,
        allow_async_review: Annotated[
            bool,
            typer.Option(
                "--allow-async-review",
                help="Allow REVIEW through async send. Use only when review is not a gate.",
            ),
        ] = False,
    ) -> None:
        from orchlink.cli.main import print_orch_exception  # late import

        mode = infer_task_mode(message)
        if mode == "REVIEW" and not allow_async_review:
            console.print("[Orch] REVIEW is a gate by default.")
            console.print(f"[Orch] Use blocking review: orch ask work --wait -t {task_id} -m \"Please review ...\"")
            console.print("[Orch] Or pass --allow-async-review only if lead will not act on the review result.")
            raise typer.Exit(1)

        config = load_project_or_exit()
        try:
            _cli_main.ensure_broker_running(config)
            _cli_main.send_worker_sync(
                config=config,
                worker=worker_id,
                task_id=task_id,
                message=message,
                timeout_seconds=timeout,
            )
        except (RuntimeError, httpx.HTTPError) as exc:
            print_orch_exception(exc)
            raise typer.Exit(1) from exc
        print_async_guidance(config, worker_id, task_id)
        if mode == "REVIEW" and allow_async_review:
            console.print(
                f"[Orch] Async REVIEW is not a gate. Before acting on it, verify the exact result with: orch wait {task_id}"
            )
