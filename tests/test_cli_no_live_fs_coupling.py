"""M5 guard: task/talk CLI must not depend on ``.orch`` live state.

Context
-------
v1 of the coordination-kernel hardening (G005) keeps Goal Mode
file-coupled (``.orch/goals/*``), but task/talk/session *live* state must
flow through broker HTTP APIs (``BrokerClient``), not direct filesystem
reads of ``.orch/goals/*`` or live ``.orch/run/*`` state.

This guard prevents regressions that would re-introduce
filesystem-as-coordination for task/talk state, which would block the v1.1
Goal Mode projection (M6). See ``docs/v1.1-roadmap.md`` and
``docs/coordination-kernel-implementation-plan.md`` (M5/M6).

Rules enforced
--------------
1. Task/talk CLI modules must not reference ``.orch/goals`` live-state
   paths at all.
2. Task/talk CLI modules may reference ``.orch/run`` only for the broker
   store-path default (plumbing). Any other ``.orch/run`` literal is a
   live-state smell.
3. Task/talk CLI modules must not import the Goal Mode live-state layer
   (``orchlink.goal.{store,runner,checks,models}``). Importing the goal
   Typer app (``orchlink.goal.cli``) for command registration is allowed.
4. Task/talk state must go through ``BrokerClient`` (positive guard).
5. The Goal Mode module retains file-based storage in v1 (scope clarity:
   the guard targets task/talk CLI only, not the goal module).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Task/talk/session CLI modules that must stay broker/API-driven.
TASK_TALK_CLI_MODULES = [
    ROOT / "src" / "orchlink" / "cli" / "main.py",
]

# Goal live-state layer imports forbidden in the task/talk CLI. Importing the
# goal Typer app (orchlink.goal.cli) for command registration is NOT in this
# set and is therefore allowed.
FORBIDDEN_GOAL_LIVE_STATE_IMPORTS = {
    "orchlink.goal.store",
    "orchlink.goal.runner",
    "orchlink.goal.checks",
    "orchlink.goal.models",
}

# .orch/run literals allowed in task/talk CLI only for broker store-path
# plumbing. Adding another .orch/run literal requires updating this allowlist
# deliberately -- that friction is the point of the guard.
ALLOWED_ORCH_RUN_LITERALS = {".orch/run/orchlink-journal.jsonl"}


def _string_literals(source: str) -> list[str]:
    """All string-literal values in source, including f-string parts."""
    tree = ast.parse(source)
    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            values.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            for part in node.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    values.append(part.value)
    return values


def _imported_modules(source: str) -> set[str]:
    """All module names imported (top-level and from-imports)."""
    tree = ast.parse(source)
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module)
    return mods


@pytest.mark.parametrize("module_path", TASK_TALK_CLI_MODULES, ids=lambda p: p.name)
def test_task_talk_cli_does_not_reference_goal_live_state_paths(module_path: Path) -> None:
    """Rule 1: no ``.orch/goals`` path literals in task/talk CLI."""
    source = module_path.read_text(encoding="utf-8")
    offenders = [s for s in _string_literals(source) if ".orch/goals" in s]
    assert not offenders, (
        f"{module_path.name} must not reference .orch/goals/* live state; "
        f"task/talk state must go through broker APIs. Found: {offenders}"
    )


@pytest.mark.parametrize("module_path", TASK_TALK_CLI_MODULES, ids=lambda p: p.name)
def test_task_talk_cli_orch_run_literals_are_plumbing_only(module_path: Path) -> None:
    """Rule 2: ``.orch/run`` literals allowed only as store-path plumbing."""
    source = module_path.read_text(encoding="utf-8")
    run_refs = [s for s in _string_literals(source) if ".orch/run" in s]
    bad = [s for s in run_refs if s not in ALLOWED_ORCH_RUN_LITERALS]
    assert not bad, (
        f"{module_path.name} may reference .orch/run/* only for the broker "
        f"store-path default. Unexpected .orch/run literals: {bad}"
    )


@pytest.mark.parametrize("module_path", TASK_TALK_CLI_MODULES, ids=lambda p: p.name)
def test_task_talk_cli_does_not_import_goal_live_state_layer(module_path: Path) -> None:
    """Rule 3: no imports of the Goal Mode live-state layer in task/talk CLI."""
    source = module_path.read_text(encoding="utf-8")
    bad = _imported_modules(source) & FORBIDDEN_GOAL_LIVE_STATE_IMPORTS
    assert not bad, (
        f"{module_path.name} must not import the Goal Mode live-state layer "
        f"({FORBIDDEN_GOAL_LIVE_STATE_IMPORTS}); goal state belongs in the goal "
        f"module. Forbidden imports found: {bad}"
    )


def test_task_talk_cli_uses_broker_client_for_state() -> None:
    """Rule 4 (positive): task/talk state flows through ``BrokerClient``."""
    main = (ROOT / "src" / "orchlink" / "cli" / "main.py").read_text(encoding="utf-8")
    sync_module = (ROOT / "src" / "orchlink" / "client" / "sync.py").read_text(encoding="utf-8")
    combined = main + "\n" + sync_module
    assert "BrokerClient" in combined, (
        "task/talk CLI must obtain live state via BrokerClient (broker HTTP API), "
        "not direct filesystem reads."
    )
    # The symbol is imported and actually constructed in the CLI or its client
    # helpers, not just mentioned. Import shape may be multi-line; only the
    # module path matters for this check.
    assert "from orchlink.client import" in combined
    assert "BrokerClient" in combined
    assert "BrokerClient(" in combined


def test_goal_module_keeps_file_based_storage_in_v1() -> None:
    """Rule 5 (scope clarity): Goal Mode stays file-coupled in v1.

    The guard above scopes only to the task/talk CLI. The goal module retains
    direct filesystem storage for goal live state in v1; projection to the
    broker journal is a v1.1 target (M6).
    """
    store = (ROOT / "src" / "orchlink" / "goal" / "store.py").read_text(encoding="utf-8")
    assert "orch_dir(config)" in store, (
        "goal/store.py should still derive its root from orch_dir(config) in v1; "
        "if this changed, the M5 guard scope assumption is broken."
    )
    assert '"goals"' in store, (
        "goal/store.py should still keep goal live state under the 'goals' dir in v1."
    )