import asyncio
import uuid
from typing import Any

import httpx
from rich.console import Console

from orchlink.bridge.prompt_templates import render_worker_prompt
from orchlink.connector.pi_connector import PiConnector, PiRunResult
from orchlink.project.config import broker_api_key, broker_url, role_agent_id


REPLY_TYPES = {"PLAN", "RESULT", "BLOCKER"}
CHAT_REQUEST_TYPES = {"CHAT_START", "CHAT_TURN"}


def _is_project_config(config: dict[str, Any]) -> bool:
    return "work" in config and "broker" in config


def _worker_config(config: dict[str, Any]) -> dict[str, Any]:
    if not _is_project_config(config):
        return config
    work = dict(config.get("work") or {})
    work.setdefault("agent_id", role_agent_id(config, "work"))
    work.setdefault("role", "worker")
    work.setdefault("display_name", "Worker")
    work.setdefault("scope", config.get("scope") or {})
    return work


def _config_api_key(config: dict[str, Any]) -> str:
    if _is_project_config(config):
        return broker_api_key(config)
    return str(config.get("api_key", "change-me"))


def _config_broker_url(config: dict[str, Any]) -> str:
    if _is_project_config(config):
        return broker_url(config)
    return str(config.get("broker_url", "http://127.0.0.1:8787"))


def auth_headers(config: dict[str, Any]) -> dict[str, str]:
    return {"X-API-Key": _config_api_key(config)}


def detect_reply_type(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("TYPE:"):
            value = stripped.removeprefix("TYPE:").strip().split(" ", 1)[0]
            if value in REPLY_TYPES:
                return value
    return "RESULT"


def build_reply(
    message: dict[str, Any],
    config: dict[str, Any],
    run_result: Any,
) -> dict[str, Any]:
    worker_config = _worker_config(config)
    failed = not run_result.succeeded
    is_chat = message.get("type") in CHAT_REQUEST_TYPES
    reply_type = "CHAT_REPLY" if is_chat else ("BLOCKER" if failed else detect_reply_type(run_result.stdout))
    status = "FAILED" if failed else "DONE"
    turn = min(int(message.get("turn", 1)) + 1, int(message.get("max_turns", 6)))

    return {
        "protocol": message.get("protocol", "orch-a2a-v1"),
        "message_id": f"reply-{uuid.uuid4()}",
        "correlation_id": message["correlation_id"],
        "project_id": message.get("project_id", config.get("project_id", "default")),
        "conversation_id": message.get("conversation_id", "orchlink"),
        "task_id": message.get("task_id"),
        "from_agent": worker_config.get("agent_id", "work"),
        "to_agent": message["from_agent"],
        "type": reply_type,
        "status": status,
        "turn": turn,
        "max_turns": message.get("max_turns", 6),
        "requires_reply": False,
        "timeout_seconds": 1,
        "delivery": "conversation" if is_chat else message.get("delivery", "async"),
        "payload": {
            "mode": "TALK" if is_chat else (message.get("payload") or {}).get("mode"),
            "summary": run_result.stdout.strip() or run_result.stderr.strip(),
            "stdout": run_result.stdout,
            "stderr": run_result.stderr,
            "exit_code": run_result.exit_code,
            "timed_out": run_result.timed_out,
        },
    }


async def register_worker(client: httpx.AsyncClient, config: dict[str, Any]) -> dict[str, Any]:
    worker_config = _worker_config(config)
    response = await client.post(
        "/v1/agents/register",
        headers=auth_headers(config),
        json={
            "project_id": str(config.get("project_id", "default")),
            "agent_id": worker_config.get("agent_id", "work"),
            "role": "worker",
            "display_name": worker_config.get("display_name", "Worker"),
            "capabilities": worker_config.get("capabilities", ["inspection", "implementation", "tests"]),
        },
    )
    response.raise_for_status()
    return response.json()


async def process_one_message(
    client: httpx.AsyncClient,
    config: dict[str, Any],
    message: dict[str, Any],
    connector: PiConnector | None = None,
    console: Console | None = None,
) -> dict[str, Any]:
    worker_config = _worker_config(config)
    prompt = render_worker_prompt(message, worker_config)
    active_connector = connector or PiConnector(config)
    timeout_seconds = int(worker_config.get("timeout_seconds") or config.get("agent_timeout_seconds") or 1800)

    if console is not None:
        console.print(f"[Orch] Received {message.get('type')} {message.get('task_id')} from {message.get('from_agent')}")
        console.print("[Orch] Sending task to Pi worker session...")

    run_result: PiRunResult = await active_connector.run_worker_prompt(prompt, timeout_seconds)
    reply = build_reply(message, config, run_result)
    response = await client.post(
        f"/v1/messages/{message['message_id']}/reply",
        headers=auth_headers(config),
        json=reply,
    )
    response.raise_for_status()

    if console is not None:
        console.print("[Orch] Worker replied")
        console.print("[Orch] Reply sent to lead")

    return response.json()


async def run_worker_loop(
    config: dict[str, Any],
    once: bool = False,
    console: Console | None = None,
    connector: PiConnector | None = None,
    register: bool = True,
) -> None:
    wait_seconds = int(_worker_config(config).get("poll_wait_seconds") or 5)
    agent_id = str(_worker_config(config).get("agent_id") or role_agent_id(config, "work"))
    async with httpx.AsyncClient(base_url=_config_broker_url(config), timeout=None) as client:
        if register:
            await register_worker(client, config)
        while True:
            response = await client.get(
                f"/v1/agents/{agent_id}/next?wait_seconds={wait_seconds}",
                headers=auth_headers(config),
            )
            response.raise_for_status()
            body = response.json()
            if body.get("status") == "message":
                await process_one_message(client, config, body["message"], connector=connector, console=console)
                if once:
                    return
            elif once:
                return
            await asyncio.sleep(0)
