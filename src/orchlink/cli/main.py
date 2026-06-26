import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from typing import Annotated, Any

import httpx
import typer
import uvicorn
from rich.console import Console

from orchlink.bridge.ask import (
    ask_worker_sync as project_ask_worker_sync,
    close_talk_sync,
    infer_task_mode,
    send_worker_sync,
    say_talk_sync,
    start_talk_sync,
)
from orchlink.bridge.monitor import fetch_events, fetch_status, format_event
from orchlink.broker.main import BROKER_CAPABILITIES, VERSION as BROKER_VERSION
from orchlink.connector.pi_connector import PiConnector, PiConnectorError
from orchlink.project.config import (
    ProjectConfigError,
    broker_api_key,
    broker_auto_start,
    broker_host,
    broker_port,
    broker_url,
    load_project_config,
    project_root,
    role_agent_id,
    run_dir,
)
from orchlink.project.init import LEAD_SKILL, WORK_SKILL, init_project


def discover_project_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    return Path.cwd()


PROJECT_ROOT = discover_project_root()
app = typer.Typer(help="Local broker and connector for two Pi coding-agent sessions.")
broker_app = typer.Typer(help="Run and manage the local Orchlink broker.")
app.add_typer(broker_app, name="broker")
console = Console()


def print_orch_exception(exc: Exception) -> None:
    if isinstance(exc, httpx.HTTPStatusError):
        try:
            detail = exc.response.json().get("detail")
        except ValueError:
            detail = None
        if isinstance(detail, dict):
            console.print(f"[Orch] {detail.get('message') or detail.get('error') or exc}")
            if detail.get("blocking_id"):
                console.print(
                    f"[Orch] Blocking work: {detail.get('blocking_id')} "
                    f"({detail.get('blocking_kind', 'work')} {detail.get('blocking_status', '')})"
                )
            return
        if detail:
            console.print(f"[Orch] {detail}")
            return
    console.print(f"[Orch] {exc}")


async def register_project_role(config: dict[str, Any], role: str) -> dict[str, Any]:
    role_key = "work" if role == "worker" else role
    role_config = config.get(role_key) or {}
    display_name = "Worker" if role == "worker" else "Lead"
    capabilities = ["inspection", "implementation", "tests", "talk"] if role == "worker" else ["delegation", "review", "talk"]
    async with httpx.AsyncClient(base_url=broker_url(config)) as client:
        response = await client.post(
            "/v1/agents/register",
            headers={"X-API-Key": broker_api_key(config)},
            json={
                "project_id": str(config.get("project_id", "default")),
                "agent_id": role_agent_id(config, role_key),
                "role": role,
                "display_name": role_config.get("display_name", display_name),
                "capabilities": role_config.get("capabilities", capabilities),
            },
        )
        response.raise_for_status()
        return response.json()


def register_project_role_sync(config: dict[str, Any], role: str) -> dict[str, Any]:
    return asyncio.run(register_project_role(config, role))


