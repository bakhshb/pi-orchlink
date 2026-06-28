"""Tests for goal check execution (``orchlink.goal.checks.run_check``).

Locks the goal-check execution boundary: inline ``python3 -c`` checks (used by
the G005 acceptance criteria) must execute under ``shell=False`` even though
they contain ``;``/``()`` as Python syntax. Explicit ``&&`` chains are run as
sequential commands without a shell so documentation grep checks can run without
opening broader shell evaluation.
"""

from __future__ import annotations

from pathlib import Path

from orchlink.goal.checks import run_check


def test_run_check_executes_python3_c_inline_script_with_metacharacters(tmp_path: Path):
    command = 'python3 -c "import sys; sys.exit(0)"'
    result = run_check(command, cwd=tmp_path)
    assert "shell metacharacters" not in (result.stderr or "")
    assert result.exit_code == 0


def test_run_check_python3_c_passes_through_nonzero_exit(tmp_path: Path):
    command = 'python3 -c "import sys; sys.exit(3)"'
    result = run_check(command, cwd=tmp_path)
    assert result.exit_code == 3


def test_run_check_python3_c_runs_real_pytest_style_check(tmp_path: Path):
    # Mirrors the shape of the G005 AC checks (python3 -c invoking pytest).
    script = tmp_path / "test_ok.py"
    script.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    command = f"python3 -c \"import pytest, sys; sys.exit(pytest.main(['{script}', '-q']))\""
    result = run_check(command, cwd=tmp_path)
    assert result.exit_code == 0
    assert "1 passed" in (result.stdout or "")


def test_run_check_runs_explicit_and_chain_without_shell(tmp_path: Path):
    readme = tmp_path / "README.md"
    readme.write_text("foo\nbar\n", encoding="utf-8")
    command = "grep -q foo README.md && grep -q bar README.md"
    result = run_check(command, cwd=tmp_path)
    assert result.exit_code == 0


def test_run_check_and_chain_short_circuits_on_failure(tmp_path: Path):
    command = "python3 -c 'import sys; sys.exit(7)' && python3 -c 'raise SystemExit(0)'"
    result = run_check(command, cwd=tmp_path)
    assert result.exit_code == 7


def test_run_check_still_refuses_other_shell_metacharacters(tmp_path: Path):
    result = run_check("printf foo | grep foo", cwd=tmp_path)
    assert result.exit_code == 2
    assert "shell metacharacters" in (result.stderr or "")


def test_run_check_refuses_unbalanced_quotes(tmp_path: Path):
    result = run_check('python3 -c "unbalanced', cwd=tmp_path)
    assert result.exit_code == 2
    assert "shell metacharacters" not in (result.stderr or "")