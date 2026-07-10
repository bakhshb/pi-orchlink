from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "orchlink"


def _text(path: str) -> str:
    return (SRC / path).read_text(encoding="utf-8")


def test_broker_routes_use_route_adapter_not_store_dependency() -> None:
    text = _text("broker/main.py")
    adapter = _text("broker/route_adapter.py")
    assert "Depends(get_store)" not in text
    assert "MessageStore = Depends" not in text
    assert "Depends(get_service)" not in text
    assert "Depends(get_adapter)" in text
    assert "BrokerRouteAdapter" in text
    assert "BrokerService" in adapter


def test_broker_routes_do_not_import_or_decode_checkpoints() -> None:
    for module in ("broker/main.py", "broker/route_adapter.py"):
        text = _text(module)
        assert "orchlink.broker.checkpoint" not in text
        assert "load_checkpoint" not in text
        assert "record_lease" not in text
        assert "_decode_lease_wire" not in text


def test_broker_routes_do_not_reach_into_store_private_state() -> None:
    text = _text("broker/main.py")
    for forbidden in ("store._state", "store_obj._state", "getattr(store, \"_state\"", "_state.events", "_state.sessions"):
        assert forbidden not in text


def test_broker_routes_declare_response_models() -> None:
    text = _text("broker/main.py")
    assert text.count("response_model=") >= 25
    assert "response_models" in text


def test_goal_runner_delegates_worker_io_prompt_and_criteria_work() -> None:
    text = _text("goal/runner.py")
    assert "GoalDispatcher" in text
    assert "GoalCriteriaEngine" in text
    assert "result[\"reply\"]" not in text
    assert "result['reply']" not in text
    assert "_worker_prompt" not in text
    assert "_audit_prompt" not in text


def test_goal_runner_remains_small_orchestration_module() -> None:
    lines = _text("goal/runner.py").splitlines()
    assert len(lines) < 300


def test_core_models_have_no_static_dependency_on_core_views() -> None:
    tree = ast.parse(_text("core/models.py"))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            assert node.module != "orchlink.core.views"
        if isinstance(node, ast.Import):
            assert all(alias.name != "orchlink.core.views" for alias in node.names)


def test_core_model_wire_compatibility_methods_delegate_to_views() -> None:
    text = _text("core/models.py")
    for class_name in (
        "JobLease",
        "TaskJobPayload",
        "TalkJobPayload",
        "TaskProjection",
        "TaskResult",
        "BrokerEvent",
        "ActivityRecord",
        "StoredMessage",
        "Conversation",
    ):
        class_index = text.index(f"class {class_name}")
        method_index = text.index("def to_wire_dict", class_index)
        next_class = text.find("\n@dataclass", method_index + 1)
        body = text[method_index : next_class if next_class != -1 else len(text)]
        assert "from orchlink.core.views import" in body
        assert "return " in body


def test_production_broker_and_goal_do_not_call_domain_to_wire_dict() -> None:
    allowed = {SRC / "core" / "models.py", SRC / "core" / "views.py"}
    offenders: list[str] = []
    for path in [*SRC.rglob("*.py")]:
        if path in allowed or "__pycache__" in path.parts:
            continue
        if ".to_wire_dict(" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_memory_storage_is_split_into_focused_components() -> None:
    memory = _text("broker/storage/memory.py")
    assert len(memory.splitlines()) < 900
    for component in (
        "memory_state.py",
        "memory_event_log.py",
        "memory_session_store.py",
        "memory_activity_store.py",
        "memory_job_projector.py",
        "memory_work_queue.py",
    ):
        assert (SRC / "broker" / "storage" / component).is_file()
    assert "class MemoryMessageStore" in memory
    assert "class MemorySessionStore" not in memory
    assert "class MemoryWorkQueue" not in memory


def test_goal_store_delegates_file_and_journal_boundaries() -> None:
    store = _text("goal/store.py")
    assert len(store.splitlines()) < 300
    assert "GoalFileStore" in store
    assert "GoalJournal" in store
    assert "import yaml" not in store
    assert "import json" not in store
    assert "import httpx" not in store


