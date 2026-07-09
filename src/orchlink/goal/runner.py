"""Goal Mode execution loop.

``GoalRunner`` orchestrates the work/derive/signoff/audit/cancel operations.
Prompt construction lives in :mod:`orchlink.goal.prompts`, worker dispatch and
reply parsing in :mod:`orchlink.goal.dispatcher`, criteria selection and
noncore processing in :mod:`orchlink.goal.criteria`, and objective check
execution in :mod:`orchlink.goal.checks`.
"""

from __future__ import annotations

from typing import Any

import httpx

from orchlink import client
from orchlink.client import ask_worker_sync
from orchlink.goal.checks import CheckResult, run_objective_checks
from orchlink.goal.criteria import GoalCriteriaEngine
from orchlink.goal.dispatcher import (
    GoalDispatcher,
    compact_result,
    format_failed_checks,
    parse_blocker,
    parse_derivation_reply,
    reply_kind,
    result_summary,
)
from orchlink.goal.lifecycle import GoalEventType, GoalStatus
from orchlink.goal.prompts import audit_prompt, derivation_prompt, worker_prompt
from orchlink.goal.store import GoalStore, GoalStoreError


class GoalEvidenceAdapter:
    def __init__(self, store: GoalStore) -> None:
        self.store = store

    def attach_evidence(self, *, goal_id: str, evidence: dict[str, Any]) -> None:
        self.store.record_evidence(goal_id, evidence)


