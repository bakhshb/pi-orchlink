"""Secret loading for loop connectors.

Connector secrets live outside project state. This gateway reads tokens from the
process environment or an external secrets directory and never stores or logs the
secret value.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)


class ConnectorSecretMissing(RuntimeError):
    """Raised when a required connector secret is unavailable or unsafe."""

    def __init__(self, name: str, message: str | None = None) -> None:
        self.name = name
        super().__init__(message or f"connector secret missing: {name}")


class ConnectorSecretGateway:
    def __init__(self, secrets_dir: Path | str | None = None) -> None:
        if secrets_dir is None:
            override = os.environ.get("ORCHLINK_SECRETS_DIR")
            secrets_dir = Path(override).expanduser() if override else Path("~/.config/orchlink/secrets").expanduser()
        self.secrets_dir = Path(secrets_dir).expanduser()

    def get(self, name: str) -> str | None:
        self._ensure_external(name)
        key = self._env_key(name)
        value = os.environ.get(key)
        if value:
            log.debug("connector secret %s loaded from env", name)
            return value
        path = self.secrets_dir / f"{name}.token"
        try:
            file_value = path.read_text(encoding="utf-8").strip()
        except OSError:
            log.debug("connector secret %s missing", name)
            return None
        if not file_value:
            log.debug("connector secret %s missing", name)
            return None
        log.debug("connector secret %s loaded from file", name)
        return file_value

    def require(self, name: str) -> str:
        value = self.get(name)
        if value is None:
            raise ConnectorSecretMissing(name)
        return value

    def _env_key(self, name: str) -> str:
        normalized = "".join(char if char.isalnum() else "_" for char in name.upper())
        return f"ORCHLINK_{normalized}_TOKEN"

    def _ensure_external(self, name: str) -> None:
        try:
            parts = self.secrets_dir.resolve().parts
        except OSError:
            parts = self.secrets_dir.absolute().parts
        if ".orch" in parts:
            raise ConnectorSecretMissing(
                name,
                f"connector secret directory for {name} must be outside .orch: {self.secrets_dir}",
            )


__all__ = ["ConnectorSecretGateway", "ConnectorSecretMissing"]