def test_pi_extension_generator_is_split_by_responsibility() -> None:
    facade = _text("connector/pi_extension.py")
    assert len(facade.splitlines()) < 80
    assert (SRC / "connector" / "pi_extension_worker.py").is_file()
    assert (SRC / "connector" / "pi_extension_ui.py").is_file()
    assert (SRC / "connector" / "pi_extension_writer.py").is_file()
    assert "ORCHLINK_PI_EXTENSION" in facade
    assert "ORCHLINK_PI_UI_EXTENSION" in facade


def test_no_orchlink_pi_compaction_hooks_or_visible_worker_stop_regression() -> None:
    worker = _text("connector/pi_extension_worker.py")
    ui = _text("connector/pi_extension_ui.py")
    assert "compaction" not in worker.lower()
    assert "compaction" not in ui.lower()
    assert "Visible worker terminals are not stopped from this panel." in ui


def test_loop_item_private_state_is_only_written_by_aggregate() -> None:
    allowed = SRC / "loop" / "domain" / "item.py"
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if path == allowed or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        loop_item_names = {"LoopItem"}
        references_loop_item = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {
                "orchlink.loop",
                "orchlink.loop.domain",
                "orchlink.loop.domain.item",
            }:
                for alias in node.names:
                    if alias.name == "LoopItem":
                        loop_item_names.add(alias.asname or alias.name)
                        references_loop_item = True
            if isinstance(node, ast.Name) and node.id in loop_item_names:
                references_loop_item = True
        if not references_loop_item:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in loop_item_names:
                    if any(keyword.arg == "_state" for keyword in node.keywords):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} constructs LoopItem with _state")
                if isinstance(node.func, ast.Name) and node.func.id == "replace":
                    if any(keyword.arg == "_state" for keyword in node.keywords):
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} replaces LoopItem _state")
                if isinstance(node.func, ast.Attribute) and node.func.attr == "__setattr__" and len(node.args) >= 2:
                    field = node.args[1]
                    if isinstance(field, ast.Constant) and field.value == "_state":
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} sets LoopItem _state")
            if isinstance(node, ast.Attribute) and node.attr == "_state" and isinstance(getattr(node, "ctx", None), ast.Store):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} assigns LoopItem _state")
    assert offenders == []


def test_loop_service_does_not_reconstruct_loop_item_attempts() -> None:
    path = SRC / "loop" / "services" / "loop_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Name):
            continue
        if node.func.id == "replace" and any(keyword.arg == "attempts" for keyword in node.keywords):
            offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} replaces attempts")
        if node.func.id == "LoopItem" and any(keyword.arg == "attempts" for keyword in node.keywords):
            offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} constructs LoopItem with attempts")
    assert offenders == []


def test_goal_lifecycle_fields_are_assigned_only_by_policy() -> None:
    """Only orchlink.goal.policy may mutate Goal lifecycle fields.

    Status, gates, active_task_id, evidence, blockers, deferred, and
    ac_status are owned by the Goal lifecycle policy. Store, runner, checks,
    criteria, CLI, and other contexts must call policy/store methods rather
    than assigning them directly.
    """
    lifecycle_fields = {"status", "ac_gate", "plan_gate", "active_task_id", "evidence", "blockers", "deferred", "ac_status"}
    policy_file = SRC / "goal" / "policy.py"
    offenders: list[str] = []

    for path in SRC.rglob("*.py"):
        if path == policy_file or "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        goal_names: set[str] = set()
        references_goal = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {
                "orchlink.goal",
                "orchlink.goal.models",
            }:
                for alias in node.names:
                    if alias.name == "Goal":
                        goal_names.add(alias.asname or alias.name)
                        references_goal = True
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "orchlink.goal.models":
                        goal_names.add("Goal")
                        references_goal = True
            if isinstance(node, ast.Name) and node.id in goal_names:
                references_goal = True
        if not references_goal:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in lifecycle_fields and isinstance(getattr(node, "ctx", None), ast.Store):
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} assigns Goal lifecycle field {node.attr}")
            if isinstance(node, ast.Subscript) and isinstance(getattr(node, "ctx", None), ast.Store):
                value = node.value
                if isinstance(value, ast.Attribute) and value.attr == "ac_status":
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} assigns Goal.ac_status")
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "append" and isinstance(func.value, ast.Attribute):
                    outer = func.value
                    if outer.attr in {"evidence", "blockers", "deferred"}:
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} appends to Goal.{outer.attr}")
                if isinstance(func, ast.Name) and func.id == "setattr" and len(node.args) >= 2:
                    field = node.args[1]
                    if isinstance(field, ast.Constant) and field.value in lifecycle_fields:
                        offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} setattr on Goal lifecycle field {field.value!r}")
    assert offenders == []


