# Orchlink Goal Mode design

## Problem

A user often starts with a PRD or implementation plan and asks an agent to build it. The agent works through local pieces, loses the larger purpose, then reports that the work is done. A later audit against the PRD shows missing requirements, partial tests, skipped edge cases, or forgotten docs. The user has to prompt again with the missing pieces.

Goal Mode should remove that back-and-forth. The user should approve the definition of done once, approve the plan once, then let Orchlink keep working until the goal is verified, blocked, cancelled, or capped.

## Product idea

Goal Mode turns a PRD into a tracked goal with acceptance criteria, a dependency graph, evidence, and verification. It does not run a generic autonomous loop. It runs a bounded PRD-to-done process over existing Orchlink lead/work primitives.

Working name:

```text
Orchlink Goal Mode
```

Possible product phrase:

```text
PRD-to-done without babysitting
```

The distinctive idea is a work-conserving goal runner. If work hits a non-core blocker, the runner defers that item and keeps working on unblocked core requirements. It interrupts the human only when a decision blocks completion.

## Goal vs task vs talk

| Surface | Purpose | Lifetime |
| --- | --- | --- |
| `orch ask work --wait` | One blocking worker task, review, or decision | One task |
| `orch send work` | One async worker task | One task |
| `orch talk` / `say` / `close` | Short lead/work discussion | One conversation |
| `orch jobs` / `idle` / `task` / `peek` | Inspect broker state and activity | Browser/safety tools |
| `orch goal ...` | Durable PRD-driven execution across many tasks, checks, gates, and retries | Many tasks until done or blocked |

Goal Mode should compose the current commands and broker features. It should not replace them.

## User experience

The user can talk naturally to the lead:

```text
Start a goal from docs/export-prd.md and keep working until done or truly blocked.
```

The lead can drive the CLI:

```bash
orch goal start "Implement export feature" --prd docs/export-prd.md
orch goal show G001
orch goal approve G001 ac
orch goal approve G001 plan
orch goal work G001 --until done
```

`work` means: advance the goal until it reaches `done`, `blocked`, `cancelled`, a cap, or a required human gate. It must not mean "run one step and stop."

Aliases may exist, but docs should teach `start`, `show`, `approve`, and `work`.

## Gates

Goal Mode should not ask the human to approve every subtask. That would preserve the current pain.

MVP gates:

1. Acceptance criteria gate
   - Defines what "done" means.
   - Runs before implementation.
   - User edits or approves the generated acceptance file.

2. Plan gate
   - Confirms the plan covers the acceptance criteria.
   - Runs before implementation.
   - The runner highlights uncovered ACs before approval.

3. Narrow final sign-off, only when needed
   - Used for subjective ACs that no command or artifact check can verify.
   - The runner should batch these at the end when possible.

No per-subtask approvals in the default flow.

## Acceptance criteria model

Goal Mode derives acceptance criteria from a PRD by default, then writes them to an editable file. Users can also provide a curated acceptance file.

Example:

```yaml
id: AC-4
text: "Export respects active filters"
type: objective
priority: core
depends_on:
  - AC-1
  - AC-2
check: "python3 -m pytest tests/export/test_filters.py -v"
source: "docs/export-prd.md#filtered-export"
confidence: high
status: pending
evidence: []
blocker: null
```

Fields:

- `id`: stable AC ID, such as `AC-1`.
- `text`: acceptance criterion.
- `type`: `objective` or `subjective`.
- `priority`: `core` or `noncore`.
- `depends_on`: other ACs needed first.
- `check`: command for objective verification when available.
- `source`: PRD section or file line reference.
- `confidence`: high, medium, low, or invented.
- `status`: pending, running, passed, failed, deferred, blocked, human-approved.
- `evidence`: command outputs, diffs, files, screenshots, notes.
- `blocker`: typed blocker when work cannot proceed.

Derived ACs should include source and confidence. Low-confidence or invented ACs should stand out in `goal show`.

## Critical path and deferral

The runner should model the goal as an AC dependency graph.

Core ACs block meaningful completion. Non-core ACs may include polish, docs, optional UX copy, or secondary improvements. The runner computes the critical path from the AC graph and updates it as ACs pass, fail, or become blocked.

Behavior:

- Work on the highest-priority unblocked core AC.
- If a non-core AC needs human input, defer it and keep working.
- If a core AC needs human input and no other unblocked core work exists, pause with one specific question.
- If a core AC is blocked but other core work can continue, record the blocker and continue.
- If only deferred non-core work remains, summarize the deferred items and ask for one batch decision.

