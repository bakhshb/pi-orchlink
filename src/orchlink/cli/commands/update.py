"""``orch update`` — refresh the install from git and reinstall the venv."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from rich.console import Console

from orchlink.cli import main as _cli_main


console = Console()


def run_update(ref: str, reinstall_only: bool = False) -> None:
    root = _cli_main.PROJECT_ROOT
    if not (root / ".git").is_dir():
        raise RuntimeError("This Orchlink install is not a git checkout. Re-run the install script to update.")

    if not reinstall_only:
        subprocess.run(["git", "-C", str(root), "fetch", "--tags", "--prune", "origin"], check=True)
        subprocess.run(["git", "-C", str(root), "checkout", ref], check=True)
        remote_branch = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", f"origin/{ref}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if remote_branch.returncode == 0:
            subprocess.run(["git", "-C", str(root), "pull", "--ff-only", "origin", ref], check=True)

    subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(root)], check=True)
    for old_alias in (Path(sys.executable).parent / "orchlink", Path.home() / ".local" / "bin" / "orchlink"):
        if old_alias.is_file() or old_alias.is_symlink():
            old_alias.unlink()


def register_update(app: typer.Typer) -> None:
    """Register the ``update`` command on the given Typer app."""

    @app.command(help="Update this Orchlink install from git and reinstall the package.")
    def update(
        ref: Annotated[
            str,
            typer.Option("--ref", help="Git branch, tag, or commit to update to."),
        ] = "main",
        reinstall_only: Annotated[
            bool,
            typer.Option(
                "--reinstall-only",
                help="Only reinstall the current checkout into the venv.",
            ),
        ] = False,
    ) -> None:
        console.print(f"[Orch] Updating Orchlink in {_cli_main.PROJECT_ROOT}")
        try:
            run_update(ref=ref, reinstall_only=reinstall_only)
        except FileNotFoundError as exc:
            console.print(f"[Orch] Missing command: {exc.filename}")
            raise typer.Exit(1) from exc
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            console.print(f"[Orch] Update failed: {exc}")
            raise typer.Exit(1) from exc
        console.print("[Orch] Update complete.")
        console.print("[Orch] In each Orchlink project, refresh .orch files and restart sessions:")
        console.print("[Orch]   orch init --refresh-skills")
        console.print("[Orch]   orch stop")
        console.print("[Orch]   orch lead --new")
        console.print("[Orch]   orch work --new")
