"""Tests for the expanded Goal Mode runner slice.

These tests pin the next-slice contract that ``src`` must satisfy:

- ``acceptance.md`` carries a ```yaml``` fenced block with an ``acceptance``
  list of AC maps. Each AC has: ``id``, ``text``, ``type`` (objective|
  subjective), ``priority`` (core|noncore), ``depends_on`` (list of AC ids),
  ``check`` (shell command, may be empty), ``status`` (pending|verified|failed|
  deferred).
- The runner selects only **unblocked core** ACs to verify each step, where
  unblocked means every AC in ``depends_on`` is already ``verified``.
- A failing **noncore** AC is recorded as ``deferred`` (in both the AC block
  status and ``goal.deferred``) and does **not** block the whole goal; the goal
  is ``done`` once all core ACs verify, even if noncore ACs remain deferred.
- A failing **core** AC is ``failed`` (not deferred) and drives the gap/cap
  loop.
- A worker reply with ``reply.type == "BLOCKER"`` marks the goal ``blocked``,
  records a ``worker_blocker`` history event carrying the blocker summary, and
  stops dispatching (one worker call) instead of looping blindly.
- ``orch goal show <id>`` prints an Evidence section and a Deferred section.

All tests stay broker-free by monkeypatching ``orchlink.goal.runner.ask_worker_sync``;
objective ``check`` commands run as real local subprocesses against temp check scripts.
"""

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from orchlink.cli import main as cli_main
from orchlink.project.init import init_project


runner = CliRunner()


def _init(tmp_path: Path, monkeypatch) -> None:
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)


def _goal(tmp_path: Path, goal_id: str = "G001") -> dict:
    return yaml.safe_load((tmp_path / ".orch" / "goals" / goal_id / "goal.yaml").read_text(encoding="utf-8"))


def _history(tmp_path: Path, goal_id: str = "G001") -> list[dict]:
    path = tmp_path / ".orch" / "goals" / goal_id / "history.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _acceptance_path(tmp_path: Path, goal_id: str = "G001") -> Path:
    return tmp_path / ".orch" / "goals" / goal_id / "acceptance.md"


def _ac(
    id: str,
    text: str,
    *,
    type: str = "objective",
    priority: str = "core",
    depends_on: list[str] | None = None,
    check: str = "",
    status: str = "pending",
) -> dict:
    return {
        "id": id,
        "text": text,
        "type": type,
        "priority": priority,
        "depends_on": depends_on or [],
        "check": check,
        "status": status,
    }


def _write_acceptance(tmp_path: Path, acs: list[dict], goal_id: str = "G001") -> None:
    body = yaml.safe_dump({"acceptance": acs}, sort_keys=False)
    _acceptance_path(tmp_path, goal_id).write_text(f"# Acceptance\n\n```yaml\n{body}```\n", encoding="utf-8")


def _acs(tmp_path: Path, goal_id: str = "G001") -> list[dict]:
    text = _acceptance_path(tmp_path, goal_id).read_text(encoding="utf-8")
    start = text.index("```yaml\n") + len("```yaml\n")
    end = text.index("\n```", start)
    return yaml.safe_load(text[start:end])["acceptance"]


def _ac_status(tmp_path: Path, ac_id: str, goal_id: str = "G001") -> str:
    for ac in _acs(tmp_path, goal_id):
        if ac["id"] == ac_id:
            return ac["status"]
    raise AssertionError(f"AC {ac_id} not found in acceptance.md")


def _write_check(tmp_path: Path, name: str, exit_code: int) -> None:
    (tmp_path / name).write_text(f"raise SystemExit({exit_code})\n", encoding="utf-8")


def _create_ready_goal(tmp_path: Path) -> None:
    (tmp_path / "prd.md").write_text("Build thing", encoding="utf-8")
    assert runner.invoke(cli_main.app, ["goal", "start", "Build", "--prd", "prd.md"]).exit_code == 0
    assert runner.invoke(cli_main.app, ["goal", "gate", "G001", "approve"]).exit_code == 0


def _result_reply(task_id: str, summary: str = "changed files") -> dict:
    return {"status": "DONE", "task_id": task_id, "reply": {"type": "RESULT", "payload": {"summary": summary}}}


