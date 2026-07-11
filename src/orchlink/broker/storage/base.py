from abc import ABC, abstractmethod
from typing import Any, Callable, Union

from orchlink.core.envelope import AgentRegistration, MessageEnvelope
from orchlink.core.models import Agent, SessionAcquire, SessionHeartbeat, StoredMessage, TaskTelemetry, WorkerActivityInput, TranscriptBatch


# Storage inputs are typed inside the broker. Wire dictionaries are decoded at
# FastAPI/client/JSONL boundaries before reaching the store abstraction.
MessageInput = Union[MessageEnvelope, StoredMessage]
AgentInput = Union[AgentRegistration, Agent]
SessionAcquireInput = SessionAcquire
SessionHeartbeatInput = SessionHeartbeat
ActivityInput = WorkerActivityInput
TranscriptBatchInput = Union[TranscriptBatch, dict[str, Any]]
TaskTelemetryInput = Union[TaskTelemetry, dict[str, Any]]


class MessageStoreBusy(RuntimeError):
    def __init__(self, detail: dict[str, Any]) -> None:
        self.detail = detail
        super().__init__(str(detail.get("message") or "Worker is busy."))


class LeaseConflictError(RuntimeError):
    """Raised when a lease operation conflicts (stale epoch/holder, or reclaim of a non-expired lease)."""


class MessageStore(ABC):
    def current_job_leases(self) -> dict[str, tuple[int, str]]:
        """Snapshot active task leases for checkpoint reconciliation."""
        return {}

    def append_checkpoint_drifts(self, drifts: list[Any]) -> None:
        """Expose startup checkpoint drift records through the store event stream."""
        return None

    @abstractmethod
    async def register_agent(self, agent: AgentInput) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def enqueue_message(
        self,
        message: MessageInput,
        create_waiter: bool = False,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_next_message(
        self,
        agent_id: str,
        wait_seconds: int,
        lease_id: str | None = None,
        project_id: str | None = None,
        on_delivered: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any] | None:
        """Return the next deliverable message for ``agent_id``.

        The store must perform any blocking wait without holding its mutation
        lock. Once a message is actually delivered, the store must invoke
        ``on_delivered`` synchronously while still holding the mutation lock
        (and after any durable append has completed), so a caller can record
        the delivery atomically before releasing the lock. Callback failures
        must not roll back the delivery.
        """
        raise NotImplementedError

    @abstractmethod
    async def save_reply(
        self,
        message_id: str,
        reply: MessageInput,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
        session_lease_id: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def update_message_status(
        self,
        message_id: str,
        status: str,
        session_lease_id: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def record_activity(self, activity: ActivityInput) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def list_activity(
        self,
        item_id: str | None = None,
        limit: int = 20,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def acquire_session(self, session: SessionAcquireInput) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def heartbeat_session(
        self,
        lease_id: str,
        project_id: str | None = None,
        heartbeat: SessionHeartbeatInput | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def release_session(self, lease_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def expire_sessions(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def list_sessions(self, project_id: str | None = None, active: bool = False) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def can_auto_stop(self, project_id: str | None = None) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def cancel_work(self, item_id: str, reason: str = "", project_id: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def heartbeat_job(
        self,
        task_id: str,
        holder: str,
        epoch: int,
        project_id: str | None = None,
        heartbeat_ms: int | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def reclaim_job(self, task_id: str, holder: str, project_id: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def close_conversation(self, conversation_id: str, message: MessageInput) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def wait_for_reply(self, correlation_id: str, timeout_seconds: int) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def wait_for_task(self, task_id: str, timeout_seconds: int, project_id: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_task_result(self, task_id: str, project_id: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def list_jobs(
        self,
        limit: int = 50,
        project_id: str | None = None,
        active: bool = False,
        status: str | None = None,
        kind: str | None = None,
        item_id: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def list_agents(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def list_active_messages(self, project_id: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def list_conversations(self, project_id: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def list_events(self, since: int = 0, limit: int = 100, project_id: str | None = None) -> list[dict[str, Any]]:
        raise NotImplementedError

    # --- Transcript (G018) -------------------------------------------------

    @abstractmethod
    async def append_transcript_batch(
        self,
        batch: TranscriptBatchInput,
        task_id: str,
        project_id: str,
        agent_id: str,
        session_lease_id: str | None = None,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def read_transcript_events(
        self,
        task_id: str,
        project_id: str,
        after: int = 0,
        limit: int = 100,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def wait_transcript_events(
        self,
        task_id: str,
        project_id: str,
        after: int = 0,
        limit: int = 100,
        wait_seconds: int = 0,
    ) -> dict[str, Any]:
        raise NotImplementedError

    # --- Telemetry (G019 AC-5) --------------------------------------------

    @abstractmethod
    async def record_task_telemetry(
        self,
        telemetry: TaskTelemetryInput,
        *,
        agent_id: str,
        session_lease_id: str | None = None,
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_task_telemetry(
        self,
        task_id: str,
        project_id: str,
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def list_task_telemetry(
        self,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError


__all__ = ["ActivityInput", "AgentInput", "LeaseConflictError", "MessageInput", "MessageStore", "MessageStoreBusy", "SessionAcquireInput", "SessionHeartbeatInput", "TaskTelemetryInput", "TranscriptBatchInput"]
