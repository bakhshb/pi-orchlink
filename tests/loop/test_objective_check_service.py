from __future__ import annotations

import subprocess

from orchlink.loop.services import ObjectiveCheckService


def write_checks(project_dir, content):
    checks_dir = project_dir / ".orch" / "loop"
    checks_dir.mkdir(parents=True)
    (checks_dir / "checks.yaml").write_text(content, encoding="utf-8")


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, command, cwd, timeout):
        self.calls.append({"command": command, "cwd": cwd, "timeout": timeout})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def completed(command="cmd", code=0, stdout="out", stderr=""):
    return subprocess.CompletedProcess(command, code, stdout=stdout, stderr=stderr)


def service(tmp_path, runner=None):
    return ObjectiveCheckService({"_project_root": str(tmp_path)}, runner=runner)


def test_run_checks_missing_config_fails_closed(tmp_path):
    report = service(tmp_path).run_checks()

    assert report.overall_pass is False
    assert report.any_required_failed is True
    assert report.results[0].status == "error"
    assert report.results[0].required is True
    assert "missing .orch/loop/checks.yaml" in report.results[0].stderr


def test_run_checks_malformed_config_fails_closed(tmp_path):
    write_checks(tmp_path, "not: [valid")

    report = service(tmp_path).run_checks()

    assert report.overall_pass is False
    assert report.any_required_failed is True
    assert report.results[0].status == "error"
    assert "ParserError" in report.results[0].stderr or "ScannerError" in report.results[0].stderr


def test_run_checks_zero_valid_checks_fails_closed(tmp_path):
    write_checks(tmp_path, "checks:\n  - id: missing-command\n")

    report = service(tmp_path).run_checks()

    assert report.overall_pass is False
    assert report.any_required_failed is True
    assert report.results[0].status == "error"
    assert "zero valid checks" in report.results[0].stderr


def test_run_checks_with_passing_check(tmp_path):
    write_checks(tmp_path, "checks:\n  - id: pytest\n    command: pytest\n    required: true\n")
    runner = FakeRunner([completed(code=0, stdout="ok")])

    report = service(tmp_path, runner).run_checks()

    assert report.overall_pass is True
    assert report.any_required_failed is False
    assert report.results[0].id == "pytest"
    assert report.results[0].status == "pass"
    assert report.results[0].exit_code == 0
    assert report.results[0].stdout == "ok"


def test_run_checks_with_failing_required_check(tmp_path):
    write_checks(tmp_path, "checks:\n  - id: pytest\n    command: pytest\n    required: true\n")

    report = service(tmp_path, FakeRunner([completed(code=2, stderr="failed")])).run_checks()

    assert report.results[0].status == "fail"
    assert report.results[0].exit_code == 2
    assert report.overall_pass is False
    assert report.any_required_failed is True


def test_run_checks_with_failing_non_required_check(tmp_path):
    write_checks(tmp_path, "checks:\n  - id: lint\n    command: ruff check\n    required: false\n")

    report = service(tmp_path, FakeRunner([completed(code=1, stderr="lint")])).run_checks()

    assert report.results[0].status == "fail"
    assert report.results[0].required is False
    assert report.overall_pass is True
    assert report.any_required_failed is False


def test_run_checks_with_timeout(tmp_path):
    write_checks(tmp_path, "checks:\n  - id: slow\n    command: sleep 99\n    timeout_seconds: 1\n    required: true\n")
    timeout = subprocess.TimeoutExpired("sleep 99", 1, output="partial", stderr="too slow")

    report = service(tmp_path, FakeRunner([timeout])).run_checks()

    assert report.results[0].status == "timeout"
    assert report.results[0].exit_code == -1
    assert report.results[0].stdout == "partial"
    assert "too slow" in report.results[0].stderr
    assert report.any_required_failed is True


def test_run_checks_with_crashing_command(tmp_path):
    write_checks(tmp_path, "checks:\n  - id: crash\n    command: boom\n    required: true\n")

    report = service(tmp_path, FakeRunner([RuntimeError("broken")])).run_checks()

    assert report.results[0].status == "error"
    assert report.results[0].exit_code == -1
    assert "broken" in report.results[0].stderr
    assert report.any_required_failed is True


def test_run_checks_truncates_stdout_and_stderr(tmp_path):
    write_checks(tmp_path, "checks:\n  - id: noisy\n    command: noisy\n")

    report = service(tmp_path, FakeRunner([completed(stdout="x" * 2100, stderr="y" * 2101)])).run_checks()

    assert report.results[0].stdout == "x" * 2000
    assert report.results[0].stderr == "y" * 2000


def test_run_checks_uses_worktree_as_cwd_when_provided(tmp_path):
    worktree = tmp_path / "wt"
    worktree.mkdir()
    write_checks(tmp_path, "checks:\n  - id: pytest\n    command: pytest\n    timeout_seconds: 12\n")
    runner = FakeRunner([completed(code=0)])

    service(tmp_path, runner).run_checks(worktree)

    assert runner.calls == [{"command": "pytest", "cwd": worktree.resolve(), "timeout": 12}]


def test_run_checks_never_raises_on_failure_modes(tmp_path):
    write_checks(tmp_path, "checks:\n  - id: crash\n    command: boom\n    required: true\n")

    report = service(tmp_path, FakeRunner([ValueError("bad")])).run_checks()

    assert report.results[0].status == "error"
    assert report.overall_pass is False
