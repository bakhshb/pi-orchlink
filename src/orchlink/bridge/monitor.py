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


def _short_agent(value: Any) -> str:
    text = str(value or "-")
    if "." in text:
        return text.rsplit(".", 1)[-1]
    return text


def format_event(event: dict[str, Any]) -> str:
    timestamp = str(event.get("time", ""))
    timestamp = timestamp[11:19] if len(timestamp) >= 19 else timestamp
    from_agent = _short_agent(event.get("from_agent") or event.get("agent_id"))
    to_agent = _short_agent(event.get("to_agent"))
    message_type = str(event.get("message_type") or event.get("type") or "EVENT")
    mode = str(event.get("mode") or "").upper()
    delivery = str(event.get("delivery") or "").upper()
    status = str(event.get("status") or "").upper()
    task_or_conversation = event.get("conversation_id") if message_type.startswith("CHAT_") else event.get("task_id")
    preview = event.get("preview") or ""

    parts = [f"[{timestamp}]", from_agent]
    if to_agent != "-":
        parts.extend(["→", to_agent])
    parts.extend([message_type, str(task_or_conversation or "-")])
    if message_type == "TASK":
        if mode:
            parts.append(mode)
        if delivery:
            parts.append(delivery)
    elif message_type in {"PLAN", "RESULT", "BLOCKER", "REVIEW"}:
        if status:
            parts.append(status)

    first_line = " ".join(part for part in parts if part)
    return f"{first_line}\n{preview}" if preview else first_line
