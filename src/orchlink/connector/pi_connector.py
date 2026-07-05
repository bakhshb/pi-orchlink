import os
import shutil
import signal
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from orchlink.connector.pi_extension import ensure_orchlink_ui_extension, ensure_pi_extension
from orchlink.project.config import (
    broker_api_key,
    broker_session_grace_seconds,
    broker_session_heartbeat_interval_seconds,
    broker_url,
    project_root,
    role_agent_id,
    skill_path,
)


DEFAULT_PI_SESSION_DIR = ".orch/run/pi-sessions"


class PiConnectorError(RuntimeError):
    """Raised when Orchlink cannot launch the configured Pi command."""


@dataclass
class PiSessionLease:
    """Broker lease and heartbeat for one Pi process."""

    connector: "PiConnector"
    role: str
    pid: int
    lease_id: str = ""
    stop_event: threading.Event | None = None
    heartbeat_thread: threading.Thread | None = None

    def acquire(self) -> "PiSessionLease":
        self.lease_id = self.connector._acquire_session(self.role, self.pid, lease_id=self.lease_id or None)
        self.stop_event = threading.Event()
        self.heartbeat_thread = threading.Thread(
            target=self.connector._heartbeat_loop,
            args=(self.lease_id, self.stop_event),
            daemon=True,
        )
        self.heartbeat_thread.start()
        return self

    def release(self, reason: str) -> None:
        if self.stop_event is not None:
            self.stop_event.set()
        if self.heartbeat_thread is not None:
            self.heartbeat_thread.join(timeout=2)
        if self.lease_id:
            self.connector._release_session(self.lease_id, reason)

    def __enter__(self) -> "PiSessionLease":
        return self.acquire()

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.release(f"{self.role} session exited.")


