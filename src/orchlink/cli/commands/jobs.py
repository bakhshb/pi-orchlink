"""``orch task``, ``orch jobs``, ``orch idle``, ``orch peek``, ``orch sessions``,
``orch get``, ``orch wait``, ``orch cancel`` — job inspection and wait commands."""

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
from orchlink.project.config import broker_api_key, broker_url


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
    from orchlink.cli.commands.ask import _print_task_body as _ask_version

    _ask_version(body)


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
    console.print("[Orch] Run: orch stop && orch lead --new && orch work --new")
    raise typer.Exit(1)


def register_jobs(app: typer.Typer) -> None:
    """Register all jobs/sessions/inspection/wait commands on the given Typer app."""

    @app.command(help="Show live broker status for a task: route, delivery, and latest activity. Use `orch get` for the final result body.")
    def task(task_id: str) -> None:
        config = load_project_or_exit()
        try:
            _cli_main.ensure_broker_running(config)
            status_body = _cli_main.fetch_status_sync(
                broker_url(config), broker_api_key(config), project_id=current_project_id(config)
            )
            events_body = _cli_main.fetch_events_sync(
                broker_url(config), broker_api_key(config), limit=500, project_id=current_project_id(config)
            )
        except (RuntimeError, httpx.HTTPError) as exc:
            console.print(f"[Orch] {exc}")
            raise typer.Exit(1) from exc

        messages = [item for item in status_body.get("active_messages", []) if item.get("task_id") == task_id]
        events = [item for item in events_body.get("events", []) if item.get("task_id") == task_id]
        if not messages and not events:
            console.print(f"[Orch] No broker record found for task {task_id}.")
            return

        latest_message = messages[-1] if messages else {}
        status_text = str(latest_message.get("status") or "UNKNOWN")
        console.print(f"[Orch] Task {task_id}: {status_text}")
        console.print(f"[Orch] Route: {latest_message.get('from_agent', '-')} → {latest_message.get('to_agent', '-')}")
        activity_events = [item for item in events if item.get("type") == "worker_activity"]
        if activity_events:
            console.print(f"[Orch] Last worker activity: {format_activity(activity_events[-1].get('payload') or activity_events[-1])}")

        reply_events = [item for item in events if item.get("type") == "reply_received"]
        if reply_events:
            reply = reply_events[-1]
            console.print(
                f"[Orch] Reply: {reply.get('message_type', 'RESULT')} "
                f"from {reply.get('from_agent', 'work')} to {reply.get('to_agent', 'lead')}"
            )
            preview = str(reply.get("preview") or "").strip()
            if preview:
                console.print(preview)
            return

        delivered_events = [item for item in events if item.get("type") == "message_delivered"]
        if delivered_events:
            delivered = delivered_events[-1]
            console.print(f"[Orch] Delivered to {delivered.get('to_agent', 'worker')}. Worker is still in progress.")
        else:
            console.print("[Orch] Queued. Waiting for worker pickup.")

    @app.command(help="Show recent tasks and Talk conversations for the current project.")
    def jobs(
        limit: Annotated[int, typer.Option("--limit", help="Maximum number of recent jobs to show.")] = 50,
        active: Annotated[bool, typer.Option("--active", help="Show only pending/running/open work.")] = False,
        status: Annotated[str | None, typer.Option("--status", help="Show only jobs with this status.")] = None,
        kind: Annotated[str | None, typer.Option("--kind", help="Show only task or talk jobs.")] = None,
        item_id: Annotated[str | None, typer.Option("--id", help="Show one task/conversation/message ID.")] = None,
        json_output: Annotated[bool, typer.Option("--json", help="Print raw jobs JSON.")] = False,
    ) -> None:
        normalized_kind = kind.lower() if kind else None
        if normalized_kind and normalized_kind not in {"task", "talk"}:
            console.print("[Orch] --kind must be 'task' or 'talk'.")
            raise typer.Exit(1)

        config = load_project_or_exit()
        broker_limit = 500 if (active or status or normalized_kind or item_id) else limit
        try:
            _cli_main.ensure_broker_running(config)
            body = _cli_main.broker_get_sync(
                config,
                jobs_query(
                    config,
                    limit=broker_limit,
                    active=active,
                    status=status,
                    kind=normalized_kind,
                    item_id=item_id,
                ),
            )
        except (RuntimeError, httpx.HTTPError) as exc:
            console.print(f"[Orch] {exc}")
            raise typer.Exit(1) from exc
        body["jobs"] = [
            sanitize_job(job)
            for job in filter_jobs(
                body.get("jobs", []),
                active=active,
                status=status,
                kind=normalized_kind,
                item_id=item_id,
            )[:limit]
        ]
        if json_output:
            console.print_json(json.dumps(body))
            return

        console.print("ID\tKIND\tMODE\tSTATUS\tUPDATED\tROUTE\tPREVIEW")
        for job in body.get("jobs", []):
            preview = str(job.get("preview") or job.get("last_message_preview") or "")
            console.print(
                f"{job_id(job)}\t{job_kind(job)}\t{job.get('mode', '-')}\t{job.get('status', '-')}\t"
                f"{human_age(job.get('updated_at') or job.get('created_at'))}\t{job_route(job)}\t{preview}"
            )
            activity = _cli_main.job_activity_line(job)
            if activity:
                console.print(f"  last activity: {activity}")

    @app.command(help="Show registered lead/work Pi sessions for the current project.")
    def sessions(
        active: Annotated[
            bool,
            typer.Option("--active/--all", help="Show only active sessions, or include released history."),
        ] = True,
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

        console.print("AGENT\tROLE\tSTATUS\tPID\tSESSION\tHEARTBEAT")
        for session in sessions_list:
            console.print(
                f"{session.get('agent_id', '-')}\t{session.get('role', '-')}\t{session.get('status', '-')}\t"
                f"{session.get('pid', '-')}\t{session.get('session_id', '-')}\t{human_age(session.get('last_heartbeat_at'))}"
            )

    @app.command(help="Exit 0 if the worker lane is idle; exit 1 if active work exists.")
    def idle(
        limit: Annotated[int, typer.Option("--limit", help="Maximum number of recent jobs to inspect.")] = 50,
    ) -> None:
        config = load_project_or_exit()
        try:
            _cli_main.ensure_broker_running(config)
            body = _cli_main.broker_get_sync(config, jobs_query(config, limit=limit, active=True))
        except (RuntimeError, httpx.HTTPError) as exc:
            console.print(f"[Orch] {exc}")
            raise typer.Exit(1) from exc

        pending = blocking_jobs(body.get("jobs", []))
        if not pending:
            console.print("[Orch] Worker idle: no pending tasks or open talks.")
            return

        console.print("[Orch] Worker is not idle. Pending worker work exists:")
        for job in pending:
            preview = str(job.get("preview") or job.get("last_message_preview") or "")
            console.print(f"- {job_id(job)} {job_kind(job)} {job.get('mode', '-')} {job.get('status', '-')}: {preview}")
            activity = _cli_main.job_activity_line(job)
            if activity:
                console.print(f"  last activity: {activity}")
        console.print("[Orch] Do not run dependent full tests or final conclusions yet.")
        raise typer.Exit(1)

    @app.command(help="Show recent worker activity for a long-running task or conversation.")
    def peek(
        item_id: str,
        limit: Annotated[int, typer.Option("--limit", min=1, max=100, help="Maximum activity rows to show.")] = 10,
    ) -> None:
        config = load_project_or_exit()
        try:
            _cli_main.ensure_broker_running(config)
            try:
                body = _cli_main.broker_get_sync(config, task_activity_query(config, item_id, limit=limit))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                body = _cli_main.broker_get_sync(config, activity_query(config, item_id=item_id, limit=limit))
        except (RuntimeError, httpx.HTTPError) as exc:
            console.print(f"[Orch] {exc}")
            raise typer.Exit(1) from exc

        activity = body.get("activity") or []
        if not activity:
            console.print(f"[Orch] No worker activity recorded for {item_id}.")
            console.print(
                "[Orch] If the task is pending, the worker may not have picked it up yet or the broker/session is stale."
            )
            return

        console.print(f"[Orch] Recent worker activity for {item_id}:")
        for item in activity:
            console.print(f"- {format_activity(item)}")

    @app.command("get", help="Print a completed task result, or a conversation summary for a conversation ID.")
    def get_command(item_id: str) -> None:
        config = load_project_or_exit()
        try:
            _cli_main.ensure_broker_running(config)
            body = _cli_main.broker_get_sync(config, f"/v1/tasks/{item_id}{project_query(config)}")
            validate_task_body_project(config, body, item_id)
            if body.get("status") == "missing":
                conversation = conversation_state_shim(config, item_id)
                if conversation is not None:
                    _print_conversation_body(conversation)
                    _print_conversation_turns(config, item_id)
                    return
        except (RuntimeError, httpx.HTTPError) as exc:
            console.print(f"[Orch] {exc}")
            raise typer.Exit(1) from exc
        _print_task_body(body)

    @app.command("wait", help="Wait for one exact task result; timeout does not cancel the task.")
    def wait_command(
        task_id: str,
        timeout: Annotated[int, typer.Option("--timeout", help="Maximum seconds to wait in this shell.")] = 1800,
        progress: Annotated[
            bool,
            typer.Option("--progress/--no-progress", help="Print worker activity while waiting."),
        ] = True,
        poll_seconds: Annotated[
            int,
            typer.Option("--poll-seconds", min=1, max=60, help="Seconds between progress polls."),
        ] = 5,
    ) -> None:
        config = load_project_or_exit()
        try:
            _cli_main.ensure_broker_running(config)
        except RuntimeError as exc:
            console.print(f"[Orch] {exc}")
            raise typer.Exit(1) from exc

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
            try:
                body = _cli_main.broker_get_sync(
                    config,
                    f"/v1/tasks/{task_id}/wait?timeout_seconds={wait_seconds}{project_query(config, '&')}",
                )
            except httpx.HTTPError as exc:
                console.print(f"[Orch] {exc}")
                raise typer.Exit(1) from exc
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

    @app.command(help="Mark active work CANCELLED and ask Pi to stop the current turn.")
    def cancel(
        item_id: str,
        reason: Annotated[str, typer.Option("--reason", "-m", help="Reason recorded with the cancellation.")] = "Cancelled by lead.",
    ) -> None:
        from orchlink.cli.main import print_orch_exception  # late import

        config = load_project_or_exit()
        try:
            _cli_main.ensure_broker_running(config)
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


def conversation_state_shim(config: dict[str, Any], conversation_id: str) -> dict[str, Any] | None:
    return _cli_main.conversation_state(config, conversation_id)
