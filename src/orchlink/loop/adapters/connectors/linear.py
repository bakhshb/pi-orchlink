"""Linear loop triage connector shell.

Phase 4 only defines the adapter shape. Actual Linear fetching is deferred to a
later slice so credentials and API behavior can be reviewed separately.
"""

from __future__ import annotations

from typing import Any

from orchlink.loop.services.triage_service import ItemCandidate


class LinearConnector:
    name = "linear"

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})

    async def discover(self) -> list[ItemCandidate]:
        return []
