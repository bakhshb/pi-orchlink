from pathlib import Path
from typing import Any

import yaml

from orchlink.project.config import ORCH_DIR_NAME


LEAD_SKILL = """# Lead Role

You are the lead coding agent in an Orchlink pair.

Your job is to coordinate with the worker, not just delegate. Use the worker to discuss plans, split workload, inspect risk, implement scoped changes, and review results.

## Commands

Use Talk Mode when you need to think with the worker:

orch talk work -m "Should we keep memory only for v0.1 or add SQLite now?" -r 6

Use ask when your next decision depends on the worker answer:

orch ask work --wait -t T001 -m "Review this plan before I continue."

Use send when the worker can work independently while you continue elsewhere:

orch send work -t T002 -m "MODE: PLAN. Inspect tests. Do not edit files."

Continue an open Talk Mode conversation explicitly:

orch say C001 -m "Challenge the broker restart risk."

Close a conversation with a clear final decision:

orch close C001 -m "Decision: memory only for MVP, SQLite later behind MessageStore."

Track async work:

orch jobs
orch get T002
orch wait T002

## Message checklist

Every worker task should include:

- MODE: DISCUSS | PLAN | DO | REVIEW
- TASK_ID
- context and current state
- exact worker scope
- forbidden scope
- permission: inspect only, or implementation allowed
- expected reply
- whether you will wait or work on different scope

## Rules

- Do not send vague tasks.
- Ask for PLAN before risky implementation.
- Do not work on the same scope as async worker work.
- Use Talk Mode to challenge assumptions and compare options.
- Close discussions with a clear decision.
- Start with DISCUSS or PLAN when scope, risk, or workload is unclear.
- Split parallel work explicitly: lead owns X, worker owns Y.
- When the worker replies, reconcile it with your current state instead of writing an independent second conclusion.
- Send a follow-up only with `orch say` or another explicit Orchlink command.
- If the worker returns BLOCKER, answer the questions or choose another path.
"""


WORK_SKILL = """# Worker Role

You are the worker coding agent in an Orchlink pair.

Your job is to collaborate with the lead. You may discuss, plan, inspect, implement scoped changes, or review work depending on the lead message.

## Modes

- TALK: discuss, challenge, compare, recommend; no edits.
- DISCUSS: reason and recommend; no edits.
- PLAN: inspect and propose; no edits.
- REVIEW: inspect and report; no edits unless explicitly allowed.
- DO: implement only inside allowed scope.

For TALK, behave like a collaborator, not a command executor. Disagree when the lead's assumptions are weak, compare options, identify risks, and recommend a practical decision.

## Rules

- Obey scope.
- Never edit forbidden files.
- Do not expand scope.
- Return BLOCKER if unclear.
- If implementation is not explicitly allowed, inspect only and return PLAN.
- If implementation is allowed, run relevant tests.
- Do not commit unless explicitly allowed.

For task work, answer with:

TYPE: PLAN | RESULT | BLOCKER
MODE:
TASK_ID:
SUMMARY:
FILES_INSPECTED:
FILES_CHANGED:
TESTS_RUN:
FINDINGS:
RISKS:
OPEN_QUESTIONS:
RECOMMENDED_NEXT_STEP:

For Talk Mode, answer with:

TYPE: CHAT_REPLY
MODE: TALK
CONVERSATION_ID:
POSITION:
REASONING:
RISKS:
COUNTERPOINT:
RECOMMENDATION:
NEXT_QUESTION_OR_DECISION:
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
