from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from orchlink.goal.checks import parse_acceptance_criteria
from orchlink.goal.runner import GoalRunner
from orchlink.goal.store import GoalStore, GoalStoreError
from orchlink.project.config import ProjectConfigError, load_project_config


goal_app = typer.Typer(help="Durable PRD/plan-driven goal tracking.")
console = Console()


def load_config_or_exit() -> dict:
    try:
        return load_project_config()
    except ProjectConfigError as exc:
        console.print(f"[Orch] {exc}")
        raise typer.Exit(1) from exc


def store_or_exit() -> GoalStore:
    return GoalStore(load_config_or_exit())


def print_goal_error(exc: Exception) -> None:
    console.print(f"[Orch] {exc}")


def read_source(prd: str | None, plan: str | None, text: str | None) -> tuple[str, str]:
    selected = [("prd", prd), ("plan", plan), ("text", text)]
    present = [(kind, value) for kind, value in selected if value]
    if len(present) != 1:
        raise GoalStoreError("Provide exactly one source: --prd, --plan, or --text.")
    source_type, value = present[0]
    assert value is not None
    if source_type == "text":
        if not value.strip():
            raise GoalStoreError("--text cannot be empty.")
        return source_type, value
    path = Path(value)
    if not path.is_file():
        raise GoalStoreError(f"Source file not found: {value}")
    return source_type, path.read_text(encoding="utf-8")


@goal_app.command(help="Create a goal from a PRD, plan, or inline text source.")
def start(
    title: str,
    prd: Annotated[str | None, typer.Option("--prd", help="Path to a PRD source file.")] = None,
    plan: Annotated[str | None, typer.Option("--plan", help="Path to a plan source file.")] = None,
    text: Annotated[str | None, typer.Option("--text", help="Inline goal source text.")] = None,
    derive_artifacts: Annotated[bool, typer.Option("--derive", help="Ask the worker to derive acceptance criteria and plan immediately.")] = False,
    timeout: Annotated[int, typer.Option("--timeout", help="Seconds to wait when --derive is used.")] = 1800,
) -> None:
    try:
        store = store_or_exit()
        source_type, source_text = read_source(prd, plan, text)
        goal = store.create_goal(title=title, source_type=source_type, source_text=source_text)
        if derive_artifacts:
            message = GoalRunner(load_config_or_exit()).derive(goal.id, timeout_seconds=timeout)
            console.print(f"[Orch] {message}")
    except (GoalStoreError, OSError) as exc:
        print_goal_error(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] Created goal {goal.id}: {goal.title}")
    console.print(f"[Orch] Source: {goal.source}")
    console.print(f"[Orch] State: .orch/goals/{goal.id}")
    console.print(f"[Orch] Review acceptance.md and plan.md, then approve: orch goal gate {goal.id} approve")


@goal_app.command("list", help="List goals in this project.")
def list_goals() -> None:
    try:
        goals = store_or_exit().list_goals()
    except GoalStoreError as exc:
        print_goal_error(exc)
        raise typer.Exit(1) from exc
    if not goals:
        console.print("[Orch] No goals found.")
        return
    console.print("ID\tSTATUS\tAC\tPLAN\tTITLE")
    for goal in goals:
        console.print(f"{goal.id}\t{goal.status}\t{goal.ac_gate}\t{goal.plan_gate}\t{goal.title}")


