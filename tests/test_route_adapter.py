from __future__ import annotations

import asyncio

from fastapi import HTTPException

from orchlink.broker.route_adapter import BrokerRouteAdapter
from orchlink.broker.service import BrokerService
from orchlink.broker.storage import MemoryMessageStore, MessageStoreBusy
from orchlink.broker.storage.base import LeaseConflictError


class _RaisingService:
    async def enqueue_message(self, *_args, **_kwargs):
        raise MessageStoreBusy({"error": "worker_busy", "message": "busy"})

    async def get_next_message(self, *_args, **_kwargs):
        raise LeaseConflictError("stale lease")

    async def cancel_work(self, *_args, **_kwargs):
        raise ValueError("missing task")

    async def update_message_status(self, *_args, **_kwargs):
        raise ValueError("missing message")


def _adapter() -> BrokerRouteAdapter:
    adapter = BrokerRouteAdapter(MemoryMessageStore())
    adapter.service = _RaisingService()  # type: ignore[assignment]
    return adapter


def test_route_adapter_wraps_raw_message_store_for_compatibility() -> None:
    adapter = BrokerRouteAdapter(MemoryMessageStore())

    assert isinstance(adapter.service, BrokerService)


def test_route_adapter_maps_busy_store_to_409() -> None:
    async def run() -> None:
        try:
            await _adapter().enqueue_message(object())  # type: ignore[arg-type]
        except HTTPException as exc:
            assert exc.status_code == 409
            assert exc.detail["error"] == "worker_busy"
        else:  # pragma: no cover
            raise AssertionError("expected HTTPException")

    asyncio.run(run())


def test_route_adapter_maps_lease_conflict_to_409() -> None:
    async def run() -> None:
        try:
            await _adapter().get_next_message("demo.work", 0)
        except HTTPException as exc:
            assert exc.status_code == 409
            assert exc.detail == "stale lease"
        else:  # pragma: no cover
            raise AssertionError("expected HTTPException")

    asyncio.run(run())


def test_route_adapter_maps_not_found_value_error_to_404() -> None:
    async def run() -> None:
        try:
            await _adapter().cancel_work("T404")
        except HTTPException as exc:
            assert exc.status_code == 404
            assert exc.detail == "missing task"
        else:  # pragma: no cover
            raise AssertionError("expected HTTPException")

    asyncio.run(run())


def test_route_adapter_maps_status_not_found_value_error_to_404() -> None:
    async def run() -> None:
        try:
            await _adapter().update_message_status("missing", "RUNNING")
        except HTTPException as exc:
            assert exc.status_code == 404
            assert exc.detail == "missing message"
        else:  # pragma: no cover
            raise AssertionError("expected HTTPException")

    asyncio.run(run())
