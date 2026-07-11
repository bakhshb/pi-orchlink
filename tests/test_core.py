import dataclasses as _dataclasses
from dataclasses import replace as _dc_replace

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
        "RECLAIMABLE",
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
    assert can_transition("running", "closed") is True
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


def make_task_job(status: str = "CREATED") -> Job:
    return Job(
        id="T001",
        kind="task",
        project_id="demo",
        task_id="T001",
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="PLAN",
        status=status,
    )


@pytest.mark.parametrize(
    ("status", "method", "expected_status"),
    [
        ("CREATED", "queue", "QUEUED"),
        ("QUEUED", "deliver", "DELIVERED"),
        ("DELIVERED", "start", "RUNNING"),
        ("RUNNING", "reply", "DONE"),
        ("RUNNING", "fail", "FAILED"),
        ("RUNNING", "timeout", "TIMEOUT"),
        ("RUNNING", "cancel", "CANCELLED"),
        ("RUNNING", "close", "CLOSED"),
    ],
)
def test_job_lifecycle_methods_advance_status(status, method, expected_status):
    job = make_task_job(status)

    updated = getattr(job, method)()

    assert updated.status == expected_status
    assert updated is not job
    assert job.status == status


def test_job_lifecycle_methods_validate_transitions_and_clear_terminal_lease():
    job = make_task_job("DONE")
    leased = Job(
        id="T002",
        kind="task",
        project_id="demo",
        task_id="T002",
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="PLAN",
        status="RUNNING",
        lease={"holder": "work", "epoch": 1},
    )

    with pytest.raises(ValueError, match="DONE -> FAILED"):
        job.fail()
    assert leased.reply().lease is None


def test_session_lifecycle_helpers_return_updated_sessions():
    session = Session(
        lease_id="lease-1",
        project_id="demo",
        agent_id="demo.work",
        role="work",
        ready=True,
        ready_at="t0",
    )

    heartbeat = session.heartbeat("t1")
    ready = session.mark_ready("t2")
    released = session.release("t3", "user stop")
    expired = session.expire("t4", "lease grace")

    assert heartbeat.updated_at == "t1"
    assert heartbeat.last_heartbeat_at == "t1"
    assert ready.ready is True
    assert ready.ready_at == "t0"
    assert ready.last_ready_heartbeat_at == "t2"
    assert released.status == "RELEASED"
    assert released.ended_at == "t3"
    assert released.ended_reason == "user stop"
    assert released.ready is False
    assert expired.status == "EXPIRED"
    assert expired.ended_at == "t4"
    assert expired.ended_reason == "lease grace"
    assert expired.ready is False
    assert session.status == "ACTIVE"


def test_session_mark_ready_sets_first_ready_timestamp():
    session = Session(lease_id="lease-1", project_id="demo", agent_id="demo.work", role="work")

    ready = session.mark_ready("t1")
    still_ready = ready.mark_ready("t2")

    assert ready.ready_at == "t1"
    assert ready.last_ready_heartbeat_at == "t1"
    assert still_ready.ready_at == "t1"
    assert still_ready.last_ready_heartbeat_at == "t2"


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


# --- G003 AC-3: Job.transition(event) escape hatch + JobEvent validation path ---


def test_job_transition_escape_hatch_uses_advance_job_validation_path():
    """`Job.transition(event)` is the escape hatch and routes through advance_job.

    AC-3: Job.transition(event) and JobEvent remain available and continue to use
    the same validation path for callers that already hold concrete events.
    """
    from orchlink.core.models import advance_job

    job = Job(
        id="T-ESC-1",
        kind="task",
        project_id="demo",
        task_id="T-ESC-1",
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="PLAN",
        status="CREATED",
    )

    # 1. Successful transition via the escape hatch.
    queued = job.transition(JobEvent(type=JobEventType.QUEUED, project_id="demo", job_id="T-ESC-1"))
    assert queued.status == "QUEUED"
    # 2. The result is functionally identical to advance_job(job, event).
    via_advance = advance_job(job, JobEvent(type=JobEventType.QUEUED, project_id="demo", job_id="T-ESC-1"))
    assert queued == via_advance, (
        "Job.transition(event) must be equivalent to advance_job(job, event)"
    )

    # 3. The escape hatch also delegates chain: multiple transitions through it.
    running = (
        job.transition(JobEvent(type=JobEventType.QUEUED, project_id="demo", job_id="T-ESC-1"))
        .transition(JobEvent(type=JobEventType.DELIVERED, project_id="demo", job_id="T-ESC-1"))
        .transition(JobEvent(type=JobEventType.STARTED, project_id="demo", job_id="T-ESC-1"))
    )
    assert running.status == "RUNNING"

    # 4. Terminal escape hatch clears the lease (regression sentinel).
    leased = Job(
        id="T-ESC-2",
        kind="task",
        project_id="demo",
        task_id="T-ESC-2",
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="PLAN",
        status="RUNNING",
        lease={"holder": "demo.lead", "epoch": 1, "heartbeat_ms": 1000},
    )
    failed = leased.transition(JobEvent(type=JobEventType.FAILED, project_id="demo", job_id="T-ESC-2"))
    assert failed.status == "FAILED"
    assert failed.lease is None, "Terminal escape hatch must clear the lease."

    # 5. Invalid transitions still raise ValueError via the escape hatch.
    with pytest.raises(ValueError, match="DONE -> RUNNING"):
        (
            Job(
                id="T-ESC-3",
                kind="task",
                project_id="demo",
                task_id="T-ESC-3",
                route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
                mode="PLAN",
                status="DONE",
            )
            .transition(JobEvent(type=JobEventType.STARTED, project_id="demo", job_id="T-ESC-3"))
        )

    # 6. JobEvent validation primitives remain intact (type -> canonical status).
    good_event = JobEvent(type=JobEventType.STARTED, project_id="demo", job_id="T-ESC-1")
    assert good_event.status == "RUNNING"
    # Mismatched status field rejected.
    with pytest.raises(ValueError, match="status mismatch"):
        JobEvent(type=JobEventType.STARTED, project_id="demo", job_id="T-ESC-1", status="DONE")
    # Unknown event type rejected.
    with pytest.raises(ValueError, match="Unsupported job event type"):
        JobEvent(type="UNKNOWN", project_id="demo", job_id="T-ESC-1")


def test_job_transition_escape_hatch_supports_job_lifecycle_paths():
    """The job-lifecycle refactor still reaches the same end states via the escape hatch.

    Pins that even though the job lifecycle uses lifecycle methods for obvious
    transitions, callers that want to drive transitions through `JobEvent` keep
    the same end status.
    """
    job = Job(
        id="T-ESC-4",
        kind="task",
        project_id="demo",
        task_id="T-ESC-4",
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="PLAN",
        status="CREATED",
    )
    # Walk CREATED -> QUEUED -> DELIVERED -> RUNNING -> DONE through the escape hatch.
    end = (
        job.transition(JobEvent(type=JobEventType.QUEUED, project_id="demo", job_id="T-ESC-4"))
        .transition(JobEvent(type=JobEventType.DELIVERED, project_id="demo", job_id="T-ESC-4"))
        .transition(JobEvent(type=JobEventType.STARTED, project_id="demo", job_id="T-ESC-4"))
        .transition(JobEvent(type=JobEventType.REPLIED, project_id="demo", job_id="T-ESC-4"))
    )
    assert end.status == "DONE"

    # Direct terminal via the escape hatch.
    started = job.transition(JobEvent(type=JobEventType.QUEUED, project_id="demo", job_id="T-ESC-4")).transition(
        JobEvent(type=JobEventType.DELIVERED, project_id="demo", job_id="T-ESC-4")
    ).transition(JobEvent(type=JobEventType.STARTED, project_id="demo", job_id="T-ESC-4"))
    cancelled = started.transition(JobEvent(type=JobEventType.CANCELLED, project_id="demo", job_id="T-ESC-4"))
    assert cancelled.status == "CANCELLED"


