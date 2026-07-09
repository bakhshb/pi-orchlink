from __future__ import annotations

from pathlib import Path

import pytest

from orchlink.loop.adapters import WorktreeCreateError, WorktreeService
from orchlink.loop.domain import Worktree


class Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeGitRunner:
    def __init__(self, results=None):
        self.results = list(results or [Result()])
        self.calls = []

    def __call__(self, args, cwd):
        self.calls.append((list(args), cwd))
        return self.results.pop(0) if self.results else Result()


def test_create_runs_git_worktree_add_with_branch_and_default_path(tmp_path):
    runner = FakeGitRunner()
    service = WorktreeService(tmp_path, runner=runner)

    worktree = service.create("maker-1", base_ref="main")

    expected_path = tmp_path.parent / f"{tmp_path.name}-maker-1"
    assert runner.calls == [
        (["git", "worktree", "add", "--track", "-b", "loop/maker-1", str(expected_path), "main"], tmp_path.resolve())
    ]
    assert worktree == Worktree(path=str(expected_path), branch="loop/maker-1", base_ref="main")


def test_create_fails_if_path_exists(tmp_path):
    existing = tmp_path / "existing"
    existing.mkdir()
    runner = FakeGitRunner()

    with pytest.raises(WorktreeCreateError, match="already exists"):
        WorktreeService(tmp_path, runner=runner).create("maker-1", path=existing)

    assert runner.calls == []


def test_create_fails_if_git_returns_nonzero(tmp_path):
    runner = FakeGitRunner([Result(returncode=1, stderr="branch exists")])

    with pytest.raises(WorktreeCreateError, match="branch exists"):
        WorktreeService(tmp_path, runner=runner).create("maker-1")


def test_create_with_explicit_path_uses_that_path(tmp_path):
    explicit = tmp_path / "../explicit-wt"
    runner = FakeGitRunner()

    worktree = WorktreeService(tmp_path, runner=runner).create("maker-1", base_ref="develop", path=explicit)

    assert runner.calls[0][0] == [
        "git",
        "worktree",
        "add",
        "--track",
        "-b",
        "loop/maker-1",
        str(explicit.expanduser().resolve()),
        "develop",
    ]
    assert worktree.path == str(explicit.expanduser().resolve())
    assert worktree.base_ref == "develop"


def test_create_default_path_uses_project_parent_project_name_and_worker(tmp_path):
    runner = FakeGitRunner()

    worktree = WorktreeService(tmp_path, runner=runner).create("review")

    assert Path(worktree.path) == tmp_path.parent / f"{tmp_path.name}-review"


def test_remove_runs_worktree_remove_and_does_not_raise_on_failure(tmp_path):
    runner = FakeGitRunner([Result(returncode=1, stderr="busy"), Result(returncode=1, stderr="branch missing")])
    service = WorktreeService(tmp_path, runner=runner)

    service.remove(Worktree(path=str(tmp_path / "wt"), branch="loop/maker-1", base_ref="main"))

    assert runner.calls[0] == (["git", "worktree", "remove", str(tmp_path / "wt")], tmp_path.resolve())


def test_remove_also_attempts_branch_deletion(tmp_path):
    runner = FakeGitRunner([Result(), Result()])

    WorktreeService(tmp_path, runner=runner).remove(Worktree(path=str(tmp_path / "wt"), branch="loop/maker-1"))

    assert runner.calls[1] == (["git", "branch", "-D", "loop/maker-1"], tmp_path.resolve())