A blocker should be typed:

```yaml
blocker:
  type: decision | asset | upstream | external | ambiguity | failed_check
  message: "Should archived records be included in filtered exports?"
  blocks:
    - AC-3
  core: true
```

The runner should not ask vague questions like "what next?" It should ask for the exact decision that unblocks completion.

## Execution loop

After the AC and plan gates pass, Goal Mode should run without babysitting:

```text
while goal is not done, blocked, cancelled, or capped:
    choose next unblocked core AC or useful non-core AC
    dispatch worker maker task
    wait for result
    record task id and result
    run objective checks
    lead verifies artifacts against ACs
    record evidence
    if gaps remain:
        create a focused gap-fix task
    if non-core blocker appears:
        defer it and continue
    if core blocker stops progress:
        pause with one specific unblock request
```

The worker must never close the goal by saying "done." The runner computes done from AC status and verification evidence.

## Roles

MVP uses the existing visible lead/work pair.

- Worker: maker
  - Implements scoped tasks.
  - Reports files changed, checks run, and risks.
  - Cannot mark the goal done.

- Lead: orchestrator and checker
  - Maintains goal state.
  - Dispatches worker tasks.
  - Verifies artifacts against ACs.
  - Runs objective checks when appropriate.
  - Escalates subjective ACs to the human.

- Human: definition-of-done owner
  - Approves ACs.
  - Approves the plan.
  - Resolves subjective criteria or true core blockers.

A third checker session is not part of MVP. Add it later only if trials show the lead checker rubber-stamps gaps that a human audit catches.

## Integration with current Orchlink foundations

Goal Mode should reuse existing infrastructure:

- Project config and project scoping.
- Lead/work visible sessions.
- Broker task routing.
- Single-flight worker lane.
- Task IDs and results.
- `wait`, `get`, `jobs`, `task`, and `peek` for visibility.
- Cancellation and hard timeouts.
- Existing review-gate safety rules.
- Activity logs.

Goal state should link to existing task IDs instead of creating a parallel task system.

Suggested module layout:

```text
src/orchlink/goal/
  models.py
  store.py
  runner.py
  prompts.py
  checks.py
```

Suggested state layout:

```text
.orch/goals/G001/
  goal.yaml
  acceptance.md
  plan.md
  history.jsonl
```

## Command surface

Recommended MVP commands:

```bash
orch goal start "Implement export feature" --prd docs/export-prd.md
orch goal list
orch goal show G001
orch goal review G001
orch goal approve G001 ac
orch goal approve G001 plan
orch goal gate G001 approve
orch goal work G001 --until done
orch goal audit G001
orch goal signoff G001 AC-4
orch goal trial G001 --baseline 12 --outcome done --caught-gap AC-3 --evidence-quality high
orch goal trials G001
orch goal resume G001
orch goal cancel G001
```

Optional lower-level or alias commands:

```bash
orch goal gate G001 ac approve
orch goal gate G001 plan reject --note "Plan misses AC-4"
orch goal run G001          # alias for work/continue, if kept
orch goal continue G001     # alias for work
```

Docs should avoid teaching `run one step` semantics. The default behavior should be work-until-gate/done/block/cap.

## Example

PRD excerpt:

```text
Build export feature:
- CSV export
- JSON export
- active filters respected
- empty results produce a valid empty file
- docs updated
- UI copy polished
```

Goal Mode derives:

```text
Core:
AC-1 CSV export works
AC-2 JSON export works
AC-3 active filters are respected
AC-4 empty result export works
AC-5 tests pass

Non-core:
AC-6 docs updated
AC-7 UI copy polished
```

During execution, worker gets blocked on AC-7 because product copy is unclear. Goal Mode records:

```text
Deferred AC-7: needs human copy decision.
Continuing AC-3: active filters respected.
```

The goal does not stop.

If all core ACs pass and only AC-7 remains, Goal Mode asks:

```text
Goal G001 is functionally complete. One deferred non-core AC needs input:
AC-7 UI copy polished.
Options:
A. Accept worker-proposed wording.
B. Provide custom wording.
C. Mark AC-7 out of scope.
```

If AC-3 is blocked by a product decision, Goal Mode pauses with a specific question:

```text
Goal G001 is blocked on core AC-3.
Decision needed: should archived records be included when filters are active?
```

## MVP non-goals

Do not ship in MVP:

- Scheduled loops.
- Third persistent checker session.
- Parallel worker tasks.
- Auto-approve or `--yes`.
- Nested goals or goal graphs across goals.
- Direct LLM client inside Orchlink core.
- Dashboard/TUI.
- Auto-merge.

