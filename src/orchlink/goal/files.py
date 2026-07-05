from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import yaml

from orchlink.goal.models import Goal, utc_now_iso


GOAL_ID_RE = re.compile(r"^G(\d{3,})$")


class GoalFileStore:
    """YAML/JSONL persistence boundary for Goal Mode state."""

    def __init__(self, root: Path, error_factory: Callable[[str], Exception] = RuntimeError) -> None:
        self.root = root
        self._error_factory = error_factory

    def goal_dir(self, goal_id: str) -> Path:
        return self.root / goal_id

    def require_goal_dir(self, goal_id: str) -> Path:
        directory = self.goal_dir(goal_id)
        if not directory.is_dir():
            raise self._error_factory(f"Goal not found: {goal_id}")
        return directory

    def list_goal_ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return [path.name for path in sorted(self.root.iterdir()) if path.is_dir() and (path / "goal.yaml").is_file()]

    def next_goal_id(self) -> str:
        highest = 0
        if self.root.is_dir():
            for path in self.root.iterdir():
                match = GOAL_ID_RE.match(path.name)
                if match:
                    highest = max(highest, int(match.group(1)))
        return f"G{highest + 1:03d}"

    def create_goal_dir(self, goal_id: str) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        directory = self.goal_dir(goal_id)
        directory.mkdir()
        return directory

    def load_goal(self, goal_id: str) -> Goal:
        path = self.goal_dir(goal_id) / "goal.yaml"
        if not path.is_file():
            raise self._error_factory(f"Goal not found: {goal_id}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Goal.from_dict(data)

    def save_goal(self, goal: Goal) -> None:
        directory = self.require_goal_dir(goal.id)
        goal.updated_at = utc_now_iso()
        (directory / "goal.yaml").write_text(yaml.safe_dump(goal.to_dict(), sort_keys=False), encoding="utf-8")

    def write_source(self, goal_id: str, source_text: str) -> None:
        self.require_goal_dir(goal_id).joinpath("source.md").write_text(source_text, encoding="utf-8")

    def write_acceptance(self, goal_id: str, acceptance: str) -> None:
        self.require_goal_dir(goal_id).joinpath("acceptance.md").write_text(acceptance, encoding="utf-8")

    def write_plan(self, goal_id: str, plan: str) -> None:
        self.require_goal_dir(goal_id).joinpath("plan.md").write_text(plan, encoding="utf-8")

    def write_coverage(self, goal_id: str, coverage: str) -> None:
        self.require_goal_dir(goal_id).joinpath("coverage.md").write_text(coverage, encoding="utf-8")

    def write_audit(self, goal_id: str, audit: str) -> Path:
        path = self.require_goal_dir(goal_id) / "audit.md"
        path.write_text(audit, encoding="utf-8")
        return path

    def append_history(self, goal_id: str, event: dict[str, Any]) -> dict[str, Any]:
        directory = self.require_goal_dir(goal_id)
        record = {"time": utc_now_iso(), **event}
        with (directory / "history.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, sort_keys=True) + "\n")
        return record

    def history(self, goal_id: str) -> list[dict[str, Any]]:
        path = self.goal_dir(goal_id) / "history.jsonl"
        if not path.is_file():
            raise self._error_factory(f"Goal not found: {goal_id}")
        events = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
        return events

    def append_trial(self, goal_id: str, trial: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
        directory = self.require_goal_dir(goal_id)
        path = directory / "trials.jsonl"
        record = {"time": utc_now_iso(), **trial}
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, sort_keys=True) + "\n")
        return path, record

    def update_acceptance_status(self, goal_id: str, ac_id: str, status: str) -> None:
        path = self.goal_dir(goal_id) / "acceptance.md"
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
        updated = self.update_fenced_acceptance_yaml(text, ac_id, status)
        path.write_text(updated, encoding="utf-8")

    @staticmethod
    def update_fenced_acceptance_yaml(text: str, ac_id: str, status: str) -> str:
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


__all__ = ["GOAL_ID_RE", "GoalFileStore"]
