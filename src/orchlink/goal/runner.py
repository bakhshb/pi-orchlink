from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from orchlink.bridge.ask import ask_worker_sync
from orchlink.cli.client import BrokerClient
from orchlink.goal.checks import CheckResult, extract_check_commands, parse_acceptance_criteria, run_check
from orchlink.goal.prompts import derivation_prompt
from orchlink.goal.store import GoalStore, GoalStoreError
from orchlink.project.config import project_root


class GoalRunner:
    """MVP goal execution loop over existing Orchlink worker tasks."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.store = GoalStore(config)

    def resume_status(self, goal_id: str) -> str:
        goal = self.store.load(goal_id)
        if goal.status == "cancelled":
            raise GoalStoreError(f"Goal {goal_id} is cancelled.")
        if goal.ac_gate != "approved" or goal.plan_gate != "approved":
            return f"Goal {goal.id} is waiting for gate approval."
        if goal.status == "done":
            return f"Goal {goal.id} is already done."
        if goal.status == "blocked":
            return f"Goal {goal.id} is blocked. Inspect history.jsonl for the blocker."
        return f"Goal {goal.id} is ready for worker execution."

    def work(self, goal_id: str, max_steps: int = 10, timeout_seconds: int = 1800) -> str:
        goal = self.store.load(goal_id)
        if goal.status == "cancelled":
            raise GoalStoreError(f"Goal {goal_id} is cancelled.")
        if goal.ac_gate != "approved" or goal.plan_gate != "approved":
            self.store.append_history(goal.id, {"type": "gate_required", "ac_gate": goal.ac_gate, "plan_gate": goal.plan_gate})
            return f"Goal {goal.id} paused: approve the AC and plan gates before work."
        if goal.status == "done":
            return f"Goal {goal.id} is already done."

        steps = 0
        last_summary = ""
        while steps < max_steps:
            selected = self._selected_criterion(goal.id)
            if selected is None:
                self._process_noncore_criteria(goal.id, timeout_seconds=timeout_seconds)
                if self._all_required_criteria_satisfied(goal.id):
                    self.store.set_status(goal.id, "done", "verified_done", {"steps": steps})
                    return f"Goal {goal.id} done: all core acceptance criteria are verified."
                subjective = self._pending_core_subjective(goal.id)
                if subjective:
                    ids = [item.id for item in subjective]
                    self.store.set_status(goal.id, "gated", "subjective_signoff_required", {"criteria": ids})
                    return f"Goal {goal.id} paused: subjective sign-off required for {', '.join(ids)}."
                self.store.set_status(
                    goal.id,
                    "blocked",
                    "manual_verification_required",
                    {"message": "No unblocked objective core acceptance criterion is available."},
                )
                return f"Goal {goal.id} paused: manual verification is required."

            steps += 1
            task_id = f"{goal.id}-WORK-{self.store.next_task_number(goal.id):03d}"
            prompt = self._worker_prompt(goal.id, previous_summary=last_summary)
            self.store.record_task(goal.id, task_id)
            try:
                result = ask_worker_sync(
                    config=self.config,
                    worker="work",
                    task_id=task_id,
                    message=prompt,
                    timeout_seconds=timeout_seconds,
                    wait=True,
                )
            except (httpx.HTTPError, RuntimeError) as exc:
                self.store.set_status(goal.id, "blocked", "broker_blocked", {"message": str(exc), "task_id": task_id})
                return f"Goal {goal.id} blocked while dispatching {task_id}: {exc}"
            self.store.record_task_result(goal.id, task_id, self._compact_result(result))
            last_summary = self._result_summary(result)

            blocker_type = self._reply_type(result)
            if blocker_type == "BLOCKER":
                criterion = selected
                blocker = self._parse_blocker(result, task_id=task_id, criterion_id=criterion.id if criterion else None)
                if criterion and criterion.priority != "core":
                    self.store.defer_ac(goal.id, criterion.id, "Worker reported a non-core blocker.", blocker)
                    last_summary = f"Deferred non-core {criterion.id}; continue unblocked work."
                    continue
                self.store.record_blocker(goal.id, blocker)
                self.store.set_status(goal.id, "blocked", "worker_blocker", {"blocker_type": blocker["type"], "blocker": blocker, "message": blocker["message"], "task_id": task_id})
                return f"Goal {goal.id} blocked by worker response from {task_id}."

            check_results = self.run_objective_checks(goal.id, timeout_seconds=timeout_seconds)
            if not check_results:
                self.store.set_status(
                    goal.id,
                    "blocked",
                    "manual_verification_required",
                    {"message": "No objective check commands found in acceptance.md."},
                )
                return f"Goal {goal.id} paused: no objective check commands found; manual verification is required."
            failed = [item for item in check_results if not item.passed]
            if not failed:
                self._process_noncore_criteria(goal.id, timeout_seconds=timeout_seconds)
                if self._all_required_criteria_satisfied(goal.id):
                    self.store.set_status(goal.id, "done", "verified_done", {"steps": steps})
                    return f"Goal {goal.id} done: all objective checks passed after {steps} step(s)."
                last_summary = f"Verified {selected.id}; continue remaining acceptance criteria."
                continue
            last_summary = self._format_failed_checks(failed)
            self.store.append_history(goal.id, {"type": "gap_detected", "failed_checks": [item.command for item in failed]})

        self.store.set_status(goal.id, "blocked", "cap_reached", {"max_steps": max_steps})
        return f"Goal {goal.id} stopped at cap: {max_steps} step(s). Inspect failed checks and resume when ready."

    def run_objective_checks(self, goal_id: str, timeout_seconds: int = 1800) -> list[CheckResult]:
        goal_dir = self.store.goal_dir(goal_id)
        acceptance = (goal_dir / "acceptance.md").read_text(encoding="utf-8")
        criteria = parse_acceptance_criteria(acceptance)
        selected = self._selected_criterion(goal_id)
        if criteria:
            runnable = [selected] if selected and selected.check and self._dependencies_satisfied(goal_id, selected) else []
            commands = [item.check for item in runnable if item.check]
        else:
            commands = extract_check_commands(acceptance)
        results: list[CheckResult] = []
        for command in commands:
            result = run_check(command, cwd=project_root(self.config), timeout_seconds=timeout_seconds)
            results.append(result)
            criterion_id = selected.id if selected else self._criterion_for_check(criteria, command)
            if criterion_id:
                self.store.set_ac_status(goal_id, criterion_id, "verified" if result.passed else "failed")
            self.store.record_evidence(
                goal_id,
                {
                    "type": "check",
                    "criterion_id": criterion_id,
                    "command": result.command,
                    "exit_code": result.exit_code,
                    "passed": result.passed,
                    "stdout": result.stdout[-4000:],
                    "stderr": result.stderr[-4000:],
                },
            )
        return results

    def derive(self, goal_id: str, timeout_seconds: int = 1800) -> str:
        goal = self.store.load(goal_id)
        source = self._read(self.store.goal_dir(goal_id) / "source.md")
        task_id = f"{goal.id}-DERIVE-{self._next_derivation_number(goal.id):03d}"
        self.store.record_task(goal.id, task_id, event_type="derivation_dispatched")
        try:
            result = ask_worker_sync(
                config=self.config,
                worker="work",
                task_id=task_id,
                message=derivation_prompt(goal, source),
                timeout_seconds=timeout_seconds,
                wait=True,
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            self.store.set_status(goal.id, "blocked", "derivation_blocked", {"message": str(exc), "task_id": task_id})
            return f"Goal {goal.id} blocked during derivation: {exc}"
        self.store.record_task_result(goal.id, task_id, self._compact_result(result))
        acceptance, plan, coverage = self._parse_derivation(result, goal)
        self.store.write_artifacts(goal.id, acceptance=acceptance, plan=plan, coverage=coverage)
        self.store.append_history(goal.id, {"type": "derived", "task_id": task_id})
        return f"Goal {goal.id} derived acceptance criteria and plan. Review then approve the gate."

    def signoff(self, goal_id: str, ac_id: str | None = None, all_pending: bool = False, note: str = "") -> str:
        criteria = self._criteria(goal_id)
        selected = []
        for criterion in criteria:
            if criterion.priority == "core" and (criterion.type == "subjective" or not criterion.check) and criterion.status != "verified":
                if all_pending or criterion.id == ac_id:
                    selected.append(criterion)
        if not selected:
            raise GoalStoreError("No matching subjective core acceptance criteria need sign-off.")
        blocked = [criterion.id for criterion in selected if not self._dependencies_satisfied(goal_id, criterion)]
        if blocked:
            raise GoalStoreError(f"Cannot sign off {', '.join(blocked)}: dependencies are not verified.")
        for criterion in selected:
            self.store.set_ac_status(goal_id, criterion.id, "human-approved")
            self.store.append_history(goal_id, {"type": "subjective_approved", "id": criterion.id, "note": note})
        if self._all_required_criteria_satisfied(goal_id):
            self.store.set_status(goal_id, "done", "verified_done", {"signoff": [item.id for item in selected]})
        return f"Signed off {', '.join(item.id for item in selected)} for {goal_id}."

    def audit(self, goal_id: str, timeout_seconds: int = 1800) -> str:
        goal = self.store.load(goal_id)
        original_status = goal.status
        task_id = f"{goal.id}-AUDIT-{self._next_audit_number(goal.id):03d}"
        self.store.record_task(goal.id, task_id, event_type="audit_dispatched")
        try:
            result = ask_worker_sync(
                config=self.config,
                worker="work",
                task_id=task_id,
                message=self._audit_prompt(goal.id),
                timeout_seconds=timeout_seconds,
                wait=True,
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            self.store.set_status(goal.id, "blocked", "audit_blocked", {"message": str(exc), "task_id": task_id})
            return f"Goal {goal.id} audit blocked: {exc}"
        self.store.record_task_result(goal.id, task_id, self._compact_result(result))
        reply = result.get("reply") or {}
        payload = reply.get("payload") or {}
        audit_text = str(payload.get("audit") or self._result_summary(result) or "No audit summary returned.")
        findings = payload.get("findings") or []
        restored = self.store.load(goal.id)
        restored.status = original_status
        restored.active_task_id = None
        self.store.save(restored)
        self.store.write_audit(goal.id, audit_text, task_id)
        self.store.record_evidence(goal.id, {"type": "audit", "task_id": task_id, "summary": audit_text[:4000], "findings": findings})
        return f"Goal {goal.id} audit recorded from {task_id}."

    def cancel(self, goal_id: str, reason: str = "Cancelled by user.") -> str:
        goal = self.store.load(goal_id)
        active_task_id = goal.active_task_id
        if active_task_id:
            try:
                BrokerClient(self.config).cancel(active_task_id, reason)
            except httpx.HTTPError as exc:
                self.store.append_history(goal.id, {"type": "cancel_task_failed", "task_id": active_task_id, "message": str(exc)})
        self.store.cancel(goal_id, reason=reason)
        return f"Cancelled {goal_id}."

    def _next_derivation_number(self, goal_id: str) -> int:
        return self._next_number_for_prefix(goal_id, f"{goal_id}-DERIVE-")

    def _next_audit_number(self, goal_id: str) -> int:
        return self._next_number_for_prefix(goal_id, f"{goal_id}-AUDIT-")

    def _next_number_for_prefix(self, goal_id: str, prefix: str) -> int:
        highest = 0
        for event in self.store.history(goal_id):
            task_id = str(event.get("task_id") or "")
            if task_id.startswith(prefix):
                suffix = task_id[len(prefix) :]
                if suffix.isdigit():
                    highest = max(highest, int(suffix))
        return highest + 1

    def _criteria(self, goal_id: str):
        acceptance_path = self.store.goal_dir(goal_id) / "acceptance.md"
        if not acceptance_path.is_file():
            return []
        return parse_acceptance_criteria(acceptance_path.read_text(encoding="utf-8"))

    def _selected_criterion(self, goal_id: str):
        statuses = self.store.load(goal_id).ac_status
        for criterion in self._criteria(goal_id):
            if criterion.priority != "core":
                continue
            status = statuses.get(criterion.id, criterion.status)
            if status in {"verified", "human-approved", "deferred"}:
                continue
            if criterion.type == "subjective" or not criterion.check:
                continue
            if self._dependencies_satisfied(goal_id, criterion):
                return criterion
        return None

    def _pending_core_subjective(self, goal_id: str):
        statuses = self.store.load(goal_id).ac_status
        return [
            criterion
            for criterion in self._criteria(goal_id)
            if criterion.priority == "core"
            and statuses.get(criterion.id, criterion.status) not in {"verified", "human-approved"}
            and (criterion.type == "subjective" or not criterion.check)
            and self._dependencies_satisfied(goal_id, criterion)
        ]

    def _dependencies_satisfied(self, goal_id: str, criterion) -> bool:
        statuses = {item.id: item.status for item in self._criteria(goal_id)}
        statuses.update(self.store.load(goal_id).ac_status)
        return all(statuses.get(dep) in {"verified", "human-approved"} for dep in criterion.depends_on)

    def _all_required_criteria_satisfied(self, goal_id: str) -> bool:
        criteria = self._criteria(goal_id)
        if not criteria:
            return False
        statuses = self.store.load(goal_id).ac_status
        for criterion in criteria:
            status = statuses.get(criterion.id, criterion.status)
            if criterion.priority == "core" and status not in {"verified", "human-approved"}:
                return False
        return True

    def _process_noncore_criteria(self, goal_id: str, timeout_seconds: int = 1800) -> None:
        for criterion in self._criteria(goal_id):
            if criterion.priority == "core" or criterion.status in {"verified", "deferred"}:
                continue
            if not self._dependencies_satisfied(goal_id, criterion):
                continue
            if criterion.type == "subjective" or not criterion.check:
                self.store.defer_ac(goal_id, criterion.id, "Non-core subjective or manually verified criterion deferred.")
                continue
            result = run_check(criterion.check, cwd=project_root(self.config), timeout_seconds=timeout_seconds)
            self.store.record_evidence(
                goal_id,
                {
                    "type": "check",
                    "criterion_id": criterion.id,
                    "command": result.command,
                    "exit_code": result.exit_code,
                    "passed": result.passed,
                    "stdout": result.stdout[-4000:],
                    "stderr": result.stderr[-4000:],
                },
            )
            if result.passed:
                self.store.set_ac_status(goal_id, criterion.id, "verified")
            else:
                self.store.defer_ac(goal_id, criterion.id, "Non-core objective check failed.", {"command": result.command, "exit_code": result.exit_code})

    @staticmethod
    def _criterion_for_check(criteria, command: str) -> str | None:
        for criterion in criteria:
            if criterion.check == command:
                return criterion.id
        return None

    @staticmethod
    def _reply_type(result: dict[str, Any]) -> str:
        return str((result.get("reply") or {}).get("type") or "RESULT").upper()

    @staticmethod
    def _parse_blocker(result: dict[str, Any], task_id: str, criterion_id: str | None = None) -> dict[str, Any]:
        reply = result.get("reply") or {}
        payload = reply.get("payload") or {}
        summary = str(payload.get("summary") or payload.get("stdout") or "")
        typed = payload.get("blocker") if isinstance(payload.get("blocker"), dict) else {}
        blocker_type = str(typed.get("type") or "ambiguity").lower()
        message = str(typed.get("message") or summary)
        if blocker_type not in {"decision", "asset", "upstream", "external", "ambiguity", "failed_check"}:
            blocker_type = "ambiguity"
        if not typed:
            for line in summary.splitlines():
                key, _, value = line.partition(":")
                if key.strip().lower() in {"type", "blocker_type", "blocker type"}:
                    candidate = value.strip().lower()
                    if candidate in {"decision", "asset", "upstream", "external", "ambiguity", "failed_check"}:
                        blocker_type = candidate
                        break
        return {"type": blocker_type, "task_id": task_id, "criterion_id": criterion_id, "message": message}

    @staticmethod
    def _parse_derivation(result: dict[str, Any], goal) -> tuple[str, str, str | None]:
        reply = result.get("reply") or {}
        payload = reply.get("payload") or {}
        output = str(payload.get("summary") or payload.get("stdout") or "")
        acceptance = str(payload.get("acceptance") or "") or GoalRunner._labeled_fenced_block(output, "acceptance")
        plan = str(payload.get("plan") or "") or GoalRunner._labeled_fenced_block(output, "plan")
        coverage = str(payload.get("coverage") or "") or GoalRunner._labeled_fenced_block(output, "coverage")
        if not acceptance:
            yaml_block = GoalRunner._acceptance_yaml_block(output)
            if yaml_block:
                acceptance = f"# Acceptance criteria for {goal.id}: {goal.title}\n\n```yaml\n{yaml_block.rstrip()}\n```"
        if not acceptance:
            acceptance = output.strip() or f"# Acceptance criteria for {goal.id}: {goal.title}\n"
        if not plan:
            plan = f"# Plan for {goal.id}: {goal.title}\n\nReview derived acceptance criteria and fill in the execution plan.\n"
        return acceptance.rstrip() + "\n", plan.rstrip() + "\n", (coverage.rstrip() + "\n" if coverage else None)

    @staticmethod
    def _labeled_fenced_block(text: str, label: str) -> str:
        import re

        lines = text.splitlines()
        start: int | None = None
        for index, line in enumerate(lines):
            if re.match(rf"^\s*```{label}\s*$", line, flags=re.IGNORECASE):
                start = index + 1
                break
        if start is None:
            for match in re.finditer(rf"^\s*```{label}\s*$", text, flags=re.IGNORECASE | re.MULTILINE):
                return GoalRunner._labeled_fenced_block(text[match.start() :], label)
            return ""

        end = len(lines)
        next_label = re.compile(r"^\s*```(?:acceptance|plan|coverage)\s*$", flags=re.IGNORECASE)
        for index in range(start, len(lines)):
            if next_label.match(lines[index]):
                end = index
                break
        section = lines[start:end]
        while section and not section[-1].strip():
            section.pop()
        if section and section[-1].strip() == "```":
            section.pop()
        while section and not section[-1].strip():
            section.pop()
        return "\n".join(section).strip()

    @staticmethod
    def _acceptance_yaml_block(text: str) -> str:
        import re

        for match in re.finditer(r"```(?:yaml|yml)\s*\n(.*?)\n```", text, flags=re.IGNORECASE | re.DOTALL):
            block = match.group(1)
            if "acceptance:" in block or "acceptance_criteria:" in block or "criteria:" in block or "acs:" in block:
                return block.strip()
        return ""

    def _audit_prompt(self, goal_id: str) -> str:
        goal = self.store.load(goal_id)
        goal_dir = self.store.goal_dir(goal_id)
        source = self._read(goal_dir / "source.md")
        acceptance = self._read(goal_dir / "acceptance.md")
        plan = self._read(goal_dir / "plan.md")
        coverage = self._read(goal_dir / "coverage.md")
        return f"""Audit goal {goal.id}: {goal.title} against its source, acceptance criteria, plan, coverage, and recorded evidence.

