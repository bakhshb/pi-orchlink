from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from orchlink.goal.models import AcceptanceCriterion
from orchlink.goal.store import GoalStore
from orchlink.project.config import project_root

if TYPE_CHECKING:
    from orchlink.goal.criteria import GoalCriteriaEngine


CHECK_LINE_RE = re.compile(r"^\s*check\s*:\s*(.+?)\s*$", re.IGNORECASE)
UNSAFE_SHELL_CHARS = set("|&;<>()`$\\\n")

# Trusted interpreters that may be invoked inline (e.g. ``python3 -c "..."``).
# ``run_check`` executes with ``shell=False`` via ``shlex.split`` +
# ``subprocess.run``, so for these interpreters shell metacharacters are
# interpreter syntax inside the quoted argument, never shell separators. The
# goal author is already trusted to write check scripts, so allowing inline
# ``python3 -c`` does not widen the trust boundary. We test the actual
# executable (``args[0]`` after ``shlex.split``) rather than a string prefix so
# leading quotes/whitespace from YAML folding cannot defeat the safe-runner
# path.
SAFE_RUNNER_INTERPRETERS = {"python3", "python"}
CHAIN_TOKEN = "&&"


@dataclass
class CheckResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


def _strip_quotes(value: str) -> str:
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def extract_check_commands(markdown: str) -> list[str]:
    criteria = parse_acceptance_criteria(markdown)
    if criteria:
        return [item.check for item in criteria if item.check]
    commands: list[str] = []
    in_fence = False
    for line in markdown.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = CHECK_LINE_RE.match(line)
        if not match:
            continue
        value = _strip_quotes(match.group(1).strip())
        if value:
            commands.append(value)
    return commands


def parse_acceptance_criteria(markdown: str) -> list[AcceptanceCriterion]:
    data = _load_acceptance_yaml(markdown)
    if not data:
        return []
    raw_items: Any
    if isinstance(data, dict):
        raw_items = data.get("acceptance") or data.get("acceptance_criteria") or data.get("criteria") or data.get("acs")
    else:
        raw_items = data
    if not isinstance(raw_items, list):
        return []
    criteria = []
    for item in raw_items:
        if isinstance(item, dict) and item.get("id"):
            criteria.append(AcceptanceCriterion.from_dict(item))
    return criteria


def _load_acceptance_yaml(markdown: str) -> Any:
    candidates = [markdown]
    fenced = re.findall(r"```(?:yaml|yml)?\s*\n(.*?)\n```", markdown, flags=re.IGNORECASE | re.DOTALL)
    candidates = fenced + candidates
    for candidate in candidates:
        text = _extract_yaml_list(candidate)
        try:
            parsed = yaml.safe_load(text)
        except yaml.YAMLError:
            parsed = None
        if isinstance(parsed, (list, dict)):
            return parsed
    return None


def _extract_yaml_list(text: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("- id:") or stripped in {"acceptance:", "acceptance_criteria:", "criteria:", "acs:"}:
            return "\n".join(lines[index:])
    return text


def run_check(command: str, cwd: Path, timeout_seconds: int = 1800) -> CheckResult:
    try:
        args = shlex.split(command)
    except ValueError as exc:
        return CheckResult(command=command, exit_code=2, stdout="", stderr=str(exc))
    if not args:
        return CheckResult(command=command, exit_code=2, stdout="", stderr="Empty check command.")
    if CHAIN_TOKEN in args:
        return _run_chained_check(command, args, cwd=cwd, timeout_seconds=timeout_seconds)
    # Safe runner: trusted interpreters execute via shell=False, so shell
    # metacharacters inside their arguments are interpreter syntax, not shell
    # separators. Check the actual executable so YAML quoting/whitespace cannot
    # defeat this path.
    if args[0] in SAFE_RUNNER_INTERPRETERS:
        process = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False, timeout=timeout_seconds)
        return CheckResult(command=command, exit_code=process.returncode, stdout=process.stdout, stderr=process.stderr)
    if any(char in command for char in UNSAFE_SHELL_CHARS):
        return CheckResult(
            command=command,
            exit_code=2,
            stdout="",
            stderr="Check command contains shell metacharacters; refusing to run without an explicit safe runner.",
        )
    process = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False, timeout=timeout_seconds)
    return CheckResult(command=command, exit_code=process.returncode, stdout=process.stdout, stderr=process.stderr)


def _run_chained_check(command: str, args: list[str], cwd: Path, timeout_seconds: int) -> CheckResult:
    segments: list[list[str]] = [[]]
    for arg in args:
        if arg == CHAIN_TOKEN:
            if not segments[-1]:
                return CheckResult(command=command, exit_code=2, stdout="", stderr="Empty command before &&.")
            segments.append([])
            continue
        segments[-1].append(arg)
    if not segments[-1]:
        return CheckResult(command=command, exit_code=2, stdout="", stderr="Empty command after &&.")

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    for segment in segments:
        segment_command = shlex.join(segment)
        result = run_check(segment_command, cwd=cwd, timeout_seconds=timeout_seconds)
        stdout_parts.append(result.stdout)
        stderr_parts.append(result.stderr)
        if not result.passed:
            return CheckResult(command=command, exit_code=result.exit_code, stdout="".join(stdout_parts), stderr="".join(stderr_parts))
    return CheckResult(command=command, exit_code=0, stdout="".join(stdout_parts), stderr="".join(stderr_parts))


def run_objective_checks(
    store: GoalStore,
    config: dict[str, Any],
    goal_id: str,
    *,
    criteria_engine: "GoalCriteriaEngine",
    timeout_seconds: int = 1800,
) -> list[CheckResult]:
    """Run the objective checks for the next core criterion (or fall back to free-form commands)."""
    goal_dir = store.goal_dir(goal_id)
    acceptance = (goal_dir / "acceptance.md").read_text(encoding="utf-8")
    criteria = parse_acceptance_criteria(acceptance)
    selected = criteria_engine.selected(goal_id)
    if criteria:
        runnable = [selected] if selected and selected.check and criteria_engine.dependencies_satisfied(goal_id, selected) else []
        commands = [item.check for item in runnable if item.check]
    else:
        commands = extract_check_commands(acceptance)
    results: list[CheckResult] = []
    for command in commands:
        result = run_check(command, cwd=project_root(config), timeout_seconds=timeout_seconds)
        results.append(result)
        criterion_id = selected.id if selected else GoalCriteriaEngine.criterion_for_check(criteria, command)
        if criterion_id:
            store.set_ac_status(goal_id, criterion_id, "verified" if result.passed else "failed")
        store.record_evidence(
            goal_id,
            {
                "type": "check",
                "criterion_id": criterion_id,
                "command": result.command,
                "exit_code": result.exit_code,
                "passed": result.passed,
                "stdout": result.stdout[-4000:],
                "stderr": result.stderr[-4000:],
            },
        )
    return results
