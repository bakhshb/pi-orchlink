from __future__ import annotations

from contextlib import contextmanager
from threading import local
from typing import Callable, Iterator

from orchlink.goal.files import GoalFileStore


class GoalTransactionManager:
    """Per-store reentrant transactions backed by a per-goal file lock."""

    def __init__(self, files: GoalFileStore, error_factory: Callable[[str], Exception]) -> None:
        self.files = files
        self._error_factory = error_factory
        self._state = local()

    @contextmanager
    def transaction(self, goal_id: str) -> Iterator[None]:
        active_goal_id = getattr(self._state, "goal_id", None)
        if active_goal_id is not None:
            if active_goal_id != goal_id:
                raise self._error_factory(
                    f"Nested goal transactions are not supported: {active_goal_id} then {goal_id}"
                )
            self._state.depth += 1
            try:
                yield
            finally:
                self._state.depth -= 1
            return

        with self.files.lock_goal(goal_id):
            self._state.goal_id = goal_id
            self._state.depth = 1
            try:
                yield
            finally:
                self._state.goal_id = None
                self._state.depth = 0


__all__ = ["GoalTransactionManager"]
