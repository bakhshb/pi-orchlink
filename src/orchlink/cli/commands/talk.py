"""``orch talk``, ``orch say``, ``orch close`` — Talk Mode commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import httpx
import typer

from rich.console import Console

from orchlink.cli import main as _cli_main
from orchlink.cli.commands._helpers import conversation_state, load_project_or_exit
from orchlink.cli.message_input import resolve_message_option


console = Console()


def require_nonempty_talk_message(message: str, command_name: str) -> None:
    if message.strip():
        return
    console.print(f"[Orch] {command_name} message cannot be empty. Use -m, -m -, --message-file, or --edit.")
    raise typer.Exit(1)


def _print_conversation_body(conversation: dict[str, Any]) -> None:
    conversation_id = str(conversation.get("conversation_id") or "")
    console.print(f"[Orch] Conversation {conversation_id}: {conversation.get('status', 'UNKNOWN')}")
    console.print(f"[Orch] Turn: {conversation.get('turn', '?')}/{conversation.get('max_turns', '?')}")
    preview = str(conversation.get("last_message_preview") or conversation.get("preview") or "").strip()
    if preview:
        console.print(preview)
    if conversation.get("status") == "OPEN":
        console.print(f"[Orch] Continue: orch say {conversation_id} -m \"...\"")
        console.print(f"[Orch] Close: orch close {conversation_id} -m \"Decision: ...\"")


def register_talk(app: typer.Typer) -> None:
    """Register talk/say/close on the given Typer app."""

    @app.command(help="Start a visible Talk Mode discussion with work.")
    def talk(
        worker_id: str,
        message: Annotated[str, typer.Option("--msg", "--message", "-m", help="First Talk message to send. Use - to read from stdin.")] = "",
        message_file: Annotated[
            Path | None,
            typer.Option("--message-file", "-F", help="Read the Talk message from a UTF-8 file. Use - for stdin."),
        ] = None,
        edit: Annotated[
            bool,
            typer.Option("--edit", "-e", help="Open VISUAL/EDITOR to write the Talk message."),
        ] = False,
        rounds: Annotated[
            int,
            typer.Option("--rounds", "-r", min=1, max=6, help="Number of lead↔worker back-and-forth rounds."),
        ] = 6,
        timeout: Annotated[
            int,
            typer.Option("--timeout", help="Conversation turn timeout in seconds."),
        ] = 1800,
        thinking: Annotated[
            str | None,
            typer.Option("--thinking", help="Override worker thinking for this talk turn: off, minimal, low, medium, high, xhigh."),
        ] = None,
    ) -> None:
        from orchlink.cli.main import print_orch_exception  # late import

        config = load_project_or_exit()
        try:
            message = resolve_message_option(message, message_file, edit, config, "talk", worker_id, "talk", required=False)
            require_nonempty_talk_message(message, "Talk")
            _cli_main.ensure_broker_running(config)
            conversation_id = _cli_main.next_conversation_id(config)
            max_turns = rounds * 2
            _cli_main.start_talk_sync(
                config=config,
                worker=worker_id,
                conversation_id=conversation_id,
                message=message,
                max_turns=max_turns,
                timeout_seconds=timeout,
                wait=False,
                thinking=thinking,
            )
        except (RuntimeError, httpx.HTTPError, ValueError) as exc:
            print_orch_exception(exc)
            raise typer.Exit(1) from exc
        console.print(f"[Orch] Started conversation {conversation_id} with {worker_id}.")
        console.print(f"[Orch] Max rounds: {rounds} ({max_turns} turns)")
        console.print("[Orch] Reply will arrive as a [Orchlink] message in the lead Pi chat — no polling needed.")
        console.print("[Orch] This is turn 1, not a final answer. Continue with: orch say " + conversation_id + " -m \"...\"")
        console.print("[Orch] Close only when the discussion reaches a decision: orch close " + conversation_id + " -m \"...\"")

    @app.command(help="Send the next message in an open Talk Mode conversation.")
    def say(
        conversation_id: str,
        message: Annotated[str, typer.Option("--msg", "--message", "-m", help="Next Talk message to send. Use - to read from stdin.")] = "",
        message_file: Annotated[
            Path | None,
            typer.Option("--message-file", "-F", help="Read the Talk message from a UTF-8 file. Use - for stdin."),
        ] = None,
        edit: Annotated[
            bool,
            typer.Option("--edit", "-e", help="Open VISUAL/EDITOR to write the Talk message."),
        ] = False,
        timeout: Annotated[
            int,
            typer.Option("--timeout", help="Conversation turn timeout in seconds."),
        ] = 1800,
        thinking: Annotated[
            str | None,
            typer.Option("--thinking", help="Override worker thinking for this talk turn: off, minimal, low, medium, high, xhigh."),
        ] = None,
    ) -> None:
        import httpx as _httpx
        from orchlink.cli.main import print_orch_exception  # late import

        config = load_project_or_exit()
        try:
            message = resolve_message_option(message, message_file, edit, config, conversation_id, "work", "talk", required=False)
            require_nonempty_talk_message(message, "Say")
            _cli_main.ensure_broker_running(config)
            state = conversation_state(config, conversation_id)
            if state is None:
                console.print(f"[Orch] Conversation not found: {conversation_id}")
                raise typer.Exit(1)
            if state.get("status") != "OPEN":
                console.print(f"[Orch] Conversation {conversation_id} is {state.get('status')}.")
                raise typer.Exit(1)
            turn = int(state.get("turn") or 1) + 1
            max_turns = int(state.get("max_turns") or 6)
            if turn > max_turns:
                console.print(f"[Orch] Conversation {conversation_id} reached max turns ({max_turns}).")
                raise typer.Exit(1)
            worker = str(state.get("to_agent") or "work")
            _cli_main.say_talk_sync(
                config=config,
                worker=worker,
                conversation_id=conversation_id,
                message=message,
                turn=turn,
                max_turns=max_turns,
                timeout_seconds=timeout,
                thinking=thinking,
            )
        except (RuntimeError, _httpx.HTTPError, ValueError) as exc:
            print_orch_exception(exc)
            raise typer.Exit(1) from exc
        console.print(f"[Orch] Sent turn {turn}/{max_turns} to {worker} for {conversation_id}.")
        console.print("[Orch] Reply will arrive as a [Orchlink] message in the lead Pi chat — no polling needed.")
        console.print("[Orch] Continue with another orch say if the discussion is not resolved; close when there is a decision.")

    @app.command(help="Close a Talk Mode conversation with a decision or summary.")
    def close(
        conversation_id: str,
        message: Annotated[
            str,
            typer.Option("--msg", "--message", "-m", help="Optional final decision or summary. Use - to read from stdin."),
        ] = "",
        message_file: Annotated[
            Path | None,
            typer.Option("--message-file", "-F", help="Read the close message from a UTF-8 file. Use - for stdin."),
        ] = None,
        edit: Annotated[
            bool,
            typer.Option("--edit", "-e", help="Open VISUAL/EDITOR to write the close message."),
        ] = False,
        timeout: Annotated[
            int,
            typer.Option("--timeout", help="Close message timeout in seconds."),
        ] = 1800,
    ) -> None:
        import httpx as _httpx
        from orchlink.cli.main import print_orch_exception  # late import

        config = load_project_or_exit()
        try:
            message = resolve_message_option(message, message_file, edit, config, conversation_id, "work", "close", required=False)
            _cli_main.ensure_broker_running(config)
            state = conversation_state(config, conversation_id)
            if state is None:
                console.print(f"[Orch] Conversation not found: {conversation_id}")
                raise typer.Exit(1)
            turn = min(int(state.get("turn") or 1) + 1, int(state.get("max_turns") or 6))
            max_turns = int(state.get("max_turns") or 6)
            worker = str(state.get("to_agent") or "work")
            _cli_main.close_talk_sync(
                config=config,
                worker=worker,
                conversation_id=conversation_id,
                message=message,
                turn=turn,
                max_turns=max_turns,
                timeout_seconds=timeout,
            )
        except (RuntimeError, _httpx.HTTPError, ValueError) as exc:
            print_orch_exception(exc)
            raise typer.Exit(1) from exc
        console.print(f"[Orch] Closed conversation {conversation_id}.")
        if message:
            console.print(message)