def _read_goal_artifact(store: GoalStore, goal_id: str, name: str) -> str:
    path = store.goal_dir(goal_id) / name
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def _source_summary(source: str, limit: int = 500) -> str:
    cleaned = " ".join(line.strip() for line in source.splitlines() if line.strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


@goal_app.command(help="Show one goal and its artifact paths.")
def show(goal_id: str) -> None:
    try:
        store = store_or_exit()
        goal = store.load(goal_id)
    except GoalStoreError as exc:
        print_goal_error(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] Goal {goal.id}: {goal.title}")
    console.print(f"Status: {goal.status}")
    console.print(f"Source: {goal.source}")
    console.print(f"Acceptance gate: {goal.ac_gate}")
    console.print(f"Plan gate: {goal.plan_gate}")
    console.print(f"Artifacts: .orch/goals/{goal.id}/")
    if goal.active_task_id:
        console.print(f"Active task: {goal.active_task_id}")
    if goal.evidence:
        console.print("Evidence:")
        for item in goal.evidence[-10:]:
            criterion = item.get("criterion_id") or "-"
            command = item.get("command") or item.get("type") or "evidence"
            passed = item.get("passed")
            suffix = f" passed={passed}" if passed is not None else ""
            console.print(f"- {criterion}: {command}{suffix}")
    if goal.deferred:
        console.print("Deferred:")
        for item in goal.deferred:
            console.print(f"- {item.get('id', '-')}: {item.get('reason', '')}")


@goal_app.command(help="Show the combined source, acceptance, plan, coverage, and warning view before approval.")
def review(goal_id: str) -> None:
    try:
        store = store_or_exit()
        goal = store.load(goal_id)
    except GoalStoreError as exc:
        print_goal_error(exc)
        raise typer.Exit(1) from exc
    source = _read_goal_artifact(store, goal_id, "source.md")
    acceptance = _read_goal_artifact(store, goal_id, "acceptance.md")
    plan = _read_goal_artifact(store, goal_id, "plan.md")
    coverage = _read_goal_artifact(store, goal_id, "coverage.md")
    criteria = parse_acceptance_criteria(acceptance)
    console.print(f"[Orch] Goal {goal.id}: {goal.title}")
    console.print(f"Source type: {goal.source}")
    console.print("Source summary:")
    console.print(_source_summary(source) or "- Missing source.md")
    console.print("Acceptance criteria:")
    if criteria:
        for item in criteria:
            console.print(f"- {item.id} [{item.priority}/{item.type}/{item.confidence}/{item.status}] {item.text}")
    else:
        console.print(acceptance.strip() or "- No acceptance criteria found.")
    console.print("Plan:")
    console.print(plan.strip() or "- Missing plan.md")
    console.print("Coverage:")
    console.print(coverage.strip() or "- No coverage.md found. Run derive or audit before approving.")
    low = [item for item in criteria if item.confidence in {"low", "invented"}]
    uncovered = [line for line in coverage.splitlines() if "uncovered" in line.lower() and "none" not in line.lower()]
    invented = [item for item in criteria if item.confidence == "invented"]
    console.print("Warnings:")
    if not low and not uncovered and not invented:
        console.print("- None")
    for item in low:
        console.print(f"- Low-confidence AC: {item.id} ({item.confidence})")
    for item in invented:
        console.print(f"- Invented AC: {item.id}")
    for line in uncovered:
        console.print(f"- Uncovered: {line.strip()}")


@goal_app.command(help="Ask the worker to derive acceptance criteria and a plan for an existing goal.")
def derive(
    goal_id: str,
    timeout: Annotated[int, typer.Option("--timeout", help="Seconds to wait for the derivation task.")] = 1800,
) -> None:
    try:
        message = GoalRunner(load_config_or_exit()).derive(goal_id, timeout_seconds=timeout)
    except GoalStoreError as exc:
        print_goal_error(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] {message}")


@goal_app.command(help="Ask the worker to audit goal artifacts and evidence without editing or closing the goal.")
def audit(
    goal_id: str,
    timeout: Annotated[int, typer.Option("--timeout", help="Seconds to wait for the audit task.")] = 1800,
) -> None:
    try:
        message = GoalRunner(load_config_or_exit()).audit(goal_id, timeout_seconds=timeout)
    except GoalStoreError as exc:
        print_goal_error(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] {message}")


@goal_app.command(help="Record a real-PRD Goal Mode trial result for later evaluation.")
def trial(
    goal_id: str,
    baseline_prompts: Annotated[int, typer.Option("--baseline", "--baseline-prompts", min=0, help="Manual baseline human prompt count.")] = 0,
    outcome: Annotated[str, typer.Option("--outcome", help="Trial outcome: done, blocked, gated, cancelled, or capped.")] = "done",
    caught_gaps: Annotated[list[str] | None, typer.Option("--caught-gap", help="AC/gap caught by Goal Mode. Repeat for multiple gaps.")] = None,
    deferrals: Annotated[int, typer.Option("--deferrals", min=0, help="Number of non-core deferrals.")] = 0,
    evidence_quality: Annotated[str, typer.Option("--evidence-quality", help="Evidence quality note or rating.")] = "unknown",
    note: Annotated[str, typer.Option("--note", help="Trial notes.")] = "",
) -> None:
    try:
        store = store_or_exit()
        store.load(goal_id)
        path = store.append_trial(
            goal_id,
            {
                "goal_id": goal_id,
                "baseline_prompts": baseline_prompts,
                "outcome": outcome,
                "caught_gaps": caught_gaps or [],
                "deferrals": deferrals,
                "evidence_quality": evidence_quality,
                "note": note,
            },
        )
    except GoalStoreError as exc:
        print_goal_error(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] Recorded trial for {goal_id}: {path}")


@goal_app.command(help="List recorded Goal Mode trial results for a goal.")
def trials(goal_id: str) -> None:
    try:
        store = store_or_exit()
        store.load(goal_id)
    except GoalStoreError as exc:
        print_goal_error(exc)
        raise typer.Exit(1) from exc
    path = store.goal_dir(goal_id) / "trials.jsonl"
    if not path.is_file():
        console.print(f"[Orch] No trials recorded for {goal_id}.")
        return
    console.print("TIME\tOUTCOME\tBASELINE\tGAPS\tDEFERRALS\tEVIDENCE")
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        import json

        record = json.loads(line)
        gaps = ", ".join(str(item) for item in record.get("caught_gaps") or [])
        console.print(
            f"{record.get('time', '-')}	{record.get('outcome', '-')}	{record.get('baseline_prompts', 0)}	"
            f"{gaps}	{record.get('deferrals', 0)}	{record.get('evidence_quality', '-') }"
        )


@goal_app.command(help="Approve one gate: ac or plan.")
def approve(goal_id: str, gate: str) -> None:
    try:
        goal = store_or_exit().approve_gate(goal_id, gate)
    except GoalStoreError as exc:
        print_goal_error(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] Approved {gate} gate for {goal.id}.")
    console.print(f"[Orch] Status: {goal.status}")


@goal_app.command(help="Approve or reject the combined AC/plan gate.")
def gate(
    goal_id: str,
    action: str,
    note: Annotated[str, typer.Option("--note", help="Reason to record when rejecting.")] = "",
) -> None:
    try:
        store = store_or_exit()
        if action == "approve":
            goal = store.approve_combined_gate(goal_id)
            console.print(f"[Orch] Approved combined gate for {goal.id}.")
            console.print(f"[Orch] Status: {goal.status}")
            return
        if action == "reject":
            goal = store.reject_combined_gate(goal_id, note=note)
            console.print(f"[Orch] Rejected combined gate for {goal.id}.")
            if note:
                console.print(note)
            return
        raise GoalStoreError("Gate action must be 'approve' or 'reject'.")
    except GoalStoreError as exc:
        print_goal_error(exc)
        raise typer.Exit(1) from exc


@goal_app.command(help="Advance a goal until done, blocked, gated, or capped.")
def work(
    goal_id: str,
    max_steps: Annotated[int, typer.Option("--max-steps", min=1, help="Maximum worker iterations before stopping at a cap.")] = 10,
    timeout: Annotated[int, typer.Option("--timeout", help="Seconds to wait for each worker task and check.")] = 1800,
    until: Annotated[str | None, typer.Option("--until", help="Stop target; currently only 'done' is supported and still obeys --max-steps.")] = None,
) -> None:
    if until is not None and until != "done":
        console.print("[Orch] --until currently supports only 'done'.")
        raise typer.Exit(1)
    try:
        message = GoalRunner(load_config_or_exit()).work(goal_id, max_steps=max_steps, timeout_seconds=timeout)
    except GoalStoreError as exc:
        print_goal_error(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] {message}")


@goal_app.command(help="Alias for work.")
def resume(
    goal_id: str,
    max_steps: Annotated[int, typer.Option("--max-steps", min=1, help="Maximum worker iterations before stopping at a cap.")] = 10,
    timeout: Annotated[int, typer.Option("--timeout", help="Seconds to wait for each worker task and check.")] = 1800,
    until: Annotated[str | None, typer.Option("--until", help="Stop target; currently only 'done' is supported and still obeys --max-steps.")] = None,
) -> None:
    work(goal_id, max_steps=max_steps, timeout=timeout, until=until)


@goal_app.command(help="Human sign-off for subjective core acceptance criteria.")
def signoff(
    goal_id: str,
    ac_id: Annotated[str | None, typer.Argument(help="Acceptance criterion ID to sign off. Omit with --all.")] = None,
    all_pending: Annotated[bool, typer.Option("--all", help="Sign off all pending subjective core acceptance criteria.")] = False,
    note: Annotated[str, typer.Option("--note", help="Optional sign-off note.")] = "",
) -> None:
    try:
        message = GoalRunner(load_config_or_exit()).signoff(goal_id, ac_id=ac_id, all_pending=all_pending, note=note)
    except GoalStoreError as exc:
        print_goal_error(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] {message}")


@goal_app.command(help="Cancel a goal and record the reason.")
def cancel(
    goal_id: str,
    reason: Annotated[str, typer.Option("--reason", "-m", help="Cancellation reason to record.")] = "Cancelled by user.",
) -> None:
    try:
        message = GoalRunner(load_config_or_exit()).cancel(goal_id, reason=reason)
    except GoalStoreError as exc:
        print_goal_error(exc)
        raise typer.Exit(1) from exc
    console.print(f"[Orch] {message}")
