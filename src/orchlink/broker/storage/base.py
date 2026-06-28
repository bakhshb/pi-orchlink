from abc import ABC, abstractmethod
from typing import Any


class MessageStoreBusy(RuntimeError):
    def __init__(self, detail: dict[str, Any]) -> None:
        self.detail = detail
        super().__init__(str(detail.get("message") or "Worker is busy."))


class LeaseConflictError(RuntimeError):
    """Raised when a lease operation conflicts (stale epoch/holder, or reclaim of a non-expired lease)."""



class MessageStore(ABC):
    @abstractmethod
    async def register_agent(self, agent: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def enqueue_message(
        self,
        message: dict[str, Any],
        create_waiter: bool = False,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_next_message(self, agent_id: str, wait_seconds: int) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def save_reply(
        self,
        message_id: str,
        reply: dict[str, Any],
        lease_epoch: int | None = None,
        lease_holder: str | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def update_message_status(self, message_id: str, status: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def record_activity(self, activity: dict[str, Any]) -> dict[str, Any]:
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
    async def acquire_session(self, session: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def heartbeat_session(self, lease_id: str, project_id: str | None = None) -> dict[str, Any]:
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
    async def close_conversation(self, conversation_id: str, message: dict[str, Any]) -> dict[str, Any]:
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
