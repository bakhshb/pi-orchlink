"""Git worktree adapter for loop worker scopes."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Callable, Protocol

from orchlink.loop.domain.worktree import Worktree

log = logging.getLogger(__name__)


class SubprocessResult(Protocol):
    returncode: int
    stdout: str | bytes | None
    stderr: str | bytes | None


Runner = Callable[[list[str], Path], SubprocessResult]


class WorktreeCreateError(RuntimeError):
    """Raised when a git worktree cannot be created."""


class WorktreeService:
    def __init__(self, project_root: Path, runner: Runner | None = None) -> None:
        self.project_root = project_root.resolve()
        self.runner = runner or _run_git

    def create(self, name: str, base_ref: str = "main", path: Path | None = None) -> Worktree:
        resolved_path = (path.expanduser().resolve() if path is not None else (self.project_root.parent / f"{self.project_root.name}-{name}").resolve())
        branch = f"loop/{name}"
        if resolved_path.exists():
            raise WorktreeCreateError(f"worktree path already exists: {resolved_path}")
        args = ["git", "worktree", "add", "--track", "-b", branch, str(resolved_path), base_ref]
        result = self.runner(args, self.project_root)
        if int(result.returncode) != 0:
            detail = _to_text(result.stderr).strip() or _to_text(result.stdout).strip() or f"git exited {result.returncode}"
            raise WorktreeCreateError(detail)
        return Worktree(path=str(resolved_path), branch=branch, base_ref=base_ref)

    def remove(self, worktree: Worktree) -> None:
        path = Path(worktree.path).expanduser()
        try:
            remove_result = self.runner(["git", "worktree", "remove", str(path)], self.project_root)
            if int(remove_result.returncode) != 0:
                log.warning("git worktree remove failed for %s: %s", path, _to_text(remove_result.stderr).strip())
        except Exception as exc:
            log.warning("git worktree remove failed for %s: %s", path, exc)
        if worktree.branch:
            try:
                branch_result = self.runner(["git", "branch", "-D", worktree.branch], self.project_root)
                if int(branch_result.returncode) != 0:
                    log.warning("git branch delete failed for %s: %s", worktree.branch, _to_text(branch_result.stderr).strip())
            except Exception as exc:
                log.warning("git branch delete failed for %s: %s", worktree.branch, exc)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)  # noqa: S603 - git adapter executes fixed argv.


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


__all__ = ["WorktreeCreateError", "WorktreeService"]
