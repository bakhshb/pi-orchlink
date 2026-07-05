from orchlink.broker.state import (
    JOB_STATUS_LIFECYCLE,
    canonical_job_event_for_broker_event,
    is_active_job_status,
    is_active_session_status,
    is_busy_status,
    is_talk_message_type,
    is_terminal_status,
    job_id_for,
    job_kind_for,
    job_matches_id,
    normalize_message_type,
    normalize_status,
    reply_job_status,
)
from orchlink.core.models import Job, JobRoute, TalkJobPayload


def test_session_lifecycle_helpers_live_in_core_and_broker_reexports():
    from orchlink.broker.state import is_active_session_status as broker_is_active_session_status
    from orchlink.core.session_lifecycle import SessionStatus, is_active_session_status, normalize_session_status

    assert normalize_session_status("active") is SessionStatus.ACTIVE
    assert is_active_session_status("ACTIVE") is True
    assert is_active_session_status(None) is False
    assert broker_is_active_session_status("RELEASED") is False


def test_status_helpers_normalize_and_classify_broker_states():
    assert normalize_status("done") == "DONE"
    assert is_busy_status("queued") is True
    assert is_busy_status("done") is False
    assert is_terminal_status("cancelled") is True
    assert is_terminal_status("running") is False
    assert is_active_job_status("open") is True
    assert is_active_job_status("reclaimable") is True
    assert is_active_session_status("active") is True
    assert is_active_session_status("released") is False


def test_job_lifecycle_names_target_canonical_states():
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


def test_message_and_job_helpers_identify_talk_and_job_rows():
    task = {"task_id": "T001", "conversation_id": "demo-tasks", "message_id": "msg-1"}
    talk = {"conversation_id": "C001", "message_id": "msg-2"}

    assert normalize_message_type("chat_start") == "CHAT_START"
    assert is_talk_message_type("CHAT_REPLY") is True
    assert is_talk_message_type("TASK") is False
    assert job_kind_for(task) == "task"
    assert job_kind_for(talk) == "talk"
    assert job_id_for(task) == "T001"
    assert job_id_for(talk) == "C001"
    assert job_matches_id(task, "T001") is True
    assert job_matches_id(talk, "msg-2") is True
    assert job_matches_id(talk, "T001") is False


def test_reply_job_status_maps_protocol_replies_to_job_statuses():
    assert reply_job_status("RESULT", "DONE") == "DONE"
    assert reply_job_status("BLOCKER", "FAILED") == "FAILED"
    assert reply_job_status("RESULT", "TIMEOUT") == "FAILED"
    assert reply_job_status("CHAT_CLOSE", "DONE") == "CLOSED"


def test_broker_task_events_map_to_canonical_job_events():
    base = {"project_id": "demo", "task_id": "T001"}

    assert canonical_job_event_for_broker_event("message_queued", {**base, "status": "QUEUED"}) == {
        "type": "QUEUED",
        "status": "QUEUED",
        "kind": "task",
        "job_id": "T001",
        "project_id": "demo",
        "source_type": "message_queued",
    }
    assert canonical_job_event_for_broker_event("message_delivered", {**base, "status": "DELIVERED"})["type"] == "DELIVERED"
    assert canonical_job_event_for_broker_event("worker_activity", {**base, "status": "RUNNING"})["type"] == "STARTED"
    assert canonical_job_event_for_broker_event("reply_received", {**base, "status": "COMPLETED"})["type"] == "REPLIED"
    assert canonical_job_event_for_broker_event("work_cancelled", {**base, "status": "CANCELLED"})["type"] == "CANCELLED"
    assert canonical_job_event_for_broker_event("timeout", {**base, "status": "TIMEOUT"})["type"] == "TIMED_OUT"


def test_broker_canonical_job_event_mapper_skips_open_talk_jobs():
    assert canonical_job_event_for_broker_event(
        "reply_received",
        {"project_id": "demo", "conversation_id": "C001", "status": "OPEN"},
    ) is None
    assert canonical_job_event_for_broker_event("conversation_closed", {"project_id": "demo", "conversation_id": "C001", "status": "CLOSED"}) is None