class GoalRunner:
    """MVP goal execution loop over existing Orchlink worker tasks."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        maker_worker: str = "work",
        verifier_worker: str = "work",
        maker_model: str | None = None,
        verifier_model: str | None = None,
    ) -> None:
        self.config = config
        self.maker_worker = maker_worker
        self.verifier_worker = verifier_worker
        self.maker_model = maker_model
        self.verifier_model = verifier_model
        self.store = GoalStore(config)
        self.criteria = GoalCriteriaEngine(self.store, config=config)
        self.dispatcher = GoalDispatcher(
            config,
            ask_fn=ask_worker_sync,
            maker_worker=maker_worker,
            verifier_worker=verifier_worker,
            maker_model=maker_model,
            verifier_model=verifier_model,
        )

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
            self.store.append_history(goal.id, {"type": GoalEventType.GATE_REQUIRED.value, "ac_gate": goal.ac_gate, "plan_gate": goal.plan_gate})
            return f"Goal {goal.id} paused: approve the AC and plan gates before work."
        if goal.status == "done":
            return f"Goal {goal.id} is already done."

        steps = 0
        last_summary = ""
        while steps < max_steps:
            selected = self.criteria.selected(goal.id)
            if selected is None:
                self.criteria.process_noncore(goal.id, timeout_seconds=timeout_seconds)
                if self.criteria.all_required_criteria_satisfied(goal.id):
                    self.store.set_status(goal.id, GoalStatus.DONE.value, GoalEventType.VERIFIED_DONE.value, {"steps": steps})
                    return f"Goal {goal.id} done: all core acceptance criteria are verified."
                subjective = self.criteria.pending_core_subjective(goal.id)
                if subjective:
                    ids = [item.id for item in subjective]
                    self.store.set_status(goal.id, GoalStatus.GATED.value, GoalEventType.SUBJECTIVE_SIGNOFF_REQUIRED.value, {"criteria": ids})
                    return f"Goal {goal.id} paused: subjective sign-off required for {', '.join(ids)}."
                self.store.set_status(
                    goal.id,
                    GoalStatus.BLOCKED.value,
                    GoalEventType.MANUAL_VERIFICATION_REQUIRED.value,
                    {"message": "No unblocked objective core acceptance criterion is available."},
                )
                return f"Goal {goal.id} paused: manual verification is required."

            steps += 1
            task_id = f"{goal.id}-WORK-{self.store.next_task_number(goal.id):03d}"
            prompt = worker_prompt(
                self.store,
                goal.id,
                selected_criterion_text=f"{selected.id} {selected.text}",
                previous_summary=last_summary,
            )
            self.store.record_task(goal.id, task_id, detail=self._dispatch_detail("maker"))
            try:
                result = self.dispatcher.dispatch(
                    role="maker",
                    task_id=task_id,
                    prompt=prompt,
                    timeout_seconds=timeout_seconds,
                    model=self.maker_model,
                )
            except (httpx.HTTPError, RuntimeError) as exc:
                self.store.set_status(goal.id, GoalStatus.BLOCKED.value, GoalEventType.BROKER_BLOCKED.value, {"message": str(exc), "task_id": task_id})
                return f"Goal {goal.id} blocked while dispatching {task_id}: {exc}"
            self.store.record_task_result(goal.id, task_id, compact_result(result))
            last_summary = result_summary(result)

            if reply_kind(result) == "BLOCKER":
                blocker = parse_blocker(result, task_id=task_id, criterion_id=selected.id if selected else None)
                if selected.priority != "core":
                    self.store.defer_ac(goal.id, selected.id, "Worker reported a non-core blocker.", blocker)
                    last_summary = f"Deferred non-core {selected.id}; continue unblocked work."
                    continue
                self.store.record_blocker(goal.id, blocker)
                self.store.set_status(
                    goal.id,
                    GoalStatus.BLOCKED.value,
                    GoalEventType.WORKER_BLOCKER.value,
                    {"blocker_type": blocker["type"], "blocker": blocker, "message": blocker["message"], "task_id": task_id},
                )
                return f"Goal {goal.id} blocked by worker response from {task_id}."

            check_results = self.run_objective_checks(goal.id, timeout_seconds=timeout_seconds)
            if not check_results:
                self.store.set_status(
                    goal.id,
                    GoalStatus.BLOCKED.value,
                    GoalEventType.MANUAL_VERIFICATION_REQUIRED.value,
                    {"message": "No objective check commands found in acceptance.md."},
                )
                return f"Goal {goal.id} paused: no objective check commands found; manual verification is required."
            failed = [item for item in check_results if not item.passed]
            if not failed:
                self.criteria.process_noncore(goal.id, timeout_seconds=timeout_seconds)
                if self.criteria.all_required_criteria_satisfied(goal.id):
                    self.store.set_status(goal.id, GoalStatus.DONE.value, GoalEventType.VERIFIED_DONE.value, {"steps": steps})
                    return f"Goal {goal.id} done: all objective checks passed after {steps} step(s)."
                last_summary = f"Verified {selected.id}; continue remaining acceptance criteria."
                continue
            last_summary = format_failed_checks(failed)
            self.store.append_history(goal.id, {"type": GoalEventType.GAP_DETECTED.value, "failed_checks": [item.command for item in failed]})

        self.store.set_status(goal.id, GoalStatus.BLOCKED.value, GoalEventType.CAP_REACHED.value, {"max_steps": max_steps})
        return f"Goal {goal.id} stopped at cap: {max_steps} step(s). Inspect failed checks and resume when ready."

    def run_objective_checks(self, goal_id: str, timeout_seconds: int = 1800) -> list[CheckResult]:
        """Public wrapper kept for backward compatibility; delegates to ``goal.checks.run_objective_checks``."""
        return run_objective_checks(
            self.store,
            self.config,
            goal_id,
            criteria_engine=self.criteria,
            timeout_seconds=timeout_seconds,
        )

    def derive(self, goal_id: str, timeout_seconds: int = 1800) -> str:
        goal = self.store.load(goal_id)
        source_path = self.store.goal_dir(goal_id) / "source.md"
        source = source_path.read_text(encoding="utf-8") if source_path.is_file() else ""
        task_id = f"{goal.id}-DERIVE-{self._next_derivation_number(goal.id):03d}"
        self.store.record_task(goal.id, task_id, event_type=GoalEventType.DERIVATION_DISPATCHED.value, detail=self._dispatch_detail("maker"))
        try:
            result = self.dispatcher.dispatch(
                role="maker",
                task_id=task_id,
                prompt=derivation_prompt(goal, source),
                timeout_seconds=timeout_seconds,
                model=self.maker_model,
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            self.store.set_status(goal.id, GoalStatus.BLOCKED.value, GoalEventType.DERIVATION_BLOCKED.value, {"message": str(exc), "task_id": task_id})
            return f"Goal {goal.id} blocked during derivation: {exc}"
        self.store.record_task_result(goal.id, task_id, compact_result(result))
        acceptance, plan, coverage = parse_derivation_reply(result, goal)
        self.store.write_artifacts(goal.id, acceptance=acceptance, plan=plan, coverage=coverage)
        self.store.append_history(goal.id, {"type": GoalEventType.DERIVED.value, "task_id": task_id})
        return f"Goal {goal.id} derived acceptance criteria and plan. Review then approve the gate."

    def signoff(self, goal_id: str, ac_id: str | None = None, all_pending: bool = False, note: str = "") -> str:
        criteria = self.criteria.criteria(goal_id)
        selected = []
        for criterion in criteria:
            if criterion.priority == "core" and (criterion.type == "subjective" or not criterion.check) and criterion.status != "verified":
                if all_pending or criterion.id == ac_id:
                    selected.append(criterion)
        if not selected:
            raise GoalStoreError("No matching subjective core acceptance criteria need sign-off.")
        blocked = [criterion.id for criterion in selected if not self.criteria.dependencies_satisfied(goal_id, criterion)]
        if blocked:
            raise GoalStoreError(f"Cannot sign off {', '.join(blocked)}: dependencies are not verified.")
        for criterion in selected:
            self.store.set_ac_status(goal_id, criterion.id, "human-approved")
            self.store.append_history(goal_id, {"type": GoalEventType.SUBJECTIVE_APPROVED.value, "id": criterion.id, "note": note})
        if self.criteria.all_required_criteria_satisfied(goal_id):
            self.store.set_status(goal_id, GoalStatus.DONE.value, GoalEventType.VERIFIED_DONE.value, {"signoff": [item.id for item in selected]})
        return f"Signed off {', '.join(item.id for item in selected)} for {goal_id}."

    def audit(self, goal_id: str, timeout_seconds: int = 1800) -> str:
        goal = self.store.load(goal_id)
        task_id = f"{goal.id}-AUDIT-{self._next_audit_number(goal.id):03d}"
        self.store.record_task(goal.id, task_id, event_type=GoalEventType.AUDIT_DISPATCHED.value, detail=self._dispatch_detail("verifier"))
        try:
            result = self.dispatcher.dispatch(
                role="verifier",
                task_id=task_id,
                prompt=audit_prompt(self.store, goal.id),
                timeout_seconds=timeout_seconds,
                model=self.verifier_model,
            )
        except (httpx.HTTPError, RuntimeError) as exc:
            self.store.set_status(goal.id, GoalStatus.BLOCKED.value, GoalEventType.AUDIT_BLOCKED.value, {"message": str(exc), "task_id": task_id})
            return f"Goal {goal.id} audit blocked: {exc}"
        self.store.record_task_result(goal.id, task_id, compact_result(result))
        reply = result.get("reply") or {}
        payload = reply.get("payload") or {}
        audit_text = str(payload.get("audit") or result_summary(result) or "No audit summary returned.")
        findings = payload.get("findings") or []
        restored = self.store.load(goal.id)
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
                client.BrokerClient(self.config).cancel(active_task_id, reason)
            except httpx.HTTPError as exc:
                self.store.append_history(goal.id, {"type": GoalEventType.CANCEL_TASK_FAILED.value, "task_id": active_task_id, "message": str(exc)})
        self.store.cancel(goal_id, reason=reason)
        return f"Cancelled {goal_id}."

    def _dispatch_detail(self, role: str) -> dict[str, Any]:
        if role == "verifier":
            detail: dict[str, Any] = {"worker_role": role, "worker": self.verifier_worker}
            if self.verifier_model:
                detail["model"] = self.verifier_model
            return detail
        detail = {"worker_role": role, "worker": self.maker_worker, "verifier_worker": self.verifier_worker}
        if self.maker_model:
            detail["model"] = self.maker_model
        if self.verifier_model:
            detail["verifier_model"] = self.verifier_model
        return detail

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


__all__ = ["GoalEvidenceAdapter", "GoalRunner"]