"""``orch jobs`` and ``orch sessions`` — job inspection and session commands."""

from __future__ import annotations

import json
import time
from typing import Annotated, Any

import httpx
import typer

from rich.console import Console

from orchlink.cli import main as _cli_main
from orchlink.cli.commands._helpers import (
    activity_query,
    blocking_jobs,
    current_project_id,
    conversation_state,
    filter_jobs,
    format_activity,
    human_age,
    job_id,
    job_kind,
    job_route,
    jobs_query,
    load_project_or_exit,
    project_query,
    sanitize_job,
    task_activity_query,
    task_body_project_id,
)
from orchlink.project.config import broker_api_key, broker_url, normalize_worker_name, resolve_agent_id, worker_name_from_agent


console = Console()


def _print_conversation_body(conversation: dict[str, Any]) -> None:
    from orchlink.cli.commands.talk import _print_conversation_body as _talk_version

    _talk_version(conversation)


def _chat_text_from_event(event: dict[str, Any]) -> str:
    message_payload = event.get("payload") or {}
    text = message_payload.get("summary") or message_payload.get("stdout") or message_payload.get("message") or ""
    return str(text).strip()


def _conversation_turns_from_events(config: dict[str, Any], conversation_id: str) -> list[dict[str, str]]:
    body = _cli_main.fetch_events_sync(
        broker_url(config),
        broker_api_key(config),
        since=0,
        limit=200,
        project_id=current_project_id(config),
    )
    turns: list[dict[str, str]] = []
    for event in body.get("events", []):
        if event.get("type") not in {"message_queued", "reply_received"}:
            continue
        if event.get("conversation_id") != conversation_id:
            continue
        message_type = str(event.get("message_type") or "")
        if message_type not in {"CHAT_START", "CHAT_TURN", "CHAT_REPLY", "CHAT_CLOSE"}:
            continue
        text = _chat_text_from_event(event)
        if not text:
            continue
        turns.append(
            {
                "speaker": str(event.get("from_agent") or "?"),
                "turn": str(event.get("turn") or "?"),
                "max_turns": str(event.get("max_turns") or "?"),
                "text": text,
            }
        )
    return turns


def _print_conversation_turns(config: dict[str, Any], conversation_id: str) -> None:
    try:
        turns = _conversation_turns_from_events(config, conversation_id)
    except (RuntimeError, httpx.HTTPError):
        return
    if not turns:
        return
    console.print("[Orch] Conversation turns:")
    for turn in turns:
        text = turn["text"]
        if len(text) > 1200:
            text = f"{text[:1199]}…"
        console.print(f"- {turn['speaker']} · {turn['turn']}/{turn['max_turns']}: {text}")


def _print_task_body(body: dict[str, Any]) -> None:
    from orchlink.cli.commands.tasks import _print_task_body as _tasks_version

    _tasks_version(body)


def worker_name_for_job(config: dict[str, Any], job: dict[str, Any]) -> str:
    return str(job.get("worker_name") or worker_name_from_agent(config, str(job.get("to_agent") or "")))


def filter_jobs_by_worker_name(config: dict[str, Any], jobs: list[dict[str, Any]], worker_name: str | None) -> list[dict[str, Any]]:
    if not worker_name:
        return jobs
    target_name = normalize_worker_name(worker_name)
    target_agent = resolve_agent_id(config, target_name)
    return [job for job in jobs if str(job.get("to_agent") or "") == target_agent or worker_name_for_job(config, job) == target_name]


def validate_task_body_project(config: dict[str, Any], body: dict[str, Any], task_id: str) -> None:
    """Refuse cross-project / unscoped task results so lead does not act on stale data."""
    status = str(body.get("status") or "").upper()
    if status in {"WAIT_TIMEOUT", "MISSING"}:
        return
    expected = current_project_id(config)
    actual = task_body_project_id(body)
    if actual == expected:
        return
    if actual:
        console.print(
            f"[Orch] Refusing cross-project result for {task_id}: broker returned project {actual}, current project is {expected}."
        )
    else:
        console.print(
            f"[Orch] Refusing unscoped result for {task_id}: broker response has no project_id. The broker is likely stale."
        )
    console.print("[Orch] Run: orch stop --all && orch lead --new && orch work --new")
    raise typer.Exit(1)


def _jobs_body(
    config: dict[str, Any],
    *,
    limit: int,
    active: bool = False,
    status: str | None = None,
    kind: str | None = None,
    item_id: str | None = None,
    worker_name: str | None = None,
) -> dict[str, Any]:
    broker_limit = 500 if (active or status or kind or item_id or worker_name) else limit
    body = _cli_main.broker_get_sync(
        config,
        jobs_query(config, limit=broker_limit, active=active, status=status, kind=kind, item_id=item_id),
    )
    filtered = filter_jobs(body.get("jobs", []), active=active, status=status, kind=kind, item_id=item_id)
    filtered = filter_jobs_by_worker_name(config, filtered, worker_name)
    body["jobs"] = [sanitize_job(job) for job in filtered[:limit]]
    return body


