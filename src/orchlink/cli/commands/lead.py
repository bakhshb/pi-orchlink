"""``orch lead``, ``orch work``, ``orch stop`` — Pi session commands."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from rich.console import Console

# ``main`` is the cli/main module; we look up the test-patched symbols on its
# namespace at call time so existing ``monkeypatch.setattr(cli_main, "X", ...)``
# patterns keep working. See ``orchlink/cli/main.py`` for the full rationale.
from orchlink.cli import main as _cli_main

from orchlink.broker.state import is_active_session_status
from orchlink.cli.commands._helpers import auto_refresh_project_skills, load_project_or_exit, project_query
from orchlink.client import THINKING_LEVELS, normalize_thinking_level
from orchlink.client.process import broker_pid_path
from orchlink.connector.pi_connector import PiConnectorError
from orchlink.project.config import DEFAULT_WORKER_NAME, normalize_worker_name, project_root, role_agent_id, save_project_config, with_worker_name, worker_agent_id


console = Console()


def with_new_pi_session(config: dict[str, Any], role: str) -> tuple[dict[str, Any], str]:
    """Reset the saved ``lead``/``work`` session id so the next Pi process starts fresh."""
    # ``time.strftime`` is module-attribute access on the stdlib ``time`` module;
    # ``cli/main.py`` re-imports ``time`` so the test pattern
    # ``cli_main.time.strftime`` rebinds globally.
    session_id = f"{role}-{time.strftime('%Y%m%d-%H%M%S')}"
    updated = dict(config)
    updated[role] = dict(config.get(role) or {})
    updated[role]["session_id"] = session_id
    save_project_config(updated)
    return updated, session_id


def worker_runtime_state_path(config: dict[str, Any], worker_name: str) -> Path:
    return project_root(config) / ".orch" / "run" / "workers" / worker_name / "state.json"


def saved_worker_session_id(config: dict[str, Any], worker_name: str) -> str:
    if worker_name == DEFAULT_WORKER_NAME:
        return str((config.get("work") or {}).get("session_id") or DEFAULT_WORKER_NAME)
    path = worker_runtime_state_path(config, worker_name)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return worker_name
    return str(state.get("session_id") or worker_name)


def save_worker_runtime_state(config: dict[str, Any], worker_name: str, session_id: str) -> None:
    if worker_name == DEFAULT_WORKER_NAME:
        return
    path = worker_runtime_state_path(config, worker_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "name": worker_name,
                "agent_id": worker_agent_id(config, worker_name),
                "session_id": session_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def with_named_worker_session(config: dict[str, Any], worker_name: str, new: bool = False) -> tuple[dict[str, Any], str]:
    if worker_name == DEFAULT_WORKER_NAME and new:
        updated, session_id = with_new_pi_session(config, "work")
        return with_worker_name(updated, worker_name, session_id=session_id), session_id
    if new:
        session_id = f"{worker_name}-{time.strftime('%Y%m%d-%H%M%S')}"
        save_worker_runtime_state(config, worker_name, session_id)
    else:
        session_id = saved_worker_session_id(config, worker_name)
    return with_worker_name(config, worker_name, session_id=session_id), session_id


def with_worker_profile(config: dict[str, Any], model: str | None = None, thinking: str | None = None) -> dict[str, Any]:
    if model is None and thinking is None:
        return config
    updated = dict(config)
    work_config = dict(config.get("work") or {})
    if model is not None:
        work_config["model"] = str(model).strip()
    if thinking is not None:
        work_config["thinking"] = normalize_thinking_level(thinking)
    updated["work"] = work_config
    return updated


def model_lookup_pattern(model: str) -> str:
    """Return the part of a Pi model pattern safe to pass to `pi --list-models`.

    Pi accepts `model:thinking` shorthand, while some model IDs contain colons
    themselves (for example Ollama IDs). Strip only a recognized thinking suffix.
    """
    value = str(model or "").strip()
    if ":" not in value:
        return value
    base, suffix = value.rsplit(":", 1)
    if base and suffix.lower() in THINKING_LEVELS:
        return base
    return value


def pi_list_models(command: str, search: str | None = None) -> str:
    args = [command, "--list-models"]
    if search:
        args.append(search)
    result = subprocess.run(  # noqa: S603 - calls the configured local Pi executable.
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=20,
        check=False,
    )
    output = result.stdout or ""
    if result.returncode != 0:
        detail = output.strip() or "no output"
        raise PiConnectorError(f"`pi --list-models` failed with exit code {result.returncode}: {detail}")
    return output


def pi_model_rows(output: str) -> list[str]:
    lines = [line.rstrip() for line in output.splitlines() if line.strip()]
    if not lines or lines[0].startswith("No models matching"):
        return []
    if lines[0].lower().startswith("provider"):
        return lines[1:]
    return lines


def ensure_pi_model_available(connector: Any, model: str | None) -> None:
    if not model:
        return
    requested = str(model).strip()
    if not requested:
        return
    search = model_lookup_pattern(requested)
    try:
        matching_output = pi_list_models(connector.resolved_pi_command(), search)
    except (OSError, subprocess.SubprocessError) as exc:
        raise PiConnectorError(f"Could not check Pi model availability with `pi --list-models`: {exc}") from exc
    if pi_model_rows(matching_output):
        return

    console.print(f"[Orch] Pi model is not registered or available: {requested}")
    if search != requested:
        console.print(f"[Orch] Checked model pattern: {search}")
    try:
        available_output = pi_list_models(connector.resolved_pi_command())
    except (OSError, subprocess.SubprocessError, PiConnectorError):
        available_output = ""
    rows = pi_model_rows(available_output)
    if rows:
        console.print("[Orch] Available models from `pi --list-models`:")
        header = next((line.rstrip() for line in available_output.splitlines() if line.strip()), "")
        if header and header.lower().startswith("provider"):
            console.print(header)
        for line in rows[:50]:
            console.print(line)
        if len(rows) > 50:
            console.print(f"[Orch] ... {len(rows) - 50} more. Run `pi --list-models` to see all models.")
    else:
        console.print("[Orch] `pi --list-models` returned no available models. Configure/login to Pi first.")
    raise typer.Exit(1)


def active_work_sessions(config: dict[str, Any], worker_name: str | None = None) -> list[dict[str, Any]]:
    body = _cli_main.broker_get_sync(config, f"/v1/sessions?active=true{project_query(config, '&')}")
    sessions = [
        session
        for session in body.get("sessions", [])
        if str(session.get("role") or "") == "work"
    ]
    if worker_name is None:
        return sessions
    target_agent = role_agent_id(config, "work") if worker_name == DEFAULT_WORKER_NAME else worker_agent_id(config, worker_name)
    return [
        session
        for session in sessions
        if str(session.get("agent_id") or "") == target_agent
        or str(session.get("worker_name") or "") == worker_name
        or (worker_name == DEFAULT_WORKER_NAME and str(session.get("agent_id") or "") in {"", target_agent})
    ]


def work_background_paths(config: dict[str, Any], worker_name: str = DEFAULT_WORKER_NAME) -> tuple[Path, Path]:
    run_path = project_root(config) / ".orch" / "run"
    if worker_name == DEFAULT_WORKER_NAME:
        return run_path / "orch-work.pid", run_path / "orch-work.log"
    worker_run_path = run_path / "workers" / worker_name
    return worker_run_path / "orch-work.pid", worker_run_path / "orch-work.log"


def read_pid_file(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def is_ready_work_session(config: dict[str, Any], session: dict[str, Any]) -> bool:
    if not bool(session.get("ready")):
        return False
    last_ready = _parse_time(session.get("last_ready_heartbeat_at") or session.get("ready_at"))
    if last_ready is None:
        return False
    if last_ready.tzinfo is None:
        last_ready = last_ready.replace(tzinfo=timezone.utc)
    grace = int(((config.get("broker") or {}).get("session_grace_seconds")) or 25)
    return (datetime.now(timezone.utc) - last_ready).total_seconds() < grace


def background_work_sessions(sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        session
        for session in sessions
        if str(session.get("backend") or "") == "rpc-supervisor"
        or str(session.get("runtime_mode") or "") == "rpc"
    ]


def release_work_sessions(config: dict[str, Any], sessions: list[dict[str, Any]], reason: str) -> None:
    for session in sessions:
        if not is_active_session_status(session.get("status")):
            continue
        lease_id = str(session.get("lease_id") or "")
        if not lease_id:
            continue
        try:
            _cli_main.broker_post_sync(
                config,
                f"/v1/sessions/{lease_id}/release",
                {"project_id": str(config.get("project_id") or "default"), "reason": reason},
            )
        except httpx.HTTPError:
            continue


def launch_work_background(config: dict[str, Any], worker_name: str = DEFAULT_WORKER_NAME, oneshot: bool = False) -> int:
    pid_path, log_path = work_background_paths(config, worker_name)
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "orchlink.worker.supervisor",
        "--project-root",
        str(project_root(config)),
        "--worker-name",
        worker_name,
    ]
    work_config = config.get("work") or {}
    if work_config.get("model"):
        command.extend(["--model", str(work_config["model"])])
    if work_config.get("thinking"):
        command.extend(["--thinking", str(work_config["thinking"])])
    if work_config.get("_worktree_project_dir"):
        command.extend(["--project-dir", str(work_config["_worktree_project_dir"])])
    if oneshot:
        command.append("--oneshot")
    env = os.environ.copy()
    python_path = str(Path(sys.executable).parent)
    path_value = env.get("PATH") or env.get("Path") or ""
    env["PATH"] = python_path if not path_value else f"{python_path}{os.pathsep}{path_value}"
    env["Path"] = env["PATH"]
    log_file = log_path.open("w", encoding="utf-8")
    start_new_session = sys.platform != "win32"
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if sys.platform == "win32" else 0
    try:
        process = subprocess.Popen(  # noqa: S603 - starts the local Orchlink worker supervisor.
            command,
            cwd=project_root(config),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=start_new_session,
            creationflags=creationflags,
        )
    finally:
        log_file.close()
    pid_path.write_text(str(process.pid), encoding="utf-8")
    return process.pid


def tail_file(path: Path, lines: int = 20) -> str:
    if not path.is_file():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])


def wait_for_background_worker(
    config: dict[str, Any], timeout_seconds: int, expected_session_id: str, worker_name: str = DEFAULT_WORKER_NAME
) -> dict[str, Any] | None:
    deadline = time.monotonic() + max(0, timeout_seconds)
    while True:
        sessions = active_work_sessions(config, worker_name)
        for session in sessions:
            if str(session.get("session_id") or "") == expected_session_id and is_ready_work_session(config, session):
                return session
        if time.monotonic() >= deadline:
            return None
        time.sleep(1)


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate_pid(pid: int, timeout_seconds: float = 5.0) -> bool:
    """Terminate a tracked background worker supervisor and its process group."""
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not _pid_running(pid):
            return True
        time.sleep(0.1)
    if sys.platform != "win32":
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
        except OSError:
            os.kill(pid, signal.SIGKILL)
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            return True
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not _pid_running(pid):
            return True
        time.sleep(0.1)
    return not _pid_running(pid)


def stop_pid_file(path: Path, label: str) -> bool:
    """Stop the background process recorded in ``path`` (a PID file)."""
    if not path.is_file():
        console.print(f"[Orch] No {label} PID file found for this project.")
        return False
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        path.unlink(missing_ok=True)
        console.print(f"[Orch] Removed invalid {label} PID file.")
        return False
    stopped = _terminate_pid(pid)
    if stopped:
        console.print(f"[Orch] Stopped {label} PID {pid}")
    else:
        console.print(f"[Orch] Could not stop {label} PID {pid}; process may need manual cleanup.")
    path.unlink(missing_ok=True)
    return stopped


def worker_pid_files(config: dict[str, Any]) -> list[tuple[str, Path]]:
    run_path = project_root(config) / ".orch" / "run"
    paths: list[tuple[str, Path]] = []
    default_path = run_path / "orch-work.pid"
    if default_path.is_file():
        paths.append((DEFAULT_WORKER_NAME, default_path))
    workers_path = run_path / "workers"
    if workers_path.is_dir():
        for pid_path in sorted(workers_path.glob("*/orch-work.pid")):
            paths.append((pid_path.parent.name, pid_path))
    return paths


def register_lead(app: typer.Typer) -> None:
    """Register the lead/work/stop commands on the given Typer app."""

    @app.command(help="Start or reopen the visible Pi lead session for this project.")
    def lead(
        new: Annotated[
            bool,
            typer.Option("--new", help="Start a new Pi lead session instead of reopening the saved lead session."),
        ] = False,
    ) -> None:
        config = load_project_or_exit()
        try:
            auto_refresh_project_skills(config)
            _cli_main.ensure_broker_running(config)
            console.print("[Orch] Broker online")
            _cli_main.register_project_role_sync(config, "lead")
            console.print(f"[Orch] Registered: {role_agent_id(config, 'lead')}")
            if new:
                config, session_id = with_new_pi_session(config, "lead")
                console.print(f"[Orch] New Pi lead session: {session_id}")
            console.print("[Orch] Worker available: work")
            console.print("[Orch] Starting Pi lead session...")
            console.print("[Orch] Lead will listen for worker replies and talk messages.")
            exit_code = _cli_main.PiConnector(config).run_lead()
        except typer.Exit:
            raise
        except (RuntimeError, PiConnectorError, httpx.HTTPError) as exc:
            console.print(f"[Orch] {exc}")
            raise typer.Exit(1) from exc
        raise typer.Exit(exit_code)

    @app.command(help="Start or reopen the Pi worker session; use --background for the headless RPC worker.")
    def work(
        new: Annotated[
            bool,
            typer.Option("--new", help="Start a new Pi worker session instead of reopening the saved worker session."),
        ] = False,
        background: Annotated[
            bool,
            typer.Option("--background", help="Start the headless RPC worker, wait for readiness, then return."),
        ] = False,
        timeout: Annotated[
            int,
            typer.Option("--timeout", min=1, help="Seconds to wait for background worker readiness."),
        ] = 30,
        worker_name: Annotated[
            str,
            typer.Option("--name", help="Configless worker name to start or reopen, default: work."),
        ] = DEFAULT_WORKER_NAME,
        replace: Annotated[
            bool,
            typer.Option("--replace", help="Release/fence an active session with the same worker name before starting."),
        ] = False,
        background_test: Annotated[
            bool,
            typer.Option("--test", help="Start a safe fresh background test worker (default name: bg-test)."),
        ] = False,
        oneshot: Annotated[
            bool,
            typer.Option("--oneshot", help="For background workers only: exit after one completed task reply."),
        ] = False,
        model: Annotated[
            str | None,
            typer.Option("--model", help="Pi model pattern for this worker session, e.g. provider/model or model:thinking."),
        ] = None,
        thinking: Annotated[
            str | None,
            typer.Option("--thinking", help="Default worker thinking: off, minimal, low, medium, high, xhigh."),
        ] = None,
        worktree: Annotated[
            Path | None,
            typer.Option("--worktree", help="Run this worker from PATH while keeping broker identity tied to this project."),
        ] = None,
    ) -> None:
        config = load_project_or_exit()
        try:
            if background_test:
                background = True
                new = True
                if worker_name == DEFAULT_WORKER_NAME:
                    worker_name = "bg-test"
            if oneshot and not background:
                console.print("[Orch] --oneshot is only supported with --background; visible workers are not auto-terminated.")
                raise typer.Exit(1)
            worker_name = normalize_worker_name(worker_name)
            thinking = normalize_thinking_level(thinking)
            worktree_path: Path | None = None
            if worktree is not None:
                worktree_path = worktree.expanduser().resolve()
                if not worktree_path.exists():
                    console.print(f"[Orch] --worktree path does not exist: {worktree_path}")
                    raise typer.Exit(1)
                if not worktree_path.is_dir():
                    console.print(f"[Orch] --worktree path is not a directory: {worktree_path}")
                    raise typer.Exit(1)
            config, session_id = with_named_worker_session(config, worker_name, new=new)
            config = with_worker_profile(config, model=model, thinking=thinking)
            if worktree_path is not None:
                updated = dict(config)
                work_config = dict(config.get("work") or {})
                work_config["project_dir"] = str(worktree_path)
                work_config["_worktree_project_dir"] = str(worktree_path)
                updated["work"] = work_config
                config = updated
            profile_override = model is not None or thinking is not None or oneshot or worktree_path is not None
            named_worker_label = f" worker '{worker_name}'" if worker_name != DEFAULT_WORKER_NAME else " worker"
            auto_refresh_project_skills(config)
            _cli_main.ensure_broker_running(config)
            console.print("[Orch] Broker online")
            connector = _cli_main.PiConnector(config)
            if not connector.check_available():
                raise PiConnectorError(f"Pi command not found: {connector.pi_command()}")
            ensure_pi_model_available(connector, (config.get("work") or {}).get("model"))

            pid_path, log_path = work_background_paths(config, worker_name)
            existing = active_work_sessions(config, worker_name)
            tracked_pid = read_pid_file(pid_path)
            existing_background = background_work_sessions(existing)
            ready_background = [session for session in existing_background if is_ready_work_session(config, session)]
            if existing and not replace:
                if background and ready_background and not new and not profile_override and len(existing_background) == len(existing):
                    session_id = str(ready_background[0].get("session_id") or "unknown")
                    console.print(f"[Orch] Background worker already ready: {session_id}")
                    console.print("[Orch] Use --new --replace to start a fresh worker session.")
                    raise typer.Exit(0)
                console.print(f"[Orch] Worker '{worker_name}' is already active.")
                if background and worker_name == DEFAULT_WORKER_NAME:
                    console.print("[Orch] To test background safely, run: orch work --background --name bg-test --new")
                console.print(f"[Orch] Use `orch stop --name {worker_name}` first, or pass --replace after confirming it is safe.")
                raise typer.Exit(1)
            if existing and replace:
                stopped_existing = stop_pid_file(pid_path, f"worker '{worker_name}'")
                release_work_sessions(config, existing_background, f"Replaced by orch work --name {worker_name} --replace.")
                remaining_sessions = [session for session in existing if session not in existing_background]
                if remaining_sessions:
                    console.print(f"[Orch] Worker '{worker_name}' still has a visible active session.")
                    console.print("[Orch] Stop it in that terminal with Ctrl-C, then retry.")
                    raise typer.Exit(1)
                if existing and not stopped_existing:
                    console.print(f"[Orch] Fenced existing worker '{worker_name}' session lease(s).")
            elif background and new and tracked_pid is not None:
                stop_pid_file(pid_path, f"worker '{worker_name}'")

            if background:
                if new:
                    console.print(f"[Orch] New Pi{named_worker_label} session: {session_id}")
                if worktree_path is not None:
                    console.print(f"[Orch] Worktree: {worktree_path}")
                console.print(f"[Orch] Starting headless Pi RPC{named_worker_label}...")
                pid = launch_work_background(config, worker_name, oneshot=oneshot)
                console.print(f"[Orch] Supervisor PID: {pid} ({pid_path})")
                console.print(f"[Orch] Log: {log_path}")
                session = wait_for_background_worker(config, timeout, session_id, worker_name)
                if session is None:
                    console.print(f"[Orch] Worker process started but did not become ready within {timeout}s.")
                    console.print(f"[Orch] Check log: {log_path}")
                    fallback_command = "orch work" if worker_name == DEFAULT_WORKER_NAME else f"orch work --name {worker_name}"
                    console.print(f"[Orch] Fallback: run `{fallback_command}` in another terminal for a visible worker.")
                    tail = tail_file(log_path)
                    if tail:
                        console.print("[Orch] Last worker log lines:")
                        console.print(tail)
                    raise typer.Exit(1)
                console.print(f"[Orch] Headless worker ready: {session.get('session_id') or 'unknown'}")
                raise typer.Exit(0)

            _cli_main.register_project_role_sync(config, "worker")
            console.print(f"[Orch] Registered: {role_agent_id(config, 'work')}")
            if new:
                console.print(f"[Orch] New Pi{named_worker_label} session: {session_id}")
                connector = _cli_main.PiConnector(config)

            if worktree_path is not None:
                console.print(f"[Orch] Worktree: {worktree_path}")
            console.print(f"[Orch] Starting Pi{named_worker_label} session...")
            console.print("[Orch] Tasks and talk turns will be posted directly into this Pi chat.")
            exit_code = connector.run_work()
        except typer.Exit:
            raise
        except (RuntimeError, PiConnectorError, httpx.HTTPError, ValueError) as exc:
            console.print(f"[Orch] {exc}")
            raise typer.Exit(1) from exc
        raise typer.Exit(exit_code)

    @app.command(help="Stop this project's background worker by default; add --broker or --all for broker cleanup.")
    def stop(
        broker: Annotated[
            bool,
            typer.Option("--broker", help="Stop the shared broker only; use carefully when other projects may be active."),
        ] = False,
        all_: Annotated[
            bool,
            typer.Option("--all", help="Stop this project's background worker and the shared broker."),
        ] = False,
        worker_name: Annotated[
            str | None,
            typer.Option("--name", help="Stop/release one named worker, default: work."),
        ] = None,
    ) -> None:
        if broker and all_:
            console.print("[Orch] Use either --broker or --all, not both.")
            raise typer.Exit(1)
        config = load_project_or_exit()
        target_worker_name = normalize_worker_name(worker_name or DEFAULT_WORKER_NAME)
        explicit_worker_name = worker_name is not None
        stop_worker = not broker or all_
        stop_broker = broker or all_
        if stop_worker:
            if all_:
                stopped_any = False
                for name, pid_path in worker_pid_files(config):
                    stopped_any = stop_pid_file(pid_path, f"worker '{name}'") or stopped_any
                try:
                    release_work_sessions(config, active_work_sessions(config), "Stopped by orch stop --all.")
                except httpx.HTTPError:
                    pass
                if not stopped_any:
                    console.print("[Orch] No tracked background worker PID files found for this project.")
            else:
                worker_pid_path, _ = work_background_paths(config, target_worker_name)
                try:
                    sessions = active_work_sessions(config, target_worker_name)
                except httpx.HTTPError:
                    sessions = []
                background_sessions = background_work_sessions(sessions)
                if stop_pid_file(worker_pid_path, f"worker '{target_worker_name}'"):
                    release_work_sessions(config, background_sessions, f"Stopped by orch stop --name {target_worker_name}.")
                elif sessions and explicit_worker_name:
                    release_work_sessions(config, sessions, f"Stopped by orch stop --name {target_worker_name}.")
                    console.print(f"[Orch] Fenced active worker '{target_worker_name}' session lease(s).")
                    console.print("[Orch] If it is a visible worker terminal, stop it there with Ctrl-C.")
                elif sessions:
                    console.print("[Orch] Active worker session exists but no tracked background PID was found.")
                    console.print("[Orch] If it is a visible worker terminal, stop it there with Ctrl-C.")
        if stop_broker:
            stop_pid_file(broker_pid_path(config), "broker")
        else:
            console.print("[Orch] Broker left running. Use `orch stop --broker` or `orch stop --all` only when no other project needs it.")
