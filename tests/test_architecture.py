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
