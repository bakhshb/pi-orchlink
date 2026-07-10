"""Loop adapters."""

from orchlink.loop.adapters.connectors import (
    Connector,
    ConnectorConfig,
    ConnectorSecretGateway,
    ConnectorSecretMissing,
    GitHubConnector,
    SECRET_KEY_NAMES,
    LinearConnector,
    LocalGitConnector,
)
from orchlink.loop.adapters.markdown_codec import decode_markdown, encode_markdown
from orchlink.loop.adapters.state_repo import LoopStateRepo
from orchlink.loop.adapters.worktree_evidence import WorktreeEvidenceCollector
from orchlink.loop.ports import WorktreeEvidence
from orchlink.loop.adapters.worktree_service import WorktreeCreateError, WorktreeService

__all__ = [
    "Connector",
    "ConnectorConfig",
    "ConnectorSecretGateway",
    "ConnectorSecretMissing",
    "SECRET_KEY_NAMES",
    "GitHubConnector",
    "LinearConnector",
    "LocalGitConnector",
    "LoopStateRepo",
    "WorktreeCreateError",
    "WorktreeEvidence",
    "WorktreeEvidenceCollector",
    "WorktreeService",
    "decode_markdown",
    "encode_markdown",
]
