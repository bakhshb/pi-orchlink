"""Bounded Git evidence collection for loop verifier prompts."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Protocol

from orchlink.loop.domain.worktree import Worktree
from orchlink.loop.ports import WorktreeEvidence, WorktreeEvidencePort

DEFAULT_TIMEOUT_SECONDS = 3
DEFAULT_DIFF_LIMIT = 4000


class SubprocessResult(Protocol):
    returncode: int
    stdout: str | bytes | None
    stderr: str | bytes | None


Runner = Callable[[list[str], Path, int], SubprocessResult]


class WorktreeEvidenceCollector(WorktreeEvidencePort):
    def __init__(
        self,
        *,
        runner: Runner | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        diff_limit: int = DEFAULT_DIFF_LIMIT,
    ) -> None:
        self.runner = runner or _run_git
        self.timeout_seconds = timeout_seconds
        self.diff_limit = diff_limit

    def collect(self, worktree: Worktree | None) -> WorktreeEvidence:
        if worktree is None:
            return WorktreeEvidence(unavailable_reason="no worktree provided")
        cwd = Path(worktree.path).expanduser()
        if not cwd.is_dir():
            return WorktreeEvidence(unavailable_reason=f"worktree path is not a directory: {cwd}")
        try:
            status = self.runner(["git", "status", "--porcelain"], cwd, self.timeout_seconds)
            if int(status.returncode) != 0:
                return WorktreeEvidence(unavailable_reason=_error_text(status) or "git status failed")
            changed_files = _parse_status_files(_to_text(status.stdout))
            diff_parts = []
            for args in (["git", "diff", "--stat"], ["git", "diff", "--cached", "--stat"]):
                result = self.runner(args, cwd, self.timeout_seconds)
                if int(result.returncode) != 0:
                    return WorktreeEvidence(changed_files=changed_files, unavailable_reason=_error_text(result) or "git diff failed")
                text = _to_text(result.stdout).strip()
                if text:
                    diff_parts.append(text)
            diff = "\n".join(diff_parts).strip() or "none (no tracked diff available)"
            return WorktreeEvidence(changed_files=changed_files, diff_evidence=_truncate(diff, self.diff_limit))
        except subprocess.TimeoutExpired:
            return WorktreeEvidence(unavailable_reason="git evidence collection timed out")
        except Exception as exc:
            return WorktreeEvidence(unavailable_reason=f"git evidence collection failed: {exc}")


def _run_git(args: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603 - bounded git adapter.


def _parse_status_files(text: str) -> tuple[str, ...]:
    files: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            files.append(path)
    return tuple(files)


def _error_text(result: SubprocessResult) -> str:
    return (_to_text(result.stderr) or _to_text(result.stdout)).strip()


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...<truncated>"


__all__ = ["WorktreeEvidenceCollector"]