def fetch_status_sync(
    url: str,
    api_key: str,
    project_id: str | None = None,
    task_id: str | None = None,
    since: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    return asyncio.run(fetch_status(url, api_key, project_id=project_id, task_id=task_id, since=since, limit=limit))


def fetch_events_sync(url: str, api_key: str, since: int = 0, limit: int = 50, project_id: str | None = None) -> dict[str, Any]:
    return asyncio.run(fetch_events(url, api_key, since=since, limit=limit, project_id=project_id))


def broker_get_sync(config: dict[str, Any], path: str) -> dict[str, Any]:
    with httpx.Client(base_url=broker_url(config), timeout=None) as client:
        response = client.get(path, headers=broker_headers(config))
        response.raise_for_status()
        return response.json()


def broker_post_sync(config: dict[str, Any], path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    with httpx.Client(base_url=broker_url(config), timeout=None) as client:
        response = client.post(path, headers=broker_headers(config), json=body or {})
        response.raise_for_status()
        return response.json()


def current_project_id(config: dict[str, Any]) -> str:
    return str(config.get("project_id") or "default")


def project_query(config: dict[str, Any], prefix: str = "?") -> str:
    project_id = quote(current_project_id(config), safe="")
    return f"{prefix}project_id={project_id}"


def broker_headers(config: dict[str, Any]) -> dict[str, str]:
    return {"X-API-Key": broker_api_key(config), "X-Orchlink-Project-ID": current_project_id(config)}


def activity_query(config: dict[str, Any], item_id: str | None = None, limit: int = 10) -> str:
    path = f"/v1/activity?limit={limit}{project_query(config, '&')}"
    if item_id:
        path += f"&item_id={quote(item_id, safe='')}"
    return path


def task_activity_query(config: dict[str, Any], task_id: str, limit: int = 10) -> str:
    return f"/v1/tasks/{quote(task_id, safe='')}/activity?limit={limit}{project_query(config, '&')}"


def parse_iso_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def human_age(value: Any) -> str:
    parsed = parse_iso_time(value)
    if parsed is None:
        return "unknown age"
    seconds = max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s ago"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m ago"


def activity_preview(activity: dict[str, Any]) -> str:
    tool = str(activity.get("tool_name") or "")
    detail = str(activity.get("detail") or activity.get("phase") or activity.get("activity_type") or "").strip()
    if tool and detail:
        return f"{tool}: {detail}"
    return tool or detail


def format_activity(activity: dict[str, Any]) -> str:
    timestamp = str(activity.get("time") or "")
    stamp = timestamp[11:19] if len(timestamp) >= 19 else timestamp
    age = human_age(timestamp)
    kind = str(activity.get("activity_type") or "activity")
    preview = activity_preview(activity)
    return f"[{stamp}] {kind} ({age}) {preview}".rstrip()


def stale_heartbeat(job: dict[str, Any]) -> bool:
    status = str(job.get("status") or "").upper()
    return job.get("last_activity_type") == "heartbeat" and status not in ACTIVE_ACTIVITY_STATUSES


def sanitize_job(job: dict[str, Any]) -> dict[str, Any]:
    clean = dict(job)
    if stale_heartbeat(clean):
        clean.pop("last_activity_at", None)
        clean.pop("last_activity_type", None)
        clean.pop("last_activity_tool", None)
        clean.pop("last_activity_preview", None)
    return clean


def job_activity_line(job: dict[str, Any]) -> str:
    if stale_heartbeat(job) or not job.get("last_activity_at"):
        return ""
    activity = {
        "time": job.get("last_activity_at"),
        "activity_type": job.get("last_activity_type"),
        "tool_name": job.get("last_activity_tool"),
        "detail": job.get("last_activity_preview"),
    }
    return format_activity(activity)


def task_body_project_id(body: dict[str, Any]) -> str | None:
    for source in (body, body.get("job") or {}, body.get("reply") or {}):
        value = source.get("project_id") if isinstance(source, dict) else None
        if value:
            return str(value)
    return None


def validate_task_body_project(config: dict[str, Any], body: dict[str, Any], task_id: str) -> None:
    status = str(body.get("status") or "").upper()
    if status in {"WAIT_TIMEOUT", "MISSING"}:
        return
    expected = current_project_id(config)
    actual = task_body_project_id(body)
    if actual == expected:
        return
    if actual:
        console.print(f"[Orch] Refusing cross-project result for {task_id}: broker returned project {actual}, current project is {expected}.")
    else:
        console.print(f"[Orch] Refusing unscoped result for {task_id}: broker response has no project_id. The broker is likely stale.")
    console.print("[Orch] Run: orch stop && orch lead --new && orch work --new")
    raise typer.Exit(1)


def next_conversation_id(config: dict[str, Any]) -> str:
    try:
        body = broker_get_sync(config, f"/v1/jobs?limit=500{project_query(config, '&')}")
    except httpx.HTTPError:
        return "C001"
    highest = 0
    for job in body.get("jobs", []):
        value = str(job.get("conversation_id") or "")
        if len(value) == 4 and value.startswith("C") and value[1:].isdigit():
            highest = max(highest, int(value[1:]))
    return f"C{highest + 1:03d}"


BLOCKING_JOB_STATUSES = {"PENDING", "QUEUED", "DELIVERED", "RUNNING", "IN_PROGRESS", "OPEN"}
ACTIVE_ACTIVITY_STATUSES = {"DELIVERED", "RUNNING", "IN_PROGRESS"}


def conversation_state(config: dict[str, Any], conversation_id: str) -> dict[str, Any] | None:
    body = broker_get_sync(config, f"/v1/jobs?limit=500{project_query(config, '&')}")
    for job in body.get("jobs", []):
        if job.get("conversation_id") == conversation_id:
            return job
    return None


def blocking_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [job for job in jobs if str(job.get("status") or "").upper() in BLOCKING_JOB_STATUSES]


def job_id(job: dict[str, Any]) -> str:
    return str(job.get("task_id") or job.get("conversation_id") or job.get("message_id") or "-")


def job_kind(job: dict[str, Any]) -> str:
    if job.get("task_id"):
        return "task"
    if job.get("conversation_id"):
        return "talk"
    return str(job.get("kind") or "-")


def job_route(job: dict[str, Any]) -> str:
    return f"{job.get('from_agent', '-')} → {job.get('to_agent', '-')}"


def filter_jobs(
    jobs: list[dict[str, Any]],
    active: bool = False,
    status: str | None = None,
    kind: str | None = None,
    item_id: str | None = None,
) -> list[dict[str, Any]]:
    selected = list(jobs)
    if active:
        selected = blocking_jobs(selected)
    if status:
        expected_status = status.upper()
        selected = [job for job in selected if str(job.get("status") or "").upper() == expected_status]
    if kind:
        expected_kind = kind.lower()
        selected = [job for job in selected if job_kind(job) == expected_kind]
    if item_id:
        selected = [
            job
            for job in selected
            if str(job.get("task_id") or "") == item_id
            or str(job.get("conversation_id") or "") == item_id
            or str(job.get("message_id") or "") == item_id
        ]
    return selected


def jobs_query(
    config: dict[str, Any],
    limit: int = 50,
    active: bool = False,
    status: str | None = None,
    kind: str | None = None,
    item_id: str | None = None,
) -> str:
    params: dict[str, str] = {"limit": str(limit), "project_id": current_project_id(config)}
    if active:
        params["active"] = "true"
    if status:
        params["status"] = status.upper()
    if kind:
        params["kind"] = kind.lower()
    if item_id:
        params["id"] = item_id
    return f"/v1/jobs?{urlencode(params)}"


def run_update(ref: str, reinstall_only: bool = False) -> None:
    root = PROJECT_ROOT
    if not (root / ".git").is_dir():
        raise RuntimeError("This Orchlink install is not a git checkout. Re-run the install script to update.")

    if not reinstall_only:
        subprocess.run(["git", "-C", str(root), "fetch", "--tags", "--prune", "origin"], check=True)
        subprocess.run(["git", "-C", str(root), "checkout", ref], check=True)
        remote_branch = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--verify", f"origin/{ref}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if remote_branch.returncode == 0:
            subprocess.run(["git", "-C", str(root), "pull", "--ff-only", "origin", ref], check=True)

    subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(root)], check=True)
    for old_alias in (Path(sys.executable).parent / "orchlink", Path.home() / ".local" / "bin" / "orchlink"):
        if old_alias.is_file() or old_alias.is_symlink():
            old_alias.unlink()