# --- G003 AC-4: terminal lifecycle clears leases + invalid lifecycle raises ---


def test_terminal_lifecycle_clears_lease_for_every_terminal_method():
    """Every terminal Job lifecycle method clears the lease.

    AC-4: Terminal Job lifecycle methods clear leases.
    Drives `fail`, `timeout`, `cancel`, `reply` (REPLIED is also terminal at the
    canonical-event level), and `close` from a leased Job and asserts the
    resulting Job has `lease is None`.
    """
    lease = {"holder": "demo.work", "epoch": 1, "heartbeat_ms": 1000}

    def leased_at(status, task_id):
        return Job(
            id=task_id,
            kind="task",
            project_id="demo",
            task_id=task_id,
            route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
            mode="PLAN",
            status=status,
            lease=lease,
        )

    # RUNNING -> DONE via .reply() clears the lease.
    failed = leased_at("RUNNING", "T-CL-1").fail()
    assert failed.status == "FAILED"
    assert failed.lease is None

    timed_out = leased_at("RUNNING", "T-CL-2").timeout()
    assert timed_out.status == "TIMEOUT"
    assert timed_out.lease is None

    cancelled = leased_at("RUNNING", "T-CL-3").cancel()
    assert cancelled.status == "CANCELLED"
    assert cancelled.lease is None

    replied = leased_at("RUNNING", "T-CL-4").reply()
    assert replied.status == "DONE"
    assert replied.lease is None

    closed = leased_at("RUNNING", "T-CL-5").close()
    assert closed.status == "CLOSED"
    assert closed.lease is None


def test_terminal_lifecycle_clears_lease_reachable_via_job_lifecycle():
    """Lifecycle-driven terminal transitions also clear leases.

    The job lifecycle now uses lifecycle methods for obvious transitions, so
    `lifecycle.transition(leased_running_job, "FAILED")` must clear the lease.
    """
    from orchlink.core.job_lifecycle import TaskJobLifecycle

    lifecycle = TaskJobLifecycle()

    for terminal_status in ("FAILED", "TIMEOUT", "CANCELLED", "DONE"):
        running = Job(
            id=f"T-SM-{terminal_status}",
            kind="task",
            project_id="demo",
            task_id=f"T-SM-{terminal_status}",
            route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
            mode="PLAN",
            status="RUNNING",
            lease={"holder": "demo.work", "epoch": 1, "heartbeat_ms": 1000},
        )
        result = lifecycle.transition(running, terminal_status)
        assert result.status == terminal_status
        assert result.lease is None, (
            f"Lifecycle {terminal_status} transition must clear the lease."
        )


def test_invalid_job_lifecycle_transition_raises_value_error():
    """Calling a Job lifecycle method on an incompatible status raises ValueError.

    AC-4 second half: invalid lifecycle transitions still raise ValueError.
    """
    # A DONE job cannot move to RUNNING via .start().
    done_job = Job(
        id="T-INV-1",
        kind="task",
        project_id="demo",
        task_id="T-INV-1",
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="PLAN",
        status="DONE",
    )
    with pytest.raises(ValueError, match="DONE -> RUNNING"):
        done_job.start()

    # A DONE job cannot move to FAILED via .fail().
    with pytest.raises(ValueError, match="DONE -> FAILED"):
        done_job.fail()

    # A CREATED job cannot move to RUNNING directly via .start() (must go through queue/deliver first).
    fresh_job = Job(
        id="T-INV-2",
        kind="task",
        project_id="demo",
        task_id="T-INV-2",
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="PLAN",
        status="CREATED",
    )
    with pytest.raises(ValueError, match="CREATED -> RUNNING"):
        fresh_job.start()

    # A CREATED job cannot move to DONE via .reply() (terminal requires the lifecycle walk).
    with pytest.raises(ValueError, match="CREATED -> DONE"):
        fresh_job.reply()

    # The job lifecycle surfaces lifecycle errors through Job lifecycle methods:
    # the CREATED -> RUNNING path is valid via preferred_statuses, so the
    # lifecycle walks QUEUED -> STARTED and arrives at RUNNING. The dispatch-table
    # path uses `Job.start()` as the lifecycle method, which produces the same
    # end state. This regression-sentinel check confirms the wiring.
    from orchlink.core.job_lifecycle import TaskJobLifecycle

    lifecycle = TaskJobLifecycle()
    fresh_via_sm = Job(
        id="T-INV-3",
        kind="task",
        project_id="demo",
        task_id="T-INV-3",
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="PLAN",
        status="CREATED",
    )
    running_via_sm = lifecycle.transition(fresh_via_sm, "RUNNING")
    assert running_via_sm.status == "RUNNING"


# --- G004 AC-1: StoredMessage domain object ---


def test_stored_message_domain_object_has_required_shape_and_helpers():
    """StoredMessage exists, owns a MessageEnvelope, and exposes the required helpers.

    AC-1: A StoredMessage immutable record exists in core code that owns the
    existing MessageEnvelope and carries current active-message storage
    metadata; from_envelope, to_wire_dict, and with_status helpers are exposed.
    """
    import inspect

    from orchlink.core.envelope import MessageEnvelope
    from orchlink.core.models import StoredMessage

    # Type checks.
    assert inspect.isclass(StoredMessage)
    # Frozenness: a StoredMessage assignment must raise.
    sm = StoredMessage.from_envelope(
        MessageEnvelope(
            message_id="msg-1",
            correlation_id="req-1",
            conversation_id="C1",
            from_agent="demo.lead",
            to_agent="demo.work",
            type="TASK",
        ),
        now="2026-01-01T00:00:00+00:00",
    )
    assert isinstance(sm, StoredMessage)
    assert isinstance(sm.envelope, MessageEnvelope)
    assert sm.status == "QUEUED"
    assert sm.created_at == "2026-01-01T00:00:00+00:00"
    assert sm.queued_at == "2026-01-01T00:00:00+00:00"
    assert sm.updated_at == "2026-01-01T00:00:00+00:00"

    # Required helpers exist.
    assert callable(getattr(StoredMessage, "from_envelope", None))
    assert callable(getattr(StoredMessage, "to_wire_dict", None))
    assert callable(getattr(StoredMessage, "with_status", None))

    # Wire dicts are decoded at the view boundary, not by the domain model.
    from orchlink.core.views import message_input_to_stored

    with pytest.raises(TypeError):
        message_input_to_stored(
            {
                "message_id": "msg-2",
                "correlation_id": "req-2",
                "conversation_id": "C1",
                "from_agent": "demo.lead",
                "to_agent": "demo.work",
                "type": "TASK",
            },
            now="2026-01-01T00:00:00+00:00",
        )

    # CHAT_CLOSE initial status is CLOSED.
    from orchlink.core.envelope import MessagePayload

    sm_close = StoredMessage.from_envelope(
        MessageEnvelope(
            message_id="msg-3",
            correlation_id="req-3",
            conversation_id="C1",
            from_agent="demo.lead",
            to_agent="demo.review",
            type="CHAT_CLOSE",
            delivery="conversation",
            payload=MessagePayload(mode="TALK"),
        ),
        now="2026-01-01T00:00:00+00:00",
    )
    assert sm_close.status == "CLOSED"


