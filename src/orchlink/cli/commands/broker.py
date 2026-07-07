"""``orch broker`` — local Orchlink broker management and raw debug views."""

from __future__ import annotations

import json
import os
import time
from typing import Annotated

import typer
import uvicorn

from rich.console import Console

from orchlink.cli import main as _cli_main
from orchlink.cli.commands._helpers import current_project_id, load_project_or_exit
from orchlink.project.config import ProjectConfigError, broker_api_key, broker_url, load_project_config


console = Console()
broker_app = typer.Typer(help="Run and manage the local Orchlink broker.")


def register_broker(app: typer.Typer) -> None:
    """Mount the broker sub-typer on the given app."""
    app.add_typer(broker_app, name="broker")

    @broker_app.command("status", help="Print raw broker status JSON for debugging; not normal coordination output.")
    def broker_status(
        broker_url_option: Annotated[
            str,
            typer.Option("--broker-url", help="Broker base URL to query."),
        ] = "http://127.0.0.1:8787",
        api_key: Annotated[str, typer.Option("--api-key", help="Broker API key.")] = "change-me",
        project_id: Annotated[str | None, typer.Option("--project-id", help="Filter to one project_id.")] = None,
        all_projects: Annotated[
            bool,
            typer.Option("--all-projects", help="Do not apply the current project_id filter."),
        ] = False,
        task_id: Annotated[str | None, typer.Option("--task", help="Filter jobs/messages/events to one task ID.")] = None,
        since_id: Annotated[int, typer.Option("--since-id", min=0, help="Only include events after this event ID.")] = 0,
        limit: Annotated[int, typer.Option("--limit", min=1, max=500, help="Limit jobs and events in status output.")] = 20,
    ) -> None:
        effective_project_id = project_id
        if effective_project_id is None and not all_projects:
            try:
                effective_project_id = current_project_id(load_project_config())
            except ProjectConfigError:
                effective_project_id = None
        response = _cli_main.fetch_status_sync(
            broker_url_option,
            api_key,
            project_id=effective_project_id,
            task_id=task_id,
            since=since_id,
            limit=limit,
        )
        console.print_json(json.dumps(response))

    @broker_app.command("watch", help="Watch raw broker events for debugging worker activity and routing.")
    def broker_watch(
        interval_seconds: Annotated[
            float,
            typer.Option("--interval-seconds", help="Seconds between event polls."),
        ] = 2.0,
        iterations: Annotated[
            int,
            typer.Option("--iterations", help="0 means watch forever."),
        ] = 0,
        limit: Annotated[
            int,
            typer.Option("--limit", help="Maximum events to fetch per poll."),
        ] = 50,
    ) -> None:
        config = load_project_or_exit()
        try:
            _cli_main.ensure_broker_running(config)
        except RuntimeError as exc:
            console.print(f"[Orch] {exc}")
            raise typer.Exit(1) from exc

        last_event_id = 0
        count = 0
        while True:
            body = _cli_main.fetch_events_sync(
                broker_url(config),
                broker_api_key(config),
                since=last_event_id,
                limit=limit,
                project_id=current_project_id(config),
            )
            for event in body.get("events", []):
                console.print(_cli_main.format_event(event))
                console.print()
            last_event_id = int(body.get("last_event_id", last_event_id))
            count += 1
            if iterations and count >= iterations:
                return
            time.sleep(interval_seconds)

    @broker_app.command("run", help="Run the local Orchlink broker HTTP server in the foreground.")
    def broker_run(
        host: Annotated[str, typer.Option("--host", help="Host interface to bind.")] = "127.0.0.1",
        port: Annotated[int, typer.Option("--port", help="TCP port to bind.")] = 8787,
        reload: Annotated[
            bool,
            typer.Option("--reload", help="Enable uvicorn auto-reload for development."),
        ] = False,
        store_backend: Annotated[
            str,
            typer.Option("--store-backend", help="Store backend: memory or jsonl."),
        ] = "memory",
        store_path: Annotated[
            str,
            typer.Option("--store-path", help="Path for jsonl store snapshots."),
        ] = ".orch/run/orchlink-journal.jsonl",
    ) -> None:
        os.environ["ORCHLINK_STORE_BACKEND"] = store_backend
        os.environ["ORCHLINK_STORE_PATH"] = store_path
        console.print(f"[Orch] Starting broker: http://{host}:{port}")
        console.print(f"[Orch] Store: {store_backend} ({store_path})")
        uvicorn.run("orchlink.broker.main:app", host=host, port=port, reload=reload)
