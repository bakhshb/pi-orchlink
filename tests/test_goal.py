"""Tests for Orchlink Goal Mode MVP.

These tests pin the user-facing Goal Mode contract:

- a guarded ``orch goal`` Typer sub-app registered on the main CLI app;
- ``orch goal start "<title>" --prd|--plan|--text`` creates a goal directory
  ``.orch/goals/Gxxx/`` containing ``source.md``, ``acceptance.md``,
  ``plan.md``, ``goal.yaml`` and ``history.jsonl``;
- ``orch goal list`` / ``orch goal show <id>`` display goal state;
- ``orch goal approve <id> ac|plan`` flips a gate; approving both moves the
  goal to ``ready``;
- ``orch goal gate <id> approve|reject`` operates the combined gate;
- ``orch goal cancel <id>`` marks the goal ``cancelled``.

The tests never start a broker or a real worker session: ``start`` only writes
goal artifacts to disk, and gate transitions are verified by reading
``goal.yaml`` back.
"""

import json
from pathlib import Path

import yaml
from typer.testing import CliRunner

from orchlink.cli import main as cli_main
from orchlink.project.init import init_project


runner = CliRunner()


def _init_project(tmp_path: Path, monkeypatch) -> Path:
    """Initialize a project and chdir into it so .orch resolves to tmp_path."""
    init_project(tmp_path, project_id="demo")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _goals_dir(tmp_path: Path) -> Path:
    return tmp_path / ".orch" / "goals"


def _goal_dir(tmp_path: Path, goal_id: str) -> Path:
    return _goals_dir(tmp_path) / goal_id


def _load_goal(tmp_path: Path, goal_id: str) -> dict:
    return yaml.safe_load((_goal_dir(tmp_path, goal_id) / "goal.yaml").read_text(encoding="utf-8"))