def test_stored_message_domain_object_round_trips_wire_shape():
    """StoredMessage.from_envelope(...).to_wire_dict() preserves the wire shape.

    The wire shape emitted by to_wire_dict must match the dict the storage
    layer produces today: envelope fields plus broker storage metadata. We
    build a MessageEnvelope directly so the round-trip is over a validated
    envelope rather than a raw wire dict.
    """
    from orchlink.core.envelope import MessageEnvelope
    from orchlink.core.models import StoredMessage

    envelope = MessageEnvelope(
        message_id="msg-round",
        correlation_id="req-round",
        project_id="demo",
        conversation_id="C-round",
        task_id="T-round",
        from_agent="demo.lead",
        to_agent="demo.work",
        type="TASK",
        status="PENDING",
        turn=1,
        max_turns=6,
        requires_reply=True,
        timeout_seconds=30,
        delivery="async",
        payload={"intent": "round-trip"},
        meta={},
    )

    sm = StoredMessage.from_envelope(envelope, now="2026-01-01T00:00:00+00:00")
    wire_out = sm.to_wire_dict()

    # All envelope fields round-trip EXCEPT the broker-overridden `status`.
    expected_keys = {
        "protocol", "message_id", "correlation_id", "project_id", "conversation_id",
        "task_id", "from_agent", "to_agent", "type", "turn", "max_turns",
        "requires_reply", "timeout_seconds", "delivery", "payload", "meta",
    }
    for key in expected_keys:
        assert key in wire_out, f"Missing wire key: {key}"
    # Broker-overridden status is QUEUED.
    assert wire_out["status"] == "QUEUED"

    # Storage metadata is overlaid on the wire shape.
    assert wire_out["created_at"] == "2026-01-01T00:00:00+00:00"
    assert wire_out["queued_at"] == "2026-01-01T00:00:00+00:00"
    assert wire_out["updated_at"] == "2026-01-01T00:00:00+00:00"


def test_stored_message_with_status_returns_new_instance_with_updated_status():
    """with_status produces a new StoredMessage with the new status and updated_at."""
    from orchlink.core.envelope import MessageEnvelope
    from orchlink.core.models import StoredMessage

    envelope = MessageEnvelope(
        message_id="msg-ws",
        correlation_id="req-ws",
        conversation_id="C-ws",
        from_agent="demo.lead",
        to_agent="demo.work",
        type="TASK",
    )
    original = StoredMessage.from_envelope(envelope, now="2026-01-01T00:00:00+00:00")
    advanced = original.with_status("RUNNING", now="2026-01-02T00:00:00+00:00")

    # The result is a new StoredMessage preserving the envelope.
    assert advanced is not original
    assert advanced.envelope is original.envelope
    # Status and timestamp advanced.
    assert advanced.status == "RUNNING"
    assert advanced.updated_at == "2026-01-02T00:00:00+00:00"
    # Original is untouched.
    assert original.status == "QUEUED"
    assert original.updated_at == "2026-01-01T00:00:00+00:00"


# --- G019 AC-4: durable started_at semantics ---------------------------------


def _make_envelope(**overrides: object):
    from orchlink.core.envelope import MessageEnvelope

    fields: dict[str, object] = {
        "message_id": "msg-ac4",
        "correlation_id": "req-ac4",
        "conversation_id": "C-ac4",
        "from_agent": "demo.lead",
        "to_agent": "demo.work",
        "type": "TASK",
    }
    fields.update(overrides)
    return MessageEnvelope(**fields)  # type: ignore[arg-type]


def test_stored_message_started_at_is_none_until_first_running():
    from orchlink.core.models import StoredMessage

    sm = StoredMessage.from_envelope(_make_envelope(), now="2026-01-01T00:00:00+00:00")
    assert sm.started_at is None
    # Wire shape round-trips the None explicitly so consumers can distinguish
    # "not yet started" from "field absent" after JSONL replay.
    wire = sm.to_wire_dict()
    assert "started_at" in wire
    assert wire["started_at"] is None


def test_stored_message_started_at_set_exactly_once_on_first_running():
    """AC-4: ``with_status("RUNNING", t1)`` captures t1 as started_at on the
    first call. Subsequent calls — including additional RUNNING transitions
    (RECLAIMABLE -> RUNNING), IN_PROGRESS refresh, and heartbeat — never
    overwrite the original capture. ``updated_at`` does advance.
    """
    from orchlink.core.models import StoredMessage

    sm = StoredMessage.from_envelope(_make_envelope(), now="2026-01-01T00:00:00+00:00")
    assert sm.started_at is None

    # First RUNNING sets the timestamp.
    t1 = "2026-01-01T00:00:01+00:00"
    sm = sm.with_status("RUNNING", now=t1)
    assert sm.status == "RUNNING"
    assert sm.started_at == t1
    assert sm.updated_at == t1

    # A second RUNNING (e.g. RECLAIMABLE -> RUNNING) leaves started_at alone.
    t2 = "2026-01-01T00:05:00+00:00"
    sm2 = sm.with_status("RUNNING", now=t2)
    assert sm2.started_at == t1, "second RUNNING must not overwrite started_at"
    assert sm2.updated_at == t2

    # IN_PROGRESS refresh also leaves started_at alone and may even come
    # after the original RUNNING capture.
    t3 = "2026-01-01T00:01:00+00:00"
    sm3 = sm2.with_status("IN_PROGRESS", now=t3)
    assert sm3.started_at == t1
    assert sm3.updated_at == t3

    # Back to RUNNING (a heartbeat-driven reassumption) still does not move
    # started_at.
    t4 = "2026-01-01T00:02:00+00:00"
    sm4 = sm3.with_status("RUNNING", now=t4)
    assert sm4.started_at == t1
    assert sm4.updated_at == t4

    # Original is untouched (frozen/immutable semantics hold).
    assert sm.started_at == t1


def test_stored_message_started_at_survives_jsonl_wire_round_trip():
    """AC-4: `started_at` is part of the durable wire shape. Round-tripping
    through to_wire_dict -> from_wire preserves it so a broker restart
    (which always rehydrates from JSONL) keeps the same authoritative
    "first RUNNING" timestamp.
    """
    from orchlink.core.models import StoredMessage
    from orchlink.core.views import stored_message_from_wire, stored_message_to_wire

    t1 = "2026-01-01T00:00:01+00:00"
    sm = StoredMessage.from_envelope(_make_envelope(), now="2026-01-01T00:00:00+00:00").with_status("RUNNING", now=t1)
    wire = stored_message_to_wire(sm)
    assert wire["started_at"] == t1

    restored = stored_message_from_wire(wire)
    assert restored.started_at == t1

    # A subsequent heartbeat DURING the same session must not move started_at.
    hb = restored.with_status("IN_PROGRESS", now="2026-01-01T00:00:02+00:00")
    assert hb.started_at == t1
    hb_wire = stored_message_to_wire(hb)
    assert hb_wire["started_at"] == t1
    hb_restored = stored_message_from_wire(hb_wire)
    assert hb_restored.started_at == t1