def broker_info(url: str) -> dict[str, Any] | None:
    try:
        response = httpx.get(f"{url.rstrip('/')}/health", timeout=0.5)
        if response.status_code != 200:
            return None
        body = response.json()
        return body if body.get("status") == "ok" and body.get("service") == "orchlink" else None
    except Exception:
        return None


def broker_health(url: str) -> bool:
    return broker_info(url) is not None


def broker_compatible(info: dict[str, Any] | None) -> bool:
    if not info:
        return False
    capabilities = set(info.get("capabilities") or [])
    return capabilities.issuperset(set(BROKER_CAPABILITIES))


def stale_broker_message(url: str, info: dict[str, Any] | None) -> str:
    version = str((info or {}).get("version") or "unknown")
    missing = sorted(set(BROKER_CAPABILITIES) - set((info or {}).get("capabilities") or []))
    missing_text = f" Missing capabilities: {', '.join(missing)}." if missing else ""
    return (
        f"Broker at {url} is running an older incompatible Orchlink broker "
        f"(broker {version}, CLI expects {BROKER_VERSION}).{missing_text} "
        "Stop the old broker, then restart fresh Pi sessions: orch stop; orch lead --new; orch work --new"
    )


def broker_pid_path(config: dict[str, Any]) -> Path:
    return run_dir(config) / "broker.pid"


def broker_log_path(config: dict[str, Any]) -> Path:
    return run_dir(config) / "broker.log"


def pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def start_background_broker(config: dict[str, Any]) -> None:
    directory = run_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    log_path = broker_log_path(config)
    env = os.environ.copy()
    env["ORCHLINK_HOST"] = broker_host(config)
    env["ORCHLINK_PORT"] = str(broker_port(config))
    env["ORCHLINK_API_KEY"] = broker_api_key(config)
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "orchlink.broker.main:app",
        "--host",
        broker_host(config),
        "--port",
        str(broker_port(config)),
    ]
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            command,
            cwd=project_root(config),
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    broker_pid_path(config).write_text(str(process.pid), encoding="utf-8")

    url = broker_url(config)
    for _ in range(50):
        info = broker_info(url)
        if broker_compatible(info):
            return
        if info is not None and not broker_compatible(info):
            raise RuntimeError(stale_broker_message(url, info))
        if process.poll() is not None:
            raise RuntimeError(f"Broker exited during startup. See {log_path}")
        time.sleep(0.1)
    raise RuntimeError(f"Broker did not become healthy. See {log_path}")


def ensure_broker_running(config: dict[str, Any]) -> None:
    url = broker_url(config)
    info = broker_info(url)
    if broker_compatible(info):
        return
    if info is not None:
        raise RuntimeError(stale_broker_message(url, info))
    if not broker_auto_start(config):
        raise RuntimeError(f"Broker is not reachable at {url} and auto_start is disabled.")
    start_background_broker(config)


def with_new_pi_session(config: dict[str, Any], role: str) -> tuple[dict[str, Any], str]:
    session_id = f"{role}-{time.strftime('%Y%m%d-%H%M%S')}"
    updated = dict(config)
    updated[role] = dict(config.get(role) or {})
    updated[role]["session_id"] = session_id
    return updated, session_id


def stop_pid_file(path: Path, label: str) -> None:
    if not path.is_file():
        console.print(f"[Orch] No {label} PID file found for this project.")
        return
    try:
        pid = int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        path.unlink(missing_ok=True)
        console.print(f"[Orch] Removed invalid {label} PID file.")
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        console.print(f"[Orch] {label} process was not running.")
    else:
        console.print(f"[Orch] Stopped {label} PID {pid}")
    path.unlink(missing_ok=True)


def load_project_or_exit() -> dict[str, Any]:
    try:
        return load_project_config()
    except ProjectConfigError as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc


@broker_app.command("run", help="Run the local Orchlink broker HTTP server in the foreground.")
def broker_run(
    host: Annotated[str, typer.Option("--host", help="Host interface to bind.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", help="TCP port to bind.")] = 8787,
    reload: Annotated[bool, typer.Option("--reload", help="Enable uvicorn auto-reload for development.")] = False,
) -> None:
    console.print(f"[Orch] Starting broker: http://{host}:{port}")
    uvicorn.run("orchlink.broker.main:app", host=host, port=port, reload=reload)


