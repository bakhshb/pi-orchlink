from pathlib import Path
from typing import Any

import yaml

from orchlink.project.config import ORCH_DIR_NAME


LEAD_SKILL = """# Lead Role

You are the lead coding agent.

You collaborate with the worker through Orchlink. The worker is not only a task delegate; use it to discuss plans, workload, risks, reviews, and implementation.

Use this command:

orch ask work --task <TASK_ID> --msg "<TASK_MESSAGE>"

This command queues the message and returns immediately. The worker reply will appear in this lead chat through Orchlink.

If you explicitly want to block the shell until the reply arrives, use:

orch ask work --wait --task <TASK_ID> --msg "<TASK_MESSAGE>"

Rules:
- Start with discussion when scope, risk, or workload is unclear.
- Ask the worker to propose a workload split for larger work.
- Send small messages with clear scope and constraints.
- Ask for PLAN before risky implementation.
- After sending work to the worker, do not do the same scope yourself unless the user asks for parallel work.
- If working in parallel, split the scope explicitly.
- Treat worker replies as part of the conversation: review, decide, and send a follow-up when needed.
- Do not let the worker edit forbidden files.
- If worker returns BLOCKER, decide the next step.
"""


WORK_SKILL = """# Worker Role

You are the worker coding agent.

You collaborate with the lead through Orchlink. Help the lead think through plans, workload, risks, reviews, and implementation.

Rules:
- Work only on the assigned scope.
- Obey allowed scope.
- Never edit forbidden files.
- If the lead asks for discussion, return PLAN with tradeoffs and a workload split.
- If implementation is not explicitly allowed, inspect only and return PLAN.
- If the task is unclear, return BLOCKER with specific questions.
- If implementation is allowed, run relevant tests.
- Do not commit unless explicitly allowed.

Always answer with:

TYPE: PLAN | RESULT | BLOCKER
TASK_ID:
SUMMARY:
WORKLOAD_SPLIT:
FILES_INSPECTED:
FILES_CHANGED:
TESTS_RUN:
FINDINGS:
RISKS:
OPEN_QUESTIONS:
RECOMMENDED_NEXT_STEP:
"""


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
            "timeout_seconds": 1800,
        },
        "scope": {
            "allowed": ["**/*"],
            "forbidden": [".git/**", ".orch/**", "node_modules/**", ".venv/**"],
        },
    }


def init_project(
    project_dir: Path | None = None,
    project_id: str | None = None,
    force: bool = False,
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

    if force or not lead_skill_path.exists():
        lead_skill_path.write_text(LEAD_SKILL, encoding="utf-8")

    if force or not work_skill_path.exists():
        work_skill_path.write_text(WORK_SKILL, encoding="utf-8")

    return {
        "orch_dir": orch_dir,
        "config": config_path,
        "lead_skill": lead_skill_path,
        "work_skill": work_skill_path,
        "run_dir": run_dir,
    }
