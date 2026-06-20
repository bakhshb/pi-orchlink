import asyncio
import uuid
from typing import Any

import httpx

from orchlink.broker.protocol import PROTOCOL_VERSION
from orchlink.project.config import broker_api_key, broker_url, resolve_agent_id, role_agent_id


DEFAULT_EXPECTED_REPLY = [
    "summary",
    "files inspected",
    "findings",
    "risks",
    "recommended next step",
]


def build_task_envelope(
    config: dict[str, Any],
    worker: str,
    task_id: str,
    message: str,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    project_id = str(config.get("project_id") or "default")
    correlation_id = f"req-{uuid.uuid4()}"
    scope = config.get("scope") or {"allowed": ["**/*"], "forbidden": []}
    return {
        "protocol": PROTOCOL_VERSION,
        "message_id": f"msg-{uuid.uuid4()}",
        "correlation_id": correlation_id,
        "project_id": project_id,
        "conversation_id": f"{project_id}-default",
        "task_id": task_id,
        "from_agent": role_agent_id(config, "lead"),
        "to_agent": resolve_agent_id(config, worker),
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": timeout_seconds,
        "payload": {
            "intent": message,
            "scope": scope,
            "constraints": [],
            "expected_reply": DEFAULT_EXPECTED_REPLY,
        },
    }


async def ask_worker(
    config: dict[str, Any],
    worker: str,
    task_id: str,
    message: str,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    envelope = build_task_envelope(
        config=config,
        worker=worker,
        task_id=task_id,
        message=message,
        timeout_seconds=timeout_seconds,
    )
    async with httpx.AsyncClient(base_url=broker_url(config), timeout=None) as client:
        response = await client.post(
            "/v1/messages/send-and-wait",
            headers={"X-API-Key": broker_api_key(config)},
            json=envelope,
        )
        response.raise_for_status()
        return response.json()


def ask_worker_sync(
    config: dict[str, Any],
    worker: str,
    task_id: str,
    message: str,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    return asyncio.run(
        ask_worker(
            config=config,
            worker=worker,
            task_id=task_id,
            message=message,
            timeout_seconds=timeout_seconds,
        )
    )
