"""Shared helpers used by the ``cli/commands/*.py`` modules.

Helpers used by more than one command group live here instead of being
duplicated across command modules. None of these helpers register Typer
commands directly.
"""

from __future__ import annotations

import asyncio
import ipaddress
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlencode, urlparse

import httpx
import typer
from rich.console import Console

from orchlink.broker.state import ACTIVE_ACTIVITY_STATUSES, is_active_job_status, job_id_for, job_kind_for, job_matches_id
from orchlink.client.sync import broker_get_sync
from orchlink.project.config import (
    ProjectConfigError,
    broker_api_key,
    broker_url,
    load_project_config,
    project_root,
    role_agent_id,
)


def current_project_id(config: dict[str, Any]) -> str:
    return str(config.get("project_id") or "default")


console = Console()


def is_loopback_host(host: str | None) -> bool:
    value = str(host or "").strip().strip("[]").lower()
    if value == "localhost":
        return True
    if not value:
        return False
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def host_from_url(url: str) -> str:
    return str(urlparse(url).hostname or "")


async def register_project_role(config: dict[str, Any], role: str) -> dict[str, Any]:
    """Hit ``POST /v1/agents/register`` for the lead/worker registration."""
    role_key = "work" if role == "worker" else role
    role_config = config.get(role_key) or {}
    display_name = "Worker" if role == "worker" else "Lead"
    capabilities = (
        ["inspection", "implementation", "tests", "talk"]
        if role == "worker"
        else ["delegation", "review", "talk"]
    )
    async with httpx.AsyncClient(base_url=broker_url(config)) as client:
        response = await client.post(
            "/v1/agents/register",
            headers={"X-API-Key": broker_api_key(config)},
            json={
                "project_id": str(config.get("project_id", "default")),
                "agent_id": role_agent_id(config, role_key),
                "role": role,
                "display_name": role_config.get("display_name", display_name),
                "capabilities": role_config.get("capabilities", capabilities),
            },
        )
        response.raise_for_status()
        return response.json()


def register_project_role_sync(config: dict[str, Any], role: str) -> dict[str, Any]:
    return asyncio.run(register_project_role(config, role))


def project_query(config: dict[str, Any], prefix: str = "?") -> str:
    project_id = quote(current_project_id(config), safe="")
    return f"{prefix}project_id={project_id}"


def activity_query(config: dict[str, Any], item_id: str | None = None, limit: int = 10) -> str:
    path = f"/v1/activity?limit={limit}{project_query(config, '&')}"
    if item_id:
        path += f"&item_id={quote(item_id, safe='')}"
    return path


def task_activity_query(config: dict[str, Any], task_id: str, limit: int = 10) -> str:
    return f"/v1/tasks/{quote(task_id, safe='')}/activity?limit={limit}{project_query(config, '&')}"


def jobs_query(
    config: dict[str, Any],
    limit: int = 50,
    active: bool = False,
    status: str | None = None,
    kind: str | None = None,
    item_id: str | None = None,
) -> str:
    params: dict[str, str] = {"limit": str(limit), "project_id": current_project_id(config)}
    if active:
        params["active"] = "true"
    if status:
        params["status"] = status.upper()
    if kind:
        params["kind"] = kind.lower()
    if item_id:
        params["id"] = item_id
    return f"/v1/jobs?{urlencode(params)}"


def parse_iso_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def human_age(value: Any) -> str:
    parsed = parse_iso_time(value)
    if parsed is None:
        return "unknown age"
    seconds = max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))
    if seconds < 60:
        return f"{seconds}s ago"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s ago"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m ago"


def activity_preview(activity: dict[str, Any]) -> str:
    tool = str(activity.get("tool_name") or "")
    detail = str(
        activity.get("detail") or activity.get("phase") or activity.get("activity_type") or ""
    ).strip()
    if tool and detail:
        return f"{tool}: {detail}"
    return tool or detail


def format_activity(activity: dict[str, Any]) -> str:
    timestamp = str(activity.get("time") or "")
    stamp = timestamp[11:19] if len(timestamp) >= 19 else timestamp
    age = human_age(timestamp)
    kind = str(activity.get("activity_type") or "activity")
    preview = activity_preview(activity)
    return f"[{stamp}] {kind} ({age}) {preview}".rstrip()


