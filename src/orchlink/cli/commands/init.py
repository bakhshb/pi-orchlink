"""``orch init`` — bootstrap a project with ``.orch/`` config and lead/work skills."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from rich.console import Console

from orchlink.project.init import init_project


console = Console()


def register_init(app: typer.Typer) -> None:
    """Register the ``init`` command on the given Typer app."""
    @app.command("init", help="Create .orch project config and generated lead/work skills.")
    def init_command(
        project_id: Annotated[
            str | None,
            typer.Option("--project-id", help="Explicit project ID; defaults to current folder name."),
        ] = None,
        force: Annotated[
            bool,
            typer.Option("--force", help="Overwrite config and skills."),
        ] = False,
        refresh_skills: Annotated[
            bool,
            typer.Option(
                "--refresh-skills",
                help="Rewrite lead/work skills without changing project config.",
            ),
        ] = False,
    ) -> None:
        paths = init_project(Path.cwd(), project_id=project_id, force=force, refresh_skills=refresh_skills)
        console.print(f"[Orch] Initialized {paths['orch_dir']}")
        console.print(f"[Orch] Config: {paths['config']}")
        console.print(f"[Orch] Lead skill: {paths['lead_skill']}")
        console.print(f"[Orch] Worker skill: {paths['work_skill']}")
