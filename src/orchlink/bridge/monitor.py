from typing import Any

import httpx


def _headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key}


async def fetch_status(broker_url: str, api_key: str) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=broker_url) as client:
        response = await client.get("/v1/status", headers=_headers(api_key))
        response.raise_for_status()
        return response.json()


async def fetch_events(
    broker_url: str,
    api_key: str,
    since: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=broker_url) as client:
        response = await client.get(
            f"/v1/events?since={since}&limit={limit}",
            headers=_headers(api_key),
        )
        response.raise_for_status()
        return response.json()


def format_event(event: dict[str, Any]) -> str:
    timestamp = str(event.get("time", ""))
    timestamp = timestamp[11:19] if len(timestamp) >= 19 else timestamp
    from_agent = event.get("from_agent") or event.get("agent_id") or "-"
    to_agent = event.get("to_agent") or "-"
    message_type = event.get("message_type") or event.get("type") or "EVENT"
    task_id = event.get("task_id") or "-"
    preview = event.get("preview") or ""
    if to_agent != "-":
        first_line = f"[{timestamp}] {from_agent} → {to_agent} {message_type} {task_id}"
    else:
        first_line = f"[{timestamp}] {from_agent} {message_type} {task_id}"
    return f"{first_line}\n{preview}" if preview else first_line
