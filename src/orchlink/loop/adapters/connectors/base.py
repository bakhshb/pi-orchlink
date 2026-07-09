"""Connector protocol for loop triage sources."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Protocol

if TYPE_CHECKING:
    from orchlink.loop.services.triage_service import ItemCandidate


class Connector(Protocol):
    name: str

    async def discover(self) -> Iterable["ItemCandidate"]:
        ...
