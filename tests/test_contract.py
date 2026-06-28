import pytest
from pydantic import ValidationError

from orchlink.core.envelope import (
    ENVELOPE_VERSION,
    ENVELOPE_VERSION_HEADER,
    PROTOCOL_VERSION,
    MessageEnvelope,
    envelope_to_dict,
    envelope_version_headers,
)
from orchlink.broker.main import create_app
from orchlink.broker.settings import Settings
from orchlink.broker.storage.memory import MemoryMessageStore


def sample_envelope(**overrides):
    data = {
        "protocol": PROTOCOL_VERSION,
        "message_id": "msg-contract-1",
        "correlation_id": "req-contract-1",
        "project_id": "demo",
        "conversation_id": "demo-tasks",
        "task_id": "T-CONTRACT-001",
        "from_agent": "demo.lead",
        "to_agent": "demo.work",
        "type": "TASK",
        "status": "PENDING",
        "turn": 1,
        "max_turns": 6,
        "requires_reply": True,
        "timeout_seconds": 30,
        "delivery": "async",
        "payload": {
            "mode": "PLAN",
            "intent": "Inspect the contract.",
            "scope": {"allowed": ["src/**"], "forbidden": [".git/**"]},
            "constraints": ["Do not edit files."],
            "expected_reply": ["summary"],
        },
    }
    data.update(overrides)
    return data


def test_envelope_round_trips_through_core_contract():
    envelope = MessageEnvelope.model_validate(sample_envelope())

    data = envelope_to_dict(envelope)
    reparsed = MessageEnvelope.model_validate(data)

    assert data["protocol"] == PROTOCOL_VERSION
    assert reparsed == envelope
    assert reparsed.payload.scope.allowed == ["src/**"]


def test_chat_envelope_contract_requires_talk_conversation_delivery():
    envelope = MessageEnvelope.model_validate(
        sample_envelope(
            conversation_id="C001",
            task_id=None,
            type="CHAT_START",
            delivery="conversation",
            payload={"mode": "TALK", "message": "Discuss."},
        )
    )

    assert envelope.payload.mode == "TALK"

    with pytest.raises(ValidationError, match="chat messages must use TALK mode"):
        MessageEnvelope.model_validate(
            sample_envelope(
                conversation_id="C002",
                task_id=None,
                type="CHAT_START",
                delivery="conversation",
                payload={"mode": "PLAN", "message": "Discuss."},
            )
        )


def test_unknown_protocol_is_rejected_not_coerced():
    with pytest.raises(ValidationError, match="unsupported protocol"):
        MessageEnvelope.model_validate(sample_envelope(protocol="orch-a2a-v999"))


def test_envelope_version_header_constant_and_helper():
    assert ENVELOPE_VERSION_HEADER == "x-orchlink-envelope"
    assert ENVELOPE_VERSION == "1"
    assert envelope_version_headers() == {"x-orchlink-envelope": "1"}


def test_v1_broker_responses_include_envelope_version_header():
    app = create_app(store=MemoryMessageStore(), settings=Settings(api_key="test-key"))

    import httpx
    import asyncio

    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/v1/status", headers={"X-API-Key": "test-key"})

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.headers[ENVELOPE_VERSION_HEADER] == ENVELOPE_VERSION