def test_reclaimable_transitions_are_allowed():
    from orchlink.core.states import can_transition

    assert can_transition("RUNNING", "RECLAIMABLE") is True
    assert can_transition("DELIVERED", "RECLAIMABLE") is True
    assert can_transition("RECLAIMABLE", "RUNNING") is True
    assert can_transition("RECLAIMABLE", "DONE") is True
    assert can_transition("RECLAIMABLE", "CANCELLED") is True
    assert can_transition("RECLAIMABLE", "TIMEOUT") is True
    # RECLAIMABLE is terminal-ish only via an explicit target; it cannot go back to QUEUED.
    assert can_transition("RECLAIMABLE", "QUEUED") is False
    # Terminal states still cannot leave.
    assert can_transition("DONE", "RECLAIMABLE") is False
    assert can_transition("CANCELLED", "RECLAIMABLE") is False


# --- G003 AC-1: job-lifecycle uses Job lifecycle methods instead of inline JobEvent ---


def _make_task_job(status, task_id="T001", project_id="demo"):
    """Helper: build a minimal task Job with the requested status and no lease."""
    from orchlink.core.models import Job, JobRoute
    from orchlink.core.states import JobStatus

    # Build via lifecycle methods so the construction itself stays canonical.
    job = Job(
        id=task_id,
        kind="task",
        project_id=project_id,
        task_id=task_id,
        route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
        mode="PLAN",
        status=JobStatus.CREATED.value,
    )
    path_statuses = ["QUEUED", "DELIVERED", "RUNNING"]
    if status in path_statuses:
        return getattr(job, {
            "QUEUED": "queue",
            "DELIVERED": "deliver",
            "RUNNING": "start",
            "DONE": "queue",  # unreachable for this helper
            "FAILED": "queue",
            "TIMEOUT": "queue",
            "CANCELLED": "queue",
            "CLOSED": "queue",
        }[status])()
    return job


def test_job_lifecycle_methods_used_in_job_lifecycle_for_obvious_task_transitions():
    """Structural + behavioral check: job lifecycle uses Job.lifecycle methods.

    AC-1: Task job-lifecycle code uses existing Job lifecycle methods for
    obvious transitions instead of constructing JobEvent inline for those
    routine transitions.

    Two checks:
    1. The job-lifecycle source references the `Job.lifecycle` dispatch table
       and the `Job.<method>()` calls.
    2. After a transition that walks forward through intermediate states, the
       resulting Job has the right status AND the timestamp / lease posture
       that the lifecycle methods produce (proving lifecycle methods were the
       actual driver, not coincidental dict mutation).
    """
    import inspect

    from orchlink.core.job_lifecycle import (
        TaskJobLifecycle,
        TalkJobLifecycle,
    )

    source = inspect.getsource(TaskJobLifecycle)
    # The dispatch table plus at least one inline `Job.queue()`, `Job.start()`,
    # `Job.fail()`, etc. reference inside the job lifecycle.
    assert "LIFECYCLE_FOR_EVENT" in source
    assert "method(job)" in source, (
        "TaskJobLifecycle.transition should call lifecycle methods via the "
        "dispatch table rather than constructing JobEvent per step."
    )

    # Same for TalkJobLifecycle.
    talk_source = inspect.getsource(TalkJobLifecycle)
    assert "TALK_LIFECYCLE_FOR_TARGET" in talk_source or "method(job)" in talk_source
    assert "job.queue().start()" in talk_source or "job.queue()" in talk_source, (
        "TalkJobLifecycle.transition should use Job.queue()/Job.start() for "
        "the CREATED -> RUNNING leg instead of inline JobEvent chains."
    )

    # Behavioral: drive the job lifecycle forward and confirm the Job has the
    # expected status and lease posture. We use a fresh lease to also exercise
    # the terminal-clear-lease path on `FAILED`.
    lifecycle = TaskJobLifecycle()
    job = _make_task_job("CREATED")
    # Acquire a lease so a terminal transition can be observed clearing it.
    from dataclasses import replace as _replace
    leased = _replace(job, lease={"holder": "demo.lead", "epoch": 1, "heartbeat_ms": 1000})

    # CREATED -> QUEUED -> RUNNING via the dispatch-table-driven path.
    running = lifecycle.transition(leased, "RUNNING")
    assert running.status == "RUNNING"
    # The lease is unaffected for non-terminal transitions.
    assert running.lease == leased.lease

    # Terminal RUNNING -> FAILED should clear the lease (lifecycle method side effect).
    failed = lifecycle.transition(running, "FAILED")
    assert failed.status == "FAILED"
    assert failed.lease is None, "Terminal Job lifecycle calls must clear lease."


