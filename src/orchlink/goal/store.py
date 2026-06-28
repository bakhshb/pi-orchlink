from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from orchlink.goal.models import Goal, SourceType, utc_now_iso
from orchlink.project.config import broker_api_key, broker_url, orch_dir


GOAL_ID_RE = re.compile(r"^G(\d{3,})$")


# Goal history event type -> audit journal action (M1).
# Only transition-relevant events are journaled; noisy events (evidence,
# trial_recorded, artifacts_written, task_result, gap_detected, gate_required,
# cancel_task_failed) are skipped to keep the audit log meaningful.
_GOAL_EVENT_ACTION: dict[str, tuple[str, str | None]] = {
    "created": ("goal.started", "draft"),
    "gate_approved": ("goal.gated", "approved"),
    "gate_rejected": ("goal.gated", "rejected"),
    "task_dispatched": ("goal.worked", "running"),
    "derivation_dispatched": ("goal.worked", "running"),
    "audit_dispatched": ("goal.worked", "running"),
    "verified_done": ("goal.done", "done"),
    "worker_blocker": ("goal.blocked", "blocked"),
    "broker_blocked": ("goal.blocked", "blocked"),
    "derivation_blocked": ("goal.blocked", "blocked"),
    "audit_blocked": ("goal.blocked", "blocked"),
    "cap_reached": ("goal.blocked", "blocked"),
    "manual_verification_required": ("goal.blocked", "blocked"),
    "subjective_signoff_required": ("goal.blocked", "blocked"),
    "cancelled": ("goal.cancelled", "cancelled"),
    "subjective_approved": ("goal.signedoff", None),
}


