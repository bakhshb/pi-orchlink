# Orchlink core coordination reference

Use this reference for OpenClaw-as-lead coordination details.

## Command map

Human daily commands:

- `orch init` creates `.orch/project.yaml` and generated lead/work skills for a project.
- `orch lead` starts or reopens the visible Pi lead session. OpenClaw usually does not need this.
- `orch work` starts or reopens the default visible Pi worker named `work`. Use `orch work --name review` for another configless named worker, `orch work --background --name bg-test --new` for an isolated headless test worker, or `orch work --background --test` as the shortcut. Use `--model` and `--thinking` to pin a worker session's Pi model/default thinking; Orchlink validates model availability with `pi --list-models` before launching.
- `orch doctor` checks project config, broker compatibility, Pi command, and generated skills.
- `orch sessions` shows registered lead and named worker Pi sessions with worker name, model, reported thinking, runtime, backend, ready state, and lease heartbeat. Use `orch sessions --name review`, `--all`, or `--json` when useful.
- `orch jobs` browses recent and active work in the current project.
- `orch goal ...` runs PRD/plan-driven Goal Mode from source to verified completion. Read `goal-mode.md` before using it.
- `orch stop` stops this project's tracked default background worker and leaves the shared broker running. Use `orch stop --name bg-test` for a named worker, or `orch stop --broker`/`--all` only when no other project needs that broker.
- `orch update` updates Orchlink. Treat it as a human/operator command unless the human asks you to update.

Lead coordination commands:

- `orch ask work --wait -t T001 -m "..."` sends a blocking task to the named worker `work`. Replace `work` with another active worker name such as `review` or `bg-test` when intentionally targeting that worker. Use `--edit` or `--message-file` for long/shell-sensitive prompts. Use `--thinking` only when one task needs an explicit thinking override.
- `orch send work -t T002 -m "..."` sends async work to one named worker only when you can safely work on a different scope while Pi works. Different names can run independent tasks; the same name remains single-flight. Use `--edit` or `--message-file` for long/shell-sensitive prompts. Use `--thinking` only when one task needs an explicit thinking override.
- `orch wait T002` waits for one exact task result. A wait timeout does not cancel the task.
- `orch get T002` rereads a completed task result. Use `wait` or `get` routinely, not both.
- `orch idle` is the safety gate. Run it before dependent tests, final conclusions, or assigning more worker work.
- `orch peek T002` shows recent activity for long-running work. It does not return the final result.
- `orch cancel T002 -m "reason"` cancels stale or no-longer-needed work before assigning something else.
- `orch talk`, `orch say`, and `orch close` manage Talk Mode only when a visible lead Pi chat is part of the workflow.

## Starting worker sessions

If `orch sessions` shows no active `work` session, this is a mandatory branch before any Orchlink task:

1. Background worker, recommended for external agents: run `orch work --background`. It starts the headless Pi RPC worker named `work`, waits for readiness, and returns.
2. Visible worker terminal, if background start fails or the human prefers it: ask the human to run `orch work --new` in a separate terminal. Visible terminals are more reliable for long sessions.

For background smoke while a visible `work` terminal is already open, do not replace it. Use `orch work --background --name bg-test --new` or `orch work --background --test`, then target that worker explicitly with `orch ask bg-test ...` and stop it with `orch stop --name bg-test`.

If `orch work --background` fails, inspect `.orch/run/orch-work.log` (or `.orch/run/workers/<name>/orch-work.log` for named workers) and fall back to the visible-terminal option. If neither option is available, stop and tell the human Orchlink cannot proceed yet. Do not hide a failed background start, and do not silently substitute native subagents for named Pi workers.

## Choosing the right command

1. Need a review, decision, critique, plan, or blocker answer before you continue? Use `orch ask work --wait` or target a specific named worker, e.g. `orch ask review --wait`.
2. Need worker implementation while you can work on a separate scope? Use `orch send <name>` like spawn: start it, stay responsive, and retrieve the result later with `orch get` or `orch wait` only when needed.
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

For long prompts or text containing backticks, `$VARS`, or quotes, prefer editor or file input:

```bash
orch ask work --wait -t TREV001 --edit
orch ask work --wait -t TREV001 --message-file .orch/prompts/review.md
```

Do not proceed past a review gate until the exact task result returns.

## Async work with `send`

Use `send` like spawn: start independent worker work, keep helping the human, and retrieve the result later. Do not immediately run `orch wait` unless the result blocks your next action.

Progress discipline:

- After every async `orch send`, run `orch jobs --active` unless you are immediately doing independent lead-owned work.
- Do not use shell sleep, repeated timeout waits, or blind `orch wait --timeout ...` as the primary progress check.
- If the task remains active or may be slow, run `orch peek <task_id>` or `orch task <task_id>` before deciding it is stuck, blocked, or done.
- Use `orch wait <task_id>` only when you intentionally want to block for the final result; use `orch get <task_id>` when `jobs` shows the task is already terminal.
- Run `orch idle` before final claims or dependent full tests.

Rules:

- After sending, record the task ID and stay responsive to unrelated human questions.
- Do not stack tasks on the same worker name. Each named worker is single-flight, while different names can run independent scoped work.
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

Use `orch jobs --active` to inspect active work, especially immediately after async dispatch and before dependent decisions. Use `orch peek T002` or `orch task T002` for long-running work before judging it stuck, blocked, or done. These do not replace `wait`, `get`, or `idle`.

Do not inspect `ps`, PID lists, broker URLs, raw HTTP endpoints, or `curl` output for normal coordination. Use raw broker checks only when `orch doctor`, `orch sessions`, `orch jobs`, or `orch idle` are stale or confusing, then read `recovery.md` first.

## Talk Mode

For OpenClaw-as-lead, prefer `orch ask --wait` for discussion because the reply prints in the terminal.

Use Talk Mode only when a visible lead Pi session is running or the human explicitly wants a lead/work chat transcript.

```bash
orch talk work -m "one short question" -r 3
orch say C001 -m "answer or follow-up"
orch close C001 -m "Decision: ... Rationale: ... Dissent/risk accepted: ... Next step: ... Owner: ... Human approval needed: yes/no"
# Use --edit or --message-file for long/shell-sensitive Talk messages.
```

Talk Mode is a conversation, not a task order: no `MODE`, no `TASK_ID`, no scope boilerplate, one short question or idea per turn.