def test_loop_engine_is_async_and_creates_no_event_loop() -> None:
    engine = _text("loop/services/loop_engine.py")
    assert "async def tick" in engine
    assert "async def run" in engine
    assert "asyncio.run" not in engine
    assert "asyncio.new_event_loop" not in engine
    assert "_await_if_needed" not in engine
    assert "must be called from sync code" not in engine


def test_loop_cli_drives_async_engine_at_typer_edge() -> None:
    cli = _text("loop/cli.py")
    assert cli.count("asyncio.run(engine.run(") >= 2


def test_loop_application_services_do_not_import_adapters_or_cli() -> None:
    """Loop service modules depend only on ports, domain, and project config."""
    offenders: list[str] = []
    for path in (SRC / "loop" / "services").rglob("*.py"):
        if path.name == "__init__.py" or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for forbidden in (
            "from orchlink.loop.adapters",
            "import orchlink.loop.adapters",
            "from orchlink.loop.cli",
            "import orchlink.loop.cli",
        ):
            if forbidden in text:
                offenders.append(f"{path.relative_to(ROOT)} imports {forbidden}")
    assert offenders == []


def test_loop_cli_delegates_composition_to_runtime() -> None:
    cli = _text("loop/cli.py")
    for forbidden in (
        "class HttpLoopBrokerClient",
        "class HttpLoopWorkerGateway",
        "GoalEvidenceAdapter(",
        "from orchlink.loop.adapters.connectors import",
        "from orchlink.loop.adapters.worktree_evidence import",
    ):
        assert forbidden not in cli, f"cli.py still contains composition code: {forbidden}"
    assert "from orchlink.loop.runtime import" in cli


def test_loop_goal_evidence_does_not_use_object_or_getattr() -> None:
    """Goal evidence attachment uses the typed GoalEvidencePort, not duck typing."""
    loop_service = _text("loop/services/loop_service.py")
    assert "attach_evidence = getattr" not in loop_service
    assert "getattr(goal_service" not in loop_service
    assert "goal_service=object()" not in loop_service
    assert "goal_service: GoalEvidencePort" in loop_service


def test_worker_supervisor_uses_public_connector_operations_only() -> None:
    """The supervisor must reach PiConnector only through public operations;
    private ``_env``/``_release_session`` are compatibility wrappers for legacy
    callers and must not be invoked from the supervisor boundary."""
    tree = ast.parse(_text("worker/supervisor.py"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"_env", "_release_session"}:
            offenders.append(f"worker/supervisor.py:{node.lineno} references private connector op {node.attr!r}")
    assert offenders == []


def test_client_process_does_not_import_broker_main() -> None:
    """Broker metadata is sourced from core.broker_metadata; the client process
    helper must not import the FastAPI application module orchlink.broker.main."""
    tree = ast.parse(_text("client/process.py"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "orchlink.broker.main"
                assert not alias.name.startswith("orchlink.broker.main.")
        if isinstance(node, ast.ImportFrom):
            assert node.module != "orchlink.broker.main"
            assert not (node.module or "").startswith("orchlink.broker.main.")


def test_broker_metadata_is_single_source_for_version_and_capabilities() -> None:
    """core.broker_metadata owns the broker version/capabilities; broker.main
    only imports and re-exports them, never redefines them."""
    metadata = _text("core/broker_metadata.py")
    assert "BROKER_CAPABILITIES" in metadata
    assert "def broker_version" in metadata

    main_tree = ast.parse(_text("broker/main.py"))
    imported_from_metadata = False
    for node in ast.walk(main_tree):
        if isinstance(node, ast.ImportFrom) and node.module == "orchlink.core.broker_metadata":
            names = {alias.name for alias in node.names}
            if {"BROKER_CAPABILITIES", "BROKER_VERSION"}.issubset(names):
                imported_from_metadata = True
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"BROKER_CAPABILITIES", "BROKER_VERSION"}:
                    raise AssertionError(f"broker/main.py redefines broker metadata constant {target.id}")
    assert imported_from_metadata, "broker/main.py must import metadata from core.broker_metadata"
