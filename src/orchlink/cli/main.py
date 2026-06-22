import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Annotated, Any

import httpx
import typer
import uvicorn
import yaml
from rich.console import Console

from orchlink.bridge.ask import (
    ask_worker_sync as project_ask_worker_sync,
    close_talk_sync,
    infer_task_mode,
    send_worker_sync,
    say_talk_sync,
    start_talk_sync,
)
from orchlink.bridge.listener import run_worker_loop
from orchlink.bridge.monitor import fetch_events, fetch_status, format_event
from orchlink.bridge.orchestrator_bridge import ask_worker_sync
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
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config"

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


def resolve_config_dir(config_dir: Path | None = None) -> Path:
    if config_dir is not None:
        return config_dir
    env_config_dir = os.getenv("ORCHLINK_CONFIG_DIR")
    if env_config_dir:
        return Path(env_config_dir)
    return DEFAULT_CONFIG_DIR


def load_role_config(role: str, config_dir: Path | None = None) -> dict[str, Any]:
    path = resolve_config_dir(config_dir) / f"{role}.yaml"
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    return data


def config_api_key(config: dict[str, Any]) -> str:
    return os.getenv("ORCHLINK_API_KEY") or str(config.get("api_key", "change-me"))


def config_broker_url(config: dict[str, Any]) -> str:
    return str(config.get("broker_url", "http://127.0.0.1:8787"))


async def register_agent(config: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=config_broker_url(config)) as client:
        response = await client.post(
            "/v1/agents/register",
            headers={"X-API-Key": config_api_key(config)},
            json={
                "project_id": str(config.get("project_id", "default")),
                "agent_id": config.get("agent_id"),
                "role": config.get("role"),
                "display_name": config.get("display_name", config.get("agent_id")),
                "capabilities": config.get("capabilities", []),
            },
        )
        response.raise_for_status()
        return response.json()


def register_agent_sync(config: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(register_agent(config))


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


def fetch_status_sync(url: str, api_key: str) -> dict[str, Any]:
    return asyncio.run(fetch_status(url, api_key))


def fetch_events_sync(url: str, api_key: str, since: int = 0, limit: int = 50) -> dict[str, Any]:
    return asyncio.run(fetch_events(url, api_key, since=since, limit=limit))


def broker_get_sync(config: dict[str, Any], path: str) -> dict[str, Any]:
    with httpx.Client(base_url=broker_url(config), timeout=None) as client:
        response = client.get(path, headers={"X-API-Key": broker_api_key(config)})
        response.raise_for_status()
        return response.json()


def next_conversation_id(config: dict[str, Any]) -> str:
    try:
        body = broker_get_sync(config, "/v1/jobs?limit=500")
    except httpx.HTTPError:
        return "C001"
    highest = 0
    for job in body.get("jobs", []):
        value = str(job.get("conversation_id") or "")
        if len(value) == 4 and value.startswith("C") and value[1:].isdigit():
            highest = max(highest, int(value[1:]))
    return f"C{highest + 1:03d}"


BLOCKING_JOB_STATUSES = {"PENDING", "QUEUED", "DELIVERED", "RUNNING", "IN_PROGRESS", "OPEN"}


def conversation_state(config: dict[str, Any], conversation_id: str) -> dict[str, Any] | None:
    body = broker_get_sync(config, "/v1/jobs?limit=500")
    for job in body.get("jobs", []):
        if job.get("conversation_id") == conversation_id:
            return job
    return None


def blocking_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [job for job in jobs if str(job.get("status") or "").upper() in BLOCKING_JOB_STATUSES]


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


def broker_health(url: str) -> bool:
    try:
        response = httpx.get(f"{url.rstrip('/')}/health", timeout=0.5)
        return response.status_code == 200 and response.json().get("status") == "ok"
    except Exception:
        return False


def broker_pid_path(config: dict[str, Any]) -> Path:
    return run_dir(config) / "broker.pid"


def broker_log_path(config: dict[str, Any]) -> Path:
    return run_dir(config) / "broker.log"


def worker_listener_pid_path(config: dict[str, Any]) -> Path:
    return run_dir(config) / "work-listener.pid"


def worker_listener_log_path(config: dict[str, Any]) -> Path:
    return run_dir(config) / "work-listener.log"


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
        if broker_health(url):
            return
        if process.poll() is not None:
            raise RuntimeError(f"Broker exited during startup. See {log_path}")
        time.sleep(0.1)
    raise RuntimeError(f"Broker did not become healthy. See {log_path}")


def ensure_broker_running(config: dict[str, Any]) -> None:
    url = broker_url(config)
    if broker_health(url):
        return
    if not broker_auto_start(config):
        raise RuntimeError(f"Broker is not reachable at {url} and auto_start is disabled.")
    start_background_broker(config)


def start_background_worker_listener(config: dict[str, Any]) -> None:
    pid_path = worker_listener_pid_path(config)
    if pid_path.is_file():
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = 0
        if pid and pid_is_running(pid):
            return
        pid_path.unlink(missing_ok=True)

    directory = run_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    log_path = worker_listener_log_path(config)
    command = [sys.executable, "-m", "orchlink.cli.main", "work-listen"]
    with log_path.open("ab") as log_file:
        process = subprocess.Popen(
            command,
            cwd=project_root(config),
            env=os.environ.copy(),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    pid_path.write_text(str(process.pid), encoding="utf-8")
    time.sleep(0.2)
    if process.poll() is not None:
        pid_path.unlink(missing_ok=True)
        raise RuntimeError(f"Worker listener exited during startup. See {log_path}")


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


@broker_app.command("run")
def broker_run(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8787,
    reload: Annotated[bool, typer.Option("--reload")] = False,
) -> None:
    console.print(f"[Orch] Starting broker: http://{host}:{port}")
    uvicorn.run("orchlink.broker.main:app", host=host, port=port, reload=reload)


@app.command("init")
def init_command(
    project_id: Annotated[str | None, typer.Option("--project-id")] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite config and skills.")] = False,
    refresh_skills: Annotated[bool, typer.Option("--refresh-skills", help="Rewrite lead/work skills without changing project config.")] = False,
) -> None:
    paths = init_project(Path.cwd(), project_id=project_id, force=force, refresh_skills=refresh_skills)
    console.print(f"[Orch] Initialized {paths['orch_dir']}")
    console.print(f"[Orch] Config: {paths['config']}")
    console.print(f"[Orch] Lead skill: {paths['lead_skill']}")
    console.print(f"[Orch] Worker skill: {paths['work_skill']}")


@app.command()
def lead(
    no_pi: Annotated[bool, typer.Option("--no-pi", help="Prepare/register but do not launch Pi.")] = False,
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
        if no_pi:
            return
        exit_code = PiConnector(config).run_lead()
    except (RuntimeError, PiConnectorError, httpx.HTTPError) as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc
    raise typer.Exit(exit_code)


@app.command()
def work(
    once: Annotated[bool, typer.Option("--once", help="Process at most one poll/message then exit without launching Pi.")] = False,
    no_pi: Annotated[bool, typer.Option("--no-pi", help="Run only the task listener; do not launch the visible Pi session.")] = False,
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

        if once or no_pi:
            console.print("[Orch] Waiting for tasks...")
            asyncio.run(run_worker_loop(config, once=once, console=console, register=False))
            return

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


@app.command("work-listen", hidden=True)
def work_listen(
    once: Annotated[bool, typer.Option("--once")] = False,
) -> None:
    config = load_project_or_exit()
    ensure_broker_running(config)
    asyncio.run(run_worker_loop(config, once=once, console=None, register=True))


def print_async_guidance(config: dict[str, Any], worker_id: str, task_id: str) -> None:
    console.print(f"[Orch] Sent {task_id} to {worker_id}.")
    console.print("[Orch] Async mode: worker scope is pending.")
    console.print("[Orch] Check status: orch jobs")
    console.print(f"[Orch] Wait: orch wait {task_id}")
    console.print(f"[Orch] Read result: orch get {task_id}")


@app.command()
def ask(
    worker_id: str,
    task_id: Annotated[str, typer.Option("--task", "--task-id", "-t")],
    message: Annotated[str, typer.Option("--msg", "--message", "-m")],
    config_dir: Annotated[Path | None, typer.Option("--config-dir")] = None,
    timeout_seconds: Annotated[int, typer.Option("--timeout-seconds")] = 1800,
    wait: Annotated[bool, typer.Option("--wait/--no-wait", help="Wait in this shell for the reply. Use orch send for async tasks.")] = True,
) -> None:
    if config_dir is not None:
        config = load_role_config("orchestrator", config_dir)
        response = ask_worker_sync(
            broker_url=config_broker_url(config),
            api_key=config_api_key(config),
            worker_id=worker_id,
            task_id=task_id,
            message=message,
            from_agent=str(config.get("agent_id", "orchestrator")),
            timeout_seconds=timeout_seconds,
        )
    else:
        config = load_project_or_exit()
        try:
            ensure_broker_running(config)
            response = project_ask_worker_sync(
                config=config,
                worker=worker_id,
                task_id=task_id,
                message=message,
                timeout_seconds=timeout_seconds,
                wait=wait,
            )
        except (RuntimeError, httpx.HTTPError) as exc:
            print_orch_exception(exc)
            raise typer.Exit(1) from exc
    if config_dir is None and not wait:
        print_async_guidance(config, worker_id, task_id)
    console.print_json(json.dumps(response))


@app.command()
def send(
    worker_id: str,
    task_id: Annotated[str, typer.Option("--task", "--task-id", "-t")],
    message: Annotated[str, typer.Option("--msg", "--message", "-m")],
    timeout_seconds: Annotated[int, typer.Option("--timeout-seconds")] = 1800,
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
            timeout_seconds=timeout_seconds,
        )
    except (RuntimeError, httpx.HTTPError) as exc:
        print_orch_exception(exc)
        raise typer.Exit(1) from exc
    print_async_guidance(config, worker_id, task_id)


@app.command()
def task(task_id: str) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        status_body = fetch_status_sync(broker_url(config), broker_api_key(config))
        events_body = fetch_events_sync(broker_url(config), broker_api_key(config), limit=500)
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
    console.print(f"[Orch] Task {task_id}: {status_text}")
    reply = body.get("reply") or {}
    if reply:
        console.print(f"[Orch] Type: {reply.get('type', 'RESULT')}")
        payload = reply.get("payload") or {}
        summary = str(payload.get("summary") or payload.get("stdout") or "").strip()
        if summary:
            console.print(summary)
    elif body.get("job"):
        job = body["job"]
        console.print(f"[Orch] Route: {job.get('from_agent', '-')} → {job.get('to_agent', '-')}")
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


@app.command()
def talk(
    worker_id: str,
    message: Annotated[str, typer.Option("--msg", "--message", "-m")],
    rounds: Annotated[int, typer.Option("--rounds", "-r", min=1, max=12)] = 6,
    timeout_seconds: Annotated[int, typer.Option("--timeout-seconds")] = 1800,
) -> None:
    require_nonempty_talk_message(message, "Talk")
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        conversation_id = next_conversation_id(config)
        start_talk_sync(
            config=config,
            worker=worker_id,
            conversation_id=conversation_id,
            message=message,
            max_turns=rounds,
            timeout_seconds=timeout_seconds,
            wait=False,
        )
    except (RuntimeError, httpx.HTTPError) as exc:
        print_orch_exception(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] Started conversation {conversation_id} with {worker_id}.")
    console.print(f"[Orch] Max turns: {rounds}")
    console.print("[Orch] Waiting for worker reply in the lead Pi chat.")
    console.print("[Orch] This is turn 1, not a final answer. Continue with: orch say " + conversation_id + " -m \"...\"")
    console.print("[Orch] Close only when the discussion reaches a decision: orch close " + conversation_id + " -m \"...\"")


@app.command()
def say(
    conversation_id: str,
    message: Annotated[str, typer.Option("--msg", "--message", "-m")],
    timeout_seconds: Annotated[int, typer.Option("--timeout-seconds")] = 1800,
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
            timeout_seconds=timeout_seconds,
        )
    except (RuntimeError, httpx.HTTPError) as exc:
        print_orch_exception(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] Sent turn {turn}/{max_turns} to work for {conversation_id}.")
    console.print("[Orch] Waiting for worker reply in the lead Pi chat.")
    console.print("[Orch] Continue with another orch say if the discussion is not resolved; close when there is a decision.")


@app.command()
def close(
    conversation_id: str,
    message: Annotated[str, typer.Option("--msg", "--message", "-m")] = "",
    timeout_seconds: Annotated[int, typer.Option("--timeout-seconds")] = 1800,
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
            timeout_seconds=timeout_seconds,
        )
    except (RuntimeError, httpx.HTTPError) as exc:
        print_orch_exception(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] Closed conversation {conversation_id}.")
    if message:
        console.print(message)


@app.command()
def jobs(limit: Annotated[int, typer.Option("--limit")] = 50) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        body = broker_get_sync(config, f"/v1/jobs?limit={limit}")
    except (RuntimeError, httpx.HTTPError) as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc
    console.print("ID\tKIND\tMODE\tSTATUS\tROUTE\tCREATED\tPREVIEW")
    for job in body.get("jobs", []):
        job_id = job.get("task_id") or job.get("conversation_id") or "-"
        preview = str(job.get("preview") or job.get("last_message_preview") or "")
        console.print(
            f"{job_id}\t{job.get('kind', '-')}\t{job.get('mode', '-')}\t{job.get('status', '-')}\t"
            f"{job.get('from_agent', '-')} → {job.get('to_agent', '-')}\t{job.get('created_at', '-')}\t{preview}"
        )


@app.command()
def idle(limit: Annotated[int, typer.Option("--limit")] = 50) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        body = broker_get_sync(config, f"/v1/jobs?limit={limit}")
    except (RuntimeError, httpx.HTTPError) as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc

    pending = blocking_jobs(body.get("jobs", []))
    if not pending:
        console.print("[Orch] Worker idle: no pending tasks or open talks.")
        return

    console.print("[Orch] Worker is not idle. Pending worker work exists:")
    for job in pending:
        job_id = job.get("task_id") or job.get("conversation_id") or "-"
        preview = str(job.get("preview") or job.get("last_message_preview") or "")
        console.print(f"- {job_id} {job.get('kind', '-')} {job.get('mode', '-')} {job.get('status', '-')}: {preview}")
    console.print("[Orch] Do not run dependent full tests or final conclusions yet.")
    raise typer.Exit(1)


@app.command("get")
def get_command(item_id: str) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        body = broker_get_sync(config, f"/v1/tasks/{item_id}")
        if body.get("status") == "missing":
            conversation = conversation_state(config, item_id)
            if conversation is not None:
                _print_conversation_body(conversation)
                return
    except (RuntimeError, httpx.HTTPError) as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc
    _print_task_body(body)


@app.command("wait")
def wait_command(
    task_id: str,
    timeout_seconds: Annotated[int, typer.Option("--timeout-seconds")] = 1800,
) -> None:
    config = load_project_or_exit()
    try:
        ensure_broker_running(config)
        body = broker_get_sync(config, f"/v1/tasks/{task_id}/wait?timeout_seconds={timeout_seconds}")
    except (RuntimeError, httpx.HTTPError) as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc
    _print_task_body(body)


@app.command()
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


@app.command()
def watch(
    interval_seconds: Annotated[float, typer.Option("--interval-seconds")] = 2.0,
    iterations: Annotated[int, typer.Option("--iterations", help="0 means watch forever.")] = 0,
    limit: Annotated[int, typer.Option("--limit")] = 50,
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
        body = fetch_events_sync(broker_url(config), broker_api_key(config), since=last_event_id, limit=limit)
        for event in body.get("events", []):
            console.print(format_event(event))
            console.print()
        last_event_id = int(body.get("last_event_id", last_event_id))
        count += 1
        if iterations and count >= iterations:
            return
        time.sleep(interval_seconds)


@app.command()
def stop() -> None:
    config = load_project_or_exit()
    stop_pid_file(worker_listener_pid_path(config), "worker listener")
    stop_pid_file(broker_pid_path(config), "broker")


@app.command()
def start(
    role: str,
    config_dir: Annotated[Path | None, typer.Option("--config-dir")] = None,
    once: Annotated[bool, typer.Option("--once")] = False,
) -> None:
    config = load_role_config(role, config_dir)
    console.print(f"[Orchlink] Broker online: {config_broker_url(config)}")
    register_agent_sync(config)
    console.print(f"[Orchlink] Registered: {config.get('agent_id', role)}")

    if role == "orchestrator":
        for worker in config.get("workers", []):
            worker_id = worker.get("agent_id")
            console.print(f"[Orchlink] Available worker: {worker_id}")
        console.print("[Orchlink] Delegate with: orch ask work --task <TASK_ID> --msg <TASK_MESSAGE>")
        console.print("[Orchlink] Legacy: orchlink ask worker-backend --task-id <TASK_ID> --message <TASK_MESSAGE>")
        return

    if role == "worker-backend":
        console.print("[Orchlink] Waiting for tasks...")
        asyncio.run(run_worker_loop(config, once=once))
        return

    raise typer.BadParameter(f"Unknown role: {role}")


@app.command()
def status(
    broker_url_option: Annotated[str, typer.Option("--broker-url")] = "http://127.0.0.1:8787",
    api_key: Annotated[str, typer.Option("--api-key")] = "change-me",
) -> None:
    response = fetch_status_sync(broker_url_option, api_key)
    console.print_json(json.dumps(response))


@app.command()
def monitor(
    broker_url_option: Annotated[str, typer.Option("--broker-url")] = "http://127.0.0.1:8787",
    api_key: Annotated[str, typer.Option("--api-key")] = "change-me",
    interval_seconds: Annotated[float, typer.Option("--interval-seconds")] = 2.0,
    iterations: Annotated[int, typer.Option("--iterations")] = 1,
) -> None:
    for _ in range(iterations):
        response = fetch_status_sync(broker_url_option, api_key)
        console.print_json(json.dumps(response))
        if iterations > 1:
            time.sleep(interval_seconds)


@app.command()
def doctor(
    config_dir: Annotated[Path | None, typer.Option("--config-dir")] = None,
) -> None:
    resolved_config_dir = resolve_config_dir(config_dir)
    console.print("Orchlink doctor")
    console.print(f"Package file: {Path(__file__).resolve()}")
    console.print(f"Project root: {PROJECT_ROOT}")
    console.print(f"Config dir: {resolved_config_dir}")
    for filename in ("orchestrator.yaml", "worker-backend.yaml"):
        status_text = "found" if (resolved_config_dir / filename).is_file() else "missing"
        console.print(f"{filename}: {status_text}")

    try:
        config = load_project_config()
    except ProjectConfigError:
        console.print(".orch/project.yaml: missing")
    else:
        connector = PiConnector(config)
        console.print(f".orch/project.yaml: found ({config.get('_config_path')})")
        console.print(f"Broker URL: {broker_url(config)}")
        console.print(f"Broker reachable: {'yes' if broker_health(broker_url(config)) else 'no'}")
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

    console.print("Global CLI symlink: ~/.local/bin/orch -> <orchlink-repo>/.venv/bin/orch")
    console.print("Legacy CLI symlink: ~/.local/bin/orchlink -> <orchlink-repo>/.venv/bin/orchlink")


if __name__ == "__main__":
    app()