@app.command("init", help="Create .orch project config and generated lead/work skills.")
def init_command(
    project_id: Annotated[str | None, typer.Option("--project-id", help="Explicit project ID; defaults to current folder name.")] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite config and skills.")] = False,
    refresh_skills: Annotated[bool, typer.Option("--refresh-skills", help="Rewrite lead/work skills without changing project config.")] = False,
) -> None:
    paths = init_project(Path.cwd(), project_id=project_id, force=force, refresh_skills=refresh_skills)
    console.print(f"[Orch] Initialized {paths['orch_dir']}")
    console.print(f"[Orch] Config: {paths['config']}")
    console.print(f"[Orch] Lead skill: {paths['lead_skill']}")
    console.print(f"[Orch] Worker skill: {paths['work_skill']}")


@app.command(help="Start or reopen the visible Pi lead session for this project.")
def lead(
    new: Annotated[bool, typer.Option("--new", help="Start a new Pi lead session instead of reopening the saved lead session.")] = False,
) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        console.print("[Orch] Broker online")
        register_project_role_sync(config, "lead")
        console.print(f"[Orch] Registered: {role_agent_id(config, 'lead')}")
        if new:
            config, session_id = with_new_pi_session(config, "lead")
            console.print(f"[Orch] New Pi lead session: {session_id}")
        console.print("[Orch] Worker available: work")
        console.print("[Orch] Starting Pi lead session...")
        console.print("[Orch] Lead will listen for worker replies and talk messages.")
        exit_code = PiConnector(config).run_lead()
    except (RuntimeError, PiConnectorError, httpx.HTTPError) as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc
    raise typer.Exit(exit_code)


@app.command(help="Start or reopen the visible Pi worker session for this project.")
def work(
    new: Annotated[bool, typer.Option("--new", help="Start a new Pi worker session instead of reopening the saved worker session.")] = False,
) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        console.print("[Orch] Broker online")
        register_project_role_sync(config, "worker")
        console.print(f"[Orch] Registered: {role_agent_id(config, 'work')}")
        if new:
            config, session_id = with_new_pi_session(config, "work")
            console.print(f"[Orch] New Pi worker session: {session_id}")

        connector = PiConnector(config)
        if not connector.check_available():
            raise PiConnectorError(f"Pi command not found: {connector.pi_command()}")
        console.print("[Orch] Starting Pi worker session...")
        console.print("[Orch] Tasks and talk turns will be posted directly into this Pi chat.")
        exit_code = connector.run_work()
    except (RuntimeError, PiConnectorError, httpx.HTTPError) as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc
    raise typer.Exit(exit_code)


def print_async_guidance(config: dict[str, Any], worker_id: str, task_id: str) -> None:
    console.print(f"[Orch] Sent {task_id} to {worker_id}.")
    console.print("[Orch] Async mode: worker scope is pending.")
    console.print("[Orch] Check status: orch jobs")
    console.print(f"[Orch] Wait: orch wait {task_id}")
    console.print(f"[Orch] Read result: orch get {task_id}")


@app.command(help="Send a task to work and wait by default; use for reviews and decisions.")
def ask(
    worker_id: str,
    task_id: Annotated[str, typer.Option("--task", "--task-id", "-t", help="Exact task ID to assign, such as T001.")],
    message: Annotated[str, typer.Option("--msg", "--message", "-m", help="Task prompt for the worker.")],
    timeout: Annotated[int, typer.Option("--timeout", help="Seconds to wait for the worker reply.")] = 1800,
    wait: Annotated[bool, typer.Option("--wait/--no-wait", help="Wait in this shell for the reply. Use orch send for async tasks.")] = True,
) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        response = project_ask_worker_sync(
            config=config,
            worker=worker_id,
            task_id=task_id,
            message=message,
            timeout_seconds=timeout,
            wait=wait,
        )
    except (RuntimeError, httpx.HTTPError) as exc:
        print_orch_exception(exc)
        raise typer.Exit(1) from exc
    if not wait:
        print_async_guidance(config, worker_id, task_id)
    console.print_json(json.dumps(response))


@app.command(help="Send an async task to work when the lead can continue on another scope.")
def send(
    worker_id: str,
    task_id: Annotated[str, typer.Option("--task", "--task-id", "-t", help="Exact task ID to assign, such as T002.")],
    message: Annotated[str, typer.Option("--msg", "--message", "-m", help="Task prompt for the worker.")],
    timeout: Annotated[int, typer.Option("--timeout", help="Task timeout in seconds.")] = 1800,
    allow_async_review: Annotated[bool, typer.Option("--allow-async-review", help="Allow REVIEW through async send. Use only when review is not a gate.")] = False,
) -> None:
    mode = infer_task_mode(message)
    if mode == "REVIEW" and not allow_async_review:
        console.print("[Orch] REVIEW is a gate by default.")
        console.print(f"[Orch] Use blocking review: orch ask work --wait -t {task_id} -m \"MODE: REVIEW...\"")
        console.print("[Orch] Or pass --allow-async-review only if lead will not act on the review result.")
        raise typer.Exit(1)

    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        send_worker_sync(
            config=config,
            worker=worker_id,
            task_id=task_id,
            message=message,
            timeout_seconds=timeout,
        )
    except (RuntimeError, httpx.HTTPError) as exc:
        print_orch_exception(exc)
        raise typer.Exit(1) from exc
    print_async_guidance(config, worker_id, task_id)
    if mode == "REVIEW" and allow_async_review:
        console.print(f"[Orch] Async REVIEW is not a gate. Before acting on it, verify the exact result with: orch wait {task_id}")


