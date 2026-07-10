from __future__ import annotations

from types import SimpleNamespace

from orchlink.loop.adapters.worktree_evidence import WorktreeEvidenceCollector
from orchlink.loop.domain import Worktree


def completed(stdout="", stderr="", code=0):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, args, cwd, timeout):
        self.calls.append((args, cwd, timeout))
        return self.responses.pop(0)


def test_worktree_evidence_collects_changed_files_and_bounded_diff(tmp_path):
    runner = FakeRunner(
        [
            completed(stdout=" M src/export.py\n?? tests/test_export.py\nR  old.py -> new.py\n"),
            completed(stdout="src/export.py | 2 ++\n"),
            completed(stdout=""),
        ]
    )
    collector = WorktreeEvidenceCollector(runner=runner, timeout_seconds=2, diff_limit=100)

    evidence = collector.collect(Worktree(str(tmp_path)))

    assert evidence.changed_files == ("src/export.py", "tests/test_export.py", "new.py")
    assert evidence.diff_evidence == "src/export.py | 2 ++"
    assert evidence.unavailable_reason is None
    assert runner.calls == [
        (["git", "status", "--porcelain"], tmp_path, 2),
        (["git", "diff", "--stat"], tmp_path, 2),
        (["git", "diff", "--cached", "--stat"], tmp_path, 2),
    ]


def test_worktree_evidence_reports_unavailable_on_safe_failure(tmp_path):
    runner = FakeRunner([completed(stderr="not a git repository", code=128)])
    collector = WorktreeEvidenceCollector(runner=runner)

    evidence = collector.collect(Worktree(str(tmp_path)))

    assert evidence.changed_files is None
    assert evidence.diff_evidence is None
    assert evidence.unavailable_reason == "not a git repository"