def _blocker_reply(task_id: str, summary: str = "need decision on archived records") -> dict:
    return {"status": "DONE", "task_id": task_id, "reply": {"type": "BLOCKER", "payload": {"summary": summary}}}


def _typed_blocker_reply(task_id: str, *, btype: str = "decision", message: str = "Should archived records be included?") -> dict:
    return {
        "status": "DONE",
        "task_id": task_id,
        "reply": {
            "type": "BLOCKER",
            "payload": {"summary": "blocked", "blocker": {"type": btype, "message": message}},
        },
    }


_DERIVED_ACCEPTANCE = (
    "```yaml\n"
    "acceptance:\n"
    "  - id: AC-1\n"
    "    text: CSV export works\n"
    "    type: objective\n"
    "    priority: core\n"
    "    depends_on: []\n"
    "    check: python3 check_ok.py\n"
    "    status: pending\n"
    "```\n"
)
_DERIVED_PLAN = "# Plan\n1. implement CSV export\n2. add tests\n"


def _derive_reply(task_id: str) -> dict:
    return {
        "status": "DONE",
        "task_id": task_id,
        "reply": {
            "type": "RESULT",
            "payload": {"acceptance": _DERIVED_ACCEPTANCE, "plan": _DERIVED_PLAN, "summary": "derived ACs and plan"},
        },
    }


def _nested_derivation_reply(task_id: str) -> dict:
    summary = """```acceptance
# Acceptance criteria for G001: Build

```yaml
acceptance:
- id: AC-1
  text: CSV export works
  type: objective
  priority: core
  depends_on: []
  check: python3 check_ok.py
  status: pending
```
```

```plan
# Plan
1. implement CSV export
```

```coverage
# Coverage
- AC-1 covers source requirement
- Uncovered: none
```"""
    return {"status": "DONE", "task_id": task_id, "reply": {"type": "RESULT", "payload": {"summary": summary}}}


