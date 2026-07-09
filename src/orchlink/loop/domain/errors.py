"""Typed errors for the loop domain kernel."""

from __future__ import annotations


class IllegalTransition(RuntimeError):
    """Raised when a lifecycle method is called from the wrong state."""

    def __init__(self, state: object, method: str) -> None:
        self.state = state
        self.method = method
        state_value = getattr(state, "value", state)
        super().__init__(f"illegal transition from {state_value!r} via {method}")


class BudgetExhausted(RuntimeError):
    """Raised when an item cannot reserve another attempt."""


class LockHeldError(RuntimeError):
    """Raised when the loop state lock is held by another live actor."""


class VerifierMismatch(RuntimeError):
    """Raised when verifier policy rejects the requested verifier."""


class StateCorrupt(RuntimeError):
    """Raised when the markdown state file cannot be decoded safely."""
