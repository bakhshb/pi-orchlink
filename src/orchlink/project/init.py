from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml

from orchlink.project.config import ORCH_DIR_NAME


SKILL_ROLES = ("lead", "work")


def load_skill_template(role: str) -> str:
    """Return the packaged Markdown template for a generated project skill."""
    if role not in SKILL_ROLES:
        raise ValueError(f"Unsupported skill role: {role}")
    try:
        return files("orchlink.project").joinpath("templates", f"{role}.md").read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise RuntimeError(f"Missing Orchlink skill template for {role}. Reinstall or update Orchlink.") from exc


def default_project_config(project_dir: Path, project_id: str | None = None) -> dict[str, Any]:
    resolved_project_id = project_id or project_dir.name
    return {
        "project_id": resolved_project_id,
        "broker": {
            "url": "http://127.0.0.1:8787",
            "api_key": "change-me",
            "auto_start": True,
            "host": "127.0.0.1",
            "port": 8787,
            "auto_stop": True,
            "require_peer_sessions": True,
            "session_heartbeat_interval_seconds": 10,
            "session_grace_seconds": 25,
        },
        "pi": {
            "command": "pi",
        },
        "lead": {
            "agent_id": f"{resolved_project_id}.lead",
            "session_id": "lead",
            "project_dir": ".",
        },
        "work": {
            "agent_id": f"{resolved_project_id}.work",
            "session_id": "work",
            "project_dir": ".",
            "poll_wait_seconds": 5,
        },
        "scope": {
            "allowed": ["**/*"],
            "forbidden": [".git/**", ".orch/**", "node_modules/**", ".venv/**"],
        },
    }


def project_skill_statuses(project_dir: Path | None = None) -> dict[str, str]:
    root = (project_dir or Path.cwd()).resolve()
    skills_dir = root / ORCH_DIR_NAME / "skills"
    statuses: dict[str, str] = {}
    for role in SKILL_ROLES:
        path = skills_dir / f"{role}.md"
        if not path.is_file():
            statuses[role] = "missing"
        elif path.read_text(encoding="utf-8") != load_skill_template(role):
            statuses[role] = "stale"
        else:
            statuses[role] = "current"
    return statuses


def refresh_project_skills_if_needed(project_dir: Path | None = None) -> list[str]:
    root = (project_dir or Path.cwd()).resolve()
    statuses = project_skill_statuses(root)
    changed_roles = [role for role, status in statuses.items() if status != "current"]
    if changed_roles:
        init_project(root, refresh_skills=True)
    return [f"{role}.md" for role in changed_roles]


def init_project(
    project_dir: Path | None = None,
    project_id: str | None = None,
    force: bool = False,
    refresh_skills: bool = False,
) -> dict[str, Path]:
    root = (project_dir or Path.cwd()).resolve()
    orch_dir = root / ORCH_DIR_NAME
    skills_dir = orch_dir / "skills"
    run_dir = orch_dir / "run"
    config_path = orch_dir / "project.yaml"
    lead_skill_path = skills_dir / "lead.md"
    work_skill_path = skills_dir / "work.md"

    skills_dir.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir(parents=True, exist_ok=True)

    if force or not config_path.exists():
        config = default_project_config(root, project_id=project_id)
        config_path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")

    if force or refresh_skills or not lead_skill_path.exists():
        lead_skill_path.write_text(load_skill_template("lead"), encoding="utf-8")

    if force or refresh_skills or not work_skill_path.exists():
        work_skill_path.write_text(load_skill_template("work"), encoding="utf-8")

    return {
        "orch_dir": orch_dir,
        "config": config_path,
        "lead_skill": lead_skill_path,
        "work_skill": work_skill_path,
        "run_dir": run_dir,
    }
