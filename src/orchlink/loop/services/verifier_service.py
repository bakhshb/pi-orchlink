"""Verifier prompt, verdict parsing, and gateway orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from orchlink.loop.domain.errors import VerifierMismatch
from orchlink.loop.domain.item import LoopAttempt, LoopItem
from orchlink.loop.domain.verdict import ReasonCode, Verdict, VerifierVerdict, parse_verdict_text
from orchlink.loop.domain.worktree import Worktree
from orchlink.loop.ports import WorktreeEvidence, WorktreeEvidencePort, WorkerGateway
from orchlink.loop.services.objective_check_service import CheckReport, ObjectiveCheckService


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


class VerifierService:
    def __init__(
        self,
        config: dict[str, Any] | None,
        gateway: WorkerGateway | None = None,
        evidence_collector: WorktreeEvidencePort | None = None,
    ) -> None:
        self.config = dict(config or {})
        self.gateway = gateway
        self.evidence_collector = evidence_collector

    def build_prompt(
        self,
        item: LoopItem,
        attempt: LoopAttempt,
        worktree: Worktree | None,
        check_report: CheckReport | None = None,
        *,
        changed_files: Sequence[str] | None = None,
        diff_evidence: str | None = None,
        evidence_unavailable_reason: str | None = None,
    ) -> str:
        objective = item.objective or item.title or item.source or item.item_id
        verifier_worker = attempt.verifier.worker_name if attempt.verifier else "<unassigned>"
        same_worker = attempt.verifier is not None and attempt.verifier.same_worker(attempt.maker)
        separation = (
            "ALLOW_SAME_WORKER: true (explicit override required; lower confidence)"
            if same_worker
            else "ALLOW_SAME_WORKER: false (verifier must differ from maker)"
        )
        worktree_line = "WORKTREE: none"
        unavailable_reason = evidence_unavailable_reason or "no worktree provided"
        files_line = f"FILES_CHANGED: unavailable ({unavailable_reason})"
        if worktree is not None:
            worktree_line = f"WORKTREE: {worktree.path}"
            files_line = f"FILES_CHANGED: unavailable ({evidence_unavailable_reason or 'diff collection was not provided'})"
        if changed_files is not None:
            files_line = "FILES_CHANGED:\n" + ("\n".join(f"- {path}" for path in changed_files) if changed_files else "none")
        diff_line = f"DIFF_EVIDENCE: unavailable ({unavailable_reason})"
        if worktree is not None:
            diff_line = f"DIFF_EVIDENCE: unavailable ({evidence_unavailable_reason or 'diff collection was not provided'})"
        if diff_evidence is not None:
            evidence = diff_evidence.strip() or "none"
            diff_line = "DIFF_EVIDENCE:\n" + _truncate_prompt_evidence(evidence) + "\nEND_DIFF_EVIDENCE"
        maker_result = (
            attempt.maker_result.result.strip()
            if attempt.maker_result is not None and attempt.maker_result.result.strip()
            else "unavailable (no maker result attached to attempt)"
        )
        check_lines = ["OBJECTIVE_CHECK_REPORT: unavailable (objective checks were not run)"]
        if check_report is not None:
            check_lines = [
                check_report.prompt_section(),
                "If a required objective check failed, your verdict MUST be REJECTED with reason checks_failed.",
            ]
        policy = item.verify_policy
        return "\n".join(
            [
                "# Orchlink Loop Verifier",
                f"ITEM_ID: {item.item_id}",
                f"ATTEMPT: {attempt.number}",
                f"MAKER_WORKER: {attempt.maker.worker_name}",
                f"VERIFIER_WORKER: {verifier_worker}",
                f"OBJECTIVE: {objective}",
                f"SOURCE_REF: {item.source or 'none'}",
                f"SOURCE_URL: {item.source_url or 'none'}",
                "MAKER_RESULT:",
                maker_result,
                "END_MAKER_RESULT",
                worktree_line,
                files_line,
                diff_line,
                *check_lines,
                "VERIFY_POLICY:",
                f"- require_verifier: {str(policy.require_verifier).lower()}",
                f"- require_separate_verifier_worker: {str(policy.require_separate_verifier_worker).lower()}",
                f"- {separation}",
                "",
                "Review the maker result and objective checks. LLM judgment is evidence, not proof.",
                "End with exactly this structured verdict block. Use exact lowercase reason codes:",
                "VERDICT: ACCEPTED | REJECTED | BLOCKER",
                "REASON: accepted | checks_failed | tests_failed | review_failed | objective_check_failed | blocked | policy | user_request | unknown",
                "DETAIL: <text>",
                "FIXES: <comma-separated fixes, or none>",
                "VERIFIER_WORKER: <worker name>",
            ]
        )

    def parse_verdict(self, text: str) -> VerifierVerdict:
        try:
            return parse_verdict_text(text)
        except ValueError as exc:
            raise VerdictParseError(str(exc)) from exc

    async def dispatch_and_collect(
        self,
        item: LoopItem,
        attempt: LoopAttempt,
        *,
        worktree: Worktree | None = None,
        timeout_seconds: int = 1800,
        run_checks: bool = False,
        check_service: ObjectiveCheckService | None = None,
    ) -> VerifierVerdict:
        if self.gateway is None:
            raise WorkerGatewayUnavailable("VerifierService requires a WorkerGateway to dispatch verifier work")
        if attempt.verifier is None:
            raise VerifierDispatchError("attempt has no verifier assignment")
        if attempt.maker_result is None or not attempt.maker_result.result.strip():
            raise VerifierDispatchError("attempt has no maker result to verify")
        check_report = check_service.run_checks(Path(worktree.path) if worktree is not None else None) if run_checks and check_service is not None else None
        evidence = (
            WorktreeEvidence(unavailable_reason="no evidence collector configured")
            if self.evidence_collector is None
            else self.evidence_collector.collect(worktree)
        )
        prompt = self.build_prompt(
            item,
            attempt,
            worktree,
            check_report=check_report,
            changed_files=evidence.changed_files,
            diff_evidence=evidence.diff_evidence,
            evidence_unavailable_reason=evidence.unavailable_reason,
        )
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
        verdict = self.parse_verdict(result.result)
        return self._apply_objective_check_override(verdict, check_report)

    def _apply_objective_check_override(
        self,
        verdict: VerifierVerdict,
        check_report: CheckReport | None,
    ) -> VerifierVerdict:
        if check_report is None or not check_report.any_required_failed:
            return verdict
        original = f"Original LLM verdict: {verdict.verdict.value}; reason={verdict.reason_code.value}; detail={verdict.detail}"
        detail = f"Required objective checks failed. {original}"
        return VerifierVerdict(
            verdict=Verdict.REJECTED,
            reason_code=ReasonCode.OBJECTIVE_CHECK_FAILED,
            detail=detail,
            required_fixes=verdict.required_fixes,
            verifier_worker=verdict.verifier_worker,
            task_id=verdict.task_id,
        )

    def validate_separation(
        self,
        maker_worker: str,
        verifier_worker: str,
        *,
        allow_same_worker: bool = False,
    ) -> None:
        if maker_worker == verifier_worker and not allow_same_worker:
            raise VerifierMismatch("verifier worker must differ from maker worker")


def _truncate_prompt_evidence(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...<truncated>"