def stale_heartbeat(job: dict[str, Any]) -> bool:
    status = str(job.get("status") or "").upper()
    return job.get("last_activity_type") == "heartbeat" and status not in ACTIVE_ACTIVITY_STATUSES


def sanitize_job(job: dict[str, Any]) -> dict[str, Any]:
    clean = dict(job)
    if stale_heartbeat(clean):
        clean.pop("last_activity_at", None)
        clean.pop("last_activity_type", None)
        clean.pop("last_activity_tool", None)
        clean.pop("last_activity_preview", None)
    return clean


def job_activity_line(job: dict[str, Any]) -> str:
    if stale_heartbeat(job) or not job.get("last_activity_at"):
        return ""
    activity = {
        "time": job.get("last_activity_at"),
        "activity_type": job.get("last_activity_type"),
        "tool_name": job.get("last_activity_tool"),
        "detail": job.get("last_activity_preview"),
    }
    return format_activity(activity)


def job_id(job: dict[str, Any]) -> str:
    return job_id_for(job)


def job_kind(job: dict[str, Any]) -> str:
    return job_kind_for(job)


def job_route(job: dict[str, Any]) -> str:
    return f"{job.get('from_agent', '-')} → {job.get('to_agent', '-')}"


def filter_jobs(
    jobs: list[dict[str, Any]],
    active: bool = False,
    status: str | None = None,
    kind: str | None = None,
    item_id: str | None = None,
) -> list[dict[str, Any]]:
    selected = list(jobs)
    if active:
        selected = [job for job in selected if is_active_job_status(job.get("status"))]
    if status:
        expected_status = status.upper()
        selected = [job for job in selected if str(job.get("status") or "").upper() == expected_status]
    if kind:
        expected_kind = kind.lower()
        selected = [job for job in selected if job_kind(job) == expected_kind]
    if item_id:
        selected = [job for job in selected if job_matches_id(job, item_id)]
    return selected


def blocking_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [job for job in jobs if is_active_job_status(job.get("status"))]


def task_body_project_id(body: dict[str, Any]) -> str | None:
    for source in (body, body.get("job") or {}, body.get("reply") or {}):
        value = source.get("project_id") if isinstance(source, dict) else None
        if value:
            return str(value)
    return None


def next_conversation_id(config: dict[str, Any]) -> str:
    try:
        body = broker_get_sync(config, f"/v1/jobs?limit=500{project_query(config, '&')}")
    except httpx.HTTPError:
        return "C001"
    highest = 0
    for job in body.get("jobs", []):
        value = str(job.get("conversation_id") or "")
        if len(value) == 4 and value.startswith("C") and value[1:].isdigit():
            highest = max(highest, int(value[1:]))
    return f"C{highest + 1:03d}"


def conversation_state(config: dict[str, Any], conversation_id: str) -> dict[str, Any] | None:
    body = broker_get_sync(config, f"/v1/jobs?limit=500{project_query(config, '&')}")
    for job in body.get("jobs", []):
        if job.get("conversation_id") == conversation_id:
            return job
    return None


def load_project_or_exit() -> dict[str, Any]:
    """Resolve ``.orch/project.yaml`` or exit with a Typer error."""
    try:
        return load_project_config()
    except ProjectConfigError as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc


def auto_refresh_project_skills(config: dict[str, Any]) -> None:
    """Refresh ``.orch/skills/*`` if templates have moved; used by lead/work."""
    from orchlink.project.init import refresh_project_skills_if_needed

    refreshed = refresh_project_skills_if_needed(project_root(config))
    if refreshed:
        console.print(
            f"[Orch] Refreshed project skills from current templates: {', '.join(refreshed)}"
        )


__all__ = [
    "register_project_role_sync",
    "register_project_role",
    "project_query",
    "activity_query",
    "task_activity_query",
    "jobs_query",
    "parse_iso_time",
    "human_age",
    "activity_preview",
    "format_activity",
    "stale_heartbeat",
    "sanitize_job",
    "job_activity_line",
    "job_id",
    "job_kind",
    "job_route",
    "filter_jobs",
    "blocking_jobs",
    "task_body_project_id",
    "next_conversation_id",
    "conversation_state",
    "load_project_or_exit",
    "auto_refresh_project_skills",
    "broker_get_sync",
]
