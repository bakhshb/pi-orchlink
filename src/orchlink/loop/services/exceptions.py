"""Loop service exception surface."""

from orchlink.loop.domain.errors import (
    BudgetExhausted,
    IllegalTransition,
    LockHeldError,
    StateCorrupt,
    VerifierMismatch,
)
from orchlink.loop.services.verifier_service import (
    VerdictParseError,
    VerifierDispatchError,
    VerifierTimeoutError,
    WorkerGatewayUnavailable,
)
from orchlink.loop.services.worker_service import MakerDispatchError, MakerTimeoutError, MakerUnreachable

__all__ = [
    "BudgetExhausted",
    "IllegalTransition",
    "LockHeldError",
    "StateCorrupt",
    "VerifierMismatch",
    "MakerDispatchError",
    "MakerTimeoutError",
    "MakerUnreachable",
    "VerdictParseError",
    "VerifierDispatchError",
    "VerifierTimeoutError",
    "WorkerGatewayUnavailable",
]
