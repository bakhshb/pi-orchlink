from abc import ABC, abstractmethod
from typing import Any


class MessageStore(ABC):
    @abstractmethod
    async def register_agent(self, agent: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def enqueue_message(self, message: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def get_next_message(self, agent_id: str, wait_seconds: int) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    async def save_reply(self, message_id: str, reply: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def wait_for_reply(self, correlation_id: str, timeout_seconds: int) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def list_agents(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def list_active_messages(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def list_events(self, since: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        raise NotImplementedError