def _history(tmp_path: Path, goal_id: str) -> list[dict]:
    lines = (_goal_dir(tmp_path, goal_id) / "history.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _required_artifacts(goal_dir: Path) -> dict[str, Path]:
    return {
        "source": goal_dir / "source.md",
        "acceptance": goal_dir / "acceptance.md",
        "plan": goal_dir / "plan.md",
        "goal": goal_dir / "goal.yaml",
        "history": goal_dir / "history.jsonl",
    }


def test_goal_start_from_prd_creates_goal_files(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    (tmp_path / "prd.md").write_text("# Export feature\n\nCSV and JSON export.\n", encoding="utf-8")

    result = runner.invoke(cli_main.app, ["goal", "start", "Implement export feature", "--prd", "prd.md"])

    assert result.exit_code == 0
    assert "G001" in result.output

    artifacts = _required_artifacts(_goal_dir(tmp_path, "G001"))
    for name, path in artifacts.items():
        assert path.is_file(), f"{name} artifact not created at {path}"

    assert "Export feature" in artifacts["source"].read_text(encoding="utf-8")
    goal = _load_goal(tmp_path, "G001")
    assert goal["id"] == "G001"
    assert goal["title"] == "Implement export feature"
    assert goal["source"] == "prd"
    assert goal["status"] == "draft"
    assert goal["ac_gate"] == "pending"
    assert goal["plan_gate"] == "pending"

    events = _history(tmp_path, "G001")
    assert events, "history.jsonl should record at least the creation event"
    assert any(e.get("type") == "created" for e in events)


def test_goal_start_from_plan_captures_plan_source(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    (tmp_path / "plan.md").write_text("# Plan\n1. add csv export\n2. add tests\n", encoding="utf-8")

    result = runner.invoke(cli_main.app, ["goal", "start", "Export via plan", "--plan", "plan.md"])

    assert result.exit_code == 0
    assert "G001" in result.output
    artifacts = _required_artifacts(_goal_dir(tmp_path, "G001"))
    assert "add csv export" in artifacts["source"].read_text(encoding="utf-8")
    goal = _load_goal(tmp_path, "G001")
    assert goal["source"] == "plan"


def test_goal_start_from_text_captures_inline_source(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_main.app, ["goal", "start", "Tiny goal", "--text", "Build CSV export with tests"])

    assert result.exit_code == 0
    assert "G001" in result.output
    artifacts = _required_artifacts(_goal_dir(tmp_path, "G001"))
    assert "Build CSV export with tests" in artifacts["source"].read_text(encoding="utf-8")
    goal = _load_goal(tmp_path, "G001")
    assert goal["source"] == "text"


def test_goal_start_requires_a_source(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_main.app, ["goal", "start", "Goal with no source"])

    assert result.exit_code == 1
    assert _goals_dir(tmp_path).exists() is False or not _goals_dir(tmp_path).exists()


def test_goal_start_rejects_missing_source_file(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_main.app, ["goal", "start", "Missing prd", "--prd", "does-not-exist.md"])

    assert result.exit_code == 1


def test_goal_ids_increment_across_starts(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    (tmp_path / "prd.md").write_text("body", encoding="utf-8")

    first = runner.invoke(cli_main.app, ["goal", "start", "First goal", "--prd", "prd.md"])
    second = runner.invoke(cli_main.app, ["goal", "start", "Second goal", "--text", "inline goal"])

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert "G001" in first.output
    assert "G002" in second.output
    assert _goal_dir(tmp_path, "G001").is_dir()
    assert _goal_dir(tmp_path, "G002").is_dir()


def test_goal_list_shows_goals_and_status(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    (tmp_path / "prd.md").write_text("body", encoding="utf-8")
    runner.invoke(cli_main.app, ["goal", "start", "First", "--prd", "prd.md"])
    runner.invoke(cli_main.app, ["goal", "start", "Second", "--text", "inline"])

    result = runner.invoke(cli_main.app, ["goal", "list"])

    assert result.exit_code == 0
    assert "G001" in result.output
    assert "G002" in result.output
    assert "draft" in result.output


def test_goal_show_displays_goal_state(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    (tmp_path / "prd.md").write_text("# Export\nCSV export.\n", encoding="utf-8")
    runner.invoke(cli_main.app, ["goal", "start", "Implement export", "--prd", "prd.md"])

    result = runner.invoke(cli_main.app, ["goal", "show", "G001"])

    assert result.exit_code == 0
    assert "G001" in result.output
    assert "draft" in result.output
    assert "prd" in result.output


def test_goal_show_unknown_id_errors(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_main.app, ["goal", "show", "G999"])

    assert result.exit_code == 1


def test_goal_approve_ac_then_plan_marks_ready(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    (tmp_path / "prd.md").write_text("body", encoding="utf-8")
    runner.invoke(cli_main.app, ["goal", "start", "Export", "--prd", "prd.md"])

    ac = runner.invoke(cli_main.app, ["goal", "approve", "G001", "ac"])
    assert ac.exit_code == 0
    goal = _load_goal(tmp_path, "G001")
    assert goal["ac_gate"] == "approved"
    assert goal["plan_gate"] == "pending"
    assert goal["status"] == "draft"

    plan = runner.invoke(cli_main.app, ["goal", "approve", "G001", "plan"])
    assert plan.exit_code == 0
    goal = _load_goal(tmp_path, "G001")
    assert goal["ac_gate"] == "approved"
    assert goal["plan_gate"] == "approved"
    assert goal["status"] == "ready"


def test_goal_approve_rejects_unknown_gate(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    (tmp_path / "prd.md").write_text("body", encoding="utf-8")
    runner.invoke(cli_main.app, ["goal", "start", "Export", "--prd", "prd.md"])

    result = runner.invoke(cli_main.app, ["goal", "approve", "G001", "bogus"])

    assert result.exit_code == 1


def test_goal_gate_approve_combined_marks_ready(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    (tmp_path / "prd.md").write_text("body", encoding="utf-8")
    runner.invoke(cli_main.app, ["goal", "start", "Export", "--prd", "prd.md"])

    result = runner.invoke(cli_main.app, ["goal", "gate", "G001", "approve"])

    assert result.exit_code == 0
    goal = _load_goal(tmp_path, "G001")
    assert goal["ac_gate"] == "approved"
    assert goal["plan_gate"] == "approved"
    assert goal["status"] == "ready"


def test_goal_gate_reject_records_note_and_gates(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    (tmp_path / "prd.md").write_text("body", encoding="utf-8")
    runner.invoke(cli_main.app, ["goal", "start", "Export", "--prd", "prd.md"])

    result = runner.invoke(cli_main.app, ["goal", "gate", "G001", "reject", "--note", "Plan misses AC-4"])

    assert result.exit_code == 0
    goal = _load_goal(tmp_path, "G001")
    assert goal["ac_gate"] == "rejected"
    assert goal["plan_gate"] == "rejected"
    events = _history(tmp_path, "G001")
    assert any("Plan misses AC-4" in json.dumps(e) for e in events), "reject note should be recorded in history"


def test_goal_cancel_marks_cancelled(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    (tmp_path / "prd.md").write_text("body", encoding="utf-8")
    runner.invoke(cli_main.app, ["goal", "start", "Export", "--prd", "prd.md"])

    result = runner.invoke(cli_main.app, ["goal", "cancel", "G001"])

    assert result.exit_code == 0
    goal = _load_goal(tmp_path, "G001")
    assert goal["status"] == "cancelled"
    events = _history(tmp_path, "G001")
    assert any(e.get("type") == "cancelled" for e in events)


def test_goal_cancel_unknown_id_errors(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)

    result = runner.invoke(cli_main.app, ["goal", "cancel", "G999"])

    assert result.exit_code == 1


def test_goal_group_is_registered_on_root_app():
    result = runner.invoke(cli_main.app, ["goal", "--help"])

    assert result.exit_code == 0
    assert "start" in result.output
    assert "list" in result.output
    assert "show" in result.output
    assert "approve" in result.output
    assert "cancel" in result.output
    assert "derive" in result.output
    assert "signoff" in result.output


def test_goal_start_exposes_derive_flag():
    result = runner.invoke(cli_main.app, ["goal", "start", "--help"])

    assert result.exit_code == 0
    assert "--derive" in result.output


def test_goal_work_exposes_until_flag():
    result = runner.invoke(cli_main.app, ["goal", "work", "--help"])

    assert result.exit_code == 0
    assert "--until" in result.output


def test_goal_signoff_command_is_registered():
    result = runner.invoke(cli_main.app, ["goal", "signoff", "--help"])

    assert result.exit_code == 0


def test_goal_help_does_not_expose_chat_plan_commands():
    result = runner.invoke(cli_main.app, ["goal", "--help"])

    assert result.exit_code == 0
    # Chat/context-plan capture was removed; the lead captures a plan into a normal
    # file and uses --plan. None of the special chat-plan surfaces should remain.
    assert "mark-plan" not in result.output
    assert "from-chat" not in result.output
    assert "plan-id" not in result.output
    # Canonical commands are still present.
    assert "start" in result.output
    assert "derive" in result.output
    assert "signoff" in result.output


def test_goal_start_help_does_not_expose_from_chat_or_plan_id():
    result = runner.invoke(cli_main.app, ["goal", "start", "--help"])

    assert result.exit_code == 0
    assert "--from-chat" not in result.output
    assert "--plan-id" not in result.output
    # --plan remains the canonical way to use a captured context plan file.
    assert "--plan" in result.output


def test_goal_start_plan_is_canonical_for_captured_context_plan(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    # The lead captures a context/chat plan into a normal plan file, then uses --plan.
    (tmp_path / "context-plan.md").write_text(
        "# Plan captured from context\n1. add csv export\n2. add tests\n", encoding="utf-8"
    )

    result = runner.invoke(cli_main.app, ["goal", "start", "Export from plan", "--plan", "context-plan.md"])

    assert result.exit_code == 0
    assert "G001" in result.output
    artifacts = _required_artifacts(_goal_dir(tmp_path, "G001"))
    assert "add csv export" in artifacts["source"].read_text(encoding="utf-8")
    goal = _load_goal(tmp_path, "G001")
    assert goal["source"] == "plan"


def test_goal_start_rejects_from_chat_and_plan_id_flags(tmp_path, monkeypatch):
    _init_project(tmp_path, monkeypatch)
    (tmp_path / "plan.md").write_text("plan body\n", encoding="utf-8")

    # --from-chat is no longer a recognized source flag.
    from_chat = runner.invoke(cli_main.app, ["goal", "start", "X", "--from-chat"])
    assert from_chat.exit_code != 0

    # --plan-id is no longer a recognized option.
    plan_id = runner.invoke(cli_main.app, ["goal", "start", "X", "--plan-id", "P001"])
    assert plan_id.exit_code != 0

    # mark-plan is no longer a registered subcommand.
    mark_plan = runner.invoke(cli_main.app, ["goal", "mark-plan", "P001", "--text", "body"])
    assert mark_plan.exit_code != 0


def test_goal_review_audit_trial_commands_are_registered():
    result = runner.invoke(cli_main.app, ["goal", "--help"])

    assert result.exit_code == 0
    assert "review" in result.output
    assert "audit" in result.output
    assert "trial" in result.output
    assert "trials" in result.output


def _write_review_artifacts(tmp_path: Path, *, with_coverage: bool = True) -> None:
    goal_dir = _goal_dir(tmp_path, "G001")
    (goal_dir / "source.md").write_text("# Export feature\nCSV and JSON export with filters.\n", encoding="utf-8")
    (goal_dir / "plan.md").write_text("# Plan\n1. csv export\n2. json export\n", encoding="utf-8")
    acceptance = (
        "# Acceptance\n\n```yaml\n"
        "acceptance:\n"
        "  - id: AC-1\n    text: CSV export works\n    type: objective\n    priority: core\n    depends_on: []\n    check: python3 check_ok.py\n    source: source.md\n    confidence: high\n    status: pending\n"
        "  - id: AC-2\n    text: JSON export works\n    type: objective\n    priority: core\n    depends_on: []\n    check: python3 check_json.py\n    source: source.md\n    confidence: high\n    status: pending\n"
        "  - id: AC-3\n    text: filters edge case\n    type: objective\n    priority: core\n    depends_on: []\n    check: \"\"\n    source: \"\"\n    confidence: low\n    status: pending\n"
        "  - id: AC-4\n    text: docs updated\n    type: objective\n    priority: noncore\n    depends_on: []\n    check: \"\"\n    source: \"\"\n    confidence: high\n    status: pending\n"
        "  - id: AC-5\n    text: nice animation\n    type: subjective\n    priority: noncore\n    depends_on: []\n    check: \"\"\n    source: \"\"\n    confidence: invented\n    status: pending\n"
        "```\n"
    )
    (goal_dir / "acceptance.md").write_text(acceptance, encoding="utf-8")
    if with_coverage:
        coverage = (
            "# Coverage\n\n```yaml\n"
            "coverage:\n"
            "  covered: [AC-1, AC-2]\n"
            "  uncovered: [AC-4]\n"
            "  low_confidence: [AC-3]\n"
            "  invented: [AC-5]\n"
            "```\n"
        )
        (goal_dir / "coverage.md").write_text(coverage, encoding="utf-8")


def _create_reviewable_goal(tmp_path: Path, monkeypatch) -> None:
    _init_project(tmp_path, monkeypatch)
    (tmp_path / "prd.md").write_text("# Export feature\n", encoding="utf-8")
    assert runner.invoke(cli_main.app, ["goal", "start", "Export", "--prd", "prd.md"]).exit_code == 0


def test_goal_review_prints_summary_acs_plan_coverage_and_warnings(tmp_path, monkeypatch):
    _create_reviewable_goal(tmp_path, monkeypatch)
    _write_review_artifacts(tmp_path, with_coverage=True)

    result = runner.invoke(cli_main.app, ["goal", "review", "G001"])

    assert result.exit_code == 0
    out = result.output
    assert "G001" in out
    assert "prd" in out  # source summary
    assert "AC-1" in out and "AC-5" in out  # acceptance criteria listed
    assert "Plan" in out or "plan" in out.lower()
    assert "Coverage" in out
    assert "Uncovered" in out and "AC-4" in out
    assert "low" in out.lower() and "AC-3" in out  # low-confidence warning
    assert "invented" in out.lower() and "AC-5" in out  # invented-AC warning


def test_goal_review_works_without_coverage_artifact(tmp_path, monkeypatch):
    _create_reviewable_goal(tmp_path, monkeypatch)
    _write_review_artifacts(tmp_path, with_coverage=False)

    result = runner.invoke(cli_main.app, ["goal", "review", "G001"])

    assert result.exit_code == 0
    assert "AC-1" in result.output
    # Without coverage.md, review still surfaces confidence from acceptance.md.
    assert "invented" in result.output.lower() and "AC-5" in result.output


def _trials_path(tmp_path: Path, goal_id: str = "G001") -> Path:
    return _goal_dir(tmp_path, goal_id) / "trials.jsonl"


def _trials(tmp_path: Path, goal_id: str = "G001") -> list[dict]:
    path = _trials_path(tmp_path, goal_id)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_goal_trial_command_records_trial_with_metrics(tmp_path, monkeypatch):
    _create_reviewable_goal(tmp_path, monkeypatch)

    result = runner.invoke(
        cli_main.app,
        [
            "goal", "trial", "G001",
            "--baseline", "12",
            "--outcome", "done",
            "--caught-gap", "AC-3",
            "--caught-gap", "AC-7",
            "--deferrals", "2",
            "--evidence-quality", "high",
        ],
    )

    assert result.exit_code == 0
    trials = _trials(tmp_path)
    assert len(trials) == 1
    trial = trials[0]
    assert trial["goal_id"] == "G001"
    assert trial["baseline_prompts"] == 12
    assert trial["outcome"] == "done"
    assert trial["caught_gaps"] == ["AC-3", "AC-7"]
    assert trial["deferrals"] == 2
    assert trial["evidence_quality"] == "high"


def test_goal_trials_command_lists_recorded_trials(tmp_path, monkeypatch):
    _create_reviewable_goal(tmp_path, monkeypatch)
    runner.invoke(
        cli_main.app,
        ["goal", "trial", "G001", "--baseline", "12", "--outcome", "done", "--caught-gap", "AC-3", "--evidence-quality", "high"],
    )
    runner.invoke(
        cli_main.app,
        ["goal", "trial", "G001", "--baseline", "20", "--outcome", "blocked", "--caught-gap", "AC-7", "--evidence-quality", "low"],
    )

    result = runner.invoke(cli_main.app, ["goal", "trials", "G001"])

    assert result.exit_code == 0
    assert "AC-3" in result.output
    assert "AC-7" in result.output
    assert "done" in result.output
    assert "blocked" in result.output
    assert len(_trials(tmp_path)) == 2