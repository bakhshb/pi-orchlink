import asyncio
from typing import Any

import httpx

from orchlink.bridge.ask import build_task_envelope as build_project_task_envelope


def _legacy_config(broker_url: str, api_key: str, from_agent: str) -> dict[str, Any]:
    project_id = from_agent.rsplit(".", 1)[0] if "." in from_agent else "default"
    return {
        "project_id": project_id,
        "broker": {"url": broker_url, "api_key": api_key},
        "lead": {"agent_id": from_agent},
        "work": {"agent_id": f"{project_id}.work"},
        "scope": {"allowed": ["**/*"], "forbidden": []},
    }


def build_task_envelope(
    worker_id: str,
    task_id: str,
    message: str,
    from_agent: str = "orchestrator",
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    config = _legacy_config("http://127.0.0.1:8787", "change-me", from_agent)
    return build_project_task_envelope(config, worker_id, task_id, message, timeout_seconds)


async def ask_worker(
    broker_url: str,
    api_key: str,
    worker_id: str,
    task_id: str,
    message: str,
    from_agent: str = "orchestrator",
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    envelope = build_project_task_envelope(
        _legacy_config(broker_url, api_key, from_agent),
        worker_id,
        task_id,
        message,
        timeout_seconds,
    )
    async with httpx.AsyncClient(base_url=broker_url, timeout=None) as client:
        response = await client.post(
            "/v1/messages/send-and-wait",
            headers={"X-API-Key": api_key},
            json=envelope,
        )
        response.raise_for_status()
        return response.json()


def ask_worker_sync(**kwargs: Any) -> dict[str, Any]:
    return asyncio.run(ask_worker(**kwargs))
