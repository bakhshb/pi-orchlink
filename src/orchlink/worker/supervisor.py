from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _saved_worker_session_id(config: dict[str, Any], worker_name: str) -> str:
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


def _terminate_process_tree(process: subprocess.Popen[str], timeout: float = 5.0) -> None:
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


def run_supervisor(
    project_root_path: Path,
    worker_name: str = "work",
    model: str | None = None,
    thinking: str | None = None,
    oneshot: bool = False,
    project_dir: Path | None = None,
) -> int:
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
    connector = PiConnector(config)
    paths = run_dir(config)
    worker_paths = paths if worker_name == "work" else paths / "workers" / worker_name
    pid_path = worker_paths / "orch-work.pid"
    child_pid_path = worker_paths / "orch-work-child.pid"
    status_path = worker_paths / "orch-work-status.json"
    supervisor_pid = os.getpid()
    lease_id = ""
    child: subprocess.Popen[str] | None = None
    session_lost_error = ""
    stop_event = threading.Event()

    def status(status: str, **extra: Any) -> None:
        _write_json(
            status_path,
            {
                "status": status,
                "backend": "rpc-supervisor",
                "runtime_mode": "rpc",
                "project_id": str(config.get("project_id") or "default"),
                "agent_id": role_agent_id(config, "work"),
                "worker_name": worker_name,
                "session_id": session_id,
                "model": (config.get("work") or {}).get("model"),
                "thinking": (config.get("work") or {}).get("thinking"),
                "supervisor_pid": supervisor_pid,
                "oneshot": bool(oneshot),
                "project_dir": str(child_cwd),
                "updated_at": _now(),
                **extra,
            },
        )

    def heartbeat_loop() -> None:
        nonlocal session_lost_error
        interval = max(1, broker_session_heartbeat_interval_seconds(config))
        while not stop_event.wait(interval):
            if not lease_id:
                continue
            try:
                connector.heartbeat_session(
                    lease_id,
                    {
                        "runtime_mode": "rpc",
                        "backend": "rpc-supervisor",
                        "worker_name": worker_name,
                        "model": (config.get("work") or {}).get("model"),
                        "thinking": (config.get("work") or {}).get("thinking"),
                        "supervisor_pid": supervisor_pid,
                        "pi_pid": child.pid if child else None,
                        "project_dir": str(child_cwd),
                    },
                )
            except Exception as exc:
                print(f"[Orch worker supervisor] heartbeat failed: {exc}", flush=True)
                if _is_lost_session_error(exc):
                    session_lost_error = str(exc)
                    status("session_lost", lease_id=lease_id, error=session_lost_error, lost_at=_now())
                    stop_event.set()
                    if child is not None:
                        _terminate_process_tree(child)
                    break

    def handle_stop(_signum: int, _frame: object) -> None:
        stop_event.set()
        if child is not None:
            _terminate_process_tree(child)

    if sys.platform != "win32":
        signal.signal(signal.SIGTERM, handle_stop)
        signal.signal(signal.SIGINT, handle_stop)
    else:
        signal.signal(signal.SIGTERM, handle_stop)

    try:
        status("starting", started_at=_now())
        lease_id = connector.acquire_session(
            "work",
            supervisor_pid,
            metadata={
                "runtime_mode": "rpc",
                "backend": "rpc-supervisor",
                "worker_name": worker_name,
                "model": (config.get("work") or {}).get("model"),
                "thinking": (config.get("work") or {}).get("thinking"),
                "supervisor_pid": supervisor_pid,
                "project_dir": str(child_cwd),
            },
        )
        heartbeat = threading.Thread(target=heartbeat_loop, daemon=True)
        heartbeat.start()
        env = connector._env(
            "work",
            {
                "ORCHLINK_SESSION_LEASE_ID": lease_id,
                "ORCHLINK_RUNTIME_MODE": "rpc",
                "ORCHLINK_BACKGROUND_BACKEND": "rpc-supervisor",
                "ORCHLINK_SUPERVISOR_PID": str(supervisor_pid),
                "ORCHLINK_READY_HEARTBEAT_MS": "5000",
                "ORCHLINK_ONESHOT": "true" if oneshot else "false",
            },
        )
        creationflags = 0
        start_new_session = False
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            start_new_session = True
        argv = connector.work_rpc_argv()
        print(f"[Orch worker supervisor] starting: {' '.join(argv)}", flush=True)
        child = subprocess.Popen(  # noqa: S603 - launches the configured local Pi command in RPC mode.
            argv,
            cwd=child_cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=start_new_session,
            creationflags=creationflags,
        )
        child_pid_path.write_text(str(child.pid), encoding="utf-8")
        status("running", lease_id=lease_id, pi_pid=child.pid, started_at=_now())
        if child.stdout is not None:
            for line in child.stdout:
                print(line.rstrip("\n"), flush=True)
                if stop_event.is_set():
                    break
        return_code = child.wait()
        exit_extra = {}
        if session_lost_error:
            exit_extra = {"stopped_reason": "session_lost", "session_lost_error": session_lost_error}
        status("exited", lease_id=lease_id, pi_pid=child.pid, exit_code=return_code, exited_at=_now(), **exit_extra)
        return int(return_code or 0)
    except Exception as exc:
        print(f"[Orch worker supervisor] failed: {exc}", flush=True)
        status("failed", lease_id=lease_id or None, error=str(exc), failed_at=_now())
        return 1
    finally:
        stop_event.set()
        if child is not None and child.poll() is None:
            _terminate_process_tree(child)
        if lease_id:
            connector._release_session(lease_id, "Background worker supervisor exited.")
        child_pid_path.unlink(missing_ok=True)
        _unlink_pid_file_if_self(pid_path, supervisor_pid)


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
