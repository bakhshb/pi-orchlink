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


def test_status_helpers_normalize_and_classify_broker_states():
    assert normalize_status("done") == "DONE"
    assert is_busy_status("queued") is True
    assert is_busy_status("done") is False
    assert is_terminal_status("cancelled") is True
    assert is_terminal_status("running") is False
    assert is_active_job_status("open") is True
    assert is_active_session_status("active") is True
    assert is_active_session_status("released") is False


def test_job_lifecycle_names_target_canonical_states():
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
