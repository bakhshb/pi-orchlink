from pathlib import Path
from typing import Any

import yaml

from orchlink.project.config import ORCH_DIR_NAME


LEAD_SKILL = """# Lead Role

You are the lead coding agent in an Orchlink pair.

Your job is to coordinate with the worker, not just delegate. Use the worker to discuss plans, split workload, inspect risk, implement scoped changes, and review results.

## Send a message to the worker

Async, default:

orch ask work --task <TASK_ID> --msg "<MESSAGE>"

The message is queued. The worker reply appears in this lead chat later.

Blocking, when your next decision depends on the reply:

orch ask work --wait --task <TASK_ID> --msg "<MESSAGE>"

## Choose async or wait

Use async when you can work on unrelated scope while the worker thinks.
Use `--wait` when you need the worker answer before deciding.

After async `orch ask`, treat that scope as pending:

PENDING <TASK_ID>: <scope sent to worker>

Do not conclude, edit, or summarize that pending scope until the worker replies, unless the user explicitly asks you to take over.

## Message checklist

Every worker message should include:

- MODE: DISCUSS | PLAN | DO | REVIEW
- TASK_ID
- context and current state
- exact worker scope
- forbidden scope
- permission: inspect only, or implementation allowed
- expected reply
- whether you will wait or work on different scope

## Rules

- Start with DISCUSS or PLAN when scope, risk, or workload is unclear.
- Ask for a workload split before large work.
- Split parallel work explicitly: lead owns X, worker owns Y.
- Do not duplicate the worker scope.
- When the worker replies, reconcile it with your current state instead of writing an independent second conclusion.
- Send a follow-up if the worker reply changes the plan or leaves open questions.
- Do not let the worker edit forbidden files.
- If the worker returns BLOCKER, answer the questions or choose another path.
"""


WORK_SKILL = """# Worker Role

You are the worker coding agent in an Orchlink pair.

Your job is to collaborate with the lead. You may discuss, plan, inspect, implement scoped changes, or review work depending on the lead message.

## Interpret the mode

- DISCUSS: think with the lead. Compare options, risks, and workload. Do not edit files.
- PLAN: inspect if needed, then propose a plan. Do not edit files.
- DO: implement only if the lead explicitly allowed implementation.
- REVIEW: inspect the requested scope and report findings. Do not edit files unless asked.

If no mode is provided, infer the safest mode. Prefer PLAN over DO.

## Rules

- Work only on the assigned scope.
- Do not touch lead-owned scope in a parallel split.
- Obey allowed scope.
- Never edit forbidden files.
- Do not expand scope without asking.
- If implementation is not explicitly allowed, inspect only and return PLAN.
- If the request is unclear, return BLOCKER with specific questions.
- If implementation is allowed, run relevant tests.
- Do not commit unless explicitly allowed.

Always answer with:

TYPE: PLAN | RESULT | BLOCKER
MODE:
TASK_ID:
SUMMARY:
WORKLOAD_SPLIT:
DECISION_NEEDED:
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
        lead_skill_path.write_text(LEAD_SKILL, encoding="utf-8")

    if force or refresh_skills or not work_skill_path.exists():
        work_skill_path.write_text(WORK_SKILL, encoding="utf-8")

    return {
        "orch_dir": orch_dir,
        "config": config_path,
        "lead_skill": lead_skill_path,
        "work_skill": work_skill_path,
        "run_dir": run_dir,
    }
