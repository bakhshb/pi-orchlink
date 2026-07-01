# Orchlink core coordination reference

Use this reference for Hermes-as-lead coordination details.

## Command map

Human daily commands:

- `orch init` creates `.orch/project.yaml` and generated lead/work skills for a project.
- `orch lead` starts or reopens the visible Pi lead session. Hermes usually does not need this.
- `orch work` starts or reopens the visible Pi worker session. Hermes usually needs this running. If no worker is active, ask whether to use a visible worker terminal or a human-approved background worker.
- `orch doctor` checks project config, broker compatibility, Pi command, and generated skills.
- `orch sessions` shows registered lead/work Pi sessions. Use `orch sessions --all` for released history and `--json` only when machine-readable output helps.
- `orch jobs` browses recent and active work in the current project.
- `orch goal ...` runs PRD/plan-driven Goal Mode from source to verified completion. Read `goal-mode.md` before using it.
- `orch stop` stops the project broker when stale or when restarting sessions.
- `orch update` updates Orchlink. Treat it as a human/operator command unless the human asks you to update.

Lead coordination commands:

- `orch ask work --wait -t T001 -m "..."` sends a blocking task. Use it for reviews, decisions, discussions, and any answer that changes your next action. `orch ask --no-wait` exists, but prefer `orch send` for async work so intent is obvious.
- `orch send work -t T002 -m "..."` sends async work only when you can safely work on a different scope while Pi works.
- `orch wait T002` waits for one exact task result. A wait timeout does not cancel the task.
- `orch get T002` rereads a completed task result. Use `wait` or `get` routinely, not both.
- `orch idle` is the safety gate. Run it before dependent tests, final conclusions, or assigning more worker work.
- `orch peek T002` shows recent activity for long-running work. It does not return the final result.
- `orch cancel T002 -m "reason"` cancels stale or no-longer-needed work before assigning something else.
- `orch talk`, `orch say`, and `orch close` manage Talk Mode only when a visible lead Pi chat is part of the workflow.

## Starting worker sessions

If `orch sessions` shows no active `work` session, offer two options and wait for the human's preference unless they already asked for one:

1. Visible worker terminal, recommended: ask the human to run `orch work --new` in a separate terminal.
2. Background worker, only with human approval and terminal access:

```bash
mkdir -p .orch/run && nohup orch work --new > .orch/run/orch-work.log 2>&1 & echo $!
orch sessions
```

If the background worker does not register, inspect `.orch/run/orch-work.log` and fall back to the visible-terminal option. Do not hide a failed background start.

## Choosing the right command

1. Need a review, decision, critique, plan, or blocker answer before you continue? Use `orch ask work --wait`.
2. Need worker implementation while you can work on a separate scope? Use `orch send`, then `orch wait` later.
3. Need short peer discussion in a visible lead/work chat? Use Talk Mode: `orch talk`, `orch say`, `orch close`.
4. Need to know whether it is safe to continue? Use `orch idle`.
5. Need to inspect what is active? Use `orch jobs --active`.
6. Need final output? Use `orch wait T002` or `orch get T002`, not `orch jobs`.
7. Need debug internals? Use `orch task`, `orch status`, `orch watch`, or `orch broker run` only after normal commands are insufficient.

## Blocking tasks with `ask`

Use `ask --wait` when the worker answer can change your next action.

```bash
orch ask work --wait -t TREV001 -m "Please review my staged parser change. Inspect parser.py and tests/test_parser.py only; do not edit. You may run python3 -m pytest tests/test_parser.py -v. Reply with verdict, risks, files inspected, tests run, and whether I can proceed."
```

Do not proceed past a review gate until the exact task result returns.

## Async work with `send`

Use `send` only when the worker has an independent scope and you can work elsewhere.

Rules:

- Do not stack tasks. The worker lane is single-flight.
- Do not send a dependent task while another task or Talk conversation is active.
- If you no longer need active work, cancel it before assigning new work.
- Do not use async REVIEW as a gate. If a review is unrelated and truly non-blocking, use `orch send --allow-async-review`, then verify the exact result with `orch wait TREV001` before using it.

## Task prompt shape

Write worker prompts in natural language. Do not force `MODE:`/`TASK_ID:` blocks or a universal checklist. The `-t` CLI option already carries the task ID; the worker can infer whether you need discussion, planning, review, or implementation from your request.

Usually include only what helps the worker act safely:

- current context
- exact allowed files, paths, or behavior
- forbidden files, paths, or behavior
- whether edits are allowed
- tests or checks the worker may run

Optional:

- desired reply shape, only when you care about the format
- whether you will wait or work on a different scope, when it affects coordination

Short, obvious tasks can be short. Risky, broad, review, or implementation tasks should include enough scope to prevent accidental edits. Do not ask the worker to scan the whole repository unless necessary.

## Worker replies and blockers

The lead chooses the reply shape. Do not force a fixed result template.

Do not require `TYPE:` labels or a fixed result schema unless you truly need them. If you do not request a shape, accept a concise answer that fits the task.

If the worker returns `BLOCKER` or asks a direct question, answer it before moving on. Do not ignore worker questions.

## Reading results safely

For blocking work, read the `orch ask --wait` output.

For async work, use one result command routinely:

```bash
orch wait T002
# or, if it already completed:
orch get T002
```

Use `get` later only to reread or debug a completed result. If a visible Pi lead chat also receives the same result, treat matching task/project IDs as one result.

Trust only the exact task ID in the current project. Orchlink refuses cross-project or unscoped task results. If you see a stale-broker or cross-project warning, stop and repair before continuing.

## Jobs, idle, and activity

Use `orch idle` as the gate. Exit code `0` means no active/blocking worker work. Exit code `1` means do not run dependent tests, final conclusions, or new worker assignments yet.

Use `orch jobs --active` to inspect active work. Use `orch peek T002` or `orch task T002` for long-running work. These do not replace `wait`, `get`, or `idle`.

## Talk Mode

For Hermes-as-lead, prefer `orch ask --wait` for discussion because the reply prints in the terminal.

Use Talk Mode only when a visible lead Pi session is running or the human explicitly wants a lead/work chat transcript.

```bash
orch talk work -m "one short question" -r 3
orch say C001 -m "answer or follow-up"
orch close C001 -m "Decision: ... Rationale: ... Dissent/risk accepted: ... Next step: ... Owner: ... Human approval needed: yes/no"
```

Talk Mode is a conversation, not a task order: no `MODE`, no `TASK_ID`, no scope boilerplate, one short question or idea per turn.
