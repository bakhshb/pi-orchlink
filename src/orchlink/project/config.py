import os
import re
from pathlib import Path
from typing import Any

import yaml


ORCH_DIR_NAME = ".orch"
PROJECT_CONFIG_NAME = "project.yaml"
DEFAULT_WORKER_NAME = "work"
_WORKER_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_RESERVED_WORKER_NAMES = {"all", "broker", "lead"}


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


def save_project_config(config: dict[str, Any]) -> Path:
    path = Path(str(config.get("_config_path") or project_config_path(Path(str(config.get("_project_root") or Path.cwd())))))
    persisted = {key: value for key, value in config.items() if not str(key).startswith("_")}
    path.write_text(yaml.safe_dump(persisted, sort_keys=False), encoding="utf-8")
    return path


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


def broker_auto_stop(config: dict[str, Any]) -> bool:
    broker = config.get("broker") or {}
    return bool(broker.get("auto_stop", True))


def broker_require_peer_sessions(config: dict[str, Any]) -> bool:
    broker = config.get("broker") or {}
    return bool(broker.get("require_peer_sessions", True))


def broker_session_heartbeat_interval_seconds(config: dict[str, Any]) -> int:
    broker = config.get("broker") or {}
    return int(broker.get("session_heartbeat_interval_seconds") or 10)


def broker_session_grace_seconds(config: dict[str, Any]) -> int:
    broker = config.get("broker") or {}
    return int(broker.get("session_grace_seconds") or 25)


def broker_store_backend(config: dict[str, Any]) -> str:
    broker = config.get("broker") or {}
    return str(broker.get("store_backend") or "memory")


def broker_store_path(config: dict[str, Any]) -> str:
    broker = config.get("broker") or {}
    return str(broker.get("store_path") or ".orch/run/orchlink-journal.jsonl")


def project_root(config: dict[str, Any]) -> Path:
    return Path(str(config.get("_project_root") or Path.cwd())).resolve()


def orch_dir(config: dict[str, Any]) -> Path:
    return project_root(config) / ORCH_DIR_NAME


def run_dir(config: dict[str, Any]) -> Path:
    return orch_dir(config) / "run"


def skill_path(config: dict[str, Any], role: str) -> Path:
    return orch_dir(config) / "skills" / f"{role}.md"


def normalize_worker_name(name: str | None = None) -> str:
    """Return a validated configless worker name.

    Worker names are user-facing context handles, not YAML registry keys.
    Keep them path-safe because runtime state may live under .orch/run/workers/.
    """
    value = str(name or DEFAULT_WORKER_NAME).strip()
    if value in _RESERVED_WORKER_NAMES or not _WORKER_NAME_PATTERN.fullmatch(value):
        raise ValueError(
            "Worker name must start with a lowercase letter and contain only lowercase letters, digits, or hyphens."
        )
    return value


def worker_agent_id(config: dict[str, Any], name: str | None = None) -> str:
    project_id = str(config.get("project_id") or project_root(config).name)
    return f"{project_id}.{normalize_worker_name(name)}"


def worker_name_from_agent(config: dict[str, Any], agent_id: str | None) -> str:
    """Return the configless worker name represented by an agent id."""
    value = str(agent_id or "")
    project_id = str(config.get("project_id") or project_root(config).name)
    prefix = f"{project_id}."
    if value.startswith(prefix):
        candidate = value[len(prefix) :]
        try:
            return normalize_worker_name(candidate)
        except ValueError:
            return candidate or value
    return value or DEFAULT_WORKER_NAME


def with_worker_name(config: dict[str, Any], name: str | None = None, session_id: str | None = None) -> dict[str, Any]:
    """Return a config overlay for a named worker without mutating project YAML."""
    worker_name = normalize_worker_name(name)
    updated = dict(config)
    work_config = dict(config.get("work") or {})
    if session_id is None:
        session_id = str(work_config.get("session_id") or DEFAULT_WORKER_NAME) if worker_name == DEFAULT_WORKER_NAME else worker_name
    if worker_name == DEFAULT_WORKER_NAME and work_config.get("agent_id"):
        work_config["agent_id"] = str(work_config["agent_id"])
    else:
        work_config["agent_id"] = worker_agent_id(config, worker_name)
    work_config["session_id"] = session_id
    work_config["name"] = worker_name
    updated["work"] = work_config
    return updated


def resolve_agent_id(config: dict[str, Any], alias_or_id: str) -> str:
    if "." in alias_or_id:
        return alias_or_id
    if alias_or_id == "lead":
        role_config = config.get(alias_or_id) or {}
        if role_config.get("agent_id"):
            return str(role_config["agent_id"])
    if alias_or_id == DEFAULT_WORKER_NAME:
        role_config = config.get("work") or {}
        if role_config.get("agent_id"):
            return str(role_config["agent_id"])
    return worker_agent_id(config, alias_or_id)


def role_agent_id(config: dict[str, Any], role: str) -> str:
    role_config = config.get(role) or {}
    if role_config.get("agent_id"):
        return str(role_config["agent_id"])
    project_id = str(config.get("project_id") or project_root(config).name)
    return f"{project_id}.{role}"