def test_goal_work_pauses_at_unapproved_gate(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    (tmp_path / "prd.md").write_text("Build thing", encoding="utf-8")
    runner.invoke(cli_main.app, ["goal", "start", "Build", "--prd", "prd.md"])

    result = runner.invoke(cli_main.app, ["goal", "work", "G001", "--max-steps", "1"])

    assert result.exit_code == 0
    assert "approve" in result.output.lower()
    assert _goal(tmp_path)["status"] == "draft"
    assert any(event.get("type") == "gate_required" for event in _history(tmp_path))


def test_goal_work_marks_done_when_single_core_ac_check_passes(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _create_ready_goal(tmp_path)
    _write_check(tmp_path, "check_ok.py", 0)
    _write_acceptance(tmp_path, [_ac("AC-1", "CSV export works", check="python3 check_ok.py")])

    seen = []

    def fake_ask_worker_sync(**kwargs):
        seen.append(kwargs["task_id"])
        assert "Do not claim the whole goal is done" in kwargs["message"]
        return _result_reply(kwargs["task_id"])

    monkeypatch.setattr("orchlink.goal.runner.ask_worker_sync", fake_ask_worker_sync)

    result = runner.invoke(cli_main.app, ["goal", "work", "G001", "--max-steps", "1"])

    assert result.exit_code == 0
    assert "done" in result.output.lower()
    goal = _goal(tmp_path)
    assert goal["status"] == "done"
    assert goal["active_task_id"] is None
    assert _ac_status(tmp_path, "AC-1") == "verified"
    assert any(event.get("type") == "verified_done" for event in _history(tmp_path))
    assert any(event.get("type") == "evidence" and event["evidence"]["passed"] for event in _history(tmp_path))


def test_goal_work_blocks_when_no_objective_checks_exist(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _create_ready_goal(tmp_path)

    monkeypatch.setattr(
        "orchlink.goal.runner.ask_worker_sync",
        lambda **kwargs: _result_reply(kwargs["task_id"]),
    )

    result = runner.invoke(cli_main.app, ["goal", "work", "G001", "--max-steps", "1"])

    assert result.exit_code == 0
    assert "manual verification" in result.output.lower()
    assert _goal(tmp_path)["status"] == "blocked"
    assert any(event.get("type") == "manual_verification_required" for event in _history(tmp_path))


def test_goal_work_core_check_failure_marks_failed_not_deferred(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _create_ready_goal(tmp_path)
    _write_check(tmp_path, "check_fail.py", 1)
    _write_acceptance(tmp_path, [_ac("AC-1", "CSV export works", check="python3 check_fail.py")])

    calls = []

    def fake_ask_worker_sync(**kwargs):
        calls.append(kwargs["task_id"])
        return _result_reply(kwargs["task_id"], summary="attempted")

    monkeypatch.setattr("orchlink.goal.runner.ask_worker_sync", fake_ask_worker_sync)

    result = runner.invoke(cli_main.app, ["goal", "work", "G001", "--max-steps", "1"])

    assert result.exit_code == 0
    assert "cap" in result.output.lower()
    assert calls == ["G001-WORK-001"]
    assert _goal(tmp_path)["status"] == "blocked"
    assert _ac_status(tmp_path, "AC-1") == "failed"
    assert _goal(tmp_path)["deferred"] == []
    assert any(event.get("type") == "gap_detected" for event in _history(tmp_path))
    assert any(event.get("type") == "cap_reached" for event in _history(tmp_path))


def test_goal_work_verifies_only_unblocked_core_acs_in_dependency_order(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _create_ready_goal(tmp_path)
    _write_check(tmp_path, "check_ok.py", 0)
    _write_acceptance(
        tmp_path,
        [
            _ac("AC-1", "CSV export", check="python3 check_ok.py"),
            _ac("AC-2", "JSON export", depends_on=["AC-1"], check="python3 check_ok.py"),
            _ac("AC-3", "filters", depends_on=["AC-1", "AC-2"], check="python3 check_ok.py"),
        ],
    )

    calls = []

    def fake_ask_worker_sync(**kwargs):
        calls.append(kwargs["task_id"])
        return _result_reply(kwargs["task_id"])

    monkeypatch.setattr("orchlink.goal.runner.ask_worker_sync", fake_ask_worker_sync)

    result = runner.invoke(cli_main.app, ["goal", "work", "G001", "--max-steps", "5"])

    assert result.exit_code == 0
    assert "done" in result.output.lower()
    assert _goal(tmp_path)["status"] == "done"
    # One worker dispatch per step; AC-2 and AC-3 are blocked until their deps verify.
    assert calls == ["G001-WORK-001", "G001-WORK-002", "G001-WORK-003"]
    assert _ac_status(tmp_path, "AC-1") == "verified"
    assert _ac_status(tmp_path, "AC-2") == "verified"
    assert _ac_status(tmp_path, "AC-3") == "verified"
    assert any(event.get("type") == "verified_done" for event in _history(tmp_path))


def test_goal_work_defers_noncore_and_does_not_block_goal(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _create_ready_goal(tmp_path)
    _write_check(tmp_path, "check_ok.py", 0)
    _write_check(tmp_path, "check_fail.py", 1)
    _write_acceptance(
        tmp_path,
        [
            _ac("AC-1", "CSV export works", check="python3 check_ok.py"),
            _ac("AC-2", "docs polished", priority="noncore", check="python3 check_fail.py"),
            _ac("AC-3", "UI copy", type="subjective", priority="noncore", check=""),
        ],
    )

    calls = []

    def fake_ask_worker_sync(**kwargs):
        calls.append(kwargs["task_id"])
        return _result_reply(kwargs["task_id"])

    monkeypatch.setattr("orchlink.goal.runner.ask_worker_sync", fake_ask_worker_sync)

    result = runner.invoke(cli_main.app, ["goal", "work", "G001", "--max-steps", "2"])

    assert result.exit_code == 0
    assert "done" in result.output.lower()
    # Core AC verified; noncore ACs deferred in a single step; goal not blocked.
    assert _goal(tmp_path)["status"] == "done"
    assert calls == ["G001-WORK-001"]
    assert _ac_status(tmp_path, "AC-1") == "verified"
    assert _ac_status(tmp_path, "AC-2") == "deferred"
    assert _ac_status(tmp_path, "AC-3") == "deferred"
    deferred = _goal(tmp_path)["deferred"]
    assert {d["id"] for d in deferred} == {"AC-2", "AC-3"}
    history = _history(tmp_path)
    assert any(event.get("type") == "deferred" for event in history)
    assert any(event.get("type") == "verified_done" for event in history)


def test_goal_work_worker_blocker_reply_stops_and_marks_blocked(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _create_ready_goal(tmp_path)
    _write_check(tmp_path, "check_ok.py", 0)
    _write_acceptance(tmp_path, [_ac("AC-1", "CSV export works", check="python3 check_ok.py")])

    calls = []

    def fake_ask_worker_sync(**kwargs):
        calls.append(kwargs["task_id"])
        return _blocker_reply(kwargs["task_id"], summary="need decision on archived records")

    monkeypatch.setattr("orchlink.goal.runner.ask_worker_sync", fake_ask_worker_sync)

    result = runner.invoke(cli_main.app, ["goal", "work", "G001", "--max-steps", "3"])

    assert result.exit_code == 0
    # The runner must not keep dispatching blindly after a BLOCKER reply.
    assert calls == ["G001-WORK-001"]
    assert _goal(tmp_path)["status"] == "blocked"
    assert _ac_status(tmp_path, "AC-1") == "pending"
    history = _history(tmp_path)
    blocker_events = [event for event in history if event.get("type") == "worker_blocker"]
    assert blocker_events, "expected a worker_blocker history event"
    assert "need decision on archived records" in json.dumps(blocker_events[0])


def test_goal_show_includes_evidence_and_deferred_summary(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _create_ready_goal(tmp_path)
    _write_check(tmp_path, "check_ok.py", 0)
    _write_check(tmp_path, "check_fail.py", 1)
    _write_acceptance(
        tmp_path,
        [
            _ac("AC-1", "CSV export works", check="python3 check_ok.py"),
            _ac("AC-2", "docs polished", priority="noncore", check="python3 check_fail.py"),
            _ac("AC-3", "UI copy", type="subjective", priority="noncore", check=""),
        ],
    )

    monkeypatch.setattr(
        "orchlink.goal.runner.ask_worker_sync",
        lambda **kwargs: _result_reply(kwargs["task_id"]),
    )
    runner.invoke(cli_main.app, ["goal", "work", "G001", "--max-steps", "2"])

    result = runner.invoke(cli_main.app, ["goal", "show", "G001"])

    assert result.exit_code == 0
    assert "G001" in result.output
    assert "Evidence" in result.output
    assert "AC-1" in result.output
    assert "Deferred" in result.output
    assert "AC-2" in result.output
    assert "AC-3" in result.output


def test_goal_derive_command_dispatches_worker_and_writes_artifacts(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    (tmp_path / "prd.md").write_text("Build CSV export", encoding="utf-8")
    assert runner.invoke(cli_main.app, ["goal", "start", "Build", "--prd", "prd.md"]).exit_code == 0

    calls = []

    def fake_ask_worker_sync(**kwargs):
        calls.append(kwargs["task_id"])
        assert "acceptance criteria" in kwargs["message"].lower()
        return _derive_reply(kwargs["task_id"])

    monkeypatch.setattr("orchlink.goal.runner.ask_worker_sync", fake_ask_worker_sync)

    result = runner.invoke(cli_main.app, ["goal", "derive", "G001"])

    assert result.exit_code == 0
    assert calls, "derive must dispatch a worker derivation task"
    acceptance = _acceptance_path(tmp_path).read_text(encoding="utf-8")
    plan = (tmp_path / ".orch" / "goals" / "G001" / "plan.md").read_text(encoding="utf-8")
    assert "AC-1" in acceptance
    assert "CSV export works" in acceptance
    assert "implement CSV export" in plan
    assert "Goal Mode has captured the source" not in acceptance
    assert any(event.get("type") == "derived" for event in _history(tmp_path))
    assert _goal(tmp_path)["status"] == "draft"


def test_goal_start_derive_flag_derives_after_create(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    calls = []

    def fake_ask_worker_sync(**kwargs):
        calls.append(kwargs["task_id"])
        return _derive_reply(kwargs["task_id"])

    monkeypatch.setattr("orchlink.goal.runner.ask_worker_sync", fake_ask_worker_sync)

    result = runner.invoke(cli_main.app, ["goal", "start", "Build", "--text", "Build CSV export", "--derive"])

    assert result.exit_code == 0
    assert "G001" in result.output
    assert len(calls) == 1, "--derive should dispatch exactly one derivation task after create"
    acceptance = _acceptance_path(tmp_path).read_text(encoding="utf-8")
    assert "AC-1" in acceptance
    assert "Goal Mode has captured the source" not in acceptance
    assert "implement CSV export" in (tmp_path / ".orch" / "goals" / "G001" / "plan.md").read_text(encoding="utf-8")


def test_goal_derive_parses_nested_acceptance_yaml_from_summary(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    (tmp_path / "prd.md").write_text("Build CSV export", encoding="utf-8")
    assert runner.invoke(cli_main.app, ["goal", "start", "Build", "--prd", "prd.md"]).exit_code == 0
    monkeypatch.setattr("orchlink.goal.runner.ask_worker_sync", lambda **kwargs: _nested_derivation_reply(kwargs["task_id"]))

    result = runner.invoke(cli_main.app, ["goal", "derive", "G001"])

    assert result.exit_code == 0
    goal_dir = tmp_path / ".orch" / "goals" / "G001"
    acceptance = (goal_dir / "acceptance.md").read_text(encoding="utf-8")
    assert "```yaml" in acceptance
    assert "AC-1" in acceptance
    assert "CSV export works" in acceptance
    assert _ac_status(tmp_path, "AC-1") == "pending"
    assert "implement CSV export" in (goal_dir / "plan.md").read_text(encoding="utf-8")
    assert "Uncovered: none" in (goal_dir / "coverage.md").read_text(encoding="utf-8")


def test_goal_work_until_done_flag_is_accepted_and_still_capped(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _create_ready_goal(tmp_path)
    _write_check(tmp_path, "check_fail.py", 1)
    _write_acceptance(tmp_path, [_ac("AC-1", "CSV export works", check="python3 check_fail.py")])

    calls = []
    monkeypatch.setattr(
        "orchlink.goal.runner.ask_worker_sync",
        lambda **kwargs: (calls.append(kwargs["task_id"]), _result_reply(kwargs["task_id"], "attempted"))[1],
    )

    result = runner.invoke(cli_main.app, ["goal", "work", "G001", "--until", "done", "--max-steps", "1"])

    assert result.exit_code == 0, "--until done must be accepted as a flag"
    assert "cap" in result.output.lower()
    assert _goal(tmp_path)["status"] == "blocked"
    assert any(event.get("type") == "cap_reached" for event in _history(tmp_path))


def test_goal_work_records_typed_blocker_in_state_and_history(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _create_ready_goal(tmp_path)
    _write_check(tmp_path, "check_ok.py", 0)
    _write_acceptance(tmp_path, [_ac("AC-1", "CSV export works", check="python3 check_ok.py")])

    calls = []
    monkeypatch.setattr(
        "orchlink.goal.runner.ask_worker_sync",
        lambda **kwargs: (calls.append(kwargs["task_id"]), _typed_blocker_reply(kwargs["task_id"], btype="decision"))[1],
    )

    result = runner.invoke(cli_main.app, ["goal", "work", "G001", "--max-steps", "3"])

    assert result.exit_code == 0
    assert calls == ["G001-WORK-001"]
    assert _goal(tmp_path)["status"] == "blocked"
    blockers = _goal(tmp_path)["blockers"]
    assert any(b.get("type") == "decision" and "archived records" in str(b.get("message", "")) for b in blockers), blockers
    history = _history(tmp_path)
    blocker_events = [event for event in history if event.get("type") == "worker_blocker"]
    assert blocker_events
    assert blocker_events[0].get("blocker_type") == "decision"


def test_goal_work_batches_core_subjective_for_signoff_after_objective_core_passes(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _create_ready_goal(tmp_path)
    _write_check(tmp_path, "check_ok.py", 0)
    _write_acceptance(
        tmp_path,
        [
            _ac("AC-1", "CSV export works", check="python3 check_ok.py"),
            _ac("AC-2", "export UX sign-off", type="subjective", priority="core", check=""),
            _ac("AC-3", "docs polished", priority="noncore", check="python3 check_ok.py"),
        ],
    )

    calls = []
    monkeypatch.setattr(
        "orchlink.goal.runner.ask_worker_sync",
        lambda **kwargs: (calls.append(kwargs["task_id"]), _result_reply(kwargs["task_id"]))[1],
    )

    result = runner.invoke(cli_main.app, ["goal", "work", "G001", "--max-steps", "3"])

    assert result.exit_code == 0
    assert _goal(tmp_path)["status"] == "gated"
    # Objective core work happens BEFORE gating: AC-1 is verified, not pending.
    assert _ac_status(tmp_path, "AC-1") == "verified"
    # The subjective core AC is batched for sign-off, not failed/deferred/blocked.
    assert _ac_status(tmp_path, "AC-2") == "pending"
    history = _history(tmp_path)
    signoff_events = [event for event in history if event.get("type") == "subjective_signoff_required"]
    assert signoff_events, "expected a subjective_signoff_required history event"
    assert "AC-2" in json.dumps(signoff_events[0])
    # Only objective work was dispatched; no maker task spun on the subjective AC.
    assert len(calls) == 1


def test_goal_signoff_command_approves_subjective_ac_and_completes_goal(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _create_ready_goal(tmp_path)
    _write_check(tmp_path, "check_ok.py", 0)
    _write_acceptance(
        tmp_path,
        [
            _ac("AC-1", "CSV export works", check="python3 check_ok.py"),
            _ac("AC-2", "export UX sign-off", type="subjective", priority="core", check=""),
        ],
    )
    monkeypatch.setattr(
        "orchlink.goal.runner.ask_worker_sync",
        lambda **kwargs: _result_reply(kwargs["task_id"]),
    )
    assert runner.invoke(cli_main.app, ["goal", "work", "G001", "--max-steps", "3"]).exit_code == 0
    assert _goal(tmp_path)["status"] == "gated"

    result = runner.invoke(cli_main.app, ["goal", "signoff", "G001", "AC-2"])

    assert result.exit_code == 0
    assert _ac_status(tmp_path, "AC-2") == "human-approved"
    assert _goal(tmp_path)["status"] == "done"
    assert any(event.get("type") == "subjective_approved" for event in _history(tmp_path))

def test_goal_signoff_all_is_atomic_when_dependency_unverified(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _create_ready_goal(tmp_path)
    _write_acceptance(
        tmp_path,
        [
            _ac("AC-1", "first subjective", type="subjective", priority="core", check=""),
            _ac("AC-2", "second subjective", type="subjective", priority="core", depends_on=["AC-3"], check=""),
            _ac("AC-3", "unverified objective", priority="core", check="python3 missing.py"),
        ],
    )

    result = runner.invoke(cli_main.app, ["goal", "signoff", "G001", "--all"])

    assert result.exit_code == 1
    assert _ac_status(tmp_path, "AC-1") == "pending"
    assert _ac_status(tmp_path, "AC-2") == "pending"
    assert not any(event.get("type") == "subjective_approved" for event in _history(tmp_path))


_DERIVED_COVERAGE = (
    "```yaml\n"
    "coverage:\n"
    "  covered: [AC-1]\n"
    "  uncovered: [AC-2]\n"
    "  low_confidence: []\n"
    "  invented: []\n"
    "```\n"
)


def _derive_reply_with_coverage(task_id: str) -> dict:
    return {
        "status": "DONE",
        "task_id": task_id,
        "reply": {
            "type": "RESULT",
            "payload": {
                "acceptance": _DERIVED_ACCEPTANCE,
                "plan": _DERIVED_PLAN,
                "coverage": _DERIVED_COVERAGE,
                "summary": "derived ACs, plan, and coverage",
            },
        },
    }


def test_goal_derive_writes_optional_coverage_artifact(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    (tmp_path / "prd.md").write_text("Build CSV export", encoding="utf-8")
    assert runner.invoke(cli_main.app, ["goal", "start", "Build", "--prd", "prd.md"]).exit_code == 0

    calls = []
    monkeypatch.setattr(
        "orchlink.goal.runner.ask_worker_sync",
        lambda **kwargs: (calls.append(kwargs["task_id"]), _derive_reply_with_coverage(kwargs["task_id"]))[1],
    )

    result = runner.invoke(cli_main.app, ["goal", "derive", "G001"])

    assert result.exit_code == 0
    assert calls, "derive must dispatch a worker task"
    goal_dir = tmp_path / ".orch" / "goals" / "G001"
    coverage = (goal_dir / "coverage.md").read_text(encoding="utf-8")
    assert "uncovered" in coverage
    assert "AC-2" in coverage
    assert "AC-1" in (goal_dir / "acceptance.md").read_text(encoding="utf-8")
    assert "implement CSV export" in (goal_dir / "plan.md").read_text(encoding="utf-8")
    assert any(event.get("type") == "derived" for event in _history(tmp_path))
    assert _goal(tmp_path)["status"] == "draft"


def _audit_reply(task_id: str) -> dict:
    return {
        "status": "DONE",
        "task_id": task_id,
        "reply": {
            "type": "RESULT",
            "payload": {
                "summary": "audited",
                "audit": "# Audit\n\n## Gaps\n- AC-3 not covered by plan\n- AC-4 has no check\n",
                "findings": [
                    {"ac": "AC-3", "severity": "gap", "note": "not covered by plan"},
                    {"ac": "AC-4", "severity": "missing", "note": "no check command"},
                ],
            },
        },
    }


def test_goal_audit_dispatches_worker_records_audit_and_does_not_mark_done(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _create_ready_goal(tmp_path)
    _write_acceptance(
        tmp_path,
        [
            _ac("AC-1", "CSV export works", check="python3 check_ok.py"),
            _ac("AC-2", "JSON export works", check="python3 check_ok.py"),
            _ac("AC-3", "filters edge case", check=""),
            _ac("AC-4", "docs updated", priority="noncore", check=""),
        ],
    )

    calls = []
    monkeypatch.setattr(
        "orchlink.goal.runner.ask_worker_sync",
        lambda **kwargs: (calls.append(kwargs["task_id"]), _audit_reply(kwargs["task_id"]))[1],
    )

    result = runner.invoke(cli_main.app, ["goal", "audit", "G001"])

    assert result.exit_code == 0
    assert len(calls) == 1, "audit must dispatch exactly one worker audit task"
    assert "audit" in calls[0].lower(), "audit task id should identify it as an audit task"
    goal_dir = tmp_path / ".orch" / "goals" / "G001"
    audit_md = (goal_dir / "audit.md").read_text(encoding="utf-8")
    assert "AC-3" in audit_md
    assert "not covered" in audit_md
    history = _history(tmp_path)
    assert any(event.get("type") == "audit" for event in history), "expected an audit history event"
    # Audit must not advance the goal to done.
    assert _goal(tmp_path)["status"] == "ready"


def test_goal_audit_preserves_done_status(tmp_path, monkeypatch):
    _init(tmp_path, monkeypatch)
    _create_ready_goal(tmp_path)
    _write_check(tmp_path, "check_ok.py", 0)
    _write_acceptance(tmp_path, [_ac("AC-1", "CSV export works", check="python3 check_ok.py")])
    monkeypatch.setattr("orchlink.goal.runner.ask_worker_sync", lambda **kwargs: _result_reply(kwargs["task_id"]))
    assert runner.invoke(cli_main.app, ["goal", "work", "G001", "--max-steps", "1"]).exit_code == 0
    assert _goal(tmp_path)["status"] == "done"

    calls = []
    monkeypatch.setattr(
        "orchlink.goal.runner.ask_worker_sync",
        lambda **kwargs: (calls.append(kwargs["task_id"]), _audit_reply(kwargs["task_id"]))[1],
    )

    result = runner.invoke(cli_main.app, ["goal", "audit", "G001"])

    assert result.exit_code == 0
    assert calls == ["G001-AUDIT-001"]
    assert _goal(tmp_path)["status"] == "done"
    assert _goal(tmp_path)["active_task_id"] is None
    assert (tmp_path / ".orch" / "goals" / "G001" / "audit.md").is_file()
