from __future__ import annotations

import asyncio
from types import SimpleNamespace

from orchlink.loop.adapters.connectors import GitHubConnector, LinearConnector, LocalGitConnector


def result(stdout="", returncode=0, stderr=""):
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


def test_local_git_connector_non_git_path_returns_empty(tmp_path):
    assert asyncio.run(LocalGitConnector(tmp_path).discover()) == []


def test_local_git_connector_clean_status_returns_empty(tmp_path):
    def runner(command, **kwargs):
        if command[1:] == ["rev-parse", "--is-inside-work-tree"]:
            return result("true\n")
        if command[1:] == ["status", "--porcelain"]:
            return result("")
        if command[1:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return result("main\n")
        raise AssertionError(command)

    assert asyncio.run(LocalGitConnector(tmp_path, runner=runner).discover()) == []


def test_local_git_connector_dirty_tree_candidate(tmp_path):
    def runner(command, **kwargs):
        if command[1:] == ["rev-parse", "--is-inside-work-tree"]:
            return result("true\n")
        if command[1:] == ["status", "--porcelain"]:
            return result(" M file.py\n")
        if command[1:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return result("main\n")
        raise AssertionError(command)

    candidates = asyncio.run(LocalGitConnector(tmp_path, runner=runner).discover())

    assert len(candidates) == 1
    assert candidates[0].title == "Working tree is dirty"
    assert candidates[0].objective == "Stash, commit, or discard the pending changes."
    assert candidates[0].source_type == "local_git"


def test_local_git_connector_recent_commits(tmp_path):
    def runner(command, **kwargs):
        if command[1:] == ["rev-parse", "--is-inside-work-tree"]:
            return result("true\n")
        if command[1:] == ["status", "--porcelain"]:
            return result("")
        if command[1:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return result("feature\n")
        if command[1:] == ["log", "--format=%H%x00%s", "main..HEAD"]:
            return result("abc123\x00Add thing\ndef456\x00Fix thing\n")
        raise AssertionError(command)

    candidates = asyncio.run(LocalGitConnector(tmp_path, runner=runner).discover())

    assert [candidate.title for candidate in candidates] == ["Add thing", "Fix thing"]
    assert {candidate.objective for candidate in candidates} == {"Review or squash the commit."}
    assert [candidate.source_ref for candidate in candidates] == ["commit:abc123", "commit:def456"]


def test_local_git_connector_isolates_signal_failures(tmp_path):
    def runner(command, **kwargs):
        if command[1:] == ["rev-parse", "--is-inside-work-tree"]:
            return result("true\n")
        if command[1:] == ["status", "--porcelain"]:
            return result(" M file.py\n")
        if command[1:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            raise RuntimeError("branch failed")
        raise AssertionError(command)

    candidates = asyncio.run(LocalGitConnector(tmp_path, runner=runner).discover())

    assert [candidate.title for candidate in candidates] == ["Working tree is dirty"]


def test_local_git_connector_runner_failure_returns_empty(tmp_path):
    def runner(command, **kwargs):
        raise RuntimeError("git missing")

    assert asyncio.run(LocalGitConnector(tmp_path, runner=runner).discover()) == []


def test_github_and_linear_shells_return_empty_without_config():
    assert asyncio.run(GitHubConnector({}).discover()) == []
    assert asyncio.run(LinearConnector({}).discover()) == []