@app.command(help="Show live broker status for a task: route, delivery, and latest activity. Use `orch get` for the final result body.")
def task(task_id: str) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        status_body = fetch_status_sync(broker_url(config), broker_api_key(config), project_id=current_project_id(config))
        events_body = fetch_events_sync(broker_url(config), broker_api_key(config), limit=500, project_id=current_project_id(config))
    except (RuntimeError, httpx.HTTPError) as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc

    messages = [item for item in status_body.get("active_messages", []) if item.get("task_id") == task_id]
    events = [item for item in events_body.get("events", []) if item.get("task_id") == task_id]
    if not messages and not events:
        console.print(f"[Orch] No broker record found for task {task_id}.")
        return

    latest_message = messages[-1] if messages else {}
    status_text = str(latest_message.get("status") or "UNKNOWN")
    console.print(f"[Orch] Task {task_id}: {status_text}")
    console.print(f"[Orch] Route: {latest_message.get('from_agent', '-')} → {latest_message.get('to_agent', '-')}")
    activity_events = [item for item in events if item.get("type") == "worker_activity"]
    if activity_events:
        console.print(f"[Orch] Last worker activity: {format_activity(activity_events[-1].get('payload') or activity_events[-1])}")

    reply_events = [item for item in events if item.get("type") == "reply_received"]
    if reply_events:
        reply = reply_events[-1]
        console.print(
            f"[Orch] Reply: {reply.get('message_type', 'RESULT')} "
            f"from {reply.get('from_agent', 'work')} to {reply.get('to_agent', 'lead')}"
        )
        preview = str(reply.get("preview") or "").strip()
        if preview:
            console.print(preview)
        return

    delivered_events = [item for item in events if item.get("type") == "message_delivered"]
    if delivered_events:
        delivered = delivered_events[-1]
        console.print(f"[Orch] Delivered to {delivered.get('to_agent', 'worker')}. Worker is still in progress.")
    else:
        console.print("[Orch] Queued. Waiting for worker pickup.")


def _print_task_body(body: dict[str, Any]) -> None:
    task_id = str(body.get("task_id") or "")
    status_text = str(body.get("status") or "UNKNOWN")
    if status_text == "WAIT_TIMEOUT":
        console.print(f"[Orch] Wait for task {task_id}: timed out, but the task is still pending unless cancelled or task timeout expires.")
        if body.get("error"):
            console.print(str(body["error"]))
        return
    console.print(f"[Orch] Task {task_id}: {status_text}")
    reply = body.get("reply") or {}
    if reply:
        console.print(f"[Orch] Type: {reply.get('type', 'RESULT')}")
        payload = reply.get("payload") or {}
        summary = str(payload.get("summary") or payload.get("stdout") or "").strip()
        stderr = str(payload.get("stderr") or "").strip()
        if summary:
            console.print(summary)
        if stderr:
            console.print("[Orch] Stderr:")
            console.print(stderr)
    elif body.get("job"):
        job = body["job"]
        console.print(f"[Orch] Route: {job.get('from_agent', '-')} → {job.get('to_agent', '-')}")
        activity = job_activity_line(job)
        if activity:
            console.print(f"[Orch] Last worker activity: {activity}")
        preview = str(job.get("preview") or "").strip()
        if preview:
            console.print(preview)
    elif body.get("error"):
        console.print(str(body["error"]))


def require_nonempty_talk_message(message: str, command_name: str) -> None:
    if message.strip():
        return
    console.print(f"[Orch] {command_name} message cannot be empty. Use -m \"your question or reply\".")
    raise typer.Exit(1)


def _print_conversation_body(conversation: dict[str, Any]) -> None:
    conversation_id = str(conversation.get("conversation_id") or "")
    console.print(f"[Orch] Conversation {conversation_id}: {conversation.get('status', 'UNKNOWN')}")
    console.print(f"[Orch] Turn: {conversation.get('turn', '?')}/{conversation.get('max_turns', '?')}")
    preview = str(conversation.get("last_message_preview") or conversation.get("preview") or "").strip()
    if preview:
        console.print(preview)
    if conversation.get("status") == "OPEN":
        console.print(f"[Orch] Continue: orch say {conversation_id} -m \"...\"")
        console.print(f"[Orch] Close: orch close {conversation_id} -m \"Decision: ...\"")


@app.command(help="Start a visible Talk Mode discussion with work.")
def talk(
    worker_id: str,
    message: Annotated[str, typer.Option("--msg", "--message", "-m", help="First Talk message to send.")],
    rounds: Annotated[int, typer.Option("--rounds", "-r", min=1, max=6, help="Number of lead↔worker back-and-forth rounds.")] = 6,
    timeout: Annotated[int, typer.Option("--timeout", help="Conversation turn timeout in seconds.")] = 1800,
) -> None:
    require_nonempty_talk_message(message, "Talk")
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        conversation_id = next_conversation_id(config)
        max_turns = rounds * 2
        start_talk_sync(
            config=config,
            worker=worker_id,
            conversation_id=conversation_id,
            message=message,
            max_turns=max_turns,
            timeout_seconds=timeout,
            wait=False,
        )
    except (RuntimeError, httpx.HTTPError) as exc:
        print_orch_exception(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] Started conversation {conversation_id} with {worker_id}.")
    console.print(f"[Orch] Max rounds: {rounds} ({max_turns} turns)")
    console.print("[Orch] Reply will arrive as a [Orchlink] message in the lead Pi chat — no polling needed.")
    console.print("[Orch] This is turn 1, not a final answer. Continue with: orch say " + conversation_id + " -m \"...\"")
    console.print("[Orch] Close only when the discussion reaches a decision: orch close " + conversation_id + " -m \"...\"")


