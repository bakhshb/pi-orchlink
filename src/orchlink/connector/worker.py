import asyncio
from typing import Any

from rich.console import Console

from orchlink.bridge.listener import run_worker_loop
from orchlink.project.config import role_agent_id


def worker_registration(config: dict[str, Any]) -> dict[str, Any]:
    project_id = str(config.get("project_id"))
    return {
        "project_id": project_id,
        "agent_id": role_agent_id(config, "work"),
        "role": "worker",
        "display_name": "Worker",
        "capabilities": ["inspection", "implementation", "tests", "talk"],
    }


def run_worker_session(config: dict[str, Any], once: bool = False, console: Console | None = None) -> None:
    asyncio.run(run_worker_loop(config, once=once, console=console))