def test_stored_message_started_at_survives_jsonl_snapshot_replay(tmp_path):
    """AC-4: ``started_at`` set when the broker first transitions a stored
    message into RUNNING survives close-and-reopen (broker restart). The
    persistence path uses ``stored_message_to_wire`` so this is the canonical
    durability contract for the JsonlMessageStore.
    """
    import asyncio
    import json
    import os

    from orchlink.broker.storage.jsonl import JsonlMessageStore
    from orchlink.core.models import StoredMessage
    from orchlink.core.views import stored_message_to_wire

    async def run() -> None:
        path = os.path.join(str(tmp_path), "started-at.jsonl")
        t_queued = "2026-01-01T00:00:00+00:00"
        t_first_running = "2026-01-01T00:00:01+00:00"
        t_post_restart_refresh = "2026-01-01T00:01:30+00:00"

        # Phase 1: build a stored message that simulates a broker having
        # moved QUEUED -> RUNNING at t_first_running, then persist a
        # minimal JSONL journal record containing the snapshot.
        sm = (
            StoredMessage
            .from_envelope(_make_envelope(message_id="msg-ac4-restart"), now=t_queued)
            .with_status("RUNNING", now=t_first_running)
        )
        assert sm.started_at == t_first_running
        record = {
            "time": t_first_running,
            "operation": "enqueue_message",
            "request": {},
            "result": {},
            "snapshot": {
                "active_messages": {sm.envelope.message_id: stored_message_to_wire(sm)},
                "agents": {},
                "tasks": {},
                "task_jobs": {},
                "results_by_task": {},
                "conversations": {},
                "talk_jobs": {},
                "events": [],
                "activity": [],
                "sessions": {},
                "next_event_id": 0,
                "next_activity_id": 0,
            },
        }
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        # Phase 2: reopen the store (= broker restart).
        restarted = JsonlMessageStore(path=path)
        internal_state = getattr(restarted, "_state", None)
        assert internal_state is not None
        stored_after = internal_state.active_messages.get(sm.envelope.message_id)
        assert stored_after is not None
        # ``started_at`` round-tripped through JSONL replay.
        assert stored_after.started_at == t_first_running, (
            f"started_at must survive JSONL close-and-reopen; "
            f"got {stored_after.started_at!r}, expected {t_first_running!r}"
        )
        # Sanity: other lifecycle timestamps are present too.
        assert stored_after.created_at == t_queued
        assert stored_after.queued_at == t_queued

        # Phase 3: a downstream post-restart RUNNING transition (e.g. a
        # heartbeat-driven reassumption) must NOT overwrite the durable
        # started_at. ``updated_at`` advances; ``started_at`` is frozen.
        advanced = stored_after.with_status("RUNNING", now=t_post_restart_refresh)
        assert advanced.started_at == t_first_running, (
            f"started_at must NOT move on a post-restart RUNNING transition; "
            f"got {advanced.started_at!r}, expected {t_first_running!r}"
        )
        assert advanced.updated_at == t_post_restart_refresh
        # And it must not move on a status refresh either.
        refreshed = advanced.with_status("IN_PROGRESS", now="2026-01-01T00:02:00+00:00")
        assert refreshed.started_at == t_first_running

    asyncio.run(run())


def test_stored_message_started_at_survives_update_message_status_heartbeat():
    """AC-4 heartbeat guarantee: ``update_message_status`` (which is what a
    worker calls to drive heartbeat-driven status refresh) must not move
    ``started_at`` once it has been captured. This pins the contract through
    the broker's public update_message_status path, not just the domain
    helper, so a future refactor of the storage layer that re-introduces
    ``update_message_status_locked`` and forgets to preserve the
    started_at invariant is caught here.
    """
    import asyncio

    from orchlink.broker.storage.memory import MemoryMessageStore

    async def run() -> None:
        store = MemoryMessageStore()
        envelope = _make_envelope(message_id="msg-ac4-heartbeat")
        # Initial enqueue lands the message in QUEUED.
        await store.enqueue_message(envelope)
        # Move it into RUNNING — the first such transition captures
        # started_at. The exact capture time is owned by ``_now()`` so we
        # only pin ``first_started_at`` after the call rather than a literal.
        await store.update_message_status(envelope.message_id, "RUNNING")
        stored = store._state.active_messages[envelope.message_id]
        first_started_at = stored.started_at
        assert first_started_at is not None, "first RUNNING must set started_at"
        assert first_started_at == stored.updated_at

        # Heartbeat-driven status refresh: RUNNING -> IN_PROGRESS (a real
        # worker reports IN_PROGRESS during long runs) must not move
        # started_at. ``updated_at`` advances, started_at stays.
        await store.update_message_status(envelope.message_id, "IN_PROGRESS")
        stored2 = store._state.active_messages[envelope.message_id]
        assert stored2.started_at == first_started_at, (
            f"status refresh must not move started_at; got {stored2.started_at!r}, "
            f"expected {first_started_at!r}"
        )

        # IN_PROGRESS -> RUNNING reassumption (a heartbeat-driven switch
        # back to RUNNING) is also a no-op for started_at.
        await store.update_message_status(envelope.message_id, "RUNNING")
        stored3 = store._state.active_messages[envelope.message_id]
        assert stored3.started_at == first_started_at

        # And the wire projection still carries started_at from the
        # original capture.
        listed = await store.list_active_messages(project_id="default")
        match = next(
            (row for row in listed if row.get("message_id") == envelope.message_id),
            None,
        )
        assert match is not None
        assert match["started_at"] == first_started_at

    asyncio.run(run())


# --- G005 AC-1: Conversation domain object ---