@app.command(help="Send the next message in an open Talk Mode conversation.")
def say(
    conversation_id: str,
    message: Annotated[str, typer.Option("--msg", "--message", "-m", help="Next Talk message to send.")],
    timeout: Annotated[int, typer.Option("--timeout", help="Conversation turn timeout in seconds.")] = 1800,
) -> None:
    require_nonempty_talk_message(message, "Say")
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        state = conversation_state(config, conversation_id)
        if state is None:
            console.print(f"[Orch] Conversation not found: {conversation_id}")
            raise typer.Exit(1)
        if state.get("status") != "OPEN":
            console.print(f"[Orch] Conversation {conversation_id} is {state.get('status')}.")
            raise typer.Exit(1)
        turn = int(state.get("turn") or 1) + 1
        max_turns = int(state.get("max_turns") or 6)
        if turn > max_turns:
            console.print(f"[Orch] Conversation {conversation_id} reached max turns ({max_turns}).")
            raise typer.Exit(1)
        say_talk_sync(
            config=config,
            worker="work",
            conversation_id=conversation_id,
            message=message,
            turn=turn,
            max_turns=max_turns,
            timeout_seconds=timeout,
        )
    except (RuntimeError, httpx.HTTPError) as exc:
        print_orch_exception(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] Sent turn {turn}/{max_turns} to work for {conversation_id}.")
    console.print("[Orch] Reply will arrive as a [Orchlink] message in the lead Pi chat — no polling needed.")
    console.print("[Orch] Continue with another orch say if the discussion is not resolved; close when there is a decision.")


@app.command(help="Close a Talk Mode conversation with a decision or summary.")
def close(
    conversation_id: str,
    message: Annotated[str, typer.Option("--msg", "--message", "-m", help="Optional final decision or summary.")] = "",
    timeout: Annotated[int, typer.Option("--timeout", help="Close message timeout in seconds.")] = 1800,
) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        state = conversation_state(config, conversation_id)
        if state is None:
            console.print(f"[Orch] Conversation not found: {conversation_id}")
            raise typer.Exit(1)
        turn = min(int(state.get("turn") or 1) + 1, int(state.get("max_turns") or 6))
        max_turns = int(state.get("max_turns") or 6)
        close_talk_sync(
            config=config,
            worker="work",
            conversation_id=conversation_id,
            message=message,
            turn=turn,
            max_turns=max_turns,
            timeout_seconds=timeout,
        )
    except (RuntimeError, httpx.HTTPError) as exc:
        print_orch_exception(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] Closed conversation {conversation_id}.")
    if message:
        console.print(message)


@app.command(help="Show recent tasks and Talk conversations for the current project.")
def jobs(
    limit: Annotated[int, typer.Option("--limit", help="Maximum number of recent jobs to show.")] = 50,
    active: Annotated[bool, typer.Option("--active", help="Show only pending/running/open work.")] = False,
    status: Annotated[str | None, typer.Option("--status", help="Show only jobs with this status.")] = None,
    kind: Annotated[str | None, typer.Option("--kind", help="Show only task or talk jobs.")] = None,
    item_id: Annotated[str | None, typer.Option("--id", help="Show one task/conversation/message ID.")] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print raw jobs JSON.")] = False,
) -> None:
    normalized_kind = kind.lower() if kind else None
    if normalized_kind and normalized_kind not in {"task", "talk"}:
        console.print("[Orch] --kind must be 'task' or 'talk'.")
        raise typer.Exit(1)

    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        body = broker_get_sync(config, jobs_query(config, limit=limit, active=active, status=status, kind=normalized_kind, item_id=item_id))
    except (RuntimeError, httpx.HTTPError) as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc
    body["jobs"] = [sanitize_job(job) for job in filter_jobs(body.get("jobs", []), active=active, status=status, kind=normalized_kind, item_id=item_id)[:limit]]
    if json_output:
        console.print_json(json.dumps(body))
        return

    console.print("ID\tKIND\tMODE\tSTATUS\tUPDATED\tROUTE\tPREVIEW")
    for job in body.get("jobs", []):
        preview = str(job.get("preview") or job.get("last_message_preview") or "")
        console.print(
            f"{job_id(job)}\t{job_kind(job)}\t{job.get('mode', '-')}\t{job.get('status', '-')}\t"
            f"{human_age(job.get('updated_at') or job.get('created_at'))}\t{job_route(job)}\t{preview}"
        )
        activity = job_activity_line(job)
        if activity:
            console.print(f"  last activity: {activity}")


