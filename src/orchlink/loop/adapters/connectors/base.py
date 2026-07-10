"""Loop triage connector protocol.

The canonical :class:`Connector` protocol lives in :mod:`orchlink.loop.ports`;
this module re-exports it for backward compatibility with existing connector
imports.
"""

from __future__ import annotations

from orchlink.loop.ports import Connector

__all__ = ["Connector"]
