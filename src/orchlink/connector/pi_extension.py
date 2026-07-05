"""Facade for generating Orchlink Pi extension files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchlink.connector.pi_extension_ui import ORCHLINK_PI_UI_EXTENSION
from orchlink.connector.pi_extension_worker import ORCHLINK_PI_EXTENSION
from orchlink.connector.pi_extension_writer import PiExtensionFile, PiExtensionWriter


def ensure_pi_extension(config: dict[str, Any]) -> Path:
    return PiExtensionWriter.for_project(config).write(
        PiExtensionFile("orchlink-pi-extension.ts", ORCHLINK_PI_EXTENSION)
    )


def ensure_orchlink_ui_extension(config: dict[str, Any]) -> Path:
    return PiExtensionWriter.for_project(config).write(
        PiExtensionFile("orchlink-pi-ui-extension.ts", ORCHLINK_PI_UI_EXTENSION)
    )


__all__ = ["ORCHLINK_PI_EXTENSION", "ORCHLINK_PI_UI_EXTENSION", "ensure_orchlink_ui_extension", "ensure_pi_extension"]
