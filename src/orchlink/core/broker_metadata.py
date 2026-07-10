"""Stable shared metadata for the Orchlink broker protocol.

This module is the single source of truth for the broker version and the
capability set the broker advertises on the ``/health`` endpoint. Clients
import these symbols directly so they no longer have to import the FastAPI
``app`` from :mod:`orchlink.broker.main` (which would couple the CLI/runtime
client to the broker's HTTP application construction).

The broker application re-exports the same symbols from
:mod:`orchlink.broker.main` for backward compatibility with older tests and
integration code.
"""

from __future__ import annotations

from orchlink.version import get_version


BROKER_CAPABILITIES: list[str] = [
    "project_header_scope",
    "task_activity_endpoint",
    "scoped_task_results",
    "status_filters",
    "session_leases",
    "session_readiness",
    "session_lease_fencing",
]


def broker_version() -> str:
    """Return the broker version string from the canonical package metadata."""
    return get_version()


# Resolved at import time; matches the historical ``VERSION`` constant exposed
# from :mod:`orchlink.broker.main`.
BROKER_VERSION: str = broker_version()


__all__ = ["BROKER_CAPABILITIES", "BROKER_VERSION", "broker_version"]