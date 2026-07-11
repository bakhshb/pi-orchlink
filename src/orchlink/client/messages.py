"""Lead→worker task and talk envelope helpers.

Builds Orchlink envelopes, posts them to the broker, and exposes synchronous
wrappers used by CLI and Goal Mode orchestration.
"""

import asyncio
import uuid
from typing import Any

import httpx

from orchlink.connector.pi_extension_pure import MODE_THINKING_DEFAULTS as SHARED_MODE_THINKING_DEFAULTS
from orchlink.connector.pi_extension_pure import THINKING_LEVELS as SHARED_THINKING_LEVELS
from orchlink.core.envelope import (
    DeliveryMode,
    MessageEnvelope,
    MessageType,
    PROTOCOL_VERSION,
    envelope_to_dict,
)
from orchlink.core.prompt_policy import TaskPromptPolicy
from orchlink.project.config import broker_api_key, broker_url, resolve_agent_id, role_agent_id


THINKING_LEVELS = set(SHARED_THINKING_LEVELS)
MODE_THINKING_DEFAULTS = dict(SHARED_MODE_THINKING_DEFAULTS)
TASK_PROMPT_POLICY = TaskPromptPolicy()
TALK_EXPECTED_REPLY: list[str] = []


def summarize_chat_topic(message: str, limit: int = 120) -> str:
    for line in message.splitlines():
        cleaned = line.strip()
        if cleaned:
            return cleaned if len(cleaned) <= limit else f"{cleaned[: limit - 1]}…"
    return "Talk Mode conversation"


