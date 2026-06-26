import pytest

from orchlink.broker import state as broker_state
from orchlink.core.models import Job, JobEvent, JobEventType, JobRoute, Session, advance_job
from orchlink.core.states import (
    JOB_STATUS_LIFECYCLE,
    JobStatus,
    can_transition,
    is_active_job_status,
    is_terminal_status,
    reply_job_status,
    require_transition,
)


def test_core_lifecycle_is_canonical_and_reused_by_broker_state():
    assert JOB_STATUS_LIFECYCLE == (
        "CREATED",
        "QUEUED",
        "DELIVERED",
        "RUNNING",
        "DONE",
        "FAILED",
        "TIMEOUT",
        "CANCELLED",
        "CLOSED",
    )
    assert broker_state.JOB_STATUS_LIFECYCLE == JOB_STATUS_LIFECYCLE


def test_core_transition_table_allows_expected_forward_paths_only():
    assert can_transition(JobStatus.CREATED, JobStatus.QUEUED) is True
    assert can_transition("queued", "delivered") is True
    assert can_transition("delivered", "running") is True
    assert can_transition("running", "done") is True
    assert can_transition("running", "cancelled") is True

    assert can_transition("DONE", "RUNNING") is False
    assert can_transition("CANCELLED", "QUEUED") is False
    with pytest.raises(ValueError, match="DONE -> RUNNING"):
        require_transition("DONE", "RUNNING")


def test_core_keeps_protocol_compatibility_classifiers():
    assert is_active_job_status("OPEN") is True
    assert is_active_job_status("IN_PROGRESS") is True
    assert is_terminal_status("COMPLETED") is True
    assert reply_job_status("RESULT", "COMPLETED") == "DONE"
    assert reply_job_status("RESULT", "TIMEOUT") == "FAILED"
    assert reply_job_status("CHAT_CLOSE", "DONE") == "CLOSED"


def test_job_model_validates_minimal_domain_invariants():
    task = Job(
        id="T001",
        kind="task",
        project_id="demo",
        task_id="T001",
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="PLAN",
        status="queued",
    )

    assert task.status == "QUEUED"

    with pytest.raises(ValueError, match="Task jobs require task_id"):
        Job(id="missing", kind="task", project_id="demo", route=task.route, mode="PLAN")

    with pytest.raises(ValueError, match="Unsupported canonical job status"):
        Job(id="open-talk", kind="talk", project_id="demo", conversation_id="C001", route=task.route, mode="TALK", status="OPEN")


def test_session_model_normalizes_and_validates_status():
    session = Session(lease_id="lease-1", project_id="demo", agent_id="demo.work", role="work", status="active")

    assert session.status == "ACTIVE"

    with pytest.raises(ValueError, match="Unsupported session status"):
        Session(lease_id="lease-2", project_id="demo", agent_id="demo.work", role="work", status="gone")


def test_job_event_derives_canonical_status_from_type():
    event = JobEvent(type=JobEventType.STARTED, project_id="demo", job_id="T001")

    assert event.type == JobEventType.STARTED
    assert event.status == "RUNNING"

    explicit = JobEvent(type="failed", project_id="demo", job_id="T001", status="failed")
    assert explicit.type == JobEventType.FAILED
    assert explicit.status == "FAILED"

    with pytest.raises(ValueError, match="status mismatch"):
        JobEvent(type="failed", project_id="demo", job_id="T001", status="timeout")

    with pytest.raises(ValueError, match="Unsupported job event type"):
        JobEvent(type="OPEN", project_id="demo", job_id="T001")


def test_advance_job_returns_new_job_for_valid_transition():
    job = Job(
        id="T001",
        kind="task",
        project_id="demo",
        task_id="T001",
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="PLAN",
        status="QUEUED",
    )

    delivered = advance_job(job, JobEvent(type=JobEventType.DELIVERED, project_id="demo", job_id="T001"))
    running = delivered.transition(JobEvent(type=JobEventType.STARTED, project_id="demo", job_id="T001"))

    assert job.status == "QUEUED"
    assert delivered.status == "DELIVERED"
    assert running.status == "RUNNING"
    assert running is not delivered


def test_advance_job_rejects_invalid_or_mismatched_events():
    job = Job(
        id="T001",
        kind="task",
        project_id="demo",
        task_id="T001",
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="PLAN",
        status="DONE",
    )

    with pytest.raises(ValueError, match="DONE -> RUNNING"):
        advance_job(job, JobEvent(type=JobEventType.STARTED, project_id="demo", job_id="T001"))

    with pytest.raises(ValueError, match="project mismatch"):
        advance_job(job, JobEvent(type=JobEventType.CANCELLED, project_id="other", job_id="T001"))

    with pytest.raises(ValueError, match="id mismatch"):
        advance_job(job, JobEvent(type=JobEventType.CANCELLED, project_id="demo", job_id="T002"))
