"""Tests for canonical task submission in orchlink.client.messages."""

from __future__ import annotations

import asyncio
from typing import Any

from orchlink.client import send_worker, send_worker_sync
from orchlink.client.messages import WorkerBridge, build_task_envelope


def _minimal_config() -> dict[str, Any]:
    return {
        "project_id": "demo",
        "lead": {"agent_id": "demo.lead"},
        "work": {"agent_id": "demo.work"},
        "scope": {"allowed": ["**/*"], "forbidden": []},
    }


def test_build_task_envelope_blocking_delivery_for_wait():
    config = _minimal_config()
    envelope = build_task_envelope(
        config=config,
        worker="work",
        task_id="T001",
        message="hello",
        delivery="blocking",
    )
    assert envelope.delivery == "blocking"
    assert envelope.task_id == "T001"
    assert envelope.to_agent == "demo.work"


def test_build_task_envelope_async_delivery_for_send():
    config = _minimal_config()
    envelope = build_task_envelope(
        config=config,
        worker="work",
        task_id="T002",
        message="hello",
        delivery="async",
    )
    assert envelope.delivery == "async"
    assert envelope.task_id == "T002"


def test_worker_bridge_send_wait_uses_send_and_wait_endpoint(monkeypatch):
    config = _minimal_config()
    calls: list[tuple[Any, bool]] = []

    async def fake_post_envelope(cfg, envelope, wait):
        calls.append((envelope, wait))
        return {"status": "completed"}

    monkeypatch.setattr("orchlink.client.messages.post_envelope", fake_post_envelope)

    result = asyncio.run(WorkerBridge(config, "work").send("T001", "hello", wait=True))

    assert result == {"status": "completed"}
    assert len(calls) == 1
    envelope, wait = calls[0]
    assert envelope.delivery == "blocking"
    assert wait is True


def test_worker_bridge_send_async_uses_send_endpoint(monkeypatch):
    config = _minimal_config()
    calls: list[tuple[Any, bool]] = []

    async def fake_post_envelope(cfg, envelope, wait):
        calls.append((envelope, wait))
        return {"status": "queued"}

    monkeypatch.setattr("orchlink.client.messages.post_envelope", fake_post_envelope)

    result = asyncio.run(WorkerBridge(config, "work").send("T002", "hello", wait=False))

    assert result == {"status": "queued"}
    assert len(calls) == 1
    envelope, wait = calls[0]
    assert envelope.delivery == "async"
    assert wait is False


def test_send_worker_accepts_wait_parameter(monkeypatch):
    config = _minimal_config()
    calls: list[tuple[Any, bool]] = []

    async def fake_post_envelope(cfg, envelope, wait):
        calls.append((envelope, wait))
        return {"status": "completed"}

    monkeypatch.setattr("orchlink.client.messages.post_envelope", fake_post_envelope)

    result = asyncio.run(send_worker(config, "work", "T001", "hello", wait=True))

    assert result == {"status": "completed"}
    assert calls[0][0].delivery == "blocking"
    assert calls[0][1] is True


def test_send_worker_sync_accepts_wait_parameter(monkeypatch):
    config = _minimal_config()
    calls: list[tuple[Any, bool]] = []

    async def fake_post_envelope(cfg, envelope, wait):
        calls.append((envelope, wait))
        return {"status": "queued"}

    monkeypatch.setattr("orchlink.client.messages.post_envelope", fake_post_envelope)

    result = send_worker_sync(config, "work", "T002", "hello", wait=False)

    assert result == {"status": "queued"}
    assert calls[0][0].delivery == "async"
    assert calls[0][1] is False


def test_build_task_envelope_thinking_default_for_plan_mode():
    config = _minimal_config()
    envelope = build_task_envelope(
        config=config,
        worker="work",
        task_id="T003",
        message="hello",
        delivery="async",
    )
    assert envelope.payload.thinking == "xhigh"


def test_build_task_envelope_explicit_thinking_override():
    config = _minimal_config()
    envelope = build_task_envelope(
        config=config,
        worker="work",
        task_id="T004",
        message="hello",
        delivery="async",
        thinking="low",
    )
    assert envelope.payload.thinking == "low"
