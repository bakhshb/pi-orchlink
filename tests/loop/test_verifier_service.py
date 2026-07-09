from __future__ import annotations

import asyncio

import pytest

from orchlink.loop.domain import (
    LoopAttempt,
    LoopItem,
    MakerResult,
    ReasonCode,
    Verdict,
    VerifierMismatch,
    WorkerAssignment,
    Worktree,
)
from orchlink.loop.services import (
    VerdictParseError,
    VerifierDispatchError,
    VerifierHandle,
    VerifierService,
    VerifierTimeoutError,
    WorkerGatewayUnavailable,
)


def attempt(maker="maker", verifier="review"):
    return LoopAttempt(
        number=1,
        maker=WorkerAssignment(worker_name=maker, task_id="T-maker"),
        verifier=WorkerAssignment(worker_name=verifier, task_id="T-review"),
        maker_result=MakerResult("implemented changes"),
    )


def item():
    return LoopItem(item_id="I-1", title="Add loop verification")


def test_build_prompt_is_deterministic_snapshot():
    service = VerifierService({})
    prompt = service.build_prompt(item(), attempt(), Worktree("/tmp/wt"))

    assert prompt == (
        "# Orchlink Loop Verifier\n"
        "ITEM_ID: I-1\n"
        "ATTEMPT: 1\n"
        "MAKER_WORKER: maker\n"
        "VERIFIER_WORKER: review\n"
        "OBJECTIVE: Add loop verification\n"
        "WORKTREE: /tmp/wt\n"
        "FILES_CHANGED: unavailable (diff collection is handled by an adapter; no git I/O here)\n"
        "VERIFY_POLICY:\n"
        "- require_verifier: true\n"
        "- require_separate_verifier_worker: true\n"
        "- ALLOW_SAME_WORKER: false (verifier must differ from maker)\n"
        "\n"
        "Review the maker result and objective checks. LLM judgment is evidence, not proof.\n"
        "End with exactly this structured verdict block. Use exact lowercase reason codes:\n"
        "VERDICT: ACCEPTED | REJECTED | BLOCKER\n"
        "REASON: accepted | tests_failed | review_failed | objective_check_failed | blocked | policy | user_request | unknown\n"
        "DETAIL: <text>\n"
        "FIXES: <comma-separated fixes, or none>\n"
        "VERIFIER_WORKER: <worker name>"
    )
    assert prompt == service.build_prompt(item(), attempt(), Worktree("/tmp/wt"))


def test_build_prompt_includes_same_worker_override_marker():
    prompt = VerifierService({}).build_prompt(item(), attempt(maker="same", verifier="same"), None)

    assert "ALLOW_SAME_WORKER: true (explicit override required; lower confidence)" in prompt
    assert "WORKTREE: none" in prompt


@pytest.mark.parametrize(
    "text,expected,reason",
    [
        ("VERDICT: ACCEPTED\nREASON: accepted\nDETAIL: ok\nFIXES: none", Verdict.ACCEPTED, ReasonCode.ACCEPTED),
        (
            "VERDICT: REJECTED\nREASON: review_failed\nDETAIL: bad\nFIXES: test one, test two",
            Verdict.REJECTED,
            ReasonCode.REVIEW_FAILED,
        ),
        ("VERDICT: BLOCKER\nREASON: blocked\nDETAIL: waiting\nFIXES: none", Verdict.BLOCKER, ReasonCode.BLOCKED),
    ],
)
def test_parse_verdict_accepts_valid_replies(text, expected, reason):
    verdict = VerifierService({}).parse_verdict(text)

    assert verdict.verdict is expected
    assert verdict.reason_code is reason
    if expected is Verdict.REJECTED:
        assert verdict.required_fixes == ("test one", "test two")


def test_parse_verdict_missing_verdict_raises():
    with pytest.raises(VerdictParseError):
        VerifierService({}).parse_verdict("REASON: accepted\nDETAIL: ok")


def test_parse_verdict_unknown_reason_raises():
    with pytest.raises(VerdictParseError):
        VerifierService({}).parse_verdict("VERDICT: ACCEPTED\nREASON: made_up\nDETAIL: ok")


def test_parse_verdict_rejected_missing_reason_raises():
    with pytest.raises(VerdictParseError):
        VerifierService({}).parse_verdict("VERDICT: REJECTED\nDETAIL: bad\nFIXES: fix")


def test_parse_verdict_ignores_template_before_final_verdict():
    text = (
        "VERDICT: ACCEPTED | REJECTED | BLOCKER\n"
        "REASON: <reason_code>\n"
        "DETAIL: <text>\n"
        "VERDICT: ACCEPTED\n"
        "REASON: accepted\n"
        "DETAIL: ok\n"
        "FIXES: none\n"
    )
    assert VerifierService({}).parse_verdict(text).verdict is Verdict.ACCEPTED


def test_parse_verdict_uses_last_verdict_when_changed_mind():
    text = (
        "VERDICT: ACCEPTED\n"
        "REASON: accepted\n"
        "DETAIL: first\n"
        "VERDICT: REJECTED\n"
        "REASON: review_failed\n"
        "DETAIL: changed mind\n"
        "FIXES: add test\n"
    )
    parsed = VerifierService({}).parse_verdict(text)
    assert parsed.verdict is Verdict.REJECTED
    assert parsed.detail == "changed mind"
    assert parsed.required_fixes == ("add test",)