## Success criteria

Goal Mode is useful if it reduces human prompting while improving PRD coverage.

Trial criteria:

- Use 3 to 5 real multi-step PRDs or plans.
- Human approves ACs and plan once each.
- Runner completes or blocks within caps.
- Runner catches at least one missing AC before the user manually audits.
- Runner defers at least one non-core blocker and continues useful work.
- Final `goal show` gives evidence for every passed AC.
- Human action count is lower than the manual baseline.

If the lead checker never fails an AC during trials, the verification path is too weak. If the runner pauses often for non-core ambiguity, the deferral scheduler is too weak.

## Design principle

Goal Mode should optimize for:

```text
minimum human interrupts
maximum verified PRD coverage
clear evidence for every done claim
```

The worker can finish a task. Only the goal runner can close the goal.

## Goal source types

Goal Mode should not require a PRD file. Users may start from a PRD, a written plan, or a short inline goal. If a plan exists only in chat/context, the lead should first write that plan to a normal markdown file, then use `--plan`. The CLI should not need special chat-plan commands.

Supported source types:

```bash
orch goal start "Feature" --prd docs/feature-prd.md
orch goal start "Feature" --plan docs/feature-plan.md
orch goal start "Feature" --text "Build CSV export with tests and docs"
```

The runner should write the captured source into the goal directory:

```text
.orch/goals/G001/
  source.md
  acceptance.md
  plan.md
  history.jsonl
```

After capture, the downstream flow is the same:

1. derive acceptance criteria from `source.md`
2. derive or confirm a plan
3. show ACs, plan, and plan-to-AC coverage in one review
4. approve once
5. work until done, blocked, cancelled, or capped

## Source-aware derivation

A PRD and a plan need different derivation prompts.

From a PRD, the worker should extract product or behavior completion criteria:

```text
What must be true for this product behavior to be complete?
```

From a plan, the worker should not treat steps as completion. It should extract outcomes:

```text
What acceptance criteria prove this implementation plan achieved its intended result?
What user-visible, testable, or reviewable outcomes should exist after these steps?
```

This avoids verifying that a step was attempted instead of verifying that the goal was achieved.

## Context plan capture

Chat history is ambiguous, and plans often get revised. Goal Mode should not guess the latest plan and should not expose special chat-plan CLI flags. When the user says "implement this plan" and the plan is only in the current conversation, the lead should first write the plan to a real file, then start the goal through the normal `--plan` path.

Preferred flow:

```text
User: Implement this plan.
Lead: writes .orch/goals/inbox/export-plan.md
Lead: orch goal start "Implement export feature" --plan .orch/goals/inbox/export-plan.md --derive
```

From that point on, the file is the source of truth, not the lead's memory of the conversation. This keeps Goal Mode's CLI source model simple and artifact-based.

Do not ship vague `--latest-plan`, `--from-chat`, or `mark-plan` behavior.

## Combined review gate

To reduce babysitting, Goal Mode may combine the AC gate and plan gate into one review gate. The gate view should show:

- source summary
- derived acceptance criteria
- source and confidence for each AC
- plan
- plan-to-AC coverage
- uncovered ACs
- low-confidence or invented ACs

Then the user approves once:

```bash
orch goal gate G001 approve
```

After that, `orch goal run G001` or `orch goal work G001` should mean work-until-done/block/cap, not one step.

MVP implements this through `orch goal work G001 --until done --max-steps N`. The cap remains mandatory to avoid unbounded unattended loops.

## Source type risks

- `.orch/goals/` is a proposed state location. Verify it fits the existing project state layout before implementation.
- Context-only plans must be written to a normal plan file before goal start. Do not add special chat-plan state or flags.
- AC derivation, planning, and checking must remain dispatched Orchlink tasks. Goal Mode should not add a direct LLM client to core.
- The lead checker must judge from artifacts and checks, not from worker self-report or old chat claims.

## Isolation and plugin-like architecture

Goal Mode should be isolated enough that a broken goal feature does not break the current Orchlink command surface. MVP should not introduce a dynamic plugin system. Instead, ship Goal Mode as a built-in, plugin-like feature module with narrow integration points.

Recommended shape:

```text
src/orchlink/goal/
  __init__.py
  cli.py
  models.py
  store.py
  runner.py
  prompts.py
  checks.py
```

Expose it as a Typer subcommand group:

```bash
orch goal ...
```

The CLI should register the goal app through a guarded or lazy import. A Goal Mode import error should degrade only `orch goal`, not `orch ask`, `orch send`, `orch talk`, `orch jobs`, `orch sessions`, or broker commands.

