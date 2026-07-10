from __future__ import annotations

from contextlib import contextmanager
import errno
import json
import os
import re
import shutil
import socket
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4

import yaml

from orchlink.goal.models import Goal, utc_now_iso


GOAL_ID_RE = re.compile(r"^G(\d{3,})$")
LOCK_STALE_SECONDS = 300
LOCK_RETRY_SECONDS = 0.05


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
        self._fsync_directory(self.root)
        return directory

    @contextmanager
    def lock_goal(self, goal_id: str) -> Iterator[None]:
        lock_path = self.require_goal_dir(goal_id) / ".goal.lock"
        token = uuid4().hex
        while True:
            try:
                lock_path.mkdir()
            except FileExistsError:
                self._recover_stale_lock(lock_path)
                time.sleep(LOCK_RETRY_SECONDS)
            else:
                break
        try:
            self._write_lock_owner(lock_path, token)
            yield
        finally:
            self._release_lock(lock_path, token)

    def load_goal(self, goal_id: str) -> Goal:
        path = self.goal_dir(goal_id) / "goal.yaml"
        if not path.is_file():
            raise self._error_factory(f"Goal not found: {goal_id}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return Goal.from_dict(data)

    def save_goal(self, goal: Goal) -> None:
        directory = self.require_goal_dir(goal.id)
        goal.updated_at = utc_now_iso()
        self._atomic_write_text(directory / "goal.yaml", yaml.safe_dump(goal.to_dict(), sort_keys=False))

    def write_source(self, goal_id: str, source_text: str) -> None:
        self._atomic_write_text(self.require_goal_dir(goal_id) / "source.md", source_text)

    def write_acceptance(self, goal_id: str, acceptance: str) -> None:
        self._atomic_write_text(self.require_goal_dir(goal_id) / "acceptance.md", acceptance)

    def write_plan(self, goal_id: str, plan: str) -> None:
        self._atomic_write_text(self.require_goal_dir(goal_id) / "plan.md", plan)

    def write_coverage(self, goal_id: str, coverage: str) -> None:
        self._atomic_write_text(self.require_goal_dir(goal_id) / "coverage.md", coverage)

    def write_audit(self, goal_id: str, audit: str) -> Path:
        path = self.require_goal_dir(goal_id) / "audit.md"
        self._atomic_write_text(path, audit)
        return path

    def append_history(self, goal_id: str, event: dict[str, Any]) -> dict[str, Any]:
        directory = self.require_goal_dir(goal_id)
        record = {"time": utc_now_iso(), **event}
        with (directory / "history.jsonl").open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, sort_keys=True) + "\n")
            file.flush()
            os.fsync(file.fileno())
        self._fsync_directory(directory)
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
            file.flush()
            os.fsync(file.fileno())
        self._fsync_directory(directory)
        return path, record

    def update_acceptance_status(self, goal_id: str, ac_id: str, status: str) -> None:
        path = self.goal_dir(goal_id) / "acceptance.md"
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
        updated = self.update_fenced_acceptance_yaml(text, ac_id, status)
        self._atomic_write_text(path, updated)

    def repair_acceptance_projection(self, goal_id: str, ac_status: dict[str, str]) -> None:
        """Rewrite acceptance.md so its YAML block matches the authoritative ac_status map."""
        path = self.goal_dir(goal_id) / "acceptance.md"
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8")
        match = re.search(r"```(?:yaml|yml)?\s*\n(.*?)\n```", text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            return
        data = yaml.safe_load(match.group(1)) or {}
        if not isinstance(data, dict):
            return
        items = data.get("acceptance") or data.get("acceptance_criteria") or data.get("criteria") or data.get("acs")
        if not isinstance(items, list):
            return
        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or "")
            if not item_id:
                continue
            authoritative_status = ac_status.get(item_id, "pending")
            if str(item.get("status") or "pending") != authoritative_status:
                item["status"] = authoritative_status
                changed = True
        if not changed:
            return
        replacement_yaml = yaml.safe_dump(data, sort_keys=False).rstrip()
        updated = text[: match.start(1)] + replacement_yaml + text[match.end(1) :]
        self._atomic_write_text(path, updated)

    @staticmethod
    def _atomic_write_text(path: Path, text: str) -> None:
        data = text.encode("utf-8")
        tmp_path: Path | None = None
        fd, raw_tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        tmp_path = Path(raw_tmp_path)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(tmp_path, path)
            GoalFileStore._fsync_directory(path.parent)
        except Exception:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError as exc:
            if GoalFileStore._directory_fsync_unsupported(exc):
                return
            raise
        try:
            try:
                os.fsync(fd)
            except OSError as exc:
                if not GoalFileStore._directory_fsync_unsupported(exc):
                    raise
        finally:
            os.close(fd)

    @staticmethod
    def _directory_fsync_unsupported(exc: OSError) -> bool:
        return os.name == "nt" or exc.errno in {
            errno.EACCES,
            errno.EINVAL,
            errno.ENOTSUP,
            errno.EOPNOTSUPP,
            errno.EPERM,
        }

    @staticmethod
    def _write_lock_owner(lock_path: Path, token: str) -> None:
        owner = {
            "hostname": socket.gethostname(),
            "pid": os.getpid(),
            "token": token,
        }
        (lock_path / "owner.json").write_text(json.dumps(owner), encoding="utf-8")

    @staticmethod
    def _read_lock_owner(lock_path: Path) -> dict[str, Any] | None:
        try:
            owner = json.loads((lock_path / "owner.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return owner if isinstance(owner, dict) else None

    @classmethod
    def _recover_stale_lock(cls, lock_path: Path) -> None:
        if not cls._lock_is_stale(lock_path):
            return
        recovered_path = lock_path.with_name(f"{lock_path.name}.stale.{uuid4().hex}")
        try:
            os.replace(lock_path, recovered_path)
        except OSError:
            return
        try:
            if recovered_path.is_dir():
                shutil.rmtree(recovered_path)
            else:
                recovered_path.unlink()
        except OSError:
            pass

    @classmethod
    def _lock_is_stale(cls, lock_path: Path) -> bool:
        try:
            age = time.time() - lock_path.stat().st_mtime
        except FileNotFoundError:
            return False
        owner = cls._read_lock_owner(lock_path)
        if owner and owner.get("hostname") == socket.gethostname():
            pid = owner.get("pid")
            if isinstance(pid, int):
                return not cls._process_is_running(pid)
        return age >= LOCK_STALE_SECONDS

    @staticmethod
    def _process_is_running(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True
        return True

    @classmethod
    def _release_lock(cls, lock_path: Path, token: str) -> None:
        owner = cls._read_lock_owner(lock_path)
        if owner is None or owner.get("token") != token:
            return
        try:
            shutil.rmtree(lock_path)
        except FileNotFoundError:
            pass

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