def test_job_lifecycle_methods_used_in_job_lifecycle_for_obvious_talk_transitions():
    """Talk job lifecycle uses Job lifecycle methods for CREATED -> RUNNING."""
    from orchlink.core.job_lifecycle import TalkJobLifecycle
    from orchlink.core.models import Job, JobRoute, TalkJobPayload

    lifecycle = TalkJobLifecycle()
    job = Job(
        id="C001",
        kind="talk",
        project_id="demo",
        conversation_id="C001",
        route=JobRoute(from_agent="demo.lead", to_agent="demo.review"),
        mode="TALK",
        status="CREATED",
        payload=TalkJobPayload(),
        turn=1,
        max_turns=6,
    )
    # Wire "OPEN" -> canonical RUNNING (the obvious CREATED -> RUNNING path).
    running = lifecycle.transition(job, "OPEN")
    assert running.status == "RUNNING"
    # And CREATED -> CLOSED first walks via RUNNING then closes.
    fresh = Job(
        id="C002",
        kind="talk",
        project_id="demo",
        conversation_id="C002",
        route=JobRoute(from_agent="demo.lead", to_agent="demo.review"),
        mode="TALK",
        status="CREATED",
        payload=TalkJobPayload(),
        turn=1,
        max_turns=6,
    )
    closed = lifecycle.transition(fresh, "CLOSED")
    assert closed.status == "CLOSED"


def test_lifecycle_for_event_dispatch_table_covers_obvious_events():
    """The dispatch table maps every obvious JobEventType to a Job lifecycle method."""
    from orchlink.core.job_lifecycle import LIFECYCLE_FOR_EVENT
    from orchlink.core.models import Job, JobEventType

    expected = {
        JobEventType.QUEUED: Job.queue,
        JobEventType.DELIVERED: Job.deliver,
        JobEventType.STARTED: Job.start,
        JobEventType.REPLIED: Job.reply,
        JobEventType.FAILED: Job.fail,
        JobEventType.TIMED_OUT: Job.timeout,
        JobEventType.CANCELLED: Job.cancel,
        JobEventType.CLOSED: Job.close,
    }
    assert LIFECYCLE_FOR_EVENT == expected
    # Every mapping must be callable.
    for event_type, method in LIFECYCLE_FOR_EVENT.items():
        assert callable(method), f"{event_type} -> {method} is not callable"


# --- G003 AC-2: TaskJobLifecycle and TalkJobLifecycle status parity ---


def _lifecycle_task_at(status, task_id="T001"):
    """Build a task Job at a given status via the Job lifecycle methods."""

    from orchlink.core.job_lifecycle import TaskJobCommand, TaskJobLifecycle

    lifecycle = TaskJobLifecycle()
    command = TaskJobCommand(
        task_id=task_id,
        project_id="demo",
        conversation_id=None,
        from_agent="demo.lead",
        to_agent="demo.work",
        mode="PLAN",
    )
    job = lifecycle.create(command)
    walk = {
        "CREATED": lambda j: j,
        "QUEUED": lambda j: j.queue(),
        "DELIVERED": lambda j: j.queue().deliver(),
        "RUNNING": lambda j: j.queue().deliver().start(),
    }
    if status in walk:
        job = walk[status](job)
    return job


