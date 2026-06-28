"""Append-only transition audit journal (M1).

Observability-only: this journal is NEVER the source of truth for broker or
goal state. It records normalized transition entries for debugging and audit.
The mutable store remains the system of record; this module only appends and
reads. Journal write failures are swallowed so an observability outage can
never block a real transition.

Two sinks:
- In-memory list (always): cheap, used by the ``GET /v1/journal`` endpoint and
  by tests. Lost on broker restart when the memory store backend is in use.
- Optional JSONL file: appended one line per entry. Enabled for the jsonl
  store backend (sibling of the store snapshot). The memory backend is
  ephemeral by design and does not persist the audit journal to disk.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class JournalEntry:
    seq: int
    time: str
    project_id: str
    actor: str | None
    action: str
    target_type: str | None
    target_id: str | None
    before: str | None
    after: str | None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "time": self.time,
            "project_id": self.project_id,
            "actor": self.actor,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "before": self.before,
            "after": self.after,
            "meta": self.meta,
        }


# Broker event type -> journal action. ``None`` means "do not journal"
# (e.g. high-volume worker_activity heartbeats would drown the audit log).
_BROKER_EVENT_ACTION: dict[str, str | None] = {
    "agent_registered": "session.registered",
    "session_acquired": "session.registered",
    "session_released": "session.released",
    "session_expired": "session.released",
    "message_queued": "job.created",
    "message_delivered": "job.dispatched",
    "reply_received": "job.replied",
    "late_reply_ignored": "job.replied",
    "message_status": "job.heartbeat",
    "job_heartbeat": "job.heartbeat",
    "job_reclaimed": "job.reclaimed",
    "work_cancelled": "job.cancelled",
    "conversation_closed": "job.terminal",
    "timeout": "job.terminal",
    "worker_activity": None,
}


def _map_broker_event(event: dict[str, Any]) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """Map a broker event dict to (action, target_type, target_id, actor, after)."""
    event_type = str(event.get("type") or "")
    action = _BROKER_EVENT_ACTION.get(event_type, f"broker.{event_type}" if event_type else None)
    if action is None:
        return None, None, None, None, None

    task_id = event.get("task_id")
    conversation_id = event.get("conversation_id")
    message_id = event.get("message_id")
    agent_id = event.get("agent_id")

    if task_id:
        target_type, target_id = "job", str(task_id)
    elif conversation_id:
        target_type, target_id = "conversation", str(conversation_id)
    elif message_id:
        target_type, target_id = "message", str(message_id)
    elif agent_id:
        target_type, target_id = "session", str(agent_id)
    else:
        target_type, target_id = None, None

    actor = event.get("from_agent") or event.get("to_agent") or (agent_id if event_type == "agent_registered" else None)
    after = event.get("status")
    return action, target_type, target_id, (str(actor) if actor else None), (str(after) if after else None)


class Journal:
    """Append-only transition journal.

    Thread-unsafe by design: the broker is single-event-loop and appends under
    the store lock. ``query`` is read-only and safe to call concurrently with
    appends from the same loop.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._entries: list[JournalEntry] = []
        self._seq = 0
        self._path: Path | None = Path(path).expanduser() if path else None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path | None:
        return self._path

    def append(
        self,
        *,
        project_id: str,
        actor: str | None,
        action: str,
        target_type: str | None = None,
        target_id: str | None = None,
        before: str | None = None,
        after: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> JournalEntry:
        entry = JournalEntry(
            seq=self._next_seq(),
            time=_now(),
            project_id=str(project_id or "default"),
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            before=before,
            after=after,
            meta=dict(meta or {}),
        )
        self._entries.append(entry)
        self._persist(entry)
        return entry

    def record_broker_event(self, event: dict[str, Any]) -> JournalEntry | None:
        """Normalize and append a broker event. Returns None if skipped."""
        action, target_type, target_id, actor, after = _map_broker_event(event)
        if action is None:
            return None
        meta = {"broker_event_type": str(event.get("type") or "")}
        # Carry identifying context without copying large payloads.
        for key in ("task_id", "conversation_id", "message_id"):
            value = event.get(key)
            if value:
                meta[key] = str(value)
        return self.append(
            project_id=str(event.get("project_id") or "default"),
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before=None,
            after=after,
            meta=meta,
        )

    def query(
        self,
        project_id: str | None = None,
        since: int = 0,
        limit: int = 100,
    ) -> list[JournalEntry]:
        """Return entries with ``seq > since``, optionally project-scoped.

        Oldest-first; capped to ``limit``. ``limit <= 0`` means unlimited.
        """
        entries = [entry for entry in self._entries if entry.seq > since]
        if project_id is not None:
            entries = [entry for entry in entries if entry.project_id == str(project_id)]
        if limit and limit > 0:
            entries = entries[:limit]
        return entries

    def last_seq(self) -> int:
        return self._entries[-1].seq if self._entries else 0

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _persist(self, entry: JournalEntry) -> None:
        if self._path is None:
            return
        try:
            with self._path.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry.to_dict(), sort_keys=True, default=str) + "\n")
        except OSError:
            # Observability-only: never propagate a journal write failure.
            pass