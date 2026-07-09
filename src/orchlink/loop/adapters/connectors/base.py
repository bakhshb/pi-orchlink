"""Connector protocol for loop triage sources."""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Protocol

if TYPE_CHECKING:
    from orchlink.loop.services.triage_service import ItemCandidate


class Connector(Protocol):
    """Read-only triage source.

    Real connectors are constructed with non-secret ConnectorConfig and obtain
    tokens only through ConnectorSecretGateway. Secrets must not be stored in
    project state or emitted in ItemCandidate values.
    """

    name: str

    async def discover(self) -> Iterable["ItemCandidate"]:
        ...
