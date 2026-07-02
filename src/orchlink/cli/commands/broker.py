"""``orch broker run`` — the local Orchlink broker HTTP server (foreground)."""

from __future__ import annotations

import os
from typing import Annotated

import typer
import uvicorn

from rich.console import Console


console = Console()
broker_app = typer.Typer(help="Run and manage the local Orchlink broker.")


def register_broker(app: typer.Typer) -> None:
    """Mount the broker sub-typer on the given app."""
    app.add_typer(broker_app, name="broker")

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
