"""``orch update`` — refresh the install from git and reinstall the venv."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Annotated

import typer

from rich.console import Console

from orchlink.cli import main as _cli_main


console = Console()


def _old_alias_paths() -> tuple[Path, ...]:
    return (Path(sys.executable).parent / "orchlink", Path.home() / ".local" / "bin" / "orchlink")


def _remove_old_aliases() -> None:
    for old_alias in _old_alias_paths():
        if old_alias.is_file() or old_alias.is_symlink():
            old_alias.unlink()


def _write_windows_reinstall_helper(root: Path) -> Path:
    log_path = Path(tempfile.gettempdir()) / "orch-update.log"
    script_path = Path(tempfile.gettempdir()) / "orch-update-reinstall.py"
    script = f"""
import subprocess
import sys
import time
from pathlib import Path

python = {str(sys.executable)!r}
root = {str(root)!r}
log_path = {str(log_path)!r}
aliases = {[str(path) for path in _old_alias_paths()]!r}

time.sleep(2)
with open(log_path, "a", encoding="utf-8") as log:
    log.write("\\n[Orch] Running deferred Windows reinstall...\\n")
    result = subprocess.run([python, "-m", "pip", "install", "-e", root], stdout=log, stderr=subprocess.STDOUT)
    if result.returncode == 0:
        for alias in aliases:
            path = Path(alias)
            if path.is_file() or path.is_symlink():
                try:
                    path.unlink()
                except OSError as exc:
                    log.write(f"[Orch] Could not remove old alias {{path}}: {{exc}}\\n")
        log.write("[Orch] Deferred reinstall complete.\\n")
    else:
        log.write(f"[Orch] Deferred reinstall failed with exit code {{result.returncode}}.\\n")
    sys.exit(result.returncode)
"""
    script_path.write_text(textwrap.dedent(script).lstrip(), encoding="utf-8")
    return script_path


def _start_windows_deferred_reinstall(root: Path) -> Path:
    script_path = _write_windows_reinstall_helper(root)
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    subprocess.Popen(  # noqa: S603 - command is the current Python executable plus generated local helper.
        [sys.executable, str(script_path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )
    return Path(tempfile.gettempdir()) / "orch-update.log"


def run_update(ref: str, reinstall_only: bool = False) -> str:
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

    if sys.platform == "win32":
        log_path = _start_windows_deferred_reinstall(root)
        return f"scheduled:{log_path}"

    subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(root)], check=True)
    _remove_old_aliases()
    return "completed"


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
            status = run_update(ref=ref, reinstall_only=reinstall_only)
        except FileNotFoundError as exc:
            console.print(f"[Orch] Missing command: {exc.filename}")
            raise typer.Exit(1) from exc
        except (RuntimeError, subprocess.CalledProcessError) as exc:
            console.print(f"[Orch] Update failed: {exc}")
            raise typer.Exit(1) from exc
        if status.startswith("scheduled:"):
            log_path = status.split(":", 1)[1]
            console.print("[Orch] Reinstall scheduled after this orch process exits.")
            console.print(f"[Orch] Windows keeps the running orch launcher locked; deferred reinstall log: {log_path}")
        else:
            console.print("[Orch] Update complete.")
        console.print("[Orch] In each Orchlink project, refresh .orch files and restart sessions:")
        console.print("[Orch]   orch init --refresh-skills")
        console.print("[Orch]   orch stop")
        console.print("[Orch]   orch lead --new")
        console.print("[Orch]   orch work --new")