def journal_goal_transition(
    config: dict[str, Any],
    goal_id: str,
    action: str,
    before: str | None,
    after: str | None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Best-effort POST of a goal transition to the broker audit journal.

    Observability-only: failures are swallowed so a journal/broker outage
    never blocks a goal operation. The goal store remains the source of truth.
    """
    try:
        import httpx

        project_id = str(config.get("project_id") or "default")
        body = {
            "project_id": project_id,
            "actor": "orchlink.goal",
            "action": action,
            "target_type": "goal",
            "target_id": goal_id,
            "before": before,
            "after": after,
            "meta": meta or {},
        }
        headers = {"X-API-Key": broker_api_key(config), "X-Orchlink-Project-ID": project_id}
        with httpx.Client(base_url=broker_url(config), timeout=2.0) as client:
            response = client.post("/v1/journal", headers=headers, json=body)
            response.raise_for_status()
    except Exception:
        # Observability-only: never propagate a journal failure.
        pass


class GoalStoreError(RuntimeError):
    """Raised when Goal Mode state cannot be read or written."""


class GoalStore:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.root = orch_dir(config) / "goals"

    def goal_dir(self, goal_id: str) -> Path:
        return self.root / goal_id

    def list_goals(self) -> list[Goal]:
        if not self.root.is_dir():
            return []
        goals: list[Goal] = []
        for path in sorted(self.root.iterdir()):
            if not path.is_dir():
                continue
            goal_file = path / "goal.yaml"
            if goal_file.is_file():
                goals.append(self.load(path.name))
        return goals

    def next_goal_id(self) -> str:
        highest = 0
        if self.root.is_dir():
            for path in self.root.iterdir():
                match = GOAL_ID_RE.match(path.name)
                if match:
                    highest = max(highest, int(match.group(1)))
        return f"G{highest + 1:03d}"

    def create_goal(self, title: str, source_type: SourceType, source_text: str) -> Goal:
        self.root.mkdir(parents=True, exist_ok=True)
        goal_id = self.next_goal_id()
        directory = self.goal_dir(goal_id)
        directory.mkdir()
        goal = Goal(id=goal_id, title=title, source=source_type)
        (directory / "source.md").write_text(source_text, encoding="utf-8")
        (directory / "acceptance.md").write_text(self.default_acceptance(goal), encoding="utf-8")
        (directory / "plan.md").write_text(self.default_plan(goal), encoding="utf-8")
        self.save(goal)
        self.append_history(goal_id, {"type": "created", "source": source_type, "title": title})
        return goal

    def load(self, goal_id: str) -> Goal:
        path = self.goal_dir(goal_id) / "goal.yaml"
        if not path.is_file():
            raise GoalStoreError(f"Goal not found: {goal_id}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Goal.from_dict(data)

    def save(self, goal: Goal) -> None:
        directory = self.goal_dir(goal.id)
        if not directory.is_dir():
            raise GoalStoreError(f"Goal not found: {goal.id}")
        goal.updated_at = utc_now_iso()
        (directory / "goal.yaml").write_text(yaml.safe_dump(goal.to_dict(), sort_keys=False), encoding="utf-8")

    def append_history(self, goal_id: str, event: dict[str, Any]) -> None:
        directory = self.goal_dir(goal_id)
        if not directory.is_dir():
            raise GoalStoreError(f"Goal not found: {goal_id}")
        record = {"time": utc_now_iso(), **event}
        with (directory / "history.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, sort_keys=True) + "\n")
        mapping = _GOAL_EVENT_ACTION.get(str(record.get("type") or ""))
        if mapping is not None:
            action, default_after = mapping
            after = record.get("status") or default_after
            try:
                journal_goal_transition(
                    self.config,
                    goal_id,
                    action,
                    before=None,
                    after=after,
                    meta={"event_type": str(record.get("type") or ""), "source": "goal"},
                )
            except Exception:
                # Observability-only: never let a journal failure block a goal transition.
                pass

    def history(self, goal_id: str) -> list[dict[str, Any]]:
        path = self.goal_dir(goal_id) / "history.jsonl"
        if not path.is_file():
            raise GoalStoreError(f"Goal not found: {goal_id}")
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
        return events

    def approve_gate(self, goal_id: str, gate: str) -> Goal:
        goal = self.load(goal_id)
        if gate == "ac":
            goal.ac_gate = "approved"
        elif gate == "plan":
            goal.plan_gate = "approved"
        else:
            raise GoalStoreError("Gate must be 'ac' or 'plan'.")
        goal.refresh_status_from_gates()
        self.save(goal)
        self.append_history(goal.id, {"type": "gate_approved", "gate": gate})
        return goal

    def approve_combined_gate(self, goal_id: str) -> Goal:
        goal = self.load(goal_id)
        goal.ac_gate = "approved"
        goal.plan_gate = "approved"
        goal.refresh_status_from_gates()
        self.save(goal)
        self.append_history(goal.id, {"type": "gate_approved", "gate": "combined"})
        return goal

    def reject_combined_gate(self, goal_id: str, note: str = "") -> Goal:
        goal = self.load(goal_id)
        goal.ac_gate = "rejected"
        goal.plan_gate = "rejected"
        if goal.status == "ready":
            goal.status = "draft"
        self.save(goal)
        event: dict[str, Any] = {"type": "gate_rejected", "gate": "combined"}
        if note:
            event["note"] = note
        self.append_history(goal.id, event)
        return goal

    def record_task(self, goal_id: str, task_id: str, event_type: str = "task_dispatched", detail: dict[str, Any] | None = None) -> Goal:
        goal = self.load(goal_id)
        goal.active_task_id = task_id
        if goal.status == "ready":
            goal.status = "running"
        self.save(goal)
        event: dict[str, Any] = {"type": event_type, "task_id": task_id}
        if detail:
            event.update(detail)
        self.append_history(goal.id, event)
        return goal

    def record_task_result(self, goal_id: str, task_id: str, result: dict[str, Any]) -> Goal:
        goal = self.load(goal_id)
        if goal.active_task_id == task_id:
            goal.active_task_id = None
        self.save(goal)
        self.append_history(goal.id, {"type": "task_result", "task_id": task_id, "result": result})
        return goal

    def write_artifacts(self, goal_id: str, acceptance: str | None = None, plan: str | None = None, coverage: str | None = None) -> None:
        directory = self.goal_dir(goal_id)
        if not directory.is_dir():
            raise GoalStoreError(f"Goal not found: {goal_id}")
        if acceptance is not None:
            (directory / "acceptance.md").write_text(acceptance, encoding="utf-8")
        if plan is not None:
            (directory / "plan.md").write_text(plan, encoding="utf-8")
        if coverage is not None:
            (directory / "coverage.md").write_text(coverage, encoding="utf-8")
        self.append_history(
            goal_id,
            {"type": "artifacts_written", "acceptance": acceptance is not None, "plan": plan is not None, "coverage": coverage is not None},
        )

    def write_audit(self, goal_id: str, audit: str, task_id: str) -> Path:
        directory = self.goal_dir(goal_id)
        if not directory.is_dir():
            raise GoalStoreError(f"Goal not found: {goal_id}")
        path = directory / "audit.md"
        path.write_text(audit, encoding="utf-8")
        self.append_history(goal_id, {"type": "audit", "task_id": task_id})
        return path

    def append_trial(self, goal_id: str, trial: dict[str, Any]) -> Path:
        directory = self.goal_dir(goal_id)
        if not directory.is_dir():
            raise GoalStoreError(f"Goal not found: {goal_id}")
        path = directory / "trials.jsonl"
        record = {"time": utc_now_iso(), **trial}
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, sort_keys=True) + "\n")
        self.append_history(goal_id, {"type": "trial_recorded", "trial": record})
        return path

    def record_evidence(self, goal_id: str, evidence: dict[str, Any]) -> Goal:
        goal = self.load(goal_id)
        goal.evidence.append(evidence)
        self.save(goal)
        self.append_history(goal.id, {"type": "evidence", "evidence": evidence})
        return goal

    def record_blocker(self, goal_id: str, blocker: dict[str, Any]) -> Goal:
        goal = self.load(goal_id)
        goal.blockers.append(blocker)
        self.save(goal)
        self.append_history(goal.id, {"type": "blocker", "blocker": blocker})
        return goal

    def set_ac_status(self, goal_id: str, ac_id: str, status: str) -> Goal:
        goal = self.load(goal_id)
        goal.ac_status[ac_id] = status
        self._update_acceptance_status(goal_id, ac_id, status)
        self.save(goal)
        return goal

    def defer_ac(self, goal_id: str, ac_id: str, reason: str, detail: dict[str, Any] | None = None) -> Goal:
        goal = self.load(goal_id)
        if not any(item.get("id") == ac_id for item in goal.deferred):
            deferred: dict[str, Any] = {"id": ac_id, "reason": reason}
            if detail:
                deferred["detail"] = detail
            goal.deferred.append(deferred)
        goal.ac_status[ac_id] = "deferred"
        self._update_acceptance_status(goal_id, ac_id, "deferred")
        self.save(goal)
        event: dict[str, Any] = {"type": "deferred", "id": ac_id, "reason": reason}
        if detail:
            event["detail"] = detail
        self.append_history(goal.id, event)
        return goal

    def _update_acceptance_status(self, goal_id: str, ac_id: str, status: str) -> None:
        path = self.goal_dir(goal_id) / "acceptance.md"
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
        updated = self._update_fenced_acceptance_yaml(text, ac_id, status)
        path.write_text(updated, encoding="utf-8")

    @staticmethod
    def _update_fenced_acceptance_yaml(text: str, ac_id: str, status: str) -> str:
        match = re.search(r"```(?:yaml|yml)?\s*\n(.*?)\n```", text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return text
        data = yaml.safe_load(match.group(1)) or {}
        if not isinstance(data, dict):
            return text
        items = data.get("acceptance") or data.get("acceptance_criteria") or data.get("criteria") or data.get("acs")
        if not isinstance(items, list):
            return text
        changed = False
        for item in items:
            if isinstance(item, dict) and str(item.get("id") or "") == ac_id:
                item["status"] = status
                changed = True
        if not changed:
            return text
        replacement_yaml = yaml.safe_dump(data, sort_keys=False).rstrip()
        return text[: match.start(1)] + replacement_yaml + text[match.end(1) :]

    def set_status(self, goal_id: str, status: str, event_type: str, detail: dict[str, Any] | None = None) -> Goal:
        goal = self.load(goal_id)
        goal.status = status
        if status in {"blocked", "done", "cancelled"}:
            goal.active_task_id = None
        self.save(goal)
        event: dict[str, Any] = {"type": event_type, "status": status}
        if detail:
            event.update(detail)
        self.append_history(goal.id, event)
        return goal

    def next_task_number(self, goal_id: str) -> int:
        highest = 0
        for event in self.history(goal_id):
            task_id = str(event.get("task_id") or "")
            prefix = f"{goal_id}-WORK-"
            if task_id.startswith(prefix):
                suffix = task_id[len(prefix):]
                if suffix.isdigit():
                    highest = max(highest, int(suffix))
        return highest + 1

    def cancel(self, goal_id: str, reason: str = "Cancelled by user.") -> Goal:
        goal = self.load(goal_id)
        goal.status = "cancelled"
        goal.active_task_id = None
        self.save(goal)
        self.append_history(goal.id, {"type": "cancelled", "reason": reason})
        return goal

    @staticmethod
    def default_acceptance(goal: Goal) -> str:
        return (
            f"# Acceptance criteria for {goal.id}: {goal.title}\n\n"
            "Status: draft\n\n"
            "Goal Mode has captured the source. Derive or edit acceptance criteria here before approval.\n"
        )

    @staticmethod
    def default_plan(goal: Goal) -> str:
        return (
            f"# Plan for {goal.id}: {goal.title}\n\n"
            "Status: draft\n\n"
            "Goal Mode has captured the source. Derive or edit the execution plan here before approval.\n"
        )
