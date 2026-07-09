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

__all__ = [
    "BudgetExhausted",
    "IllegalTransition",
    "LockHeldError",
    "StateCorrupt",
    "VerifierMismatch",
    "VerdictParseError",
    "VerifierDispatchError",
    "VerifierTimeoutError",
    "WorkerGatewayUnavailable",
]
