"""Broker-process lifecycle helpers.

This module owns PID files, log paths, broker health checks, and background
broker startup. It is intentionally separate from the HTTP transport helpers in
:mod:`orchlink.client.sync`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from orchlink.broker.main import BROKER_CAPABILITIES, VERSION as BROKER_VERSION
from orchlink.project.config import (
    broker_api_key,
    broker_auto_stop,
    broker_host,
    broker_port,
    broker_require_peer_sessions,
    broker_session_grace_seconds,
    broker_session_heartbeat_interval_seconds,
    broker_store_backend,
    broker_store_path,
    broker_url,
    project_root,
    run_dir,
)


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
        "Stop the old broker, then restart fresh Pi sessions: orch stop --all; orch lead --new; orch work --new"
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
    env["ORCHLINK_AUTO_STOP"] = "true" if broker_auto_stop(config) else "false"
    env["ORCHLINK_REQUIRE_PEER_SESSIONS"] = "true" if broker_require_peer_sessions(config) else "false"
    env["ORCHLINK_SESSION_HEARTBEAT_INTERVAL_SECONDS"] = str(broker_session_heartbeat_interval_seconds(config))
    env["ORCHLINK_SESSION_GRACE_SECONDS"] = str(broker_session_grace_seconds(config))
    env["ORCHLINK_STORE_BACKEND"] = broker_store_backend(config)
    store_path = Path(broker_store_path(config))
    if not store_path.is_absolute():
        store_path = project_root(config) / store_path
    env["ORCHLINK_STORE_PATH"] = str(store_path)
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


__all__ = [
    "broker_info",
    "broker_health",
    "broker_compatible",
    "stale_broker_message",
    "broker_pid_path",
    "broker_log_path",
    "pid_is_running",
    "start_background_broker",
]
