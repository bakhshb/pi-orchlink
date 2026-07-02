"""Synchronous wrappers over the broker HTTP API and broker lifecycle.

Transport helpers wrap async status/event calls and ``BrokerClient`` requests.
Lifecycle helpers delegate to :mod:`orchlink.client.process` so filesystem and
subprocess handling stays separate from command presentation.
"""

from __future__ import annotations

import asyncio
from typing import Any

from orchlink.client.broker_client import BrokerClient
from orchlink.client.monitor import fetch_events, fetch_status
from orchlink.client.process import (
    broker_compatible,
    broker_info,
    start_background_broker,
    stale_broker_message,
)
from orchlink.project.config import (
    broker_auto_start,
    broker_url,
)


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
    return BrokerClient(config).get(path)


def broker_post_sync(config: dict[str, Any], path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return BrokerClient(config).post(path, body)


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


__all__ = [
    "fetch_status_sync",
    "fetch_events_sync",
    "broker_get_sync",
    "broker_post_sync",
    "ensure_broker_running",
]
