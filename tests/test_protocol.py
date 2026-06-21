import pytest
from pydantic import ValidationError

from orchlink.broker.protocol import MessageEnvelope, PROTOCOL_VERSION


def sample_envelope(**overrides):
    data = {
        "protocol": PROTOCOL_VERSION,
        "message_id": "msg-0001",
        "correlation_id": "req-0001",
        "conversation_id": "orchlink-test",
        "task_id": "TEST-001",
        "from_agent": "orchestrator",
        "to_agent": "worker-backend",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 30,
        "payload": {
            "intent": "Inspect backend code and return PLAN only.",
            "scope": {
                "allowed": ["apps/api/**"],
                "forbidden": ["apps/web/**"],
            },
            "constraints": ["Do not edit files."],
            "expected_reply": ["summary", "risks"],
        },
    }
    data.update(overrides)
    return data


def test_valid_message_envelope_parses():
    envelope = MessageEnvelope.model_validate(sample_envelope())

    assert envelope.protocol == PROTOCOL_VERSION
    assert envelope.type == "TASK"
    assert envelope.payload.intent == "Inspect backend code and return PLAN only."
    assert envelope.payload.scope.allowed == ["apps/api/**"]


def test_chat_message_types_parse():
    for message_type in ["CHAT_START", "CHAT_TURN", "CHAT_REPLY", "CHAT_CLOSE"]:
        envelope = MessageEnvelope.model_validate(
            sample_envelope(
                type=message_type,
                task_id=None,
                delivery="conversation",
                payload={"mode": "TALK", "message": "Discuss tradeoffs."},
            )
        )

        assert envelope.type == message_type
        assert envelope.payload.mode == "TALK"


def test_payload_modes_parse():
    for mode in ["DISCUSS", "PLAN", "DO", "REVIEW", "TALK"]:
        payload = {"mode": mode, "intent": "x"} if mode != "TALK" else {"mode": mode, "message": "x"}
        delivery = "conversation" if mode == "TALK" else "async"
        message_type = "CHAT_START" if mode == "TALK" else "TASK"
        envelope = MessageEnvelope.model_validate(
            sample_envelope(type=message_type, task_id=None if mode == "TALK" else "TEST-001", delivery=delivery, payload=payload)
        )

        assert envelope.payload.mode == mode


def test_chat_message_requires_talk_mode():
    with pytest.raises(ValidationError):
        MessageEnvelope.model_validate(
            sample_envelope(type="CHAT_START", task_id=None, delivery="conversation", payload={"mode": "PLAN"})
        )


def test_unknown_message_type_is_rejected():
    with pytest.raises(ValidationError):
        MessageEnvelope.model_validate(sample_envelope(type="UNKNOWN"))


def test_unknown_payload_mode_is_rejected():
    with pytest.raises(ValidationError):
        MessageEnvelope.model_validate(sample_envelope(payload={"mode": "BUILD"}))


def test_turn_cannot_exceed_max_turns():
    with pytest.raises(ValidationError, match="turn cannot exceed max_turns"):
        MessageEnvelope.model_validate(sample_envelope(turn=7, max_turns=6))


def test_max_turns_cannot_exceed_twelve():
    with pytest.raises(ValidationError):
        MessageEnvelope.model_validate(sample_envelope(max_turns=13))


def test_invalid_protocol_is_rejected():
    with pytest.raises(ValidationError, match="unsupported protocol"):
        MessageEnvelope.model_validate(sample_envelope(protocol="wrong-protocol"))