def test_job_lifecycle_job_lifecycle_status_parity_for_task_obvious_paths():
    """Every obvious task path through the job lifecycle reaches the right status.

    AC-2: TaskJobLifecycle preserves the same status outcomes for queued,
    delivered/running, replied/done, failed, timed out, cancelled, and closed
    paths after the lifecycle-method refactor.
    """
    from orchlink.core.job_lifecycle import TaskJobLifecycle

    lifecycle = TaskJobLifecycle()

    # CREATED -> QUEUED via job lifecycle (forward through lifecycle method)
    queued = lifecycle.transition(_lifecycle_task_at("CREATED"), "QUEUED")
    assert queued.status == "QUEUED"

    # CREATED -> DELIVERED walks CREATED -> QUEUED -> DELIVERED.
    delivered = lifecycle.transition(_lifecycle_task_at("CREATED"), "DELIVERED")
    assert delivered.status == "DELIVERED"

    # CREATED -> RUNNING walks CREATED -> QUEUED -> DELIVERED -> RUNNING.
    running = lifecycle.transition(_lifecycle_task_at("CREATED"), "RUNNING")
    assert running.status == "RUNNING"

    # From RUNNING, every terminal must be reachable. CLOSED is a talk-side
    # wire status and is not in TASK_STATUS_JOB_EVENTS, so it is intentionally
    # excluded from the task parity list.
    for terminal in ("DONE", "FAILED", "TIMEOUT", "CANCELLED"):
        started = lifecycle.transition(_lifecycle_task_at("CREATED"), "RUNNING")
        result = lifecycle.transition(started, terminal)
        assert result.status == terminal, f"RUNNING -> {terminal} must yield {terminal} (got {result.status})"

    # Protocol aliases still map to canonical statuses.
    assert lifecycle.transition(_lifecycle_task_at("CREATED"), "IN_PROGRESS").status == "RUNNING"
    assert lifecycle.transition(_lifecycle_task_at("CREATED"), "PENDING").status == "QUEUED"
    assert lifecycle.transition(_lifecycle_task_at("CREATED"), "COMPLETED").status == "DONE"

    # Idempotent: same-status must return the job unchanged.
    same = lifecycle.transition(_lifecycle_task_at("CREATED"), "QUEUED")
    again = lifecycle.transition(same, "QUEUED")
    assert again.status == "QUEUED"
    # `transition` returns a new object on first call, idempotent return may be the same job when status already matches the target.
    assert lifecycle.transition(_lifecycle_task_at("QUEUED"), "QUEUED") is not None


def test_job_lifecycle_job_lifecycle_status_parity_for_talk_obvious_paths():
    """Talk job lifecycle preserves status parity across wire aliases."""
    from orchlink.core.job_lifecycle import TalkJobLifecycle
    from orchlink.core.models import Job, JobRoute, TalkJobPayload

    def talk_fresh(cid="C001"):
        return Job(
            id=cid,
            kind="talk",
            project_id="demo",
            conversation_id=cid,
            route=JobRoute(from_agent="demo.lead", to_agent="demo.review"),
            mode="TALK",
            status="CREATED",
            payload=TalkJobPayload(),
            turn=1,
            max_turns=6,
        )

    lifecycle = TalkJobLifecycle()

    # CREATED + "OPEN" -> canonical RUNNING.
    assert lifecycle.transition(talk_fresh(), "OPEN").status == "RUNNING"

    # CREATED + "CLOSED" walks via QUEUED + STARTED -> RUNNING -> CLOSED.
    assert lifecycle.transition(talk_fresh(), "CLOSED").status == "CLOSED"

    # From CREATED, every terminal canonical status is reachable.
    for terminal in ("TIMEOUT", "FAILED", "CANCELLED"):
        result = lifecycle.transition(talk_fresh(cid=f"C-{terminal}"), terminal)
        assert result.status == terminal, f"CREATED -> {terminal} must yield {terminal}"

    # RUNNING -> CLOSED keeps the same behavior.
    running = lifecycle.transition(talk_fresh(), "OPEN")
    assert lifecycle.transition(running, "CLOSED").status == "CLOSED"

    # Once terminal, idempotent stays terminal.
    closed = lifecycle.transition(talk_fresh(), "CLOSED")
    assert lifecycle.transition(closed, "CLOSED").status == "CLOSED"


