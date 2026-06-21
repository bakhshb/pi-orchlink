from typing import Any


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _format_list(values: list[str]) -> str:
    if not values:
        return "- None"
    return "\n".join(f"- {value}" for value in values)


def render_worker_prompt(message: dict[str, Any], worker_config: dict[str, Any]) -> str:
    payload = message.get("payload") or {}
    payload_scope = payload.get("scope") or {}
    config_scope = worker_config.get("scope") or {}
    scope = payload_scope or config_scope

    task_id = str(message.get("task_id", ""))
    intent = str(payload.get("intent") or payload.get("summary") or "")
    allowed_scope = _as_list(scope.get("allowed"))
    forbidden_scope = _as_list(scope.get("forbidden"))
    constraints = _as_list(payload.get("constraints"))
    expected_reply = _as_list(payload.get("expected_reply"))
    agent_id = str(worker_config.get("agent_id") or worker_config.get("work", {}).get("agent_id") or "work")

    return f"""You are {agent_id}, the worker coding agent in an Orchlink pair.

The lead may ask you to discuss, plan, inspect, implement scoped changes, or review work.

TASK ID:
{task_id}

INTENT:
{intent}

ALLOWED SCOPE:
{_format_list(allowed_scope)}

FORBIDDEN SCOPE:
{_format_list(forbidden_scope)}

CONSTRAINTS:
{_format_list(constraints)}

EXPECTED REPLY:
{_format_list(expected_reply)}

Mode rules:
- DISCUSS: compare options, risks, and workload. Do not edit files.
- PLAN: inspect if needed, then propose a plan. Do not edit files.
- DO: implement only if the lead explicitly allowed implementation.
- REVIEW: inspect the requested scope and report findings. Do not edit files unless asked.
- If no mode is provided, infer the safest mode. Prefer PLAN over DO.

Scope rules:
- Work only on this scope.
- Do not touch lead-owned scope in a parallel split.
- Do not expand scope.
- Do not edit forbidden files.
- If implementation is not explicitly allowed, inspect only and return PLAN.
- If the request is unclear, return BLOCKER with specific questions.
- If implementation is allowed, run relevant tests.
- Do not commit unless explicitly allowed.

Required response format:

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
