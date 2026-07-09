"""Filesystem mkdir lock for loop state mutation."""

from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import TracebackType

from orchlink.loop.domain.errors import LockHeldError


@dataclass(frozen=True, slots=True)
class LockInfo:
    actor: str
    pid: int
    acquired_at: float


class MkdirLock:
    def __init__(self, path: Path, *, stale_after_seconds: float = 3600.0) -> None:
        self.path = Path(path)
        self.stale_after_seconds = stale_after_seconds
        self.info: LockInfo | None = None
        self._held = False

    def acquire(self, actor: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.info = LockInfo(actor=actor, pid=os.getpid(), acquired_at=time.time())
        try:
            os.mkdir(self.path)
        except FileExistsError as exc:
            if self._break_stale_lock():
                try:
                    os.mkdir(self.path)
                except FileExistsError as retry_exc:
                    raise LockHeldError(f"loop state lock is held: {self.path}") from retry_exc
            else:
                raise LockHeldError(f"loop state lock is held: {self.path}") from exc
        self._held = True
        self._write_owner()

    def release(self) -> None:
        if not self._held:
            return
        try:
            shutil.rmtree(self.path)
        finally:
            self._held = False

    def __enter__(self) -> "MkdirLock":
        if self.info is None:
            self.acquire("unknown")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()

    def _write_owner(self) -> None:
        if self.info is None:
            return
        owner = self.path / "owner.json"
        owner.write_text(json.dumps(asdict(self.info), sort_keys=True), encoding="utf-8")

    def _break_stale_lock(self) -> bool:
        owner = self.path / "owner.json"
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return True
        age = time.time() - stat.st_mtime
        if age <= self.stale_after_seconds:
            return False
        pid = None
        try:
            data = json.loads(owner.read_text(encoding="utf-8"))
            pid = int(data.get("pid")) if data.get("pid") is not None else None
        except Exception:
            pid = None
        if pid is not None and _pid_is_live(pid):
            return False
        try:
            shutil.rmtree(self.path)
        except FileNotFoundError:
            return True
        return True


def _pid_is_live(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
