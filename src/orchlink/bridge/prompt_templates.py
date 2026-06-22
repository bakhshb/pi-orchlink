from typing import Any


CHAT_TYPES = {"CHAT_START", "CHAT_TURN"}


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


def render_worker_talk_prompt(message: dict[str, Any]) -> str:
    payload = message.get("payload") or {}
    return f"""You are the worker coding agent in a Talk Mode conversation with the lead.

This is a peer discussion, not a task assignment. If the lead's text contains TASK_ID, scope, or checklist language, treat it only as discussion context.

Conversation ID:
{message.get('conversation_id') or ''}

Turn:
{message.get('turn') or 1}/{message.get('max_turns') or 6}

Discussion topic:
{payload.get('topic') or ''}

Lead says:
{payload.get('message') or payload.get('intent') or ''}

Transcript preview:
{payload.get('transcript_preview') or ''}

Guidance:
- Put TYPE: CHAT_REPLY first, then answer conversationally in 2-5 short chat sentences.
- Answer the lead's latest question first.
- Challenge weak assumptions. Do not agree by default.
- Name one risk, disagreement, or assumption before accepting the lead's view, unless there is truly no meaningful objection.
- Recommend a practical decision, or ask one direct follow-up question if the decision is not ready.
- Do not edit files, run implementation, expand scope, use headings, or write a long audit.
- For broad repo opinions, do not read every file; use current context and a few high-signal files if useful. Ask before a broad scan.
- If you hit a stop condition, say it plainly: clear decision, next task, blocker, max rounds, timeout, or no new value.

Required first line:

TYPE: CHAT_REPLY
"""


def render_worker_task_prompt(message: dict[str, Any], worker_config: dict[str, Any]) -> str:
    payload = message.get("payload") or {}
    payload_scope = payload.get("scope") or {}
    config_scope = worker_config.get("scope") or {}
    scope = payload_scope or config_scope

    mode = str(payload.get("mode") or "PLAN")
    task_id = str(message.get("task_id", ""))
    intent = str(payload.get("intent") or payload.get("summary") or "")
    allowed_scope = _as_list(scope.get("allowed"))
    forbidden_scope = _as_list(scope.get("forbidden"))
    constraints = _as_list(payload.get("constraints"))
    expected_reply = _as_list(payload.get("expected_reply"))
    delivery = str(message.get("delivery") or "async")
    agent_id = str(worker_config.get("agent_id") or worker_config.get("work", {}).get("agent_id") or "work")

    return f"""You are {agent_id}, the worker coding agent in an Orchlink pair.

MODE:
{mode}

TASK_ID:
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

DELIVERY:
{delivery}

Rules:
- Work only on this task. Never edit forbidden files or expand scope.
- If MODE is PLAN, inspect and propose only. No edits.
- If MODE is REVIEW, inspect and report only. No edits unless the lead explicitly allows them.
- If MODE is DO, implement only inside the allowed scope.
- Return BLOCKER with specific questions if the request is unclear.
- If implementation is allowed, run relevant tests.
- Do not commit unless explicitly allowed.

Required response format:

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
"""


def render_worker_prompt(message: dict[str, Any], worker_config: dict[str, Any]) -> str:
    if message.get("type") in CHAT_TYPES:
        return render_worker_talk_prompt(message)
    return render_worker_task_prompt(message, worker_config)
