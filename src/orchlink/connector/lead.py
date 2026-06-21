import asyncio
from typing import Any

import httpx

from orchlink.connector.pi_connector import PiConnector
from orchlink.project.config import broker_api_key, broker_url, role_agent_id


def lead_registration(config: dict[str, Any]) -> dict[str, Any]:
    project_id = str(config.get("project_id"))
    return {
        "project_id": project_id,
        "agent_id": role_agent_id(config, "lead"),
        "role": "lead",
        "display_name": "Lead",
        "capabilities": ["delegation", "review", "talk"],
    }


async def register_lead(config: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=broker_url(config)) as client:
        response = await client.post(
            "/v1/agents/register",
            headers={"X-API-Key": broker_api_key(config)},
            json=lead_registration(config),
        )
        response.raise_for_status()
        return response.json()


def register_lead_sync(config: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(register_lead(config))


def start_lead_session(config: dict[str, Any]) -> int:
    return PiConnector(config).run_lead()
