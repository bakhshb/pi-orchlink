import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentRunResult:
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


async def run_command(
    command_config: dict[str, Any],
    prompt: str,
    timeout_seconds: int,
) -> AgentRunResult:
    mode = command_config.get("mode", "command")
    if mode != "command":
        return AgentRunResult(
            stdout="",
            stderr=f"Unsupported command mode: {mode}",
            exit_code=None,
            timed_out=False,
        )

    argv = command_config.get("argv") or []
    if not argv:
        return AgentRunResult(
            stdout="",
            stderr="No command argv configured.",
            exit_code=None,
            timed_out=False,
        )

    process = await asyncio.create_subprocess_exec(
        *argv,
        prompt,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        process.kill()
        stdout_bytes, stderr_bytes = await process.communicate()
        return AgentRunResult(
            stdout=stdout_bytes.decode(errors="replace"),
            stderr=stderr_bytes.decode(errors="replace"),
            exit_code=None,
            timed_out=True,
        )

    return AgentRunResult(
        stdout=stdout_bytes.decode(errors="replace"),
        stderr=stderr_bytes.decode(errors="replace"),
        exit_code=process.returncode,
        timed_out=False,
    )
