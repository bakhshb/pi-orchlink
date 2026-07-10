"""Atomic file-write helpers used by the JSONL broker store.

Centralizes the ``fsync + os.replace`` and ``fsync-after-append`` patterns so
the JSONL backend keeps the same durability guarantees whether it is
appending a single record or compacting the whole journal.

These helpers are deliberately narrow: the JSONL store owns its ordering
policy, serialization, and lock management. The helpers only guarantee that
the file-system call sequence either succeeds end-to-end (data + metadata
durable) or leaves the canonical file unchanged.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _fsync_directory(path: Path) -> None:
    """Best-effort fsync of the directory containing ``path``.

    On some platforms / filesystems directory fd operations are unsupported
    or fail; in those cases the exception is swallowed so the atomic write
    still succeeds. Directory fsync ensures the ``os.replace`` metadata entry
    is durable, not just the file data.
    """
    try:
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def atomic_write_text(path: Path | str, text: str) -> None:
    """Atomically replace ``path`` with ``text``.

    Writes to a sibling tmp file in the same directory, flushes + fsyncs the
    tmp file (so data + metadata are durable), and ``os.replace``'s it onto
    ``path``. On any failure the tmp file is removed and the original
    ``path`` is left untouched.
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=file_path.name + ".", suffix=".tmp", dir=str(file_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(text)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, file_path)
        _fsync_directory(file_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def atomic_append_jsonl_line(path: Path | str, line: str) -> None:
    """Append ``line`` (no trailing newline added) to ``path`` and fsync.

    The caller is responsible for serializing concurrent calls; this helper
    only guarantees that the bytes that reach the page cache are also
    flushed to durable storage before the call returns.

    ``line`` must already include its trailing ``\\n`` if the caller wants
    JSONL semantics; this helper does not add one.
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as file:
        file.write(line)
        file.flush()
        os.fsync(file.fileno())


def encode_jsonl_record(record: dict[str, Any]) -> str:
    """Encode ``record`` as a single JSONL line (no trailing newline)."""
    return json.dumps(record, sort_keys=True, default=str)


def count_complete_jsonl_lines(path: Path | str) -> int:
    """Return the number of *complete* JSONL lines in ``path``.

    A line counts when it parses as JSON. The trailing partial line (the
    bytes after the last ``\\n`` that do not parse) is excluded so callers
    can use the result as a record-count proxy without re-parsing.
    """
    file_path = Path(path)
    if not file_path.is_file():
        return 0
    count = 0
    with file_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            stripped = raw.rstrip("\n")
            if not stripped:
                continue
            try:
                json.loads(stripped)
            except json.JSONDecodeError:
                # Partial last line: stop counting. Earlier lines that
                # don't parse are also excluded so a corrupt journal
                # never inflates the count.
                break
            count += 1
    return count


def read_latest_snapshot(path: Path | str) -> dict[str, Any] | None:
    """Return the latest ``snapshot`` field from a JSONL journal file.

    Skips lines that fail to parse (including a truncated final line) and
    walks the file once. Returns ``None`` when no record carries a valid
    snapshot.
    """
    file_path = Path(path)
    if not file_path.is_file():
        return None
    latest: dict[str, Any] | None = None
    with file_path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            stripped = raw.rstrip("\n")
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError:
                # Partial / corrupt line: keep scanning earlier lines
                # but stop trusting this point forward.
                continue
            if not isinstance(record, dict):
                continue
            snapshot = record.get("snapshot")
            if isinstance(snapshot, dict):
                latest = snapshot
    return latest