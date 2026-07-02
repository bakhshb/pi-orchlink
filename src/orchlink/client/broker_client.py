"""Synchronous broker API client."""

from __future__ import annotations

from typing import Any

import httpx

from orchlink.project.config import broker_api_key, broker_url


class BrokerClient:
    """Thin synchronous broker API wrapper used by Typer commands."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.base_url = broker_url(config)
        self.project_id = str(config.get("project_id") or "default")
        self.headers = {
            "X-API-Key": broker_api_key(config),
            "X-Orchlink-Project-ID": self.project_id,
        }

    def get(self, path: str) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=None) as client:
            response = client.get(path, headers=self.headers)
            response.raise_for_status()
            return response.json()

    def post(self, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=None) as client:
            response = client.post(path, headers=self.headers, json=body or {})
            response.raise_for_status()
            return response.json()

    def jobs(self, query: str) -> dict[str, Any]:
        return self.get(query)

    def task(self, task_id: str, query: str) -> dict[str, Any]:
        return self.get(f"/v1/tasks/{task_id}{query}")

    def wait_task(self, task_id: str, timeout_seconds: int, query_suffix: str) -> dict[str, Any]:
        return self.get(f"/v1/tasks/{task_id}/wait?timeout_seconds={timeout_seconds}{query_suffix}")

    def cancel(self, item_id: str, reason: str) -> dict[str, Any]:
        return self.post(f"/v1/jobs/{item_id}/cancel", {"reason": reason, "project_id": self.project_id})