def normalize_thinking_level(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    if normalized not in THINKING_LEVELS:
        raise ValueError("Thinking level must be one of: off, minimal, low, medium, high, xhigh")
    return normalized


def thinking_for_mode(mode: str | None, explicit: str | None = None) -> str | None:
    override = normalize_thinking_level(explicit)
    if override:
        return override
    return MODE_THINKING_DEFAULTS.get(str(mode or "").upper())


def _base_envelope_fields(config: dict[str, Any], to_agent: str, timeout_seconds: int) -> dict[str, Any]:
    project_id = str(config.get("project_id") or "default")
    return {
        "protocol": PROTOCOL_VERSION,
        "message_id": f"msg-{uuid.uuid4()}",
        "correlation_id": f"req-{uuid.uuid4()}",
        "project_id": project_id,
        "from_agent": role_agent_id(config, "lead"),
        "to_agent": to_agent,
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": timeout_seconds,
    }


def build_task_envelope(
    config: dict[str, Any],
    worker: str,
    task_id: str,
    message: str,
    timeout_seconds: int = 1800,
    delivery: DeliveryMode = "async",
    mode: str | None = None,
    thinking: str | None = None,
) -> MessageEnvelope:
    project_id = str(config.get("project_id") or "default")
    scope = config.get("scope") or {"allowed": ["**/*"], "forbidden": []}
    selected_mode = TASK_PROMPT_POLICY.normalize_mode(mode, message)
    return MessageEnvelope.model_validate(
        {
            **_base_envelope_fields(config, resolve_agent_id(config, worker), timeout_seconds),
            "conversation_id": f"{project_id}-tasks",
            "task_id": task_id,
            "type": "TASK",
            "delivery": delivery,
            "payload": {
                "mode": selected_mode,
                "intent": message,
                "thinking": thinking_for_mode(selected_mode, thinking),
                "scope": scope,
                "constraints": [],
                "expected_reply": TASK_PROMPT_POLICY.default_expected_reply(),
            },
        }
    )


def build_chat_envelope(
    config: dict[str, Any],
    worker: str,
    conversation_id: str,
    message: str,
    message_type: MessageType = "CHAT_START",
    turn: int = 1,
    max_turns: int = 6,
    timeout_seconds: int = 1800,
    transcript_preview: str = "",
    requires_reply: bool | None = None,
    thinking: str | None = None,
) -> MessageEnvelope:
    if requires_reply is None:
        requires_reply = message_type != "CHAT_CLOSE"
    return MessageEnvelope.model_validate(
        {
            **_base_envelope_fields(config, resolve_agent_id(config, worker), timeout_seconds),
            "conversation_id": conversation_id,
            "task_id": None,
            "type": message_type,
            "turn": turn,
            "max_turns": max_turns,
            "requires_reply": requires_reply,
            "delivery": "conversation",
            "payload": {
                "mode": "TALK",
                "topic": summarize_chat_topic(message) if message_type == "CHAT_START" else "",
                "message": message,
                "thinking": thinking_for_mode("TALK", thinking),
                "transcript_preview": transcript_preview,
                "constraints": TASK_PROMPT_POLICY.talk_constraints(),
                "expected_reply": TALK_EXPECTED_REPLY,
            },
        }
    )


async def post_envelope(config: dict[str, Any], envelope: MessageEnvelope, wait: bool = False) -> dict[str, Any]:
    endpoint = "/v1/messages/send-and-wait" if wait else "/v1/messages/send"
    async with httpx.AsyncClient(base_url=broker_url(config), timeout=None) as client:
        response = await client.post(
            endpoint,
            headers={"X-API-Key": broker_api_key(config), "X-Orchlink-Project-ID": str(config.get("project_id") or "default")},
            json=envelope_to_dict(envelope),
        )
        response.raise_for_status()
        body = response.json()
        if wait:
            return body
        return {
            **body,
            "correlation_id": envelope.correlation_id,
            "conversation_id": envelope.conversation_id,
            "task_id": envelope.task_id,
            "to_agent": envelope.to_agent,
        }


class WorkerBridge:
    """Object-oriented bridge for lead→worker task and talk envelopes."""

    def __init__(self, config: dict[str, Any], worker: str) -> None:
        self.config = config
        self.worker = worker

    async def send(
        self,
        task_id: str,
        message: str,
        timeout_seconds: int = 1800,
        thinking: str | None = None,
        wait: bool = False,
        delivery: DeliveryMode | None = None,
    ) -> dict[str, Any]:
        envelope = build_task_envelope(
            config=self.config,
            worker=self.worker,
            task_id=task_id,
            message=message,
            timeout_seconds=timeout_seconds,
            delivery=delivery or ("blocking" if wait else "async"),
            thinking=thinking,
        )
        return await post_envelope(self.config, envelope, wait=wait)

    async def start_talk(
        self,
        conversation_id: str,
        message: str,
        max_turns: int = 6,
        timeout_seconds: int = 1800,
        wait: bool = False,
        thinking: str | None = None,
    ) -> dict[str, Any]:
        envelope = build_chat_envelope(
            config=self.config,
            worker=self.worker,
            conversation_id=conversation_id,
            message=message,
            message_type="CHAT_START",
            turn=1,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            thinking=thinking,
        )
        return await post_envelope(self.config, envelope, wait=wait)

    async def say_talk(
        self,
        conversation_id: str,
        message: str,
        turn: int,
        max_turns: int,
        timeout_seconds: int = 1800,
        thinking: str | None = None,
    ) -> dict[str, Any]:
        envelope = build_chat_envelope(
            config=self.config,
            worker=self.worker,
            conversation_id=conversation_id,
            message=message,
            message_type="CHAT_TURN",
            turn=turn,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            thinking=thinking,
        )
        return await post_envelope(self.config, envelope, wait=False)

    async def close_talk(self, conversation_id: str, message: str, turn: int, max_turns: int, timeout_seconds: int = 1800) -> dict[str, Any]:
        envelope = build_chat_envelope(
            config=self.config,
            worker=self.worker,
            conversation_id=conversation_id,
            message=message,
            message_type="CHAT_CLOSE",
            turn=turn,
            max_turns=max_turns,
            timeout_seconds=timeout_seconds,
            requires_reply=False,
        )
        return await post_envelope(self.config, envelope, wait=False)


async def send_worker(
    config: dict[str, Any],
    worker: str,
    task_id: str,
    message: str,
    timeout_seconds: int = 1800,
    thinking: str | None = None,
    wait: bool = False,
    delivery: DeliveryMode | None = None,
) -> dict[str, Any]:
    return await WorkerBridge(config, worker).send(
        task_id, message, timeout_seconds, thinking=thinking, wait=wait, delivery=delivery
    )


async def start_talk(
    config: dict[str, Any],
    worker: str,
    conversation_id: str,
    message: str,
    max_turns: int = 6,
    timeout_seconds: int = 1800,
    wait: bool = False,
    thinking: str | None = None,
) -> dict[str, Any]:
    return await WorkerBridge(config, worker).start_talk(
        conversation_id, message, max_turns, timeout_seconds, wait=wait, thinking=thinking
    )


async def say_talk(
    config: dict[str, Any],
    worker: str,
    conversation_id: str,
    message: str,
    turn: int,
    max_turns: int,
    timeout_seconds: int = 1800,
    thinking: str | None = None,
) -> dict[str, Any]:
    return await WorkerBridge(config, worker).say_talk(conversation_id, message, turn, max_turns, timeout_seconds, thinking=thinking)


async def close_talk(
    config: dict[str, Any],
    worker: str,
    conversation_id: str,
    message: str,
    turn: int,
    max_turns: int,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    return await WorkerBridge(config, worker).close_talk(conversation_id, message, turn, max_turns, timeout_seconds)


def send_worker_sync(
    config: dict[str, Any],
    worker: str,
    task_id: str,
    message: str,
    timeout_seconds: int = 1800,
    thinking: str | None = None,
    wait: bool = False,
    delivery: DeliveryMode | None = None,
) -> dict[str, Any]:
    return asyncio.run(
        send_worker(
            config=config,
            worker=worker,
            task_id=task_id,
            message=message,
            timeout_seconds=timeout_seconds,
            thinking=thinking,
            wait=wait,
            delivery=delivery,
        )
    )


def start_talk_sync(
    config: dict[str, Any],
    worker: str,
    conversation_id: str,
    message: str,
    max_turns: int = 6,
    timeout_seconds: int = 1800,
    wait: bool = False,
    thinking: str | None = None,
) -> dict[str, Any]:
    return asyncio.run(start_talk(config, worker, conversation_id, message, max_turns, timeout_seconds, wait=wait, thinking=thinking))


def say_talk_sync(
    config: dict[str, Any],
    worker: str,
    conversation_id: str,
    message: str,
    turn: int,
    max_turns: int,
    timeout_seconds: int = 1800,
    thinking: str | None = None,
) -> dict[str, Any]:
    return asyncio.run(say_talk(config, worker, conversation_id, message, turn, max_turns, timeout_seconds, thinking=thinking))


def close_talk_sync(
    config: dict[str, Any],
    worker: str,
    conversation_id: str,
    message: str,
    turn: int,
    max_turns: int,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    return asyncio.run(close_talk(config, worker, conversation_id, message, turn, max_turns, timeout_seconds))


__all__ = [
    "WorkerBridge",
    "build_chat_envelope",
    "build_task_envelope",
    "close_talk",
    "close_talk_sync",
    "normalize_thinking_level",
    "post_envelope",
    "say_talk",
    "say_talk_sync",
    "send_worker",
    "send_worker_sync",
    "start_talk",
    "start_talk_sync",
    "summarize_chat_topic",
]
