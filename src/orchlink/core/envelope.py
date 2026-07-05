from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PROTOCOL_VERSION = "orch-a2a-v1"
ENVELOPE_VERSION = "1"
ENVELOPE_VERSION_HEADER = "x-orchlink-envelope"

MessageType = Literal[
    "TASK",
    "TASK_REPLY",
    "PLAN",
    "RESULT",
    "BLOCKER",
    "REVIEW",
    "CLOSE",
    "STOP",
    "CHAT_START",
    "CHAT_TURN",
    "CHAT_REPLY",
    "CHAT_CLOSE",
]
MessageStatus = Literal[
    "PENDING",
    "QUEUED",
    "DELIVERED",
    "RUNNING",
    "IN_PROGRESS",
    "DONE",
    "COMPLETED",
    "FAILED",
    "TIMEOUT",
    "CANCELLED",
    "CLOSED",
]
MessageMode = Literal["DISCUSS", "PLAN", "DO", "REVIEW", "TALK"]
ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh"]
DeliveryMode = Literal["blocking", "async", "conversation"]
AgentRole = Literal["lead", "worker"]


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)


class MessagePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    mode: MessageMode | None = None
    intent: str | None = None
    topic: str | None = None
    message: str | None = None
    transcript_preview: str | None = None
    scope: Scope | None = None
    constraints: list[str] = Field(default_factory=list)
    expected_reply: list[str] = Field(default_factory=list)


class MessageMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    thinking: ThinkingLevel | None = None
    thinking_applied: ThinkingLevel | None = None


class AgentRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = "default"
    agent_id: str
    role: AgentRole
    display_name: str
    capabilities: list[str] = Field(default_factory=list)


class MessageEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: str = PROTOCOL_VERSION
    message_id: str
    correlation_id: str
    project_id: str = "default"
    conversation_id: str
    task_id: str | None = None
    from_agent: str
    to_agent: str
    type: MessageType
    status: MessageStatus = "PENDING"
    turn: int = Field(default=1, ge=1)
    max_turns: int = Field(default=6, ge=1, le=12)
    requires_reply: bool = True
    timeout_seconds: int = Field(default=1800, gt=0)
    delivery: DeliveryMode = "async"
    payload: MessagePayload = Field(default_factory=MessagePayload)
    meta: MessageMeta = Field(default_factory=MessageMeta)

    @field_validator("protocol")
    @classmethod
    def protocol_must_match(cls, value: str) -> str:
        if value != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol: {value}")
        return value

    @model_validator(mode="after")
    def validate_turns_and_chat_fields(self) -> "MessageEnvelope":
        if self.turn > self.max_turns:
            raise ValueError("turn cannot exceed max_turns")
        if self.type.startswith("CHAT_"):
            if self.delivery != "conversation":
                raise ValueError("chat messages must use conversation delivery")
            if self.payload.mode != "TALK":
                raise ValueError("chat messages must use TALK mode")
        elif self.delivery == "conversation":
            raise ValueError("conversation delivery requires a chat message type")
        return self


def envelope_to_dict(envelope: MessageEnvelope) -> dict[str, Any]:
    return envelope.model_dump(mode="json")


def envelope_version_headers() -> dict[str, str]:
    return {ENVELOPE_VERSION_HEADER: ENVELOPE_VERSION}