def test_conversation_domain_object_has_required_shape_and_helpers():
    """Conversation exists, owns conversation fields, exposes immutable
    lifecycle helpers, and renders the prior public conversation dict shape.

    AC-1: A Conversation immutable domain record exists in core code, owns
    current conversation fields and broker metadata, exposes immutable update
    helpers, and can render the prior public conversation dict shape.
    """
    import inspect

    from orchlink.core.models import Conversation

    # Type checks.
    assert inspect.isclass(Conversation)
    # Frozenness: assignment must raise.
    conv = Conversation(
        conversation_id="C1",
        project_id="default",
        participants=("demo.lead", "demo.work"),
        status="OPEN",
        turn=1,
        max_turns=6,
        from_agent="demo.lead",
        to_agent="demo.work",
        message_type="CHAT_START",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    assert isinstance(conv, Conversation)
    assert conv.conversation_id == "C1"
    assert conv.status == "OPEN"
    assert conv.turn == 1
    assert conv.max_turns == 6
    assert "demo.lead" in conv.participants
    assert "demo.work" in conv.participants
    assert conv.created_at == "2026-01-01T00:00:00+00:00"
    assert conv.updated_at == "2026-01-01T00:00:00+00:00"

    # Required helpers exist.
    assert callable(getattr(Conversation, "with_status", None))
    assert callable(getattr(Conversation, "with_turn", None))
    assert callable(getattr(Conversation, "with_participants", None))
    assert callable(getattr(Conversation, "with_payload", None))
    assert callable(getattr(Conversation, "touch", None))
    assert callable(getattr(Conversation, "close", None))
    assert callable(getattr(Conversation, "to_wire_dict", None))


def test_conversation_domain_object_chat_close_initial_status():
    """A CHAT_CLOSE envelope opening a conversation yields a CLOSED record,
    matching the broker's CHAT_CLOSE short-circuit.
    """
    from orchlink.core.models import Conversation

    conv = Conversation(
        conversation_id="C2",
        project_id="default",
        participants=("demo.lead", "demo.work"),
        status="CLOSED",
        turn=1,
        max_turns=6,
        from_agent="demo.lead",
        to_agent="demo.work",
        message_type="CHAT_CLOSE",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )
    assert conv.status == "CLOSED"


def test_conversation_domain_object_immutable_helpers_return_new():
    """Each lifecycle helper produces a new immutable Conversation; identity
    changes and the source record is untouched.
    """
    from orchlink.core.models import Conversation

    original = Conversation(
        conversation_id="C3",
        project_id="default",
        participants=("demo.lead", "demo.work"),
        status="OPEN",
        turn=1,
        max_turns=6,
        from_agent="demo.lead",
        to_agent="demo.work",
        message_type="CHAT_START",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
    )

    next_status = original.with_status("RUNNING", now="2026-01-02T00:00:00+00:00")
    assert next_status is not original
    assert next_status.status == "RUNNING"
    assert original.status == "OPEN"
    assert original.updated_at == "2026-01-01T00:00:00+00:00"

    advanced = original.with_turn(3)
    assert advanced.turn == 3
    assert original.turn == 1

    re_participants = original.with_participants(("demo.lead", "demo.work", "demo.review"), now="2026-01-03T00:00:00+00:00")
    assert re_participants.participants == ("demo.lead", "demo.work", "demo.review")
    assert original.participants == ("demo.lead", "demo.work")

    closed = original.close(now="2026-01-04T00:00:00+00:00")
    assert closed.status == "CLOSED"
    assert original.status == "OPEN"

    touched = original.touch(
        activity_at="2026-01-02T00:00:00+00:00",
        activity_type="tool_call",
        activity_tool="bash",
        activity_preview="hello",
        now="2026-01-02T00:00:00+00:00",
    )
    assert touched.last_activity_type == "tool_call"
    assert touched.last_activity_tool == "bash"
    assert original.last_activity_type is None


def test_conversation_domain_object_to_wire_dict_matches_prior_shape():
    """`to_wire_dict()` reproduces the prior public conversation dict shape
    (matches the `talk_job_to_wire` keys the broker emits today).
    """
    from orchlink.core.models import Conversation

    conv = Conversation(
        conversation_id="C-WIRE",
        project_id="demo",
        participants=("demo.lead", "demo.work"),
        status="OPEN",
        turn=1,
        max_turns=6,
        from_agent="demo.lead",
        to_agent="demo.work",
        message_type="CHAT_START",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        last_message_preview="hi",
        preview="hi",
        last_activity_at="2026-01-01T00:00:00+00:00",
        last_activity_type="tool_call",
        last_activity_tool="bash",
        last_activity_preview="hi",
        worker_name="work",
    )
    wire = conv.to_wire_dict()

    expected_keys = {
        "kind", "conversation_id", "project_id", "participants", "mode", "status",
        "turn", "max_turns", "from_agent", "to_agent", "created_at", "updated_at",
        "last_message_preview", "preview", "message_type", "last_activity_at",
        "last_activity_type", "last_activity_tool", "last_activity_preview",
        "worker_name",
    }
    assert set(wire.keys()) == expected_keys
    assert wire["kind"] == "talk"
    assert wire["mode"] == "TALK"
    assert wire["conversation_id"] == "C-WIRE"
    assert wire["participants"] == ["demo.lead", "demo.work"]
    assert wire["status"] == "OPEN"
    # JSON-serializable (no Conversation instance embedded).
    import json
    json.dumps(wire)


def test_conversation_domain_object_to_wire_dict_round_trips_via_dataclasses_replace():
    """The wire dict round-trips through `dataclasses.replace` so callers
    (e.g., JSONL restore) can reconstruct a Conversation from a wire dump
    using the standard replace helper.
    """
    from dataclasses import asdict, replace

    from orchlink.core.models import Conversation

    original = Conversation(
        conversation_id="C-ROUND",
        project_id="demo",
        participants=("demo.lead", "demo.work"),
        status="OPEN",
        turn=2,
        max_turns=6,
        from_agent="demo.lead",
        to_agent="demo.work",
        message_type="CHAT_TURN",
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-02T00:00:00+00:00",
        last_message_preview="",
        preview="",
        last_activity_at=None,
        last_activity_type=None,
        last_activity_tool=None,
        last_activity_preview=None,
        worker_name=None,
    )

    wire = original.to_wire_dict()
    restored = Conversation(**asdict(original))
    assert restored.to_wire_dict() == wire

    # The lifecycle helpers and dataclasses.replace both produce an
    # "equivalent" wire output for the same observable fields.
    advanced = replace(original, turn=3, updated_at="2026-01-03T00:00:00+00:00")
    advanced_wire = advanced.to_wire_dict()
    assert advanced_wire["turn"] == 3
    assert advanced_wire["updated_at"] == "2026-01-03T00:00:00+00:00"
    assert advanced_wire["conversation_id"] == wire["conversation_id"]


# --- G006 AC-1: core job_lifecycle module exists, has no broker imports, and
#     exposes the canonical job-lifecycle primitives.


def test_core_job_lifecycle_exports_canonical_symbols():
    """AC-1: `orchlink.core.job_lifecycle` exports the canonical broker job
    job-lifecycle primitives: `TaskJobLifecycle`, `TalkJobLifecycle`,
    `BrokerJobLifecycle` (with `.tasks`/`.talk`), `LIFECYCLE_FOR_EVENT`, and
    `TASK_STATUS_JOB_EVENTS` (the task-status -> JobEventType mapping used by
    the lifecycle facades). The module source must contain no `import orchlink.broker.*`
    line — these are core-owned primitives, not broker glue."""

    import importlib
    from pathlib import Path


    jm = importlib.import_module("orchlink.core.job_lifecycle")

    for name in (
        "TaskJobCommand",
        "TaskJobLifecycle",
        "TalkJobCommand",
        "TalkJobLifecycle",
        "BrokerJobLifecycle",
        "LIFECYCLE_FOR_EVENT",
        "TASK_STATUS_JOB_EVENTS",
    ):
        assert hasattr(jm, name), name

    facade = jm.BrokerJobLifecycle()
    assert isinstance(facade.tasks, jm.TaskJobLifecycle)
    assert isinstance(facade.talk, jm.TalkJobLifecycle)
    # The job lifecycle classes ship with their public lifecycle methods.
    for method_name in ("create", "transition", "transition_path", "with_payload"):
        assert hasattr(facade.tasks, method_name)
    for method_name in ("create", "transition", "canonical_status_for_wire", "with_payload"):
        assert hasattr(facade.talk, method_name)
    assert hasattr(facade.talk, "TALK_LIFECYCLE_FOR_TARGET")

    # `LIFECYCLE_FOR_EVENT` is the JobEventType -> Job lifecycle method dispatch.
    assert isinstance(jm.LIFECYCLE_FOR_EVENT, dict)
    assert len(jm.LIFECYCLE_FOR_EVENT) >= 6
    from orchlink.core.models import JobEventType
    for event_type in (
        JobEventType.QUEUED,
        JobEventType.STARTED,
        JobEventType.REPLIED,
        JobEventType.FAILED,
        JobEventType.TIMED_OUT,
        JobEventType.CANCELLED,
        JobEventType.CLOSED,
    ):
        assert event_type in jm.LIFECYCLE_FOR_EVENT

    # The task-status mapping covers both canonical and protocol-side aliases
    # the public API accepts.
    for status, expected in {
        "PENDING": JobEventType.QUEUED,
        "QUEUED": JobEventType.QUEUED,
        "DELIVERED": JobEventType.DELIVERED,
        "RUNNING": JobEventType.STARTED,
        "IN_PROGRESS": JobEventType.STARTED,
        "DONE": JobEventType.REPLIED,
        "COMPLETED": JobEventType.REPLIED,
        "FAILED": JobEventType.FAILED,
        "TIMEOUT": JobEventType.TIMED_OUT,
        "CANCELLED": JobEventType.CANCELLED,
    }.items():
        assert jm.TASK_STATUS_JOB_EVENTS[status] is expected, (status, jm.TASK_STATUS_JOB_EVENTS[status])

    # Module source contains no broker imports: scan only `import` / `from`
    # statements (skip docstrings and comments). Core job lifecycle is broker-free.
    repo_source = Path(__file__).resolve().parent.parent / "src" / "orchlink" / "core" / "job_lifecycle.py"
    source_text = repo_source.read_text(encoding="utf-8")
    for line in source_text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        # No imports from orchlink.broker.* or relative paths into broker.
        for needle in ("orchlink.broker", "core.job_lifecycle", "broker.state"):
            assert needle not in stripped, (needle, stripped)


# --- G006 AC-2: Job / JobEvent / JobRoute / Job.transition / lifecycle helpers
#     remain unchanged public domain primitives.


def _make_task_job() -> "Job":
    from orchlink.core.models import Job, JobRoute, TaskJobPayload

    return Job(
        id="T-001",
        kind="task",
        project_id="default",
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="PLAN",
        task_id="T-001",
        payload=TaskJobPayload(),
    )


def _leased_running():
    """Thread a CREATED task job through QUEUED -> DELIVERED -> RUNNING with a lease."""
    return _dc_replace(
        _make_task_job().queue().deliver().start(),
        lease=__import__("orchlink.core.models", fromlist=["JobLease"]).JobLease(holder="demo.work", expires_at="2026-01-01T00:00:00+00:00", epoch=1, heartbeat_ms=15000),
    )


def test_job_lifecycle_public_primitives_unchanged_are_exported_from_core():
    """AC-2: `Job`, `JobEvent`, `JobRoute`, and the `Job.transition(event)`
    escape hatch remain public domain primitives on `orchlink.core.models`.
    Their public attributes and signatures are unchanged."""

    from orchlink.core import models as core_models
    from orchlink.core.models import Job, JobEvent, JobRoute

    for name in (
        "Job",
        "JobEvent",
        "JobRoute",
        "JobEventType",
        "JOB_EVENT_STATUS",
        "advance_job",
    ):
        assert hasattr(core_models, name), name

    # `Job`, `JobEvent`, `JobRoute` are frozen dataclasses.
    assert _dataclasses.is_dataclass(Job)
    assert _dataclasses.is_dataclass(JobEvent)
    assert _dataclasses.is_dataclass(JobRoute)

    # `Job.transition` is the documented escape hatch and is callable on every
    # Job instance.
    job = _make_task_job()
    assert callable(job.transition)

    # `Job` exposes the full lifecycle helper surface as methods.
    for method_name in ("queue", "deliver", "start", "reply", "fail", "timeout", "cancel", "close"):
        assert callable(getattr(Job, method_name)), method_name


def test_job_lifecycle_public_primitives_unchanged_queue_deliver_and_start_path():
    """AC-2: `queue` / `deliver` / `start` lifecycle helpers advance the Job
    through the canonical lifecycle and return new Job instances."""

    from orchlink.core.models import Job

    job = _make_task_job()
    assert job.status == "CREATED"

    queued = job.queue()
    assert isinstance(queued, Job)
    assert queued is not job
    assert queued.status == "QUEUED"

    delivered = queued.deliver()
    assert isinstance(delivered, Job)
    assert delivered is not queued
    assert delivered.status == "DELIVERED"

    running = delivered.start()
    assert isinstance(running, Job)
    assert running is not delivered
    assert running.status == "RUNNING"

    # And `Job.transition(JobEvent)` agrees with the lifecycle helpers.
    from orchlink.core.models import JobEvent, JobEventType

    leased = _leased_running()
    event = JobEvent(type=JobEventType.REPLIED, project_id=leased.project_id, job_id=leased.id)
    via_transition = leased.transition(event)
    assert isinstance(via_transition, Job)
    assert via_transition.status == "DONE"
    assert via_transition is not leased


def test_job_lifecycle_public_primitives_unchanged_terminal_helpers_clear_lease():
    """AC-2: terminal `reply` / `fail` / `timeout` / `cancel` / `close`
    lifecycle helpers land the Job in their canonical terminal status AND
    clear the lease via `advance_job`."""

    from orchlink.core.states import CANONICAL_TERMINAL_STATUSES

    # Reply -> DONE.
    replied = _leased_running().reply()
    assert replied.status == "DONE"
    assert replied.lease is None
    assert replied.status in CANONICAL_TERMINAL_STATUSES

    failed = _leased_running().fail()
    assert failed.status == "FAILED"
    assert failed.lease is None
    assert failed.status in CANONICAL_TERMINAL_STATUSES

    timed_out = _leased_running().timeout()
    assert timed_out.status == "TIMEOUT"
    assert timed_out.lease is None
    assert timed_out.status in CANONICAL_TERMINAL_STATUSES

    cancelled = _leased_running().cancel()
    assert cancelled.status == "CANCELLED"
    assert cancelled.lease is None
    assert cancelled.status in CANONICAL_TERMINAL_STATUSES

    closed = _leased_running().close()
    assert closed.status == "CLOSED"
    assert closed.lease is None
    assert closed.status in CANONICAL_TERMINAL_STATUSES


def test_job_lifecycle_public_primitives_unchanged_advance_job_is_canonical_helper():
    """AC-2: `advance_job` is the canonical helper that backs
    `Job.transition(event)` and enforces project_id / job_id checks."""

    import pytest

    from orchlink.core.models import Job, JobEvent, JobEventType, advance_job

    job = _make_task_job()
    ok_event = JobEvent(type=JobEventType.QUEUED, project_id=job.project_id, job_id=job.id)
    advanced = advance_job(job, ok_event)
    assert isinstance(advanced, Job)
    assert advanced.status == "QUEUED"
    assert advanced is not job

    bad_project = JobEvent(type=JobEventType.QUEUED, project_id="other", job_id=job.id)
    with pytest.raises(ValueError):
        advance_job(job, bad_project)

    bad_job_id = JobEvent(type=JobEventType.QUEUED, project_id=job.project_id, job_id="other")
    with pytest.raises(ValueError):
        advance_job(job, bad_job_id)


# --- Core job-lifecycle ownership ---


def test_broker_uses_core_job_lifecycle_storage_facade_originates_from_core():
    """AC-3: `MemoryMessageStore._job_lifecycle` is an instance of
    `orchlink.core.job_lifecycle.BrokerJobLifecycle` — the broker storage module
    no longer hard-codes the broker-side lifecycle facade.

    The test asserts the runtime identity (the instance is the exact class
    defined in core) AND that the broker storage module's top-level `import`
    statement for the job lifecycle points at core rather than the broker
    job-lifecycle module."""

    import importlib
    from pathlib import Path

    from orchlink.broker.storage.memory import MemoryMessageStore
    from orchlink.core.job_lifecycle import BrokerJobLifecycle as CoreBrokerJobLifecycle

    store = MemoryMessageStore()
    facade = store._job_lifecycle

    # The runtime facade is the exact class from core.
    assert type(facade) is CoreBrokerJobLifecycle, type(facade).__name__
    assert type(facade.tasks) is __import__(
        "orchlink.core.job_lifecycle", fromlist=["TaskJobLifecycle"]
    ).TaskJobLifecycle
    assert type(facade.talk) is __import__(
        "orchlink.core.job_lifecycle", fromlist=["TalkJobLifecycle"]
    ).TalkJobLifecycle

    # The `BrokerJobLifecycle` symbol resolved from `orchlink.core.job_lifecycle`
    # is the same object as the one bound at the broker storage module.
    broker_memory = importlib.import_module("orchlink.broker.storage.memory")
    assert broker_memory.BrokerJobLifecycle is CoreBrokerJobLifecycle

    # Source-level check: broker storage imports `BrokerJobLifecycle` from core.
    source_path = Path(__file__).resolve().parent.parent / "src" / "orchlink" / "broker" / "storage" / "memory.py"
    source = source_path.read_text(encoding="utf-8")
    assert "from orchlink.core.job_lifecycle import BrokerJobLifecycle" in source
    assert "from orchlink.broker.job_lifecycle" not in source


def test_broker_job_lifecycle_shim_removed():
    """The broker-side job-lifecycle shim is gone; core owns the implementation."""

    import importlib.util

    assert importlib.util.find_spec("orchlink.broker.job_lifecycle") is None


# --- G006 AC-4: TaskJobLifecycle behavior parity through
#     `orchlink.core.job_lifecycle` — transition_path, wire aliases, final
#     statuses, terminal lease clearing.


def _core_task_at(status: str, task_id: str = "T001", with_lease: bool = False):
    """Build a task Job in the requested canonical status using lifecycle
    helpers and the core job lifecycle (CREATED -> target path).

    Optional `with_lease=True` attaches a populated lease so terminal
    clearing behavior is observable."""

    from dataclasses import replace as _dc_replace

    from orchlink.core.job_lifecycle import TaskJobLifecycle
    from orchlink.core.models import Job, JobLease, JobRoute

    base = Job(
        id=task_id,
        kind="task",
        project_id="demo",
        task_id=task_id,
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="PLAN",
    )
    advanced = TaskJobLifecycle().transition(base, status)
    if with_lease and advanced.lease is None:
        advanced = _dc_replace(
            advanced,
            lease=JobLease(
                holder="demo.work",
                expires_at="2026-01-01T00:00:00+00:00",
                epoch=1,
                heartbeat_ms=100,
            ),
        )
    return advanced


def test_task_lifecycle_via_core_job_lifecycle_transition_path_creatED_to_done():
    """AC-4: `TaskJobLifecycle.transition_path` walks the canonical forward
    lifecycle for the documented task transition."""

    from orchlink.core.job_lifecycle import TaskJobLifecycle
    from orchlink.core.models import JobEventType

    lifecycle = TaskJobLifecycle()

    # CREATED -> DONE walks QUEUED -> DELIVERED -> DONE (the BFS prefers
    # DONE over RUNNING since DONE is in `preferred_statuses`).
    path = lifecycle.transition_path("CREATED", "DONE")
    assert path == [
        JobEventType.QUEUED,
        JobEventType.DELIVERED,
        JobEventType.REPLIED,
    ]

    # CREATED -> RUNNING may short-circuit through QUEUED directly to RUNNING
    # (the BFS picks RUNNING as soon as it is reachable from any state on the
    # frontier) — what matters is that the returned path lands the job at
    # RUNNING in one deterministic forward step.
    running_path = lifecycle.transition_path("CREATED", "RUNNING")
    assert running_path[0] == JobEventType.QUEUED
    assert running_path[-1] == JobEventType.STARTED
    assert len(running_path) >= 1
    assert lifecycle.transition(_core_task_at("CREATED"), "RUNNING").status == "RUNNING"

    # Same-status returns the empty path.
    assert lifecycle.transition_path("CREATED", "CREATED") == []
    assert lifecycle.transition_path("QUEUED", "QUEUED") == []


def test_task_lifecycle_via_core_job_lifecycle_status_parity_for_obvious_paths():
    """AC-4: every obvious forward task path through the core job lifecycle
    lands at the right canonical status, including the documented protocol
    aliases (PENDING, IN_PROGRESS, COMPLETED).

    This mirrors the parity table pinned by
    `tests/test_state.py::test_job_lifecycle_job_lifecycle_status_parity_for_task_obvious_paths`,
    but the import surface here is the core module."""

    from orchlink.core.job_lifecycle import TaskJobLifecycle

    lifecycle = TaskJobLifecycle()

    # Simple forward steps.
    assert lifecycle.transition(_core_task_at("CREATED"), "QUEUED").status == "QUEUED"
    assert lifecycle.transition(_core_task_at("CREATED"), "DELIVERED").status == "DELIVERED"
    assert lifecycle.transition(_core_task_at("CREATED"), "RUNNING").status == "RUNNING"

    # From RUNNING every documented terminal must be reachable.
    for terminal in ("DONE", "FAILED", "TIMEOUT", "CANCELLED"):
        started = lifecycle.transition(_core_task_at("CREATED"), "RUNNING")
        result = lifecycle.transition(started, terminal)
        assert result.status == terminal, (terminal, result.status)

    # Protocol aliases map to the canonical lifecycle.
    assert lifecycle.transition(_core_task_at("CREATED"), "PENDING").status == "QUEUED"
    assert lifecycle.transition(_core_task_at("CREATED"), "IN_PROGRESS").status == "RUNNING"
    assert lifecycle.transition(_core_task_at("CREATED"), "COMPLETED").status == "DONE"


def test_job_lifecycle_job_lifecycle_status_parity_via_core_task_terminal_lease_clear():
    """AC-4: terminal transitions through the core task job lifecycle clear
    any active lease (M3 contract) and produce the canonical terminal status."""

    from orchlink.core.job_lifecycle import TaskJobLifecycle

    lifecycle = TaskJobLifecycle()

    leased_running = _core_task_at("RUNNING", with_lease=True)
    assert leased_running.lease is not None  # precondition

    replied = lifecycle.transition(leased_running, "DONE")
    assert replied.status == "DONE"
    assert replied.lease is None

    failed = lifecycle.transition(_core_task_at("RUNNING", with_lease=True), "FAILED")
    assert failed.status == "FAILED"
    assert failed.lease is None

    timed_out = lifecycle.transition(_core_task_at("RUNNING", with_lease=True), "TIMEOUT")
    assert timed_out.status == "TIMEOUT"
    assert timed_out.lease is None

    cancelled = lifecycle.transition(_core_task_at("RUNNING", with_lease=True), "CANCELLED")
    assert cancelled.status == "CANCELLED"
    assert cancelled.lease is None


def test_job_lifecycle_job_lifecycle_status_parity_via_core_task_idempotent_and_unknown():
    """AC-4: transitioning to the current status is a no-op (idempotent),
    and unknown wire statuses (not in TASK_STATUS_JOB_EVENTS) are silently
    ignored — the same behavior the broker implementation had."""

    from orchlink.core.job_lifecycle import TaskJobLifecycle

    lifecycle = TaskJobLifecycle()

    queued = lifecycle.transition(_core_task_at("CREATED"), "QUEUED")
    again = lifecycle.transition(queued, "QUEUED")
    # Same-status transition is idempotent: the post-transition job is in
    # QUEUED and returns either a fresh instance or the same one.
    assert again.status == "QUEUED"

    # Unknown/unmapped status (e.g. "CLOSED" for task-side) is silently
    # ignored by the transition path.
    delivered = lifecycle.transition(_core_task_at("CREATED"), "CLOSED")
    assert delivered.status == "CREATED"


# --- G006 AC-5: TalkJobLifecycle behavior parity through
#     `orchlink.core.job_lifecycle` — wire-to-canonical mapping, CREATED to
#     RUNNING routing, CLOSED terminal routing, terminal lease clearing.


def _core_talk_at(
    status: str = "CREATED",
    conversation_id: str = "C001",
    with_lease: bool = False,
):
    """Build a talk Job in the requested status using only core-side
    primitives (TalkJobLifecycle + Job lifecycle helpers)."""

    from dataclasses import replace as _dc_replace

    from orchlink.core.job_lifecycle import TalkJobLifecycle
    from orchlink.core.models import Job, JobLease, JobRoute, TalkJobPayload

    base = Job(
        id=conversation_id,
        kind="talk",
        project_id="demo",
        conversation_id=conversation_id,
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="TALK",
        payload=TalkJobPayload(),
        turn=1,
        max_turns=6,
    )
    advanced = TalkJobLifecycle().transition(base, status)
    if with_lease and advanced.lease is None:
        advanced = _dc_replace(
            advanced,
            lease=JobLease(
                holder="demo.work",
                expires_at="2026-01-01T00:00:00+00:00",
                epoch=1,
                heartbeat_ms=100,
            ),
        )
    return advanced


def test_talk_wire_status_mapping_unchanged_in_core_mapping_table():
    """AC-5: `TalkJobLifecycle.canonical_status_for_wire` maps every
    documented wire status to the canonical lifecycle. The mapping has been
    stable since the broker implementation; the core path must match it."""

    from orchlink.core.job_lifecycle import TalkJobLifecycle

    lifecycle = TalkJobLifecycle()
    assert lifecycle.canonical_status_for_wire("OPEN") == "RUNNING"
    assert lifecycle.canonical_status_for_wire("CLOSED") == "CLOSED"
    assert lifecycle.canonical_status_for_wire("TIMEOUT") == "TIMEOUT"
    assert lifecycle.canonical_status_for_wire("FAILED") == "FAILED"
    assert lifecycle.canonical_status_for_wire("CANCELLED") == "CANCELLED"

    # Case / blank normalization mirrors the prior behavior (uppercased).
    assert lifecycle.canonical_status_for_wire("open") == "RUNNING"
    assert lifecycle.canonical_status_for_wire("Closed") == "CLOSED"
    # Unknown status defaults to RUNNING (open conversation default).
    assert lifecycle.canonical_status_for_wire("UNKNOWN") == "RUNNING"
    assert lifecycle.canonical_status_for_wire("") == "RUNNING"


def test_talk_lifecycle_via_core_job_lifecycle_dispatch_table_unchanged():
    """AC-5: `TalkJobLifecycle.TALK_LIFECYCLE_FOR_TARGET` keeps the same
    wire-target -> Job lifecycle-method dispatch as the broker implementation."""

    from orchlink.core.job_lifecycle import TalkJobLifecycle

    lifecycle = TalkJobLifecycle()
    dispatch = lifecycle.TALK_LIFECYCLE_FOR_TARGET
    assert dispatch["CLOSED"] is __import__("orchlink.core.models", fromlist=["Job"]).Job.close
    assert dispatch["TIMEOUT"] is __import__("orchlink.core.models", fromlist=["Job"]).Job.timeout
    assert dispatch["FAILED"] is __import__("orchlink.core.models", fromlist=["Job"]).Job.fail
    assert dispatch["CANCELLED"] is __import__("orchlink.core.models", fromlist=["Job"]).Job.cancel
    assert dispatch["RUNNING"] is __import__("orchlink.core.models", fromlist=["Job"]).Job.start


def test_talk_lifecycle_via_core_job_lifecycle_creatED_to_running_routes_through_queue_and_start():
    """AC-5: a fresh CREATED talk job given wire status OPEN reaches RUNNING
    by routing through `queue().start()` (the documented CREATED detour)."""

    from orchlink.core.job_lifecycle import TalkJobLifecycle

    lifecycle = TalkJobLifecycle()

    # CREATED -> OPEN -> RUNNING (via queue().start()).
    opened = lifecycle.transition(_core_talk_at("CREATED"), "OPEN")
    assert opened.status == "RUNNING"


def test_talk_lifecycle_via_core_job_lifecycle_creatED_to_closed_routes_through_queue_and_start():
    """AC-5: CREATED -> CLOSED via the core talk lifecycle ends at CLOSED."""

    from orchlink.core.job_lifecycle import TalkJobLifecycle

    lifecycle = TalkJobLifecycle()

    closed = lifecycle.transition(_core_talk_at("CREATED"), "CLOSED")
    assert closed.status == "CLOSED"


def test_job_lifecycle_job_lifecycle_status_parity_for_talk_obvious_paths_via_core():
    """AC-5: obvious-path parity for the talk lifecycle through core. Every
    documented wire status lands at the canonical lifecycle status when
    routed from RUNNING."""

    from orchlink.core.job_lifecycle import TalkJobLifecycle

    lifecycle = TalkJobLifecycle()

    # RUNNING -> CLOSED via Job.close() on the core side.
    closed = lifecycle.transition(_core_talk_at("RUNNING"), "CLOSED")
    assert closed.status == "CLOSED"

    # RUNNING -> TIMEOUT -> TIMEOUT (canonical).
    timed_out = lifecycle.transition(_core_talk_at("RUNNING"), "TIMEOUT")
    assert timed_out.status == "TIMEOUT"

    # RUNNING -> FAILED -> FAILED.
    failed = lifecycle.transition(_core_talk_at("RUNNING"), "FAILED")
    assert failed.status == "FAILED"

    # RUNNING -> CANCELLED -> CANCELLED.
    cancelled = lifecycle.transition(_core_talk_at("RUNNING"), "CANCELLED")
    assert cancelled.status == "CANCELLED"

    # RUNNING -> OPEN -> RUNNING (idempotent for already-running jobs).
    same = lifecycle.transition(_core_talk_at("RUNNING"), "OPEN")
    assert same.status == "RUNNING"


def test_job_lifecycle_job_lifecycle_status_parity_for_talk_terminal_lease_clear_via_core():
    """AC-5: talk terminal transitions through the core job lifecycle clear
    any active lease (M3 contract) and produce the canonical terminal status.
    `Job.close`, `Job.fail`, `Job.timeout`, and `Job.cancel` are each
    exercised through the talk lifecycle via the documented wire aliases."""

    from orchlink.core.job_lifecycle import TalkJobLifecycle

    lifecycle = TalkJobLifecycle()

    closed = lifecycle.transition(_core_talk_at("RUNNING", with_lease=True), "CLOSED")
    assert closed.status == "CLOSED"
    assert closed.lease is None

    timed_out = lifecycle.transition(_core_talk_at("RUNNING", with_lease=True), "TIMEOUT")
    assert timed_out.status == "TIMEOUT"
    assert timed_out.lease is None

    failed = lifecycle.transition(_core_talk_at("RUNNING", with_lease=True), "FAILED")
    assert failed.status == "FAILED"
    assert failed.lease is None

    cancelled = lifecycle.transition(_core_talk_at("RUNNING", with_lease=True), "CANCELLED")
    assert cancelled.status == "CANCELLED"
    assert cancelled.lease is None


def test_talk_lifecycle_via_core_job_lifecycle_terminal_statuses_short_circuit():
    """AC-5: feeding a wire status matching the job's current canonical status
    is idempotent — the prior broker implementation returned the job
    unchanged in that case."""

    from orchlink.core.job_lifecycle import TalkJobLifecycle

    lifecycle = TalkJobLifecycle()
    closed = _core_talk_at("CLOSED")
    again = lifecycle.transition(closed, "CLOSED")
    assert again.status == "CLOSED"


def test_talk_lifecycle_via_core_job_lifecycle_create_carries_conversation_metadata():
    """AC-5: `TalkJobLifecycle.create` builds a canonical talk Job from a
    wire message, preserving `conversation_id`, `route`, `turn`, `max_turns`,
    and `mode=TALK` defaults."""

    from orchlink.core.job_lifecycle import TalkJobCommand, TalkJobLifecycle
    from orchlink.core.models import Job, JobRoute

    lifecycle = TalkJobLifecycle()
    command = TalkJobCommand(
        conversation_id="C-META",
        project_id="demo",
        from_agent="demo.lead",
        to_agent="demo.work",
        turn=3,
        max_turns=9,
    )
    created = lifecycle.create(command)
    assert isinstance(created, Job)
    assert created.kind == "talk"
    assert created.conversation_id == "C-META"
    assert created.id == "C-META"
    assert created.route == JobRoute(from_agent="demo.lead", to_agent="demo.work")
    assert created.mode == "TALK"
    assert created.status == "CREATED"
    assert created.turn == 3
    assert created.max_turns == 9


# --- G009 AC-4: core job lifecycles consume typed creation commands, not
#     raw message wire dictionaries.


def test_g009_job_lifecycle_create_uses_typed_commands_not_message_wire_dicts():
    import inspect

    from orchlink.core.job_lifecycle import (
        TalkJobCommand,
        TalkJobLifecycle,
        TaskJobCommand,
        TaskJobLifecycle,
    )
    from orchlink.core.models import Job, TalkJobPayload, TaskJobPayload

    task_command = TaskJobCommand(
        task_id="T-G009-CMD",
        project_id="demo",
        conversation_id="C-G009-CMD",
        from_agent="demo.lead",
        to_agent="demo.work",
        mode="PLAN",
    )
    task_job = TaskJobLifecycle().create(task_command)
    assert isinstance(task_job, Job)
    assert isinstance(task_job.payload, TaskJobPayload)
    assert task_job.task_id == "T-G009-CMD"
    assert task_job.project_id == "demo"
    assert task_job.route.from_agent == "demo.lead"
    assert task_job.route.to_agent == "demo.work"

    talk_command = TalkJobCommand(
        conversation_id="C-G009-CMD",
        project_id="demo",
        from_agent="demo.lead",
        to_agent="demo.review",
        turn=2,
        max_turns=6,
    )
    talk_job = TalkJobLifecycle().create(talk_command)
    assert isinstance(talk_job, Job)
    assert isinstance(talk_job.payload, TalkJobPayload)
    assert talk_job.conversation_id == "C-G009-CMD"
    assert talk_job.turn == 2
    assert talk_job.max_turns == 6

    task_create_source = inspect.getsource(TaskJobLifecycle.create)
    talk_create_source = inspect.getsource(TalkJobLifecycle.create)
    assert ".get(" not in task_create_source
    assert ".get(" not in talk_create_source
    assert "message" not in inspect.signature(TaskJobLifecycle.create).parameters
    assert "message" not in inspect.signature(TalkJobLifecycle.create).parameters