class PiConnector:
    """Small adapter around local Pi lead and named worker sessions."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

    def pi_command(self) -> str:
        return str((self.config.get("pi") or {}).get("command") or "pi")

    def _is_path_command(self, command: str) -> bool:
        separators = {os.path.sep, os.path.altsep, "/", "\\"}
        return any(separator and separator in command for separator in separators)

    def resolved_pi_command(self) -> str:
        command = self.pi_command()
        if self._is_path_command(command):
            return command
        return shutil.which(command) or command

    def check_available(self) -> bool:
        command = self.pi_command()
        if self._is_path_command(command):
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
        session_dir = (self.config.get("pi") or {}).get("session_dir", DEFAULT_PI_SESSION_DIR)
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

    def _lead_extension_args(self) -> list[str]:
        return [*self._extension_args(), "--extension", str(ensure_orchlink_ui_extension(self.config))]

    def _configured_model(self, role: str) -> str:
        role_key = "work" if role == "work" else "lead"
        role_config = self.config.get(role_key) or {}
        return str(role_config.get("model") or "").strip()

    def _model_thinking_args(self, role: str) -> list[str]:
        role_key = "work" if role == "work" else "lead"
        role_config = self.config.get(role_key) or {}
        args: list[str] = []
        model = self._configured_model(role)
        thinking = str(role_config.get("thinking") or "").strip()
        if model:
            args.extend(["--model", model])
        if thinking:
            args.extend(["--thinking", thinking])
        return args

    def _rpc_extension_discovery_args(self, role: str) -> list[str]:
        """Keep headless workers isolated unless their model comes from Pi extensions.

        Pi custom provider packages, such as ``pi-ollama-cloud``, are loaded by
        extension discovery. Passing ``--no-extensions`` still allows Orchlink's
        explicit bridge extension, but it also hides those package providers from
        Pi's RPC mode. Leave discovery enabled only for known provider-package
        models so ``--model ollama-cloud/...`` resolves the same way it does in
        normal Pi runs and ``pi --list-models``.
        """
        provider = self._configured_model(role).split("/", 1)[0].strip().lower()
        if provider in {"ollama", "ollama-cloud"}:
            return []
        return ["--no-extensions"]

    def _env(self, role: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        role_key = "work" if role == "work" else "lead"
        role_config = self.config.get(role_key) or {}
        command_dir = str(Path(sys.executable).parent)
        path_value = env.get("PATH") or env.get("Path") or ""
        env["PATH"] = command_dir if not path_value else f"{command_dir}{os.pathsep}{path_value}"
        env["Path"] = env["PATH"]
        env.update(
            {
                "ORCHLINK_PI_ROLE": role,
                "ORCHLINK_PROJECT_ID": str(self.config.get("project_id", "default")),
                "ORCHLINK_AGENT_ID": role_agent_id(self.config, role_key),
                "ORCHLINK_WORKER_NAME": str(role_config.get("name") or role_key),
                "ORCHLINK_BROKER_URL": broker_url(self.config),
                "ORCHLINK_API_KEY": broker_api_key(self.config),
                "ORCHLINK_POLL_WAIT_SECONDS": str(role_config.get("poll_wait_seconds", 5)),
            }
        )
        if role_key == "work":
            if role_config.get("model"):
                env["ORCHLINK_WORKER_MODEL"] = str(role_config["model"])
            if role_config.get("thinking"):
                env["ORCHLINK_WORKER_THINKING"] = str(role_config["thinking"])
        if extra:
            env.update(extra)
        return env

    def _broker_headers(self) -> dict[str, str]:
        return {
            "X-API-Key": broker_api_key(self.config),
            "X-Orchlink-Project-ID": str(self.config.get("project_id") or "default"),
        }

    def _post_broker(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        with httpx.Client(base_url=broker_url(self.config), timeout=5) as client:
            response = client.post(path, headers=self._broker_headers(), json=body)
            response.raise_for_status()
            return response.json()

    def _acquire_session(
        self,
        role: str,
        pid: int,
        lease_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        role_key = "work" if role == "work" else "lead"
        role_config = self.config.get(role_key) or {}
        body = dict(metadata or {})
        body.update(
            {
                "project_id": str(self.config.get("project_id") or "default"),
                "agent_id": role_agent_id(self.config, role_key),
                "role": role_key,
                "pid": pid,
                "session_id": str(role_config.get("session_id") or role_key),
                "lease_grace_seconds": broker_session_grace_seconds(self.config),
            }
        )
        if role_key == "work":
            body["worker_name"] = str(role_config.get("name") or role_key)
        if lease_id:
            body["lease_id"] = lease_id
        response = self._post_broker("/v1/sessions/acquire", body)
        return str(response["session"]["lease_id"])

    def acquire_session(
        self,
        role: str,
        pid: int,
        lease_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        return self._acquire_session(role, pid, lease_id=lease_id, metadata=metadata)

    def heartbeat_session(self, lease_id: str, metadata: dict[str, Any] | None = None) -> None:
        body = {"project_id": str(self.config.get("project_id") or "default")}
        if metadata:
            body.update(metadata)
        self._post_broker(f"/v1/sessions/{lease_id}/heartbeat", body)

    def _release_session(self, lease_id: str, reason: str) -> None:
        try:
            self._post_broker(
                f"/v1/sessions/{lease_id}/release",
                {"project_id": str(self.config.get("project_id") or "default"), "reason": reason},
            )
        except Exception:
            return

    def _heartbeat_loop(self, lease_id: str, stop_event: threading.Event) -> None:
        interval = max(1, broker_session_heartbeat_interval_seconds(self.config))
        while not stop_event.wait(interval):
            try:
                self.heartbeat_session(lease_id)
            except Exception:
                continue

    def _run_pi_process(self, role: str, argv: list[str]) -> int:
        if not self.check_available():
            raise PiConnectorError(f"Pi command not found: {self.pi_command()}")
        lease_id = f"lease-{uuid.uuid4()}"
        process = subprocess.Popen(
            argv,
            cwd=self._role_project_dir(role),
            env=self._env(role, {"ORCHLINK_SESSION_LEASE_ID": lease_id}),
        )
        try:
            try:
                lease = PiSessionLease(self, role, process.pid, lease_id=lease_id).acquire()
            except Exception:
                process.terminate()
                raise
            try:
                return process.wait()
            except KeyboardInterrupt:
                try:
                    process.send_signal(signal.SIGINT)
                except ProcessLookupError:
                    pass
                return process.wait()
            finally:
                lease.release(f"{role} session exited.")
        except Exception:
            raise

    def lead_argv(self) -> list[str]:
        pi_config = self.config.get("pi") or {}
        configured_args = pi_config.get("lead_args")
        if configured_args:
            return [
                self.resolved_pi_command(),
                *[str(arg) for arg in configured_args],
                *self._system_prompt_args("lead"),
                *self._lead_extension_args(),
            ]
        return [
            self.resolved_pi_command(),
            *self._session_args("lead"),
            "--name",
            "Orchlink Lead",
            *self._system_prompt_args("lead"),
            *self._lead_extension_args(),
        ]

    def work_rpc_argv(self) -> list[str]:
        return [
            self.resolved_pi_command(),
            "--mode",
            "rpc",
            *self._session_args("work"),
            "--name",
            "Orchlink Worker",
            *self._model_thinking_args("work"),
            "--approve",
            *self._rpc_extension_discovery_args("work"),
            *self._system_prompt_args("work"),
            *self._extension_args(),
        ]

    def work_interactive_argv(self) -> list[str]:
        pi_config = self.config.get("pi") or {}
        configured_args = pi_config.get("work_args")
        if configured_args:
            return [
                self.resolved_pi_command(),
                *[str(arg) for arg in configured_args],
                *self._model_thinking_args("work"),
                *self._system_prompt_args("work"),
                *self._extension_args(),
            ]
        return [
            self.resolved_pi_command(),
            *self._session_args("work"),
            "--name",
            "Orchlink Worker",
            *self._model_thinking_args("work"),
            *self._system_prompt_args("work"),
            *self._extension_args(),
        ]

    def run_lead(self) -> int:
        return self._run_pi_process("lead", self.lead_argv())

    def run_work(self) -> int:
        return self._run_pi_process("work", self.work_interactive_argv())
