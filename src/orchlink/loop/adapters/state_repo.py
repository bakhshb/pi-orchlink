"""Markdown-backed loop state repository."""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from orchlink.loop.adapters.lock import MkdirLock
from orchlink.loop.adapters.markdown_codec import (
    MarkdownStateDocument,
    decode_markdown,
    default_document,
    encode_markdown,
)
from orchlink.loop.domain.errors import IllegalTransition, StateCorrupt
from orchlink.loop.domain.item import LoopState


class LoopStateRepo:
    """Repository for `.orch/loop/state.md`.

    The single fenced `yaml orchloop.v1` block is machine state. Markdown before
    and after it is user-owned notes and is preserved byte-for-byte. Malformed
    machine state is exposed to callers only as StateCorrupt.
    """

    def __init__(self, project_dir: str | Path, *, stale_after_seconds: float = 3600.0) -> None:
        self.project_dir = Path(project_dir)
        self.state_path = self.project_dir / ".orch" / "loop" / "state.md"
        self.lock_path = self.project_dir / ".orch" / "loop" / "state.lock"
        self.stale_after_seconds = stale_after_seconds

    def read_only(self) -> LoopState:
        return self._read_document().state

    @contextmanager
    def transaction(self, actor: str) -> Iterator[LoopState]:
        lock = MkdirLock(self.lock_path, stale_after_seconds=self.stale_after_seconds)
        lock.acquire(actor)
        document = self._read_document()
        try:
            yield document.state
        except Exception:
            raise
        else:
            self._write_document(document)
        finally:
            lock.release()

    def _read_document(self) -> MarkdownStateDocument:
        if not self.state_path.exists():
            return default_document()
        text = self.state_path.read_text(encoding="utf-8")
        try:
            return decode_markdown(text)
        except StateCorrupt:
            raise
        except (KeyError, ValueError, AttributeError, TypeError, IllegalTransition) as exc:
            raise StateCorrupt(f"malformed orchloop.v1 machine state: {exc}") from exc

    def _write_document(self, document: MarkdownStateDocument) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = encode_markdown(document).encode("utf-8")
        fd = -1
        tmp_name = ""
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix="state.",
                suffix=".tmp",
                dir=str(self.state_path.parent),
            )
            with os.fdopen(fd, "wb") as handle:
                fd = -1
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.state_path)
            _fsync_dir(self.state_path.parent)
        finally:
            if fd != -1:
                os.close(fd)
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass


def _fsync_dir(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        os.fsync(fd)
    except OSError:
        return
    finally:
        if fd != -1:
            os.close(fd)
