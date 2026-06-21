import asyncio
import sys

from orchlink.bridge.agent_runner import run_command
from orchlink.bridge.prompt_templates import render_worker_prompt


def test_render_worker_prompt_uses_task_payload_and_worker_defaults():
    message = {
        "task_id": "TEST-001",
        "payload": {
            "intent": "Inspect backend duplication and return PLAN only.",
            "constraints": ["Do not edit files."],
            "expected_reply": ["summary", "risks"],
        },
    }
    worker_config = {
        "agent_id": "worker-backend",
        "scope": {
            "allowed": ["apps/api/**", "tests/**"],
            "forbidden": ["apps/web/**"],
        },
    }

    prompt = render_worker_prompt(message, worker_config)

    assert "You are worker-backend" in prompt
    assert "TASK_ID:\nTEST-001" in prompt
    assert "Inspect backend duplication" in prompt
    assert "- apps/api/**" in prompt
    assert "- apps/web/**" in prompt
    assert "TYPE: PLAN | RESULT | BLOCKER" in prompt
    assert "FILES_INSPECTED" in prompt
    assert "DELIVERY:" in prompt
    assert "If MODE is PLAN" in prompt


def test_render_worker_prompt_uses_talk_shape():
    message = {
        "type": "CHAT_START",
        "conversation_id": "C001",
        "turn": 1,
        "max_turns": 6,
        "payload": {"mode": "TALK", "topic": "SQLite?", "message": "Challenge memory-only."},
    }

    prompt = render_worker_prompt(message, {"agent_id": "worker-backend"})

    assert "Talk Mode conversation" in prompt
    assert "Conversation ID:\nC001" in prompt
    assert "Challenge weak assumptions" in prompt
    assert "TYPE: CHAT_REPLY" in prompt


def test_run_command_appends_prompt_and_captures_stdout():
    async def run():
        result = await run_command(
            {
                "mode": "command",
                "argv": [
                    sys.executable,
                    "-c",
                    "import sys; print('TYPE: PLAN'); print('PROMPT:' + sys.argv[1][:5])",
                ],
            },
            "hello worker",
            timeout_seconds=5,
        )

        assert result.exit_code == 0
        assert result.timed_out is False
        assert "TYPE: PLAN" in result.stdout
        assert "PROMPT:hello" in result.stdout
        assert result.stderr == ""

    asyncio.run(run())


def test_run_command_reports_nonzero_exit():
    async def run():
        result = await run_command(
            {
                "mode": "command",
                "argv": [sys.executable, "-c", "import sys; print('bad', file=sys.stderr); sys.exit(3)"],
            },
            "prompt",
            timeout_seconds=5,
        )

        assert result.exit_code == 3
        assert result.timed_out is False
        assert result.stderr.strip() == "bad"

    asyncio.run(run())