@app.command(help="Exit 0 if the worker lane is idle; exit 1 if active work exists.")
def idle(limit: Annotated[int, typer.Option("--limit", help="Maximum number of recent jobs to inspect.")] = 50) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        body = broker_get_sync(config, jobs_query(config, limit=limit, active=True))
    except (RuntimeError, httpx.HTTPError) as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc

    pending = blocking_jobs(body.get("jobs", []))
    if not pending:
        console.print("[Orch] Worker idle: no pending tasks or open talks.")
        return

    console.print("[Orch] Worker is not idle. Pending worker work exists:")
    for job in pending:
        preview = str(job.get("preview") or job.get("last_message_preview") or "")
        console.print(f"- {job_id(job)} {job_kind(job)} {job.get('mode', '-')} {job.get('status', '-')}: {preview}")
        activity = job_activity_line(job)
        if activity:
            console.print(f"  last activity: {activity}")
    console.print("[Orch] Do not run dependent full tests or final conclusions yet.")
    raise typer.Exit(1)


@app.command(help="Show recent worker activity for a long-running task or conversation.")
def peek(
    item_id: str,
    limit: Annotated[int, typer.Option("--limit", min=1, max=100, help="Maximum activity rows to show.")] = 10,
) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        try:
            body = broker_get_sync(config, task_activity_query(config, item_id, limit=limit))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                raise
            body = broker_get_sync(config, activity_query(config, item_id=item_id, limit=limit))
    except (RuntimeError, httpx.HTTPError) as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc

    activity = body.get("activity") or []
    if not activity:
        console.print(f"[Orch] No worker activity recorded for {item_id}.")
        console.print("[Orch] If the task is pending, the worker may not have picked it up yet or the broker/session is stale.")
        return

    console.print(f"[Orch] Recent worker activity for {item_id}:")
    for item in activity:
        console.print(f"- {format_activity(item)}")


@app.command("get", help="Print a completed task result, or a conversation summary for a conversation ID.")
def get_command(item_id: str) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        body = broker_get_sync(config, f"/v1/tasks/{item_id}{project_query(config)}")
        validate_task_body_project(config, body, item_id)
        if body.get("status") == "missing":
            conversation = conversation_state(config, item_id)
            if conversation is not None:
                _print_conversation_body(conversation)
                return
    except (RuntimeError, httpx.HTTPError) as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc
    _print_task_body(body)


@app.command("wait", help="Wait for one exact task result; timeout does not cancel the task.")
def wait_command(
    task_id: str,
    timeout: Annotated[int, typer.Option("--timeout", help="Maximum seconds to wait in this shell.")] = 1800,
    progress: Annotated[bool, typer.Option("--progress/--no-progress", help="Print worker activity while waiting.")] = True,
    poll_seconds: Annotated[int, typer.Option("--poll-seconds", min=1, max=60, help="Seconds between progress polls.")] = 5,
) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
    except RuntimeError as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc

    deadline = time.monotonic() + timeout
    last_activity_id = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _print_task_body({"status": "WAIT_TIMEOUT", "task_id": task_id, "error": "No task result arrived before the wait timeout."})
            return
        wait_seconds = timeout if not progress else max(1, min(poll_seconds, int(remaining)))
        try:
            body = broker_get_sync(config, f"/v1/tasks/{task_id}/wait?timeout_seconds={wait_seconds}{project_query(config, '&')}")
        except httpx.HTTPError as exc:
            console.print(f"[Orch] {exc}")
            raise typer.Exit(1) from exc
        if body.get("status") != "WAIT_TIMEOUT":
            returned_task_id = body.get("task_id")
            if returned_task_id and str(returned_task_id) != task_id:
                console.print(f"[Orch] Broker returned result for {returned_task_id} while waiting for {task_id}; ignoring stale response.")
                raise typer.Exit(1)
            validate_task_body_project(config, body, task_id)
            _print_task_body(body)
            if body.get("status") == "missing":
                raise typer.Exit(1)
            return
        if not progress:
            _print_task_body(body)
            return
        try:
            activity_body = broker_get_sync(config, task_activity_query(config, task_id, limit=5))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                activity_body = {"activity": []}
            else:
                try:
                    activity_body = broker_get_sync(config, activity_query(config, item_id=task_id, limit=5))
                except httpx.HTTPError:
                    activity_body = {"activity": []}
        except httpx.HTTPError:
            activity_body = {"activity": []}
        for activity in activity_body.get("activity", []):
            activity_id = int(activity.get("id") or 0)
            if activity_id <= last_activity_id:
                continue
            console.print(f"[Orch] Worker activity: {format_activity(activity)}")
            last_activity_id = activity_id


@app.command(help="Mark active work CANCELLED and ask Pi to stop the current turn.")
def cancel(
    item_id: str,
    reason: Annotated[str, typer.Option("--reason", "-m", help="Reason recorded with the cancellation.")] = "Cancelled by lead.",
) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        body = broker_post_sync(config, f"/v1/jobs/{item_id}/cancel", {"reason": reason, "project_id": current_project_id(config)})
    except (RuntimeError, httpx.HTTPError) as exc:
        print_orch_exception(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] Cancelled {item_id}.")
    console.print("[Orch] Note: cancel marks broker work CANCELLED and asks Pi to abort the current turn. Pi can stop before the next tool call; an already-running shell command may only stop if Pi's abort reaches it.")
    cancelled = body.get("cancelled") or []
    if cancelled:
        console.print(f"[Orch] Messages: {', '.join(str(item) for item in cancelled)}")


