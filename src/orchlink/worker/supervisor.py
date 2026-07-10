"""Headless Orchlink worker supervisor.

This module owns the lifecycle of one background worker subprocess launched
in Pi's ``--mode rpc`` mode. The supervisor:

* negotiates a broker session lease through :class:`LeaseGateway`;
* writes supervisor/child status JSON through :class:`StatusWriter`;
* launches and supervises the Pi child through :class:`ProcessController`;
* terminates the process tree through :class:`ProcessTerminator`.

The supervisor is intentionally split into narrow, substitutable boundaries
so that tests can fake any of them in isolation. It uses only public
:class:`PiConnector` operations and never reaches into private connector
state. Broker version and capability metadata live in
:mod:`orchlink.core.broker_metadata`; this module does not import from
:mod:`orchlink.broker.main`.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol

import httpx

from orchlink.connector.pi_connector import PiConnector
from orchlink.project.config import (
    broker_session_heartbeat_interval_seconds,
    load_project_config,
    normalize_worker_name,
    project_root,
    role_agent_id,
    run_dir,
    with_worker_name,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _saved_worker_session_id(config: Mapping[str, Any], worker_name: str) -> str:
    if worker_name == "work":
        return str((config.get("work") or {}).get("session_id") or "work")
    path = run_dir(config) / "workers" / worker_name / "state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return worker_name
    return str(state.get("session_id") or worker_name)


def _is_lost_session_error(exc: Exception) -> bool:
    if not isinstance(exc, httpx.HTTPStatusError):
        return False
    return exc.response.status_code in {404, 409}


def _unlink_pid_file_if_self(path: Path, pid: int) -> None:
    try:
        if path.read_text(encoding="utf-8").strip() == str(pid):
            path.unlink(missing_ok=True)
    except OSError:
        return


def _freeze_config_value(value: Any) -> Any:
    """Recursively freeze JSON-shaped project configuration values."""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_config_value(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_config_value(item) for item in value)
    return value


# ---------------------------------------------------------------------------
# Immutable launch specification and runtime paths
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerLaunchSpec:
    """Immutable inputs describing how to launch a headless worker.

    Frozen so the supervisor and its boundaries can rely on the values being
    stable for the duration of a single run.
    """

    project_root: Path
    config: Mapping[str, Any]
    worker_name: str
    role: str
    session_id: str
    oneshot: bool
    project_dir: Path


@dataclass(frozen=True)
class WorkerRuntimePaths:
    """Filesystem locations the supervisor owns during a worker run."""

    run_dir: Path
    worker_dir: Path
    pid_path: Path
    child_pid_path: Path
    status_path: Path


def build_launch_spec(
    project_root_path: Path,
    worker_name: str = "work",
    model: str | None = None,
    thinking: str | None = None,
    oneshot: bool = False,
    project_dir: Path | None = None,
) -> WorkerLaunchSpec:
    """Resolve the launch inputs into a :class:`WorkerLaunchSpec`.

    Performs all project-config edits needed to materialize model/thinking and
    the optional override project directory into the configuration dictionary.
    """
    worker_name = normalize_worker_name(worker_name)
    config = load_project_config(project_root_path)
    session_id = _saved_worker_session_id(config, worker_name)
    config = with_worker_name(config, worker_name, session_id=session_id)
    if model or thinking:
        updated = dict(config)
        work_config = dict(config.get("work") or {})
        if model:
            work_config["model"] = model
        if thinking:
            work_config["thinking"] = thinking
        updated["work"] = work_config
        config = updated
    root = project_root(config)
    child_cwd = Path(project_dir).resolve() if project_dir is not None else root
    if project_dir is not None:
        updated = dict(config)
        work_config = dict(config.get("work") or {})
        work_config["project_dir"] = str(child_cwd)
        updated["work"] = work_config
        config = updated
    return WorkerLaunchSpec(
        project_root=Path(project_root_path),
        config=_freeze_config_value(config),
        worker_name=worker_name,
        role="work",
        session_id=session_id,
        oneshot=oneshot,
        project_dir=child_cwd,
    )


def build_runtime_paths(spec: WorkerLaunchSpec) -> WorkerRuntimePaths:
    """Compute the filesystem locations for ``spec``."""
    paths = run_dir(spec.config)
    worker_dir = paths if spec.worker_name == "work" else paths / "workers" / spec.worker_name
    return WorkerRuntimePaths(
        run_dir=paths,
        worker_dir=worker_dir,
        pid_path=worker_dir / "orch-work.pid",
        child_pid_path=worker_dir / "orch-work-child.pid",
        status_path=worker_dir / "orch-work-status.json",
    )


# ---------------------------------------------------------------------------
# Boundary contracts (narrow, testable, no framework)
# ---------------------------------------------------------------------------


class LeaseGateway(Protocol):
    """Broker session-lease boundary used by the supervisor.

    Only the public :class:`PiConnector` operations are referenced. Tests can
    substitute a fake without reaching into private connector state.
    """

    def acquire(self, role: str, pid: int, metadata: dict[str, Any]) -> str: ...

    def heartbeat(self, lease_id: str, metadata: dict[str, Any]) -> None: ...

    def release(self, lease_id: str, reason: str) -> None: ...


class PiConnectorLeaseGateway:
    """Default lease gateway that delegates to a :class:`PiConnector`."""

    def __init__(self, connector: PiConnector) -> None:
        self._connector = connector

    def acquire(self, role: str, pid: int, metadata: dict[str, Any]) -> str:
        return self._connector.acquire_session(role, pid, metadata=metadata)

    def heartbeat(self, lease_id: str, metadata: dict[str, Any]) -> None:
        self._connector.heartbeat_session(lease_id, metadata)

    def release(self, lease_id: str, reason: str) -> None:
        # Use the public release method. ``_release_session`` remains as a
        # compatibility wrapper on the connector itself.
        self._connector.release_session(lease_id, reason)


class StatusWriter:
    """Writes the supervisor status JSON to disk.

    Centralizes the schema and timestamp handling so other boundaries never
    have to know the JSON shape.
    """

    def __init__(self, paths: WorkerRuntimePaths) -> None:
        self._paths = paths

    def write(self, status: str, **extra: Any) -> None:
        _write_json(self._paths.status_path, {"status": status, **extra})

    @property
    def paths(self) -> WorkerRuntimePaths:
        return self._paths


class ProcessTerminator(Protocol):
    """Process-tree termination boundary."""

    def terminate(self, process: subprocess.Popen[str], timeout: float = 5.0) -> None: ...


class DefaultProcessTerminator:
    """Default terminator that delegates to :func:`_terminate_process_tree`."""

    def terminate(self, process: subprocess.Popen[str], timeout: float = 5.0) -> None:
        _terminate_process_tree(process, timeout=timeout)


class ProcessController:
    """Launches the Pi RPC child with the right process group / env / IO."""

    def __init__(self, connector: PiConnector, env_extras: Mapping[str, str]) -> None:
        self._connector = connector
        self._env_extras = MappingProxyType(dict(env_extras))

    def child_env(self, role: str, lease_id: str) -> dict[str, str]:
        """Build child environment from fixed inputs and the acquired lease."""
        extras = {**self._env_extras, "ORCHLINK_SESSION_LEASE_ID": lease_id}
        return self._connector.env(role, extra=extras)

    def argv(self) -> list[str]:
        return self._connector.work_rpc_argv()

    def spawn(self, argv: list[str], cwd: Path, lease_id: str) -> subprocess.Popen[str]:
        creationflags = 0
        start_new_session = False
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            start_new_session = True
        return subprocess.Popen(  # noqa: S603 - launches the configured local Pi command in RPC mode.
            argv,
            cwd=cwd,
            env=self.child_env("work", lease_id),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=start_new_session,
            creationflags=creationflags,
        )


# ---------------------------------------------------------------------------
# Process-tree termination
# ---------------------------------------------------------------------------


def _terminate_process_tree(process: subprocess.Popen[str], timeout: float = 5.0) -> None:
    """Terminate the RPC child and its descendants.

    Kept as a module-level function so existing tests can monkeypatch it on
    ``supervisor._terminate_process_tree`` to avoid touching real processes.
    The default :class:`DefaultProcessTerminator` delegates here.
    """
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            process.terminate()
    try:
        process.wait(timeout=timeout)
        return
    except subprocess.TimeoutExpired:
        pass
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        except OSError:
            process.kill()
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        return


# ---------------------------------------------------------------------------
# Supervisor entry point
# ---------------------------------------------------------------------------


def _base_status_payload(spec: WorkerLaunchSpec, paths: WorkerRuntimePaths) -> dict[str, Any]:
    work_config = spec.config.get("work") or {}
    return {
        "backend": "rpc-supervisor",
        "runtime_mode": "rpc",
        "project_id": str(spec.config.get("project_id") or "default"),
        "agent_id": role_agent_id(spec.config, "work"),
        "worker_name": spec.worker_name,
        "session_id": spec.session_id,
        "model": work_config.get("model"),
        "thinking": work_config.get("thinking"),
        "supervisor_pid": os.getpid(),
        "oneshot": bool(spec.oneshot),
        "project_dir": str(spec.project_dir),
    }


def run_supervisor(
    project_root_path: Path,
    worker_name: str = "work",
    model: str | None = None,
    thinking: str | None = None,
    oneshot: bool = False,
    project_dir: Path | None = None,
    *,
    lease_gateway: LeaseGateway | None = None,
    status_writer: StatusWriter | None = None,
    process_controller: ProcessController | None = None,
    terminator: ProcessTerminator | None = None,
) -> int:
    """Run the supervisor loop and return the child's exit code."""
    spec = build_launch_spec(
        project_root_path,
        worker_name=worker_name,
        model=model,
        thinking=thinking,
        oneshot=oneshot,
        project_dir=project_dir,
    )
    paths = build_runtime_paths(spec)
    connector = PiConnector(spec.config)
    lease = lease_gateway or PiConnectorLeaseGateway(connector)
    writer = status_writer or StatusWriter(paths)
    controller = process_controller or ProcessController(
        connector,
        env_extras={
            "ORCHLINK_RUNTIME_MODE": "rpc",
            "ORCHLINK_BACKGROUND_BACKEND": "rpc-supervisor",
            "ORCHLINK_SUPERVISOR_PID": str(os.getpid()),
            "ORCHLINK_READY_HEARTBEAT_MS": "5000",
            "ORCHLINK_ONESHOT": "true" if oneshot else "false",
        },
    )
    process_terminator = terminator or DefaultProcessTerminator()
    supervisor_pid = os.getpid()
    lease_id = ""
    child: subprocess.Popen[str] | None = None
    session_lost_error = ""
    stop_event = threading.Event()

    def status(status_name: str, **extra: Any) -> None:
        writer.write(
            status_name,
            updated_at=_now(),
            **{k: v for k, v in _base_status_payload(spec, paths).items() if k not in extra},
            **extra,
        )

    def metadata_payload(pi_pid: int | None = None) -> dict[str, Any]:
        work_config = spec.config.get("work") or {}
        payload: dict[str, Any] = {
            "runtime_mode": "rpc",
            "backend": "rpc-supervisor",
            "worker_name": spec.worker_name,
            "model": work_config.get("model"),
            "thinking": work_config.get("thinking"),
            "supervisor_pid": supervisor_pid,
            "project_dir": str(spec.project_dir),
        }
        if pi_pid is not None:
            payload["pi_pid"] = pi_pid
        return payload

    def heartbeat_loop() -> None:
        nonlocal session_lost_error
        interval = max(1, broker_session_heartbeat_interval_seconds(spec.config))
        while not stop_event.wait(interval):
            if not lease_id:
                continue
            try:
                lease.heartbeat(
                    lease_id,
                    metadata_payload(pi_pid=child.pid if child else None),
                )
            except Exception as exc:
                print(f"[Orch worker supervisor] heartbeat failed: {exc}", flush=True)
                if _is_lost_session_error(exc):
                    session_lost_error = str(exc)
                    status(
                        "session_lost",
                        lease_id=lease_id,
                        error=session_lost_error,
                        lost_at=_now(),
                    )
                    stop_event.set()
                    if child is not None:
                        process_terminator.terminate(child)
                    break

    def handle_stop(_signum: int, _frame: object) -> None:
        stop_event.set()
        if child is not None:
            process_terminator.terminate(child)

    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, handle_stop)
        signal.signal(signal.SIGINT, handle_stop)
    else:
        signal.signal(signal.SIGTERM, handle_stop)

    try:
        status("starting", started_at=_now())
        lease_id = lease.acquire("work", supervisor_pid, metadata=metadata_payload())
        heartbeat = threading.Thread(target=heartbeat_loop, daemon=True)
        heartbeat.start()
        argv = controller.argv()
        print(f"[Orch worker supervisor] starting: {' '.join(argv)}", flush=True)
        child = controller.spawn(argv, spec.project_dir, lease_id)
        paths.child_pid_path.write_text(str(child.pid), encoding="utf-8")
        status("running", lease_id=lease_id, pi_pid=child.pid, started_at=_now())
        if child.stdout is not None:
            for line in child.stdout:
                print(line.rstrip("\n"), flush=True)
                if stop_event.is_set():
                    break
        return_code = child.wait()
        exit_extra: dict[str, Any] = {}
        if session_lost_error:
            exit_extra = {"stopped_reason": "session_lost", "session_lost_error": session_lost_error}
        status(
            "exited",
            lease_id=lease_id,
            pi_pid=child.pid,
            exit_code=return_code,
            exited_at=_now(),
            **exit_extra,
        )
        return int(return_code or 0)
    except Exception as exc:
        print(f"[Orch worker supervisor] failed: {exc}", flush=True)
        status("failed", lease_id=lease_id or None, error=str(exc), failed_at=_now())
        return 1
    finally:
        stop_event.set()
        if child is not None and child.poll() is None:
            process_terminator.terminate(child)
        if lease_id:
            lease.release(lease_id, "Background worker supervisor exited.")
        paths.child_pid_path.unlink(missing_ok=True)
        _unlink_pid_file_if_self(paths.pid_path, supervisor_pid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Orchlink headless worker supervisor.")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--worker-name", default="work")
    parser.add_argument("--model", default=None)
    parser.add_argument("--thinking", default=None)
    parser.add_argument("--oneshot", action="store_true", help="Exit after one completed task reply.")
    parser.add_argument("--project-dir", default=None, help="Working directory for the Pi RPC child process.")
    args = parser.parse_args(argv)
    if args.project_dir:
        project_dir = Path(args.project_dir)
        if not project_dir.exists():
            parser.error(f"--project-dir path does not exist: {project_dir}")
        if not project_dir.is_dir():
            parser.error(f"--project-dir path is not a directory: {project_dir}")
    return run_supervisor(
        Path(args.project_root),
        worker_name=args.worker_name,
        model=args.model,
        thinking=args.thinking,
        oneshot=args.oneshot,
        project_dir=Path(args.project_dir) if args.project_dir else None,
    )


if __name__ == "__main__":
    raise SystemExit(main())