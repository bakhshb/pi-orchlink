from __future__ import annotations

from orchlink.goal.worker_reply import WorkerBlockerType, WorkerReplyKind, compact_worker_result, parse_worker_reply


def test_parse_worker_reply_result_summary_and_compact_shape() -> None:
    result = {
        "status": "completed",
        "task_id": "T001",
        "reply": {"type": "RESULT", "payload": {"summary": "done"}},
    }

    parsed = parse_worker_reply(result)

    assert parsed.kind is WorkerReplyKind.RESULT
    assert parsed.summary == "done"
    assert parsed.blocker is None
    assert compact_worker_result(result) == {"status": "completed", "task_id": "T001", "reply_type": "RESULT", "summary": "done"}


def test_parse_worker_reply_typed_blocker_preserves_detail() -> None:
    result = {
        "reply": {
            "type": "BLOCKER",
            "payload": {
                "summary": "need decision",
                "blocker": {"type": "decision", "message": "choose path", "extra": "kept"},
            },
        }
    }

    parsed = parse_worker_reply(result, task_id="T002", criterion_id="AC-1")

    assert parsed.kind is WorkerReplyKind.BLOCKER
    assert parsed.blocker is not None
    assert parsed.blocker.type == WorkerBlockerType.DECISION.value
    assert parsed.blocker.task_id == "T002"
    assert parsed.blocker.criterion_id == "AC-1"
    assert parsed.blocker.detail == {"extra": "kept"}


def test_parse_worker_reply_blocker_type_falls_back_to_summary_label() -> None:
    result = {"reply": {"type": "BLOCKER", "payload": {"summary": "Blocker Type: upstream\nWaiting on API"}}}

    parsed = parse_worker_reply(result, task_id="T003")

    assert parsed.blocker is not None
    assert parsed.blocker.type == WorkerBlockerType.UPSTREAM.value
    assert parsed.blocker.message.startswith("Blocker Type: upstream")


def test_parse_worker_reply_unknown_types_fall_back_safely() -> None:
    parsed = parse_worker_reply({"reply": {"type": "MYSTERY", "payload": {"stdout": "ok"}}})

    assert parsed.kind is WorkerReplyKind.RESULT
    assert parsed.summary == "ok"