def test_parse_verdict_handles_trailing_whitespace_and_extra_lines():
    text = "VERDICT: BLOCKER   \nREASON: blocked   \nDETAIL: waiting   \nFIXES: none   \nextra text\n"
    parsed = VerifierService({}).parse_verdict(text)
    assert parsed.verdict is Verdict.BLOCKER
    assert parsed.reason_code is ReasonCode.BLOCKED
    assert parsed.detail == "waiting"


def test_errors_are_reexported_from_exceptions_module():
    from orchlink.loop.services.exceptions import VerdictParseError as ReexportedVerdictParseError

    assert ReexportedVerdictParseError is VerdictParseError


def test_validate_separation_default_and_override():
    service = VerifierService({})
    with pytest.raises(VerifierMismatch):
        service.validate_separation("same", "same")
    service.validate_separation("same", "same", allow_same_worker=True)
    service.validate_separation("maker", "review")


class FakeGateway:
    def __init__(self, *, result_text=None, dispatch_error=None, timeout=False, asyncio_timeout=False):
        self.result_text = result_text or "VERDICT: ACCEPTED\nREASON: accepted\nDETAIL: ok\nFIXES: none"
        self.dispatch_error = dispatch_error
        self.timeout = timeout
        self.asyncio_timeout = asyncio_timeout
        self.dispatched = []
        self.awaited = []

    async def dispatch_verifier(self, verifier_assignment, prompt):
        if self.dispatch_error:
            raise self.dispatch_error
        self.dispatched.append((verifier_assignment, prompt))
        return VerifierHandle(task_id="V-1", worker_name=verifier_assignment.worker_name)

    async def await_result(self, handle, timeout_seconds):
        if self.asyncio_timeout:
            raise asyncio.TimeoutError("async too slow")
        if self.timeout:
            raise TimeoutError("too slow")
        self.awaited.append((handle, timeout_seconds))
        return MakerResult(self.result_text)


class SpyVerifierService(VerifierService):
    def __init__(self, config, gateway):
        super().__init__(config, gateway)
        self.build_calls = []

    def build_prompt(self, item_arg, attempt_arg, worktree_arg):
        self.build_calls.append((item_arg, attempt_arg, worktree_arg))
        return super().build_prompt(item_arg, attempt_arg, worktree_arg)


def test_dispatch_and_collect_uses_gateway_and_parses_result():
    gateway = FakeGateway(result_text="VERDICT: REJECTED\nREASON: review_failed\nDETAIL: nope\nFIXES: add test")
    service = SpyVerifierService({}, gateway)
    loop_item = item()
    loop_attempt = attempt()
    worktree = Worktree("/tmp/wt")

    result = asyncio.run(service.dispatch_and_collect(loop_item, loop_attempt, worktree=worktree, timeout_seconds=12))

    assert service.build_calls == [(loop_item, loop_attempt, worktree)]
    assert gateway.dispatched[0][0] == loop_attempt.verifier
    assert "ITEM_ID: I-1" in gateway.dispatched[0][1]
    assert gateway.awaited == [(VerifierHandle(task_id="V-1", worker_name="review"), 12)]
    assert result.verdict is Verdict.REJECTED
    assert result.reason_code is ReasonCode.REVIEW_FAILED
    assert result.required_fixes == ("add test",)


def test_dispatch_and_collect_timeout_raises():
    service = VerifierService({}, FakeGateway(timeout=True))

    with pytest.raises(VerifierTimeoutError):
        asyncio.run(service.dispatch_and_collect(item(), attempt()))


def test_dispatch_and_collect_asyncio_timeout_raises_verifier_timeout():
    service = VerifierService({}, FakeGateway(asyncio_timeout=True))

    with pytest.raises(VerifierTimeoutError):
        asyncio.run(service.dispatch_and_collect(item(), attempt()))


def test_dispatch_and_collect_dispatch_timeout_raises_verifier_timeout():
    service = VerifierService({}, FakeGateway(dispatch_error=TimeoutError("dispatch too slow")))

    with pytest.raises(VerifierTimeoutError):
        asyncio.run(service.dispatch_and_collect(item(), attempt()))


def test_dispatch_and_collect_dispatch_error_raises():
    service = VerifierService({}, FakeGateway(dispatch_error=RuntimeError("boom")))

    with pytest.raises(VerifierDispatchError):
        asyncio.run(service.dispatch_and_collect(item(), attempt()))


def test_dispatch_and_collect_parse_error_propagates():
    service = VerifierService({}, FakeGateway(result_text="malformed"))

    with pytest.raises(VerdictParseError):
        asyncio.run(service.dispatch_and_collect(item(), attempt()))


def test_dispatch_and_collect_without_gateway_raises_before_parse(monkeypatch):
    service = VerifierService({}, None)

    def fail_parse(_text):
        raise AssertionError("parse_verdict should not be called")

    monkeypatch.setattr(service, "parse_verdict", fail_parse)
    with pytest.raises(WorkerGatewayUnavailable):
        asyncio.run(service.dispatch_and_collect(item(), attempt()))
