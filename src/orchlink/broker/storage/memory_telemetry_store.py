"""Latest-state, lease-fenced task telemetry store (G019 AC-5).

Storage shape contract:
    * Single record per ``(project_id, task_id)`` key.
    * Each successful update REPLACES the previous record rather than
      appending — no unbounded heartbeat history.
    * Updates that fail a lease fence or arrive for a terminal task are
      rejected; the existing record (if any) is left untouched.
    * Telemetry is best-effort and never the source of truth for task
      success: a rejected update never changes the task's success, retry,
      timeout, cancellation, or result authority.

Lease fence contract (every update must satisfy):
    1. If ``session_lease_id`` is given, the broker's session registry
       shows that lease as active for ``(agent_id, project_id)``.
    2. If ``lease_epoch`` + ``lease_holder`` are given, the broker's
       current job lease for ``(project_id, task_id)`` matches those
       values (the same fence the transcript endpoint already enforces).
    3. ``task.status`` is not in ``CANONICAL_TERMINAL_STATUSES`` (DONE,
       BLOCKED, CANCELLED, TIMEOUT, ERROR). A terminal task rejects
       telemetry forever; the broker refuses to refresh the lead UI
       view of a completed task.

Privacy boundary: the wire shape and the in-memory record carry only
numeric metrics, lease metadata, and an audit timestamp. The store
deliberately has no path that would let a worker publish prompt body,
hidden reasoning, tool arguments, raw tool output, provider data,
environment value, secret, or authorization data through this endpoint.
"""

from __future__ import annotations

from typing import Any, Callable

from orchlink.broker.storage.memory_state import InMemoryBrokerState, matches_project
from orchlink.core.models import (
    CANONICAL_TERMINAL_STATUSES,
    JobLease,
    TaskTelemetry,
)
from orchlink.core.states import normalize_status
from orchlink.core.views import task_telemetry_from_wire, task_telemetry_to_wire


def telemetry_key(project_id: str, task_id: str) -> str:
    """Storage key for the latest-state telemetry record."""
    return f"{project_id or 'default'}:{task_id or ''}"


class TelemetryRejected(RuntimeError):
    """Raised when a telemetry update is rejected (stale lease or terminal task).

    The HTTP layer maps ``reason`` to a 409 status code with the same
    ``reason`` echoed in the response body, so a lead UI / worker calling
    the endpoint sees a structured rejection instead of an opaque 500.
    """

    def __init__(self, reason: str, detail: str | None = None) -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason)


class MemoryTelemetryStore:
    def __init__(
        self,
        state: InMemoryBrokerState,
        now: Callable[[], str],
        session_store: Any,
        job_projector: Any,
    ) -> None:
        self._state = state
        self._now = now
        self._session_store = session_store
        self._job_projector = job_projector

    def _assert_session_lease(
        self,
        agent_id: str,
        project_id: str,
        session_lease_id: str | None,
    ) -> None:
        if not session_lease_id:
            return
        try:
            self._session_store.assert_active_session_lease_locked(
                str(agent_id or ""),
                project_id=str(project_id or "default"),
                lease_id=str(session_lease_id),
            )
        except Exception as exc:
            raise TelemetryRejected("stale-session-lease", str(exc)) from exc

    def _assert_job_lease(
        self,
        project_id: str,
        task_id: str,
        lease_epoch: int | None,
        lease_holder: str | None,
    ) -> None:
        if lease_epoch is None:
            return
        task_key = self._job_projector.task_key(project_id, task_id)
        job = self._state.task_jobs.get(task_key)
        lease = job.lease if job is not None else None
        if lease is None or not isinstance(lease, JobLease) or not lease.matches(
            str(lease_holder or ""), int(lease_epoch)
        ):
            raise TelemetryRejected(
                "stale-job-lease",
                f"job lease for {task_id} does not match epoch/holder",
            )

    def _assert_task_non_terminal(self, project_id: str, task_id: str) -> None:
        task_key = self._job_projector.task_key(project_id, task_id)
        task = self._state.tasks.get(task_key)
        if task is None:
            # Unknown tasks cannot be fenced because we cannot tell whether
            # they are terminal; reject so a stale worker does not seed a
            # phantom telemetry record.
            raise TelemetryRejected("unknown-task", f"no task projection for {task_id}")
        normalized = normalize_status(getattr(task, "status", None))
        if normalized in CANONICAL_TERMINAL_STATUSES:
            raise TelemetryRejected(
                "terminal-task",
                f"task {task_id} is terminal ({normalized})",
            )

    def record_or_replace(
        self,
        payload: Any,
        *,
        agent_id: str,
        session_lease_id: str | None = None,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
    ) -> dict[str, Any]:
        """Coerce wire-shaped inputs into a TaskTelemetry and run the locked
        replacement under the broker's state lock.

        This is the path used by the broker facade so the focused store
        owns the dictionary/typed payload normalization. ``memory.py``
        keeps only the lock acquisition and the forward to this method,
        which preserves the existing architectural split: facade-thin
        wrappers over focused components.
        """
        if not isinstance(payload, TaskTelemetry):
            payload = task_telemetry_from_wire(payload)
        return self.record_telemetry_locked(
            payload,
            agent_id=agent_id,
            session_lease_id=session_lease_id,
            lease_epoch=lease_epoch,
            lease_holder=lease_holder,
        )

    def record_telemetry_locked(
        self,
        payload: TaskTelemetry,
        *,
        agent_id: str,
        session_lease_id: str | None = None,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
    ) -> dict[str, Any]:
        project_id = str(payload.project_id or "default")
        task_id = str(payload.task_id or "")
        if not task_id:
            raise TelemetryRejected("invalid-task", "task_id is required")
        # Lease fences first — failing fast avoids partial state mutations
        # if a fence is violated.
        self._assert_session_lease(agent_id, project_id, session_lease_id)
        self._assert_job_lease(project_id, task_id, lease_epoch, lease_holder)
        self._assert_task_non_terminal(project_id, task_id)
        # Stamp the lease metadata onto the record so a future snapshot
        # replay can independently verify fences without re-querying the
        # store.
        stamped = TaskTelemetry(
            project_id=project_id,
            task_id=task_id,
            worker_name=str(payload.worker_name or ""),
            tokens=payload.tokens,
            context_window=payload.context_window,
            percent=payload.percent,
            tool_count=max(0, int(payload.tool_count or 0)),
            lease_epoch=int(lease_epoch or 0),
            lease_holder=str(lease_holder or ""),
            session_lease_id=session_lease_id,
            updated_at=self._now(),
        )
        key = telemetry_key(project_id, task_id)
        self._state.telemetry_by_task[key] = stamped
        return {
            "status": "recorded",
            "task_id": task_id,
            "project_id": project_id,
            "updated_at": stamped.updated_at,
        }

    def get_task_telemetry_locked(
        self,
        project_id: str,
        task_id: str,
    ) -> dict[str, Any] | None:
        key = telemetry_key(project_id, task_id)
        record = self._state.telemetry_by_task.get(key)
        if record is None:
            return None
        return task_telemetry_to_wire(record)

    def list_task_telemetry_locked(
        self,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for key, record in self._state.telemetry_by_task.items():
            wire = task_telemetry_to_wire(record)
            if not matches_project(wire, project_id):
                continue
            items.append(wire)
        return items


__all__ = ["MemoryTelemetryStore", "TelemetryRejected", "telemetry_key"]
