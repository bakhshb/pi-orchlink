# Orchlink Goal Mode reference

Use Goal Mode when the user wants a PRD, plan, or inline goal implemented until acceptance criteria are verified.

Goal Mode creates durable artifacts under `.orch/goals/Gxxx/`, derives or accepts acceptance criteria, gates approval, dispatches bounded worker slices, records evidence, and refuses to call the whole goal done until objective checks or human signoff are complete.

## Canonical flows

From a PRD:

```bash
orch goal start "Implement feature" --prd docs/feature-prd.md --derive
orch goal review G001
orch goal gate G001 approve
orch goal work G001 --until done --max-steps 20
orch goal show G001
```

From a plan file:

```bash
orch goal start "Implement feature" --plan docs/feature-plan.md --derive
```

From inline text:

```bash
orch goal start "Small cleanup" --text "Refactor export validation with tests" --derive
```

If the plan is only in the current chat/context, first write it to a normal markdown file such as `.orch/goals/inbox/feature-plan.md`, then use `--plan`:

```bash
mkdir -p .orch/goals/inbox
# write .orch/goals/inbox/feature-plan.md from the conversation plan
orch goal start "Implement feature" --plan .orch/goals/inbox/feature-plan.md --derive
```

Do not use or invent chat-plan CLI flags such as `--from-chat`, `--plan-id`, `--latest-plan`, or `mark-plan`.

## Goal state layout

Goal Mode writes durable state under `.orch/goals/Gxxx/`:

```text
source.md      captured PRD/plan/text source
acceptance.md  editable ACs with status/checks/dependencies
plan.md        editable execution plan
coverage.md    optional source/AC/plan coverage report
goal.yaml      goal status, evidence, blockers, deferrals
history.jsonl  append-only goal events
audit.md       optional audit result
trials.jsonl   optional real-PRD trial records
```

If `acceptance.md`, `plan.md`, or `goal.yaml` look vague, stop and improve the artifacts before approving the gate. Goal Mode is only as good as the acceptance criteria and plan it is allowed to run.

## Review and gate

Before approval:

```bash
orch goal review G001
```

Review:

- `acceptance.md`
- `plan.md`
- `coverage.md`, if present
- low-confidence or invented AC warnings
- uncovered source or plan warnings
- objective checks for unattended verification

Approve only after the artifacts are concrete enough to verify:

```bash
orch goal gate G001 approve
```

Reject with a note if the artifacts need more work:

```bash
orch goal gate G001 reject --note "ACs are too vague; add concrete checks for export formats."
```

## Work loop

Run bounded work until done, gated, blocked, cancelled, or capped:

```bash
orch goal work G001 --until done --max-steps 20
```

Rules:

- The worker can finish a task, but only the goal runner closes the goal.
- Objective core ACs should have check commands for unattended verification.
- Subjective core ACs stop at a signoff gate.
- Non-core blockers may be deferred; core blockers stop the goal.
- Do not claim completion until `orch goal show G001` gives evidence or the goal is gated/blocked with a specific reason.

## Worker orchestration inside goals

Goal Mode is not permission for the lead to implement everything alone. For every non-trivial AC or plan step, decide whether a named Pi worker should own a bounded implementation, verification, review, audit, or challenge slice. Prefer worker use when it reduces risk or safely parallelizes work, especially for architecture changes, broad refactors, installer/broker/goal-mode changes, release-impacting work, or independent test verification. Keep work lead-owned only when it is tiny, tightly coupled to the lead's current edit, or unclear enough to need a human answer first.

When dispatching goal-related worker work:

- Split scopes clearly: lead owns one slice, worker owns another.
- Include the goal ID, relevant AC IDs, allowed files, forbidden files, and checks the worker may run.
- Use `orch send --wait` for review gates, architecture decisions, blockers, or anything that can change the next step.
- Use `orch send` only for independent implementation or verification slices while the lead can work elsewhere.
- After async dispatch, use `orch jobs --active` and `orch jobs --live <task_id>` for progress; do not blind-wait with timeout loops.
- Reconcile worker evidence before marking an AC verified. Do not accept worker output blindly.

Before marking a risky or subjective AC complete, consider whether independent worker evidence is needed. For release-impacting, architectural, or broad cleanup ACs, worker review or audit should be treated as mandatory unless there is a clear reason it would add no value.

## Signoff

Subjective core ACs need human signoff:

```bash
orch goal signoff G001 AC-4
orch goal signoff G001 --all
```

Use `--all` only when every pending subjective AC has genuinely been reviewed. Signoff should be treated as evidence, not a shortcut around missing checks.

## Audit

Use audit for a non-editing artifact/evidence review:

```bash
orch goal audit G001
```

Audit must not mark the goal done. Reconcile audit findings before final claims.

## Trials

Record real PRD trial outcomes:

```bash
orch goal trial G001 --baseline 12 --outcome done --caught-gap AC-3 --deferrals 2 --evidence-quality good
orch goal trials G001
```

Trials are for measuring whether Goal Mode catches gaps and avoids premature done claims.