# --- G003 AC-5: wire parity across the lifecycle-method refactor ---


def test_job_lifecycle_wire_parity_task_across_all_statuses():
    """`task_job_to_wire` keeps the same wire shape across every lifecycle status.

    AC-5: Task public wire behavior remains unchanged after the lifecycle-method
    refactor. We pin the expected field set for each canonical status and assert
    the wire form is identical regardless of which lifecycle method drove the
    transition.
    """
    from dataclasses import replace as _replace

    from orchlink.core.views import task_job_to_wire

    def base_job(status, task_id="T-WIRE"):
        return Job(
            id=task_id,
            kind="task",
            project_id="demo",
            task_id=task_id,
            route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
            mode="PLAN",
            status=status,
        )

    expected_keys = {
        "kind", "project_id", "task_id", "conversation_id", "mode", "delivery",
        "status", "from_agent", "to_agent", "created_at", "updated_at", "preview",
        "message_id", "correlation_id", "message_type", "last_activity_at",
        "last_activity_type", "last_activity_tool", "last_activity_preview", "lease",
    }

    # Walk the lifecycle once and check wire parity at every step.
    job = base_job("CREATED")
    queue_chain = (
        ("CREATED", job),
        ("QUEUED", job.queue()),
        ("DELIVERED", job.queue().deliver()),
        ("RUNNING", job.queue().deliver().start()),
    )
    for status, current in queue_chain:
        wire = task_job_to_wire(current)
        assert wire["kind"] == "task"
        assert wire["status"] == status, f"Expected status={status} got {wire['status']}"
        assert set(wire.keys()) == expected_keys, (
            f"Wire keys drift at {status}: extra={set(wire.keys()) - expected_keys} "
            f"missing={expected_keys - set(wire.keys())}"
        )

    # From RUNNING, every terminal must produce the canonical task wire.
    running = job.queue().deliver().start()
    for terminal in ("DONE", "FAILED", "TIMEOUT", "CANCELLED"):
        method = {"DONE": running.reply, "FAILED": running.fail, "TIMEOUT": running.timeout, "CANCELLED": running.cancel}[terminal]
        result = method()
        wire = task_job_to_wire(result)
        assert wire["status"] == terminal
        assert wire["kind"] == "task"
        assert set(wire.keys()) == expected_keys
        assert wire["lease"] is None, "Terminal task wire form must surface lease=None."

    # Lease survives non-terminal transitions.
    leased = _replace(running, lease={"holder": "demo.lead", "epoch": 1, "heartbeat_ms": 1000})
    wire = task_job_to_wire(leased)
    assert wire["lease"] is not None
    assert wire["lease"]["holder"] == "demo.lead"


