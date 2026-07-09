"""Verifier prompt, verdict parsing, and gateway orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol

from orchlink.loop.domain.errors import VerifierMismatch
from orchlink.loop.domain.item import LoopAttempt, LoopItem, MakerResult, WorkerAssignment
from orchlink.loop.domain.verdict import ReasonCode, Verdict, VerifierVerdict
from orchlink.loop.domain.worktree import Worktree


class VerdictParseError(ValueError):
    """Raised when a verifier reply does not contain a valid structured verdict."""


class VerifierDispatchError(RuntimeError):
    """Raised when verifier dispatch fails before a result is available."""


class VerifierTimeoutError(TimeoutError):
    """Raised when waiting for the verifier result times out."""


class WorkerGatewayUnavailable(RuntimeError):
    """Raised when dispatch is requested without a WorkerGateway."""


@dataclass(frozen=True, slots=True)
class VerifierHandle:
    task_id: str
    worker_name: str


class WorkerGateway(Protocol):
    async def dispatch_verifier(self, verifier_assignment: WorkerAssignment, prompt: str) -> VerifierHandle:
        ...

    async def await_result(self, handle: VerifierHandle, timeout_seconds: int) -> MakerResult:
        ...


class VerifierService:
    def __init__(self, config: dict[str, Any] | None, gateway: WorkerGateway | None = None) -> None:
        self.config = dict(config or {})
        self.gateway = gateway

    def build_prompt(self, item: LoopItem, attempt: LoopAttempt, worktree: Worktree | None) -> str:
        objective = item.title or item.source or item.item_id
        verifier_worker = attempt.verifier.worker_name if attempt.verifier else "<unassigned>"
        same_worker = attempt.verifier is not None and attempt.verifier.same_worker(attempt.maker)
        separation = (
            "ALLOW_SAME_WORKER: true (explicit override required; lower confidence)"
            if same_worker
            else "ALLOW_SAME_WORKER: false (verifier must differ from maker)"
        )
        worktree_line = "WORKTREE: none"
        files_line = "FILES_CHANGED: unavailable (no worktree provided; no git I/O in verifier service)"
        if worktree is not None:
            worktree_line = f"WORKTREE: {worktree.path}"
            files_line = "FILES_CHANGED: unavailable (diff collection is handled by an adapter; no git I/O here)"
        policy = item.verify_policy
        return "\n".join(
            [
                "# Orchlink Loop Verifier",
                f"ITEM_ID: {item.item_id}",
                f"ATTEMPT: {attempt.number}",
                f"MAKER_WORKER: {attempt.maker.worker_name}",
                f"VERIFIER_WORKER: {verifier_worker}",
                f"OBJECTIVE: {objective}",
                worktree_line,
                files_line,
                "VERIFY_POLICY:",
                f"- require_verifier: {str(policy.require_verifier).lower()}",
                f"- require_separate_verifier_worker: {str(policy.require_separate_verifier_worker).lower()}",
                f"- {separation}",
                "",
                "Review the maker result and objective checks. LLM judgment is evidence, not proof.",
                "End with exactly this structured verdict block. Use exact lowercase reason codes:",
                "VERDICT: ACCEPTED | REJECTED | BLOCKER",
                "REASON: accepted | tests_failed | review_failed | objective_check_failed | blocked | policy | user_request | unknown",
                "DETAIL: <text>",
                "FIXES: <comma-separated fixes, or none>",
                "VERIFIER_WORKER: <worker name>",
            ]
        )

    def parse_verdict(self, text: str) -> VerifierVerdict:
        fields = self._structured_fields(text)
        raw_verdict = fields.get("VERDICT")
        if not raw_verdict:
            raise VerdictParseError("missing VERDICT line")
        try:
            verdict = Verdict(raw_verdict.strip().lower())
        except ValueError as exc:
            raise VerdictParseError(f"unknown verdict: {raw_verdict}") from exc

        raw_reason = fields.get("REASON", "").strip()
        if verdict is Verdict.REJECTED and not raw_reason:
            raise VerdictParseError("REASON is required for REJECTED verdicts")
        if not raw_reason:
            raw_reason = ReasonCode.UNKNOWN.value
        try:
            reason = ReasonCode(raw_reason.lower())
        except ValueError as exc:
            raise VerdictParseError(f"unknown reason code: {raw_reason}") from exc

        detail = fields.get("DETAIL", "").strip()
        fixes = tuple(
            part.strip()
            for part in fields.get("FIXES", "").split(",")
            if part.strip() and part.strip().lower() != "none"
        )
        verifier_worker = fields.get("VERIFIER_WORKER", "verifier").strip() or "verifier"
        task_id = fields.get("TASK_ID", "").strip() or None
        return VerifierVerdict(
            verdict=verdict,
            reason_code=reason,
            detail=detail,
            required_fixes=fixes,
            verifier_worker=verifier_worker,
            task_id=task_id,
        )

    async def dispatch_and_collect(
        self,
        item: LoopItem,
        attempt: LoopAttempt,
        *,
        worktree: Worktree | None = None,
        timeout_seconds: int = 1800,
    ) -> VerifierVerdict:
        if self.gateway is None:
            raise WorkerGatewayUnavailable("VerifierService requires a WorkerGateway to dispatch verifier work")
        if attempt.verifier is None:
            raise VerifierDispatchError("attempt has no verifier assignment")
        prompt = self.build_prompt(item, attempt, worktree)
        try:
            handle = await self.gateway.dispatch_verifier(attempt.verifier, prompt)
            try:
                result = await self.gateway.await_result(handle, timeout_seconds)
            except (asyncio.TimeoutError, TimeoutError) as exc:
                raise VerifierTimeoutError(str(exc) or "verifier timed out") from exc
            except Exception as exc:
                raise VerifierDispatchError(str(exc)) from exc
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise VerifierTimeoutError(str(exc) or "verifier timed out") from exc
        except VerifierDispatchError:
            raise
        except Exception as exc:
            raise VerifierDispatchError(str(exc)) from exc
        return self.parse_verdict(result.result)

    def validate_separation(
        self,
        maker_worker: str,
        verifier_worker: str,
        *,
        allow_same_worker: bool = False,
    ) -> None:
        if maker_worker == verifier_worker and not allow_same_worker:
            raise VerifierMismatch("verifier worker must differ from maker worker")

    def _structured_fields(self, text: str) -> dict[str, str]:
        lines = text.splitlines()
        verdict_index: int | None = None
        for index in range(len(lines) - 1, -1, -1):
            key, value = self._split_structured_line(lines[index])
            if key == "VERDICT":
                verdict_index = index
                break
        if verdict_index is None:
            return {}

        fields: dict[str, str] = {}
        key, value = self._split_structured_line(lines[verdict_index])
        fields[key] = value
        for line in lines[verdict_index + 1 :]:
            key, value = self._split_structured_line(line)
            if key in {"REASON", "DETAIL", "FIXES", "VERIFIER_WORKER", "TASK_ID"}:
                fields[key] = value
        return fields

    def _split_structured_line(self, line: str) -> tuple[str, str]:
        if ":" not in line:
            return "", ""
        key, value = line.split(":", 1)
        return key.strip().upper(), value.strip()
