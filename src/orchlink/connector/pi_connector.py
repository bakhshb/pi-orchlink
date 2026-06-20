import asyncio
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchlink.bridge.agent_runner import run_command
from orchlink.connector.pi_extension import ensure_pi_extension
from orchlink.project.config import broker_api_key, broker_url, project_root, role_agent_id, skill_path


class PiConnectorError(RuntimeError):
    """Raised when Orchlink cannot launch or use the configured Pi command."""


@dataclass(frozen=True)
class PiRunResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class PiConnector:
    """Small adapter around the local Pi CLI.

    The current Pi CLI supports named sessions and non-interactive prompt execution.
    Orchlink uses that for the worker listener: each task is sent to the configured
    worker session with `pi --print --session-id <id> <prompt>` and the captured
    answer is returned to the broker.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def pi_command(self) -> str:
        return str((self.config.get("pi") or {}).get("command") or "pi")

    def check_available(self) -> bool:
        command = self.pi_command()
        if os.path.sep in command:
            return Path(command).exists()
        return shutil.which(command) is not None

    def _role_project_dir(self, role: str) -> Path:
        role_config = self.config.get(role) or {}
        configured = Path(str(role_config.get("project_dir") or "."))
        if configured.is_absolute():
            return configured
        return project_root(self.config) / configured

    def _session_args(self, role: str) -> list[str]:
        role_config = self.config.get(role) or {}
        session_id = str(role_config.get("session_id") or role)
        args = ["--session-id", session_id]
        session_dir = (self.config.get("pi") or {}).get("session_dir")
        if session_dir:
            session_path = Path(str(session_dir))
            if not session_path.is_absolute():
                session_path = project_root(self.config) / session_path
            session_path.mkdir(parents=True, exist_ok=True)
            args.extend(["--session-dir", str(session_path)])
        return args

    def _system_prompt_args(self, role: str) -> list[str]:
        path = skill_path(self.config, role)
        if path.is_file():
            return ["--append-system-prompt", str(path)]
        return []

    def _extension_args(self) -> list[str]:
        return ["--extension", str(ensure_pi_extension(self.config))]

    def _env(self, role: str) -> dict[str, str]:
        env = os.environ.copy()
        role_key = "work" if role == "work" else "lead"
        role_config = self.config.get(role_key) or {}
        env.update(
            {
                "ORCHLINK_PI_ROLE": role,
                "ORCHLINK_PROJECT_ID": str(self.config.get("project_id", "default")),
                "ORCHLINK_AGENT_ID": role_agent_id(self.config, role_key),
                "ORCHLINK_BROKER_URL": broker_url(self.config),
                "ORCHLINK_API_KEY": broker_api_key(self.config),
                "ORCHLINK_POLL_WAIT_SECONDS": str(role_config.get("poll_wait_seconds", 5)),
            }
        )
        return env

    def lead_argv(self) -> list[str]:
        pi_config = self.config.get("pi") or {}
        configured_args = pi_config.get("lead_args")
        if configured_args:
            return [
                self.pi_command(),
                *[str(arg) for arg in configured_args],
                *self._system_prompt_args("lead"),
                *self._extension_args(),
            ]
        return [
            self.pi_command(),
            *self._session_args("lead"),
            "--name",
            "Orchlink Lead",
            *self._system_prompt_args("lead"),
            *self._extension_args(),
        ]

    def work_interactive_argv(self) -> list[str]:
        pi_config = self.config.get("pi") or {}
        configured_args = pi_config.get("work_args")
        if configured_args:
            return [
                self.pi_command(),
                *[str(arg) for arg in configured_args],
                *self._system_prompt_args("work"),
                *self._extension_args(),
            ]
        return [
            self.pi_command(),
            *self._session_args("work"),
            "--name",
            "Orchlink Worker",
            *self._system_prompt_args("work"),
            *self._extension_args(),
        ]

    def worker_argv(self, prompt: str) -> list[str]:
        pi_config = self.config.get("pi") or {}
        configured_args = pi_config.get("work_prompt_args")
        if configured_args:
            return [self.pi_command(), *[str(arg) for arg in configured_args], prompt]
        return [
            self.pi_command(),
            "--print",
            *self._session_args("work"),
            *self._system_prompt_args("work"),
            prompt,
        ]

    def run_lead(self) -> int:
        if not self.check_available():
            raise PiConnectorError(f"Pi command not found: {self.pi_command()}")
        return subprocess.call(self.lead_argv(), cwd=self._role_project_dir("lead"), env=self._env("lead"))

    def run_work(self) -> int:
        if not self.check_available():
            raise PiConnectorError(f"Pi command not found: {self.pi_command()}")
        return subprocess.call(self.work_interactive_argv(), cwd=self._role_project_dir("work"), env=self._env("work"))

    async def run_worker_prompt(self, prompt: str, timeout_seconds: int) -> PiRunResult:
        legacy_command = (self.config.get("work") or {}).get("command") or self.config.get("command")
        if legacy_command:
            result = await run_command(dict(legacy_command), prompt, timeout_seconds)
            return PiRunResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.exit_code,
                timed_out=result.timed_out,
            )

        if not self.check_available():
            return PiRunResult(
                stdout="",
                stderr=(
                    f"Pi command not found: {self.pi_command()}. "
                    "Install Pi or set pi.command in .orch/project.yaml."
                ),
                exit_code=None,
                timed_out=False,
            )

        process = await asyncio.create_subprocess_exec(
            *self.worker_argv(prompt),
            cwd=self._role_project_dir("work"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
            return PiRunResult(
                stdout=stdout_bytes.decode(errors="replace"),
                stderr=stderr_bytes.decode(errors="replace"),
                exit_code=None,
                timed_out=True,
            )

        return PiRunResult(
            stdout=stdout_bytes.decode(errors="replace"),
            stderr=stderr_bytes.decode(errors="replace"),
            exit_code=process.returncode,
            timed_out=False,
        )
