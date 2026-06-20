from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PROTOCOL_VERSION = "orch-a2a-v1"
LEGACY_PROTOCOL_VERSION = "orchlink-a2a-v1"
SUPPORTED_PROTOCOL_VERSIONS = {PROTOCOL_VERSION, LEGACY_PROTOCOL_VERSION}

MessageType = Literal["TASK", "PLAN", "RESULT", "BLOCKER", "REVIEW", "CLOSE", "STOP"]
MessageStatus = Literal["PENDING", "IN_PROGRESS", "COMPLETED", "FAILED", "TIMEOUT"]
AgentRole = Literal["lead", "worker", "orchestrator"]


class Scope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allowed: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)


class MessagePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    intent: str | None = None
    scope: Scope | None = None
    constraints: list[str] = Field(default_factory=list)
    expected_reply: list[str] = Field(default_factory=list)


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
    task_id: str
    from_agent: str
    to_agent: str
    type: MessageType
    status: MessageStatus = "PENDING"
    turn: int = Field(default=1, ge=1)
    max_turns: int = Field(default=6, ge=1, le=12)
    requires_reply: bool = True
    timeout_seconds: int = Field(default=1800, gt=0)
    payload: MessagePayload = Field(default_factory=MessagePayload)

    @field_validator("protocol")
    @classmethod
    def protocol_must_match(cls, value: str) -> str:
        if value not in SUPPORTED_PROTOCOL_VERSIONS:
            raise ValueError(f"unsupported protocol: {value}")
        return value

    @model_validator(mode="after")
    def turn_must_not_exceed_max_turns(self) -> "MessageEnvelope":
        if self.turn > self.max_turns:
            raise ValueError("turn cannot exceed max_turns")
        return self


def envelope_to_dict(envelope: MessageEnvelope) -> dict[str, Any]:
    return envelope.model_dump(mode="json")
