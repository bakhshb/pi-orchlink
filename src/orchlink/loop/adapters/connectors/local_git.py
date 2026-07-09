"""Conservative local git triage connector."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from orchlink.loop.services.triage_service import ItemCandidate, Priority

Runner = Callable[..., Any]


class LocalGitConnector:
    name = "local_git"

    def __init__(self, project_root: str | Path, runner: Runner | None = None) -> None:
        self.project_root = Path(project_root)
        self.runner = runner or subprocess.run

    async def discover(self) -> list[ItemCandidate]:
        try:
            if not self._in_git_repo():
                return []
        except Exception:
            return []

        candidates: list[ItemCandidate] = []
        if self._pytest_cache_has_failures():
            candidates.append(
                ItemCandidate(
                    id="local_git:pytest:lastfailed",
                    source_type="local_git",
                    source_ref="pytest:lastfailed",
                    title="Run the test suite",
                    objective="tests are red; run and repair the failing test suite.",
                    priority=Priority.HIGH,
                )
            )

        try:
            status = self._git(["status", "--porcelain"])
        except Exception:
            status = ""
        if status.strip():
            candidates.append(
                ItemCandidate(
                    id="local_git:dirty_tree",
                    source_type="local_git",
                    source_ref="dirty_tree",
                    title="Working tree is dirty",
                    objective="Stash, commit, or discard the pending changes.",
                    priority=Priority.NORMAL,
                )
            )

        try:
            branch = self._git(["rev-parse", "--abbrev-ref", "HEAD"]).strip()
        except Exception:
            branch = ""
        if branch and branch not in {"main", "master", "HEAD"}:
            candidates.extend(self._recent_commit_candidates(branch))
        return candidates

    def _in_git_repo(self) -> bool:
        return self._git(["rev-parse", "--is-inside-work-tree"]).strip().lower() == "true"

    def _git(self, args: list[str]) -> str:
        result = self.runner(
            ["git", *args],
            cwd=self.project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if getattr(result, "returncode", 0) != 0:
            raise RuntimeError(getattr(result, "stderr", "git failed"))
        return str(getattr(result, "stdout", "") or "")

    def _pytest_cache_has_failures(self) -> bool:
        path = self.project_root / ".pytest_cache" / "v" / "cache" / "lastfailed"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return bool(data)

    def _recent_commit_candidates(self, branch: str) -> list[ItemCandidate]:
        output = ""
        for base in ("main", "master"):
            try:
                output = self._git(["log", "--format=%H%x00%s", f"{base}..HEAD"])
                break
            except Exception:
                continue
        candidates: list[ItemCandidate] = []
        for line in output.splitlines():
            if not line.strip():
                continue
            if "\x00" in line:
                commit_hash, subject = line.split("\x00", 1)
            else:
                commit_hash, subject = line.split(maxsplit=1) if " " in line else (line, line)
            candidates.append(
                ItemCandidate(
                    id=f"local_git:commit:{commit_hash}",
                    source_type="local_git",
                    source_ref=f"commit:{commit_hash}",
                    title=subject.strip(),
                    objective="Review or squash the commit.",
                    priority=Priority.NORMAL,
                )
            )
        return candidates
