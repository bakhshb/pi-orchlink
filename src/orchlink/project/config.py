import os
from pathlib import Path
from typing import Any

import yaml


ORCH_DIR_NAME = ".orch"
PROJECT_CONFIG_NAME = "project.yaml"


class ProjectConfigError(RuntimeError):
    """Raised when a project-local Orchlink config cannot be found or loaded."""


def find_project_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / ORCH_DIR_NAME / PROJECT_CONFIG_NAME).is_file():
            return candidate
    raise ProjectConfigError("No .orch/project.yaml found. Run `orch init` in this project first.")


def project_config_path(project_root: Path | None = None) -> Path:
    root = find_project_root(project_root) if project_root is not None else find_project_root()
    return root / ORCH_DIR_NAME / PROJECT_CONFIG_NAME


def load_project_config(project_root: Path | None = None) -> dict[str, Any]:
    root = find_project_root(project_root)
    path = root / ORCH_DIR_NAME / PROJECT_CONFIG_NAME
    with path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}
    config.setdefault("project_id", root.name)
    config["_project_root"] = str(root)
    config["_config_path"] = str(path)
    return config


def broker_url(config: dict[str, Any]) -> str:
    broker = config.get("broker") or {}
    return str(os.getenv("ORCHLINK_BROKER_URL") or broker.get("url") or "http://127.0.0.1:8787")


def broker_api_key(config: dict[str, Any]) -> str:
    broker = config.get("broker") or {}
    return str(os.getenv("ORCHLINK_API_KEY") or broker.get("api_key") or "change-me")


def broker_host(config: dict[str, Any]) -> str:
    broker = config.get("broker") or {}
    return str(broker.get("host") or "127.0.0.1")


def broker_port(config: dict[str, Any]) -> int:
    broker = config.get("broker") or {}
    return int(broker.get("port") or 8787)


def broker_auto_start(config: dict[str, Any]) -> bool:
    broker = config.get("broker") or {}
    return bool(broker.get("auto_start", True))


def project_root(config: dict[str, Any]) -> Path:
    return Path(str(config.get("_project_root") or Path.cwd())).resolve()


def orch_dir(config: dict[str, Any]) -> Path:
    return project_root(config) / ORCH_DIR_NAME


def run_dir(config: dict[str, Any]) -> Path:
    return orch_dir(config) / "run"


def skill_path(config: dict[str, Any], role: str) -> Path:
    return orch_dir(config) / "skills" / f"{role}.md"


def resolve_agent_id(config: dict[str, Any], alias_or_id: str) -> str:
    if "." in alias_or_id:
        return alias_or_id
    if alias_or_id in {"lead", "work"}:
        role_config = config.get(alias_or_id) or {}
        if role_config.get("agent_id"):
            return str(role_config["agent_id"])
    project_id = str(config.get("project_id") or project_root(config).name)
    return f"{project_id}.{alias_or_id}"


def role_agent_id(config: dict[str, Any], role: str) -> str:
    role_config = config.get(role) or {}
    if role_config.get("agent_id"):
        return str(role_config["agent_id"])
    project_id = str(config.get("project_id") or project_root(config).name)
    return f"{project_id}.{role}"