@app.command(help="Update this Orchlink install from git and reinstall the package.")
def update(
    ref: Annotated[str, typer.Option("--ref", help="Git branch, tag, or commit to update to.")] = "main",
    reinstall_only: Annotated[bool, typer.Option("--reinstall-only", help="Only reinstall the current checkout into the venv.")] = False,
) -> None:
    console.print(f"[Orch] Updating Orchlink in {PROJECT_ROOT}")
    try:
        run_update(ref=ref, reinstall_only=reinstall_only)
    except FileNotFoundError as exc:
        console.print(f"[Orch] Missing command: {exc.filename}")
        raise typer.Exit(1) from exc
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        console.print(f"[Orch] Update failed: {exc}")
        raise typer.Exit(1) from exc
    console.print("[Orch] Update complete.")
    console.print("[Orch] In each Orchlink project, refresh .orch files and restart sessions:")
    console.print("[Orch]   orch init --refresh-skills")
    console.print("[Orch]   orch stop")
    console.print("[Orch]   orch lead --new")
    console.print("[Orch]   orch work --new")


@app.command(help="Watch raw broker events for debugging worker activity and routing.")
def watch(
    interval_seconds: Annotated[float, typer.Option("--interval-seconds", help="Seconds between event polls.")] = 2.0,
    iterations: Annotated[int, typer.Option("--iterations", help="0 means watch forever.")] = 0,
    limit: Annotated[int, typer.Option("--limit", help="Maximum events to fetch per poll.")] = 50,
) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
    except RuntimeError as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc

    last_event_id = 0
    count = 0
    while True:
        body = fetch_events_sync(broker_url(config), broker_api_key(config), since=last_event_id, limit=limit, project_id=current_project_id(config))
        for event in body.get("events", []):
            console.print(format_event(event))
            console.print()
        last_event_id = int(body.get("last_event_id", last_event_id))
        count += 1
        if iterations and count >= iterations:
            return
        time.sleep(interval_seconds)


@app.command(help="Stop the background broker process for this project.")
def stop() -> None:
    config = load_project_or_exit()
    stop_pid_file(broker_pid_path(config), "broker")


@app.command(help="Print raw broker status JSON for debugging; not normal coordination output.")
def status(
    broker_url_option: Annotated[str, typer.Option("--broker-url", help="Broker base URL to query.")] = "http://127.0.0.1:8787",
    api_key: Annotated[str, typer.Option("--api-key", help="Broker API key.")] = "change-me",
    project_id: Annotated[str | None, typer.Option("--project-id", help="Filter to one project_id.")] = None,
    all_projects: Annotated[bool, typer.Option("--all-projects", help="Do not apply the current project_id filter.")] = False,
    task_id: Annotated[str | None, typer.Option("--task", help="Filter jobs/messages/events to one task ID.")] = None,
    since_id: Annotated[int, typer.Option("--since-id", min=0, help="Only include events after this event ID.")] = 0,
    limit: Annotated[int, typer.Option("--limit", min=1, max=500, help="Limit jobs and events in status output.")] = 20,
) -> None:
    effective_project_id = project_id
    if effective_project_id is None and not all_projects:
        try:
            effective_project_id = current_project_id(load_project_config())
        except ProjectConfigError:
            effective_project_id = None
    response = fetch_status_sync(
        broker_url_option,
        api_key,
        project_id=effective_project_id,
        task_id=task_id,
        since=since_id,
        limit=limit,
    )
    console.print_json(json.dumps(response))


@app.command(help="Check local Orchlink project setup, broker compatibility, and generated skills.")
def doctor() -> None:
    console.print("Orchlink doctor")
    console.print(f"Package file: {Path(__file__).resolve()}")
    console.print(f"Project root: {PROJECT_ROOT}")

    try:
        config = load_project_config()
    except ProjectConfigError:
        console.print(".orch/project.yaml: missing")
    else:
        connector = PiConnector(config)
        info = broker_info(broker_url(config))
        console.print(f".orch/project.yaml: found ({config.get('_config_path')})")
        console.print(f"Project ID: {current_project_id(config)}")
        console.print(f"Broker URL: {broker_url(config)}")
        console.print(f"Broker reachable: {'yes' if info else 'no'}")
        if info:
            console.print(f"Broker version: {info.get('version', 'unknown')} ({'compatible' if broker_compatible(info) else 'stale'})")
        console.print("API key configured: yes")
        console.print(f"Pi command: {connector.pi_command()} ({'found' if connector.check_available() else 'missing'})")
        stale = False
        missing = False
        for skill_name, expected in (("lead.md", LEAD_SKILL), ("work.md", WORK_SKILL)):
            path = project_root(config) / ".orch" / "skills" / skill_name
            if not path.is_file():
                status_text = "missing"
                missing = True
            elif path.read_text(encoding="utf-8") != expected:
                status_text = "stale"
                stale = True
            else:
                status_text = "current"
            console.print(f"{skill_name}: {status_text}")
        if stale or missing:
            console.print("Project .orch files: stale")
            console.print("Run: orch init --refresh-skills")
        else:
            console.print("Project .orch files: current")

    console.print("CLI symlink: ~/.local/bin/orch -> <orchlink-repo>/.venv/bin/orch")


if __name__ == "__main__":
    app()
