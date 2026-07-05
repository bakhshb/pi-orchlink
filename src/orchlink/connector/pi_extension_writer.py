"""File writer for generated Pi extension TypeScript files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchlink.project.config import run_dir


@dataclass(frozen=True)
class PiExtensionFile:
    filename: str
    content: str


class PiExtensionWriter:
    """Write generated extension content only when the file content changed."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @classmethod
    def for_project(cls, config: dict[str, object]) -> "PiExtensionWriter":
        return cls(run_dir(config))

    def write(self, extension: PiExtensionFile) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / extension.filename
        if not path.exists() or path.read_text(encoding="utf-8") != extension.content:
            path.write_text(extension.content, encoding="utf-8")
        return path


__all__ = ["PiExtensionFile", "PiExtensionWriter"]
