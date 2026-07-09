"""Objective check runner for loop verification."""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

import yaml

from orchlink.project.config import project_root

DEFAULT_TIMEOUT_SECONDS = 300
OUTPUT_LIMIT = 2000


class SubprocessResult(Protocol):
    returncode: int
    stdout: str | bytes | None
    stderr: str | bytes | None


Runner = Callable[[str, Path, int], SubprocessResult]


@dataclass(frozen=True, slots=True)
class CheckDefinition:
    id: str
    command: str
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    required: bool = False


@dataclass(frozen=True, slots=True)
class CheckResult:
    id: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    status: str
    required: bool

    @property
    def passed(self) -> bool:
        return self.status == "pass"

    @property
    def failed_required(self) -> bool:
        return self.required and self.status in {"fail", "timeout", "error"}


@dataclass(frozen=True, slots=True)
class CheckReport:
    results: tuple[CheckResult, ...]
    overall_pass: bool
    any_required_failed: bool

    @classmethod
    def from_results(cls, results: list[CheckResult] | tuple[CheckResult, ...]) -> "CheckReport":
        normalized = tuple(results)
        any_required_failed = any(result.failed_required for result in normalized)
        return cls(
            results=normalized,
            overall_pass=not any_required_failed,
            any_required_failed=any_required_failed,
        )

    @property
    def passed(self) -> bool:
        return self.overall_pass

    @property
    def failed_required(self) -> tuple[CheckResult, ...]:
        return tuple(result for result in self.results if result.failed_required)

    def prompt_section(self) -> str:
        lines = ["Objective checks:", f"OVERALL: {'pass' if self.overall_pass else 'fail'}"]
        for result in self.results:
            required = "required" if result.required else "optional"
            lines.append(f"- {result.id} ({required}): {result.status} exit={result.exit_code}")
            if result.stdout:
                lines.append(f"  stdout: {result.stdout}")
            if result.stderr:
                lines.append(f"  stderr: {result.stderr}")
        return "\n".join(lines)


class ObjectiveCheckService:
    def __init__(self, config: dict[str, Any] | None = None, runner: Runner | None = None) -> None:
        self.config = dict(config or {})
        self.project_dir = project_root(self.config)
        self.runner = runner or _run_subprocess

    def run_checks(self, worktree: Path | None = None) -> CheckReport:
        try:
            definitions = self._load_definitions()
            cwd = worktree.resolve() if worktree is not None else self.project_dir
            results = [self._run_one(definition, cwd) for definition in definitions]
            return CheckReport.from_results(results)
        except Exception as exc:
            return CheckReport.from_results(
                [
                    CheckResult(
                        id="objective_checks",
                        exit_code=-1,
                        stdout="",
                        stderr=_truncate(f"{type(exc).__name__}: {exc}"),
                        duration_seconds=0.0,
                        status="error",
                        required=True,
                    )
                ]
            )

    def _load_definitions(self) -> list[CheckDefinition]:
        path = self.project_dir / ".orch" / "loop" / "checks.yaml"
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError:
            return []
        checks = raw.get("checks") if isinstance(raw, dict) else None
        if not isinstance(checks, list):
            return []
        definitions: list[CheckDefinition] = []
        for index, check in enumerate(checks):
            if not isinstance(check, dict):
                continue
            command = str(check.get("command") or "").strip()
            check_id = str(check.get("id") or f"check-{index + 1}").strip()
            if not command or not check_id:
                continue
            definitions.append(
                CheckDefinition(
                    id=check_id,
                    command=command,
                    timeout_seconds=int(check.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS),
                    required=bool(check.get("required", False)),
                )
            )
        return definitions

    def _run_one(self, definition: CheckDefinition, cwd: Path) -> CheckResult:
        started = time.monotonic()
        try:
            completed = self.runner(definition.command, cwd, definition.timeout_seconds)
            exit_code = int(completed.returncode)
            status = "pass" if exit_code == 0 else "fail"
            return CheckResult(
                id=definition.id,
                exit_code=exit_code,
                stdout=_truncate(_to_text(completed.stdout)),
                stderr=_truncate(_to_text(completed.stderr)),
                duration_seconds=time.monotonic() - started,
                status=status,
                required=definition.required,
            )
        except subprocess.TimeoutExpired as exc:
            return CheckResult(
                id=definition.id,
                exit_code=-1,
                stdout=_truncate(_to_text(exc.stdout)),
                stderr=_truncate(_to_text(exc.stderr)),
                duration_seconds=time.monotonic() - started,
                status="timeout",
                required=definition.required,
            )
        except Exception as exc:
            return CheckResult(
                id=definition.id,
                exit_code=-1,
                stdout="",
                stderr=_truncate(str(exc)),
                duration_seconds=time.monotonic() - started,
                status="error",
                required=definition.required,
            )


def _run_subprocess(command: str, cwd: Path, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _to_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _truncate(value: str) -> str:
    return value[:OUTPUT_LIMIT]


__all__ = ["CheckDefinition", "CheckReport", "CheckResult", "ObjectiveCheckService"]