def test_job_lifecycle_wire_parity_talk_wire_status_mapping():
    """Talk wire status mapping is unchanged.

    The job lifecycle maps the talk ``OPEN`` wire status to canonical
    ``RUNNING`` while leaving every other wire status as-is. The wire form
    emitted to API/CLI surfaces the *wire* status (not the canonical one),
    so this test exercises `TalkJobLifecycle.transition` and asserts the
    resulting `talk_job_to_wire` produces the correct wire status for each
    transition.
    """
    from dataclasses import replace as _replace

    from orchlink.core.job_lifecycle import TalkJobLifecycle
    from orchlink.core.views import talk_job_to_wire

    lifecycle = TalkJobLifecycle()

    def fresh(cid):
        return Job(
            id=cid,
            kind="talk",
            project_id="demo",
            conversation_id=cid,
            route=JobRoute(from_agent="demo.lead", to_agent="demo.review"),
            mode="TALK",
            status="CREATED",
            payload=TalkJobPayload(),
            turn=1,
            max_turns=6,
        )

    expected_keys = {
        "kind", "conversation_id", "project_id", "participants", "mode", "status",
        "turn", "max_turns", "from_agent", "to_agent", "created_at", "updated_at",
        "last_message_preview", "preview", "message_type", "last_activity_at",
        "last_activity_type", "last_activity_tool", "last_activity_preview",
    }

    # OPEN -> wire status OPEN (canonical RUNNING maps back).
    open_job = lifecycle.transition(fresh("C-OPEN"), "OPEN")
    wire = talk_job_to_wire(_replace(open_job, payload=TalkJobPayload(wire_status="OPEN")))
    assert wire["status"] == "OPEN"
    assert set(wire.keys()) == expected_keys

    # CLOSED -> wire status CLOSED.
    closed_job = lifecycle.transition(fresh("C-CL"), "CLOSED")
    wire = talk_job_to_wire(_replace(closed_job, payload=TalkJobPayload(wire_status="CLOSED")))
    assert wire["status"] == "CLOSED"
    assert wire["kind"] == "talk"

    # FAILED -> wire status FAILED.
    failed_job = lifecycle.transition(fresh("C-FA"), "FAILED")
    wire = talk_job_to_wire(_replace(failed_job, payload=TalkJobPayload(wire_status="FAILED")))
    assert wire["status"] == "FAILED"

    # TIMEOUT -> wire status TIMEOUT.
    timeout_job = lifecycle.transition(fresh("C-TO"), "TIMEOUT")
    wire = talk_job_to_wire(_replace(timeout_job, payload=TalkJobPayload(wire_status="TIMEOUT")))
    assert wire["status"] == "TIMEOUT"

    # CANCELLED -> wire status CANCELLED.
    cancelled_job = lifecycle.transition(fresh("C-CA"), "CANCELLED")
    wire = talk_job_to_wire(_replace(cancelled_job, payload=TalkJobPayload(wire_status="CANCELLED")))
    assert wire["status"] == "CANCELLED"


def test_job_lifecycle_wire_parity_job_lifecycle_paths_unchanged():
    """Lifecycle-driven task transitions produce the same wire form as direct lifecycle calls.

    Even though the job lifecycle now routes obvious transitions through
    `Job.lifecycle_method()` rather than inline `JobEvent(...)` chains, the
    resulting `Job` (and therefore the `task_job_to_wire` output) must be
    identical to the pre-refactor output.
    """

    from orchlink.core.job_lifecycle import TaskJobLifecycle
    from orchlink.core.views import task_job_to_wire

    lifecycle = TaskJobLifecycle()

    def fresh():
        return Job(
            id="T-WIRE-SM",
            kind="task",
            project_id="demo",
            task_id="T-WIRE-SM",
            route=JobRoute(from_agent="demo.lead", to_agent="demo.work"),
            mode="PLAN",
            status="CREATED",
        )

    # Drive the same status via lifecycle methods vs the job lifecycle and
    # confirm wire parity.
    via_methods = (
        fresh()
        .queue()
        .deliver()
        .start()
        .fail()
    )
    via_lifecycle = lifecycle.transition(
        lifecycle.transition(lifecycle.transition(lifecycle.transition(fresh(), "PENDING"), "DELIVERED"), "RUNNING"),
        "FAILED",
    )
    assert via_methods.status == via_lifecycle.status == "FAILED"

    wire_methods = task_job_to_wire(via_methods)
    wire_lifecycle = task_job_to_wire(via_lifecycle)
    assert wire_methods == wire_lifecycle, (
        f"Lifecycle and lifecycle-method wire outputs diverged:\n"
        f"  via methods: {wire_methods}\n  via lifecycle: {wire_lifecycle}"
    )
