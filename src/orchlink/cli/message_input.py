"""CLI message input resolution for Orchlink commands."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import subprocess
import sys
import uuid
from typing import Any

import typer
from rich.console import Console

from orchlink.project.config import run_dir


console = Console()
MAX_MESSAGE_BYTES = 256 * 1024


@dataclass(frozen=True)
class MessageInput:
    """Resolve a CLI message from inline text, stdin, a file, or an editor."""

    text: str = ""
    file: Path | None = None
    edit: bool = False
    max_bytes: int = MAX_MESSAGE_BYTES

    def resolve(
        self,
        config: dict[str, Any] | None = None,
        *,
        task_id: str = "",
        worker: str = "",
        kind: str = "message",
        required: bool = True,
    ) -> str:
        self._validate_source_count()
        if self.edit:
            if config is None:
                raise ValueError("Internal error: --edit requires project config.")
            message = self._edit_message(config, task_id=task_id, worker=worker, kind=kind)
            if not message:
                console.print("[Orch] Message edit was empty; cancelled.")
                raise typer.Exit(1)
        elif self.file is not None:
            message = self._read_file(self.file)
        elif self.text == "-":
            message = sys.stdin.read()
        else:
            message = self.text
        message = self._check_size(message)
        if required and not message.strip():
            raise ValueError("Message cannot be empty. Use -m, -m -, --message-file, or --edit.")
        return message

    def _validate_source_count(self) -> None:
        selected = sum(bool(value) for value in (self.text, self.file, self.edit))
        if selected > 1:
            raise ValueError("Use only one of -m/--message, --message-file, or --edit.")

    def _check_size(self, message: str) -> str:
        size = len(message.encode("utf-8"))
        if size > self.max_bytes:
            raise ValueError(f"Message is too large ({size} bytes); limit is {self.max_bytes} bytes.")
        return message

    def _read_file(self, path: Path) -> str:
        if str(path) == "-":
            return sys.stdin.read()
        if not path.is_file():
            raise ValueError(f"Message file not found: {path}")
        if path.stat().st_size > self.max_bytes:
            raise ValueError(f"Message file is too large; limit is {self.max_bytes} bytes: {path}")
        return path.read_text(encoding="utf-8")

    def _edit_message(self, config: dict[str, Any], *, task_id: str, worker: str, kind: str) -> str:
        directory = run_dir(config) / "prompts"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"orchlink-{kind}-{task_id or 'message'}-{uuid.uuid4().hex}.md"
        path.write_text(
            f"# Orchlink {kind} prompt for {worker} {task_id}\n"
            "# Lines starting with # are ignored. Save and close to send.\n"
            "# Leave the body empty to cancel.\n\n",
            encoding="utf-8",
        )
        try:
            result = subprocess.run([*editor_command(), str(path)], check=False)  # noqa: S603 - user-selected local editor.
            if result.returncode != 0:
                console.print("[Orch] Message edit cancelled.")
                raise typer.Exit(1)
            return strip_editor_comments(path.read_text(encoding="utf-8"))
        finally:
            path.unlink(missing_ok=True)


# Convenience helper for command modules and focused tests.
def resolve_message_option(
    message: str = "",
    message_file: Path | None = None,
    edit: bool = False,
    config: dict[str, Any] | None = None,
    task_id: str = "",
    worker: str = "",
    kind: str = "message",
    required: bool = True,
) -> str:
    return MessageInput(text=message, file=message_file, edit=edit).resolve(
        config,
        task_id=task_id,
        worker=worker,
        kind=kind,
        required=required,
    )


def editor_command() -> list[str]:
    editor = (os.environ.get("VISUAL") or os.environ.get("EDITOR") or "").strip()
    if not editor:
        raise ValueError("Set VISUAL or EDITOR to use --edit.")
    return shlex.split(editor)


def strip_editor_comments(text: str) -> str:
    lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    return "\n".join(lines).strip()