def _print_jobs_table(config: dict[str, Any], jobs: list[dict[str, Any]]) -> None:
    console.print("ID\tWORKER\tKIND\tMODE\tSTATUS\tUPDATED\tROUTE\tPREVIEW")
    for job in jobs:
        preview = str(job.get("preview") or job.get("last_message_preview") or "")
        console.print(
            f"{job_id(job)}\t{worker_name_for_job(config, job)}\t{job_kind(job)}\t{job.get('mode', '-')}\t{job.get('status', '-')}\t"
            f"{human_age(job.get('updated_at') or job.get('created_at'))}\t{job_route(job)}\t{preview}"
        )
        activity = _cli_main.job_activity_line(job)
        if activity:
            console.print(f"  last activity: {activity}")


def _print_one_job(
    config: dict[str, Any],
    item_id: str,
    *,
    json_output: bool = False,
    active: bool = False,
    status: str | None = None,
    kind: str | None = None,
    worker_name: str | None = None,
) -> None:
    body = _jobs_body(config, limit=1, active=active, status=status, kind=kind, item_id=item_id, worker_name=worker_name)
    if json_output:
        console.print_json(json.dumps(body))
        return
    jobs = body.get("jobs") or []
    if not jobs:
        console.print(f"[Orch] No job found for {item_id}.")
        raise typer.Exit(1)
    job = jobs[0]
    console.print(f"[Orch] Job {job_id(job)}: {job_kind(job)} {job.get('mode', '-')} {job.get('status', '-')}")
    console.print(f"[Orch] Worker: {worker_name_for_job(config, job)}")
    console.print(f"[Orch] Route: {job_route(job)}")
    console.print(f"[Orch] Updated: {human_age(job.get('updated_at') or job.get('created_at'))}")
    activity = _cli_main.job_activity_line(job)
    if activity:
        console.print(f"[Orch] Last activity: {activity}")
    preview = str(job.get("preview") or job.get("last_message_preview") or "").strip()
    if preview:
        console.print(preview)


def _print_idle_state(config: dict[str, Any], *, limit: int, worker_name: str | None) -> None:
    body = _cli_main.broker_get_sync(config, jobs_query(config, limit=limit, active=True))
    pending = blocking_jobs(filter_jobs_by_worker_name(config, body.get("jobs", []), worker_name))
    if not pending:
        label = f"Worker '{normalize_worker_name(worker_name)}'" if worker_name else "Worker"
        console.print(f"[Orch] {label} idle: no pending tasks or open talks.")
        return

    console.print("[Orch] Worker is not idle. Pending worker work exists:")
    for job in pending:
        preview = str(job.get("preview") or job.get("last_message_preview") or "")
        console.print(
            f"- {worker_name_for_job(config, job)}: {job_id(job)} {job_kind(job)} {job.get('mode', '-')} {job.get('status', '-')}: {preview}"
        )
        activity = _cli_main.job_activity_line(job)
        if activity:
            console.print(f"  last activity: {activity}")
    console.print("[Orch] Do not run dependent full tests or final conclusions yet.")
    raise typer.Exit(1)


def _print_activity(config: dict[str, Any], item_id: str, *, limit: int) -> None:
    try:
        body = _cli_main.broker_get_sync(config, task_activity_query(config, item_id, limit=limit))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        body = _cli_main.broker_get_sync(config, activity_query(config, item_id=item_id, limit=limit))
    activity = body.get("activity") or []
    if not activity:
        console.print(f"[Orch] No worker activity recorded for {item_id} yet.")
        console.print(f"[Orch] Check job status: orch jobs --id {item_id}")
        console.print(f"[Orch] Block for result: orch jobs --wait {item_id}")
        return

    console.print(f"[Orch] Recent worker activity for {item_id}:")
    for item in activity:
        console.print(f"- {format_activity(item)}")


def _print_result(config: dict[str, Any], item_id: str) -> None:
    body = _cli_main.broker_get_sync(config, f"/v1/tasks/{item_id}{project_query(config)}")
    validate_task_body_project(config, body, item_id)
    if body.get("status") == "missing":
        conversation = conversation_state(config, item_id)
        if conversation is not None:
            _print_conversation_body(conversation)
            _print_conversation_turns(config, item_id)
            return
        _print_task_body(body)
        raise typer.Exit(1)
    if body.get("reply") or body.get("error"):
        _print_task_body(body)
        return
    status = str(body.get("status") or "UNKNOWN")
    console.print(f"[Orch] Job {item_id} is not finished yet (status: {status}).")
    console.print(f"[Orch] Check live activity: orch jobs --live {item_id}")
    console.print(f"[Orch] Block for result: orch jobs --wait {item_id}")
    raise typer.Exit(1)


def _wait_for_result(
    config: dict[str, Any],
    task_id: str,
    *,
    timeout: int,
    progress: bool,
    poll_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout
    last_activity_id = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _print_task_body(
                {
                    "status": "WAIT_TIMEOUT",
                    "task_id": task_id,
                    "error": "No task result arrived before the wait timeout.",
                }
            )
            return
        wait_seconds = timeout if not progress else max(1, min(poll_seconds, int(remaining)))
        body = _cli_main.broker_get_sync(
            config,
            f"/v1/tasks/{task_id}/wait?timeout_seconds={wait_seconds}{project_query(config, '&')}",
        )
        if body.get("status") != "WAIT_TIMEOUT":
            returned_task_id = body.get("task_id")
            if returned_task_id and str(returned_task_id) != task_id:
                console.print(
                    f"[Orch] Broker returned result for {returned_task_id} while waiting for {task_id}; ignoring stale response."
                )
                raise typer.Exit(1)
            validate_task_body_project(config, body, task_id)
            _print_task_body(body)
            if body.get("status") == "missing":
                raise typer.Exit(1)
            return
        if not progress:
            _print_task_body(body)
            return
        try:
            activity_body = _cli_main.broker_get_sync(config, task_activity_query(config, task_id, limit=5))
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != 404:
                activity_body = {"activity": []}
            else:
                try:
                    activity_body = _cli_main.broker_get_sync(config, activity_query(config, item_id=task_id, limit=5))
                except httpx.HTTPError:
                    activity_body = {"activity": []}
        except httpx.HTTPError:
            activity_body = {"activity": []}
        for activity in activity_body.get("activity", []):
            activity_id = int(activity.get("id") or 0)
            if activity_id <= last_activity_id:
                continue
            console.print(f"[Orch] Worker activity: {format_activity(activity)}")
            last_activity_id = activity_id


def _cancel_job(config: dict[str, Any], item_id: str, *, reason: str) -> None:
    from orchlink.cli.main import print_orch_exception  # late import

    try:
        body = _cli_main.broker_post_sync(
            config,
            f"/v1/jobs/{item_id}/cancel",
            {"reason": reason, "project_id": current_project_id(config)},
        )
    except (RuntimeError, httpx.HTTPError) as exc:
        print_orch_exception(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] Cancelled {item_id}.")
    console.print(
        "[Orch] Note: cancel marks broker work CANCELLED and asks Pi to abort the current turn. Pi can stop before the next tool call; an already-running shell command may only stop if Pi's abort reaches it."
    )
    cancelled = body.get("cancelled") or []
    if cancelled:
        console.print(f"[Orch] Messages: {', '.join(str(item) for item in cancelled)}")


def register_jobs(app: typer.Typer) -> None:
    """Register jobs and sessions commands on the given Typer app."""

    @app.command(help="Inspect and control tracked work. List jobs by default; pass a job ID or use --live/--result/--wait/--cancel for one job.")
    def jobs(
        item: Annotated[str | None, typer.Argument(help="Optional job ID to inspect, equivalent to --id.")] = None,
        limit: Annotated[int, typer.Option("--limit", metavar="N", help="recent jobs to show; caps --live rows.")] = 50,
        active: Annotated[bool, typer.Option("--active", help="Show only active/open work.")] = False,
        idle: Annotated[bool, typer.Option("--idle", help="Exit 0 when no active work; 1 when busy.")] = False,
        status: Annotated[str | None, typer.Option("--status", metavar="STATUS", help="Filter by status.")] = None,
        kind: Annotated[str | None, typer.Option("--kind", metavar="KIND", help="Filter by task or talk.")] = None,
        item_id: Annotated[str | None, typer.Option("--id", metavar="ID", help="Inspect one job.")] = None,
        live_id: Annotated[str | None, typer.Option("--live", metavar="ID", help="Show recent activity for one job.")] = None,
        result_id: Annotated[str | None, typer.Option("--result", metavar="ID", help="Print completed result.")] = None,
        wait_id: Annotated[str | None, typer.Option("--wait", metavar="ID", help="Wait for one result.")] = None,
        cancel_id: Annotated[str | None, typer.Option("--cancel", metavar="ID", help="Cancel one active job.")] = None,
        reason: Annotated[str, typer.Option("--reason", "-m", metavar="TEXT", help="Cancel reason.")] = "Cancelled by lead.",
        worker_name: Annotated[str | None, typer.Option("--name", metavar="NAME", help="Filter by worker name.")] = None,
        timeout: Annotated[int, typer.Option("--timeout", metavar="SEC", help="Maximum seconds for --wait.")] = 1800,
        no_progress: Annotated[bool, typer.Option("--no-progress", help="Hide activity while --wait is pending.")] = False,
        poll_seconds: Annotated[int, typer.Option("--poll-seconds", metavar="SEC", min=1, max=60, help="Seconds between wait polls.")] = 5,
        json_output: Annotated[bool, typer.Option("--json", help="Print raw jobs JSON.")] = False,
    ) -> None:
        normalized_kind = kind.lower() if kind else None
        if normalized_kind and normalized_kind not in {"task", "talk"}:
            console.print("[Orch] --kind must be 'task' or 'talk'.")
            raise typer.Exit(1)
        if item and item_id:
            console.print("[Orch] Pass the job ID either positionally or with --id, not both.")
            raise typer.Exit(1)
        effective_item_id = item_id or item
        action_values = [value for value in [effective_item_id, live_id, result_id, wait_id, cancel_id] if value]
        if len(action_values) > 1:
            console.print("[Orch] Choose only one job action: ID, --live, --result, --wait, or --cancel.")
            raise typer.Exit(1)
        if idle and action_values:
            console.print("[Orch] --idle cannot be combined with one-job actions.")
            raise typer.Exit(1)

        config = load_project_or_exit()
        try:
            _cli_main.ensure_broker_running(config)
            if idle:
                _print_idle_state(config, limit=limit, worker_name=worker_name)
                return
            if effective_item_id:
                _print_one_job(
                    config,
                    effective_item_id,
                    json_output=json_output,
                    active=active,
                    status=status,
                    kind=normalized_kind,
                    worker_name=worker_name,
                )
                return
            if live_id:
                _print_activity(config, live_id, limit=min(max(1, limit), 100))
                return
            if result_id:
                _print_result(config, result_id)
                return
            if wait_id:
                _wait_for_result(config, wait_id, timeout=timeout, progress=not no_progress, poll_seconds=poll_seconds)
                return
            if cancel_id:
                _cancel_job(config, cancel_id, reason=reason)
                return
            body = _jobs_body(
                config,
                limit=limit,
                active=active,
                status=status,
                kind=normalized_kind,
                worker_name=worker_name,
            )
        except typer.Exit:
            raise
        except (RuntimeError, httpx.HTTPError) as exc:
            console.print(f"[Orch] {exc}")
            raise typer.Exit(1) from exc
        if json_output:
            console.print_json(json.dumps(body))
            return
        _print_jobs_table(config, body.get("jobs", []))

    @app.command(help="Show registered lead and named worker Pi sessions for the current project.")
    def sessions(
        active: Annotated[
            bool,
            typer.Option("--active/--all", help="Show only active sessions, or include released history."),
        ] = True,
        worker_name: Annotated[str | None, typer.Option("--name", help="Show sessions for one named worker.")] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Print raw sessions JSON.")] = False,
    ) -> None:
        config = load_project_or_exit()
        try:
            _cli_main.ensure_broker_running(config)
            body = _cli_main.broker_get_sync(
                config,
                f"/v1/sessions?active={str(active).lower()}{project_query(config, '&')}",
            )
        except (RuntimeError, httpx.HTTPError) as exc:
            console.print(f"[Orch] {exc}")
            raise typer.Exit(1) from exc

        sessions_list = body.get("sessions", [])
        if worker_name:
            target_name = normalize_worker_name(worker_name)
            target_agent = resolve_agent_id(config, target_name)
            sessions_list = [
                session
                for session in sessions_list
                if str(session.get("agent_id") or "") == target_agent
                or str(session.get("worker_name") or "") == target_name
            ]
            body["sessions"] = sessions_list
        if json_output:
            console.print_json(json.dumps(body))
            return

        if not sessions_list:
            console.print(
                "[Orch] No active lead/work sessions registered for this project."
                if active
                else "[Orch] No lead/work sessions registered for this project."
            )
            return

        console.print("NAME\tAGENT\tMODEL\tTHINKING\tROLE\tRUNTIME\tBACKEND\tSTATUS\tPID\tSESSION\tREADY\tHEARTBEAT")
        for session in sessions_list:
            console.print(
                f"{session.get('worker_name') or worker_name_from_agent(config, str(session.get('agent_id') or ''))}\t"
                f"{session.get('agent_id', '-')}\t{session.get('model') or '-'}\t{session.get('thinking') or '-'}\t"
                f"{session.get('role', '-')}\t{session.get('runtime_mode') or '-'}\t{session.get('backend') or '-'}\t"
                f"{session.get('status', '-')}\t{session.get('pid', '-')}\t{session.get('session_id', '-')}\t"
                f"{session.get('ready', '-')}\t{human_age(session.get('last_heartbeat_at'))}"
            )
