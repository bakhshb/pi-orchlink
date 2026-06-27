from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from orchlink.goal.models import AcceptanceCriterion


CHECK_LINE_RE = re.compile(r"^\s*check\s*:\s*(.+?)\s*$", re.IGNORECASE)
UNSAFE_SHELL_CHARS = set("|&;<>()`$\\\n")


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
    if any(char in command for char in UNSAFE_SHELL_CHARS):
        return CheckResult(
            command=command,
            exit_code=2,
            stdout="",
            stderr="Check command contains shell metacharacters; refusing to run without an explicit safe runner.",
        )
    try:
        args = shlex.split(command)
    except ValueError as exc:
        return CheckResult(command=command, exit_code=2, stdout="", stderr=str(exc))
    if not args:
        return CheckResult(command=command, exit_code=2, stdout="", stderr="Empty check command.")
    process = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False, timeout=timeout_seconds)
    return CheckResult(command=command, exit_code=process.returncode, stdout=process.stdout, stderr=process.stderr)