Minimal core touch:

```python
app.add_typer(goal_app, name="goal")
```

Guard that registration so normal Orchlink still starts if `orchlink.goal` has a bug.

## Components Goal Mode should interact with

Goal Mode should compose existing Orchlink primitives rather than modify them.

### CLI

Goal Mode adds one subcommand group under the existing Typer CLI. It should not change existing command bodies for `ask`, `send`, `talk`, `wait`, `get`, `jobs`, `idle`, or `cancel`.

### Project state

Goal Mode owns a separate state subtree:

```text
.orch/goals/G001/
  source.md
  acceptance.md
  plan.md
  goal.yaml
  history.jsonl
```

It should not change `.orch/project.yaml`, generated skills, or broker run state. A corrupt goal file should not poison project config or broker storage.

### Broker and bridge

Goal Mode should use the existing broker and bridge as a black box:

- dispatch ordinary worker tasks
- wait for exact task results
- read completed results
- inspect jobs and activity
- cancel active subtasks when the goal is cancelled

Goal subtasks should be normal Orchlink task IDs, for example:

```text
G001-PLAN
G001-WORK-AC3
G001-FIX-GAP-AC4
```

MVP should not add broker routes, broker storage tables, or new message types. It should not modify the worker lane or Talk Mode semantics.

### Pi extension

MVP should not require Pi extension changes. The existing extension already injects worker tasks and lead replies. Goal Mode can operate through normal task prompts and results.

A later Decision Panel feature may touch the Pi extension, but that should remain separate from the Goal Mode engine.

## Components Goal Mode should not touch in MVP

Do not modify these for MVP unless a narrow bug fix is required:

```text
src/orchlink/broker/
src/orchlink/broker/storage/
src/orchlink/connector/pi_extension.py
src/orchlink/project/templates/
.orch/project.yaml schema
lead/work session startup
```

This keeps Goal Mode's blast radius small.

## Plugin verdict

Goal Mode should be plugin-like, not a full dynamic plugin.

A true external plugin system would require discovery, entry points, version compatibility, failure isolation, documentation, and its own tests. That is larger than the Goal Mode MVP.

The MVP should provide plugin-level boundaries inside the package:

- own Python package
- own CLI group
- own state files
- no broker schema changes
- no project config changes
- no Pi extension requirement
- guarded CLI import

This gives most of the safety benefit without building plugin infrastructure first.

## Pi extension companion features

Goal Mode should not require Pi extension changes for its first working CLI implementation. The goal engine can operate through normal Orchlink tasks and results. However, the Pi extension is the right place for session-context and interactive-user features that make Goal Mode easier to use.

Keep this distinction:

- Goal engine: Python CLI/state/runner under `src/orchlink/goal/`.
- Pi extension: session UX and context hygiene in the visible lead/work Pi sessions.

### Phase-aware compaction

Pi already supports manual and automatic compaction. Orchlink should use that existing mechanism instead of building a new summarizer.

Add an Orchlink-aware Pi extension command later:

```text
/orch compact-phase "parser slice reviewed; tests passed; next: docs"
```

The extension should call Pi's native compaction API with focused instructions, preserving:

- completed phase summary
- review verdict
- files changed
- tests run
- current task ID
- current goal ID, if any
- scope guardrails
- unresolved blockers
- next exact step
- pointers to durable `.orch/` state files

Do not auto-compact silently. MVP should keep phase compaction manual or ask the user after a clean review:

```text
Review passed. Compact this phase now?
> Compact
  Skip
  Later
```

Only suggest phase compaction when:

- review passed or the phase is explicitly closed
- no worker task is active
- the next step is known
- important state has been written to disk

Do not compact immediately after a failed review unless the lead has reconciled the findings and recorded the next task.

### Decision Panel

A future Pi extension feature can render options as a TUI selection panel when lead or worker asks the user to choose from explicit options. This is not Goal Mode core, but Goal Mode gates and blockers can use it later.

Example:

```text
Decision needed: How should AC-7 be handled?
> Defer and continue
  Provide wording now
  Mark out of scope
  Pause goal
```

This should be a reusable Orchlink UX feature, not a goal-specific mechanism.

### Extension boundaries

Pi extension features must not change broker semantics or goal state rules. They should only improve how the user interacts with gates, decisions, and compaction.

If the extension feature fails, users must still be able to operate Goal Mode through CLI commands such as:

```bash
orch goal show G001
orch goal gate G001 approve
orch goal work G001
```