Do not edit files. Do not mark the goal done. Return gaps, risks, missing evidence, uncovered requirements, and whether the lead should proceed.

Source:
{source}

Acceptance criteria:
{acceptance}

Plan:
{plan}

Coverage:
{coverage}
""".strip()

    def _worker_prompt(self, goal_id: str, previous_summary: str = "") -> str:
        goal = self.store.load(goal_id)
        goal_dir = self.store.goal_dir(goal_id)
        source = self._read(goal_dir / "source.md")
        acceptance = self._read(goal_dir / "acceptance.md")
        plan = self._read(goal_dir / "plan.md")
        selected = self._selected_criterion(goal_id)
        selected_text = f"\nSelected acceptance criterion for this slice: {selected.id} {selected.text}\n" if selected else ""
        previous = f"\nPrevious failed checks/gaps:\n{previous_summary}\n" if previous_summary else ""
        return f"""Implement the next useful slice for goal {goal.id}: {goal.title}.

You are the maker. Do not claim the whole goal is done. The goal runner will verify checks and decide completion.

Source:
{source}

Acceptance criteria:
{acceptance}

Plan:
{plan}
{selected_text}{previous}
Scope rules:
- Make the smallest useful change for unverified acceptance criteria.
- If you hit a non-core blocker, report it and continue any unblocked core work.
- If a core blocker prevents all useful work, report the exact blocker question.
- Reply with files changed, checks run, risks, and blockers.
""".strip()

    @staticmethod
    def _read(path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    @staticmethod
    def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
        reply = result.get("reply") or {}
        payload = reply.get("payload") or {}
        return {
            "status": result.get("status"),
            "task_id": result.get("task_id"),
            "reply_type": reply.get("type"),
            "summary": str(payload.get("summary") or payload.get("stdout") or payload)[:8000],
        }

    @staticmethod
    def _result_summary(result: dict[str, Any]) -> str:
        reply = result.get("reply") or {}
        payload = reply.get("payload") or {}
        return str(payload.get("summary") or payload.get("stdout") or payload)[:4000]

    @staticmethod
    def _format_failed_checks(failed: list[CheckResult]) -> str:
        lines: list[str] = []
        for item in failed:
            lines.append(f"Check failed: {item.command} (exit {item.exit_code})")
            if item.stdout.strip():
                lines.append(item.stdout[-1000:])
            if item.stderr.strip():
                lines.append(item.stderr[-1000:])
        return "\n".join(lines)
