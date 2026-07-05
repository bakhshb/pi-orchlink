"""Focused component: session acquire/heartbeat/release/list and lease checks.

Lifted from ``memory.py``; preserves every method, body, and side effect.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Callable

from orchlink.broker.storage.base import LeaseConflictError
from orchlink.broker.storage.memory_event_log import MemoryEventLog
from orchlink.broker.storage.memory_state import (
    InMemoryBrokerState,
    session_belongs_to_project,
)
from orchlink.core.models import BrokerEventContext, Session, SessionAcquire, SessionHeartbeat, SessionRelease
from orchlink.core.session_lifecycle import is_active_session_status
from orchlink.core.views import session_to_wire


class MemorySessionStore:
    def __init__(
        self,
        state: InMemoryBrokerState,
        now: Callable[[], str],
        parse_time: Callable[[Any], datetime | None],
        event_log: MemoryEventLog,
        session_grace_seconds: int,
    ) -> None:
        self._state = state
        self._now = now
        self._parse_time = parse_time
        self._event_log = event_log
        self._session_grace_seconds = session_grace_seconds

    def active_sessions_for_agent_locked(self, agent_id: str, project_id: str | None = None) -> list[Session]:
        return [
            session
            for session in self._state.sessions.values()
            if session_belongs_to_project(session, project_id)
            and session.agent_id == agent_id
            and is_active_session_status(session.status)
        ]

    def active_session_locked(self, agent_id: str, project_id: str | None = None) -> Session | None:
        sessions = self.active_sessions_for_agent_locked(agent_id, project_id=project_id)
        return sessions[0] if sessions else None

    def assert_poll_lease_locked(
        self,
        agent_id: str,
        project_id: str | None = None,
        lease_id: str | None = None,
    ) -> None:
        """Require a current session lease before polling when a session exists."""
        sessions = self.active_sessions_for_agent_locked(agent_id, project_id=project_id)
        if not sessions:
            return
        if not lease_id:
            raise LeaseConflictError(f"Session lease required for active agent: {agent_id}")
        for session in sessions:
            if session.lease_id == lease_id:
                return
        raise LeaseConflictError(f"Stale or inactive session lease for agent: {agent_id}")

    def assert_active_session_lease_locked(
        self,
        agent_id: str,
        project_id: str | None = None,
        lease_id: str | None = None,
    ) -> None:
        """Validate an asserted session lease; empty lease means no assertion."""
        if not lease_id:
            return
        for session in self.active_sessions_for_agent_locked(agent_id, project_id=project_id):
            if session.lease_id == lease_id:
                return
        raise LeaseConflictError(f"Stale or inactive session lease for agent: {agent_id}")

    def active_session_count_locked(self, project_id: str | None = None) -> int:
        return sum(
            1
            for session in self._state.sessions.values()
            if session_belongs_to_project(session, project_id)
            and is_active_session_status(session.status)
        )

    def expire_sessions_locked(self, on_session_ended: Callable[[str, str, str], list[str]]) -> list[Session]:
        now = datetime.now(timezone.utc)
        expired: list[Session] = []
        for session in list(self._state.sessions.values()):
            if not is_active_session_status(session.status):
                continue
            last_seen = self._parse_time(session.last_heartbeat_at or session.updated_at)
            if last_seen is None:
                continue
            grace = int(session.lease_grace_seconds or self._session_grace_seconds)
            if (now - last_seen).total_seconds() < grace:
                continue
            reason = f"Session heartbeat expired: {session.agent_id}"
            expired_session = replace(
                session.expire(self._now(), reason),
                settled_work=on_session_ended(str(session.agent_id), str(session.project_id or "default"), reason),
            )
            self._state.sessions[session.lease_id] = expired_session
            expired.append(expired_session)
            self._event_log.append_event_locked(BrokerEventContext.from_fields(
                "session_expired",
                project_id=session.project_id,
                agent_id=session.agent_id,
                role=session.role,
                lease_id=session.lease_id,
                status=expired_session.status,
                payload=session_to_wire(expired_session),
                preview=reason,
            ))
        return expired

    def acquire_session_locked(self, command: SessionAcquire) -> dict[str, Any]:
        now = self._now()
        lease_id = str(command.lease_id or f"lease-{uuid.uuid4()}")
        project_id = str(command.project_id or "default")
        agent_id = str(command.agent_id or "")
        worker_name = str(command.worker_name or "")
        for active in self._state.sessions.values():
            if not session_belongs_to_project(active, project_id):
                continue
            if not is_active_session_status(active.status):
                continue
            if active.lease_id == lease_id:
                continue
            active_worker_name = str(active.worker_name or "")
            if active.agent_id == agent_id:
                raise LeaseConflictError(f"Active session already exists for agent: {agent_id}")
            if worker_name and active_worker_name == worker_name:
                raise LeaseConflictError(f"Active session already exists for worker name: {worker_name}")
        ready = bool(command.ready)
        stored = Session(
            lease_id=lease_id,
            project_id=project_id,
            agent_id=agent_id,
            role=str(command.role or "work"),
            worker_name=command.worker_name,
            pid=command.pid,
            session_id=command.session_id,
            status="ACTIVE",
            created_at=now,
            updated_at=now,
            last_heartbeat_at=now,
            lease_grace_seconds=int(command.lease_grace_seconds or self._session_grace_seconds),
            ready=ready,
            ready_at=now if ready else None,
            last_ready_heartbeat_at=now if ready else None,
            runtime_mode=command.runtime_mode,
            backend=command.backend,
            model=command.model,
            thinking=command.thinking,
            supervisor_pid=command.supervisor_pid,
            pi_pid=command.pi_pid,
        )
        self._state.sessions[lease_id] = stored
        wire = session_to_wire(stored)
        self._event_log.append_event_locked(BrokerEventContext.from_fields(
            "session_acquired",
            project_id=stored.project_id,
            agent_id=stored.agent_id,
            role=stored.role,
            lease_id=lease_id,
            status="ACTIVE",
            payload=wire,
            preview=f"session active {stored.agent_id}",
        ))
        return wire

    def heartbeat_session_locked(self, command: SessionHeartbeat) -> dict[str, Any]:
        session = self._state.sessions.get(command.lease_id)
        if session is None or not session_belongs_to_project(session, command.project_id):
            raise ValueError(f"Session not found: {command.lease_id}")
        if not is_active_session_status(session.status):
            return session_to_wire(session)
        now = self._now()
        updated = session.heartbeat(now)
        if command.ready is True:
            updated = updated.mark_ready(now)
        metadata: dict[str, Any] = {}
        for key in ("runtime_mode", "backend", "model", "thinking", "supervisor_pid", "pi_pid", "worker_name"):
            value = getattr(command, key)
            if value not in {None, ""}:
                metadata[key] = value
        if metadata:
            updated = replace(updated, **metadata)
        self._state.sessions[command.lease_id] = updated
        return session_to_wire(updated)

    def release_session_locked(
        self,
        command: SessionRelease,
        on_session_ended: Callable[[str, str, str], list[str]],
    ) -> dict[str, Any]:
        session = self._state.sessions.get(command.lease_id)
        if session is None or not session_belongs_to_project(session, command.project_id):
            raise ValueError(f"Session not found: {command.lease_id}")
        if is_active_session_status(session.status):
            release_reason = command.reason or "Session exited."
            settled = on_session_ended(
                str(session.agent_id),
                str(session.project_id or "default"),
                command.reason or f"Session exited: {session.agent_id}",
            )
            released = replace(
                session.release(self._now(), release_reason),
                settled_work=settled,
            )
            self._state.sessions[command.lease_id] = released
            self._event_log.append_event_locked(BrokerEventContext.from_fields(
                "session_released",
                project_id=session.project_id,
                agent_id=session.agent_id,
                role=session.role,
                lease_id=command.lease_id,
                status=released.status,
                payload=session_to_wire(released),
                preview=release_reason or f"session released {session.agent_id}",
            ))
            return session_to_wire(released)
        return session_to_wire(session)

    def list_sessions_locked(self, project_id: str | None = None, active: bool = False) -> list[dict[str, Any]]:
        sessions = [
            session
            for session in self._state.sessions.values()
            if session_belongs_to_project(session, project_id)
        ]
        if active:
            sessions = [session for session in sessions if is_active_session_status(session.status)]
        sessions = list(sessions)
        sessions.sort(key=lambda item: str(item.updated_at or item.created_at or ""), reverse=True)
        return [session_to_wire(s) for s in sessions]


__all__ = ["MemorySessionStore"]