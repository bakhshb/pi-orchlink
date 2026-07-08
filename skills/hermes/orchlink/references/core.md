# Orchlink core coordination reference

Use this reference for Hermes-as-lead coordination details.

## Command map

Human daily commands:

- `orch init` creates `.orch/project.yaml` and generated lead/work skills for a project.
- `orch lead` starts or reopens the visible Pi lead session. Hermes usually does not need this.
- `orch work` starts or reopens a visible or background named Pi worker. Use `orch work --name review` for another visible worker, `orch work --background --name bg-test --new` for an isolated headless worker, or `orch work --background --test` as the shortcut. Add `--replace` to release/fence an active session with the same name. For a fresh task-scoped background worker, use `orch work --background --new --replace --oneshot`.
- `orch doctor` checks project config, broker compatibility, Pi command, and generated skills.
- `orch sessions` shows registered lead and named worker Pi sessions, readiness, runtime, backend, model/thinking, and lease heartbeat.
- `orch jobs` is the single work console: list, active filter, idle gate, live activity, result retrieval, waiting, and cancellation.
- `orch goal ...` runs PRD/plan-driven Goal Mode from source to verified completion. Read `goal-mode.md` before using it.
- `orch stop` stops tracked background workers and, only when safe, broker processes.
- `orch update` updates Orchlink. Treat it as a human/operator command unless the human asks you to update.

Lead coordination commands:

- `orch ask work --wait -t T001 -m "..."` sends a blocking task to `work`. Replace `work` with `review` or `bg-test` when intentionally targeting that worker. Use it for short reviews, decisions, blockers, and discussion that changes your next safe action.
- `orch send work -t T002 -m "..."` sends async work to one named worker. Prefer it for long/heavy implementation, broad review, tests, or research when you can safely work on a different scope while Pi works.
- `orch jobs --active` shows currently active task and Talk jobs.
- `orch jobs --idle` is the safety gate. Exit code `0` means no active/blocking worker work; exit code `1` means wait, inspect, or cancel before dependent work.
- `orch jobs --live T002` shows recent worker activity for a long-running task or Talk conversation.
- `orch jobs --result T002` prints a completed task result or Talk summary.
- `orch jobs --wait T002` waits for one exact task result. Use it only when that result now blocks your next action; a wait timeout does not cancel the task.
- `orch jobs --cancel T002 -m "reason"` cancels stale or no-longer-needed work before assigning something else.
- `orch talk`, `orch say`, and `orch close` manage Talk Mode only when a visible lead Pi chat is part of the workflow.

Use `--edit` or `--message-file` for long/shell-sensitive prompts. Use `--thinking` only when one task needs an explicit thinking override.

## Complete top-level command inventory

This inventory is here so the lead can recognize every top-level `orch` command name before choosing a workflow. Use `orch <command> --help` for exact options.

| Command | Primary use |
| --- | --- |
| `orch init` | Create or refresh `.orch/` project config, skills, and references. |
| `orch lead` | Start/reopen the visible Pi lead session. |
| `orch work` | Start/reopen a visible or background named Pi worker. |
| `orch stop` | Stop tracked background workers and, only when safe, broker processes. |
| `orch ask` | Send blocking worker tasks for reviews, decisions, blockers, or short discussion. |
| `orch send` | Dispatch async worker tasks when the lead can stay responsive on another scope. |
| `orch jobs` | Inspect and control task/Talk jobs: list, active, idle, live, result, wait, cancel. |
| `orch sessions` | Show lead/worker sessions, runtime, readiness, and lease heartbeat. |
| `orch talk` | Start Talk Mode with a worker. |
| `orch say` | Send the next Talk Mode message. |
| `orch close` | Close Talk Mode with a decision or summary. |
| `orch doctor` | Check local project setup, broker compatibility, and generated skills. |
| `orch resume` | Show a single recovery report and recommended next action. |
| `orch update` | Update/reinstall Orchlink; treat as an operator command unless asked. |
| `orch goal` | Durable PRD/plan-driven goal tracking. Read `goal-mode.md` first. |
| `orch broker` | Broker management and raw diagnostics. |

`orch jobs <id>` inspects one job. Options include `--active`, `--idle`, `--id`, `--name`, `--status`, `--kind`, `--live`, `--result`, `--wait`, `--cancel`, `--limit`, and `--json`.

Goal subcommands are `start`, `list`, `show`, `review`, `derive`, `audit`, `trial`, `trials`, `approve`, `gate`, `work`, `resume`, `signoff`, and `cancel`. Broker subcommands are `status`, `watch`, and `run`.

## Jobs modes

- `orch jobs --idle` answers "can I proceed?" with a yes/no exit code. Use it before dependent tests, final conclusions, or assigning more worker work.
- `orch jobs <id>` inspects one job's current status and route.
- `orch jobs --active` answers "what is still busy?" Use it immediately after async dispatch and before dependent decisions.
- `orch jobs --live <id>` answers "what has the worker been doing lately?" It is progress, not the final result.
- `orch jobs --result <id>` rereads a terminal result.
- `orch jobs --wait <id>` blocks intentionally for one exact task result. Use it only when that result now gates your next action.
- `orch jobs --cancel <id> -m "reason"` cancels work you no longer need before assigning something else.
- `orch broker status` and `orch broker watch` are raw diagnostics. Use them only after normal commands are stale or confusing and after reading `recovery.md`.

If you are unsure, run `orch jobs --idle` first. If it says busy, run `orch jobs --active`; if one task is taking long, run `orch jobs --live <task_id>`.

## Starting worker sessions

If `orch sessions` shows no active `work` session, this is a mandatory branch before any Orchlink task:

1. Background worker, recommended for external agents: run `orch work --background`. It starts the headless Pi RPC worker named `work`, waits for readiness, and returns. For a fresh task-scoped background worker that exits after one completed task reply, use `orch work --background --new --replace --oneshot`; this avoids stale context or an active same-name session and ensures the worker exits cleanly after one reply.
2. Visible worker terminal, if background start fails or the human prefers it: ask the human to run `orch work --new` in a separate terminal. Visible terminals are more reliable for long sessions.

For isolated background testing while a visible `work` terminal is already open, do not replace `work`. Use `orch work --background --name bg-test --new --replace --oneshot` if `bg-test` may already exist, or `orch work --background --test` as the shortcut. Then target `bg-test` explicitly and stop it with `orch stop --name bg-test`.

If `orch work --background` fails, inspect `.orch/run/orch-work.log` (or `.orch/run/workers/<name>/orch-work.log` for named workers) and fall back to the visible-terminal option. If neither option is available, stop and tell the human Orchlink cannot proceed yet. Do not hide a failed background start, and do not silently substitute Hermes-native subagents for named Pi workers.

## Choosing the right command

1. Need a short review, decision, critique, plan, or blocker answer before you continue safely? Use `orch ask work --wait` or target a specific named worker, e.g. `orch ask review --wait`.
2. Need long/heavy implementation, broad review, tests, or research while you can work on a separate scope? Use `orch send <name>` like spawn: start it, record the task ID, stay responsive, and keep ownership until you read the exact result with `orch jobs --result` or report it pending. Use `orch jobs --wait` only when that result now blocks your next safe action. Do not use `orch ask --wait` for heavy implementation just to start work.
3. Need short peer discussion in a visible lead/work chat? Use Talk Mode: `orch talk`, `orch say`, `orch close`.
4. Need to know whether it is safe to continue? Use `orch jobs --idle`.
5. Need to inspect what is active? Use `orch jobs --active`.
6. Need final output? Prefer `orch jobs --result T002` once terminal; use `orch jobs --wait T002` only if you must block now. Do not rely on the plain jobs list as the result.
7. Need debug internals? Use `orch broker status`, `orch broker watch`, or `orch broker run` only after normal commands are insufficient.

## Blocking tasks with `ask`

Use `ask --wait` for short gate questions where the worker answer can change your next safe action. Do not use it to make long/heavy work synchronous.

```bash
orch ask work --wait -t TREV001 -m "Please review my staged parser change. Inspect parser.py and tests/test_parser.py only; do not edit. You may run python3 -m pytest tests/test_parser.py -v. Reply with verdict, risks, files inspected, tests run, and whether I can proceed."
```

For long prompts or text containing backticks, `$VARS`, or quotes, prefer editor or file input:

```bash
orch ask work --wait -t TREV001 --edit
orch ask work --wait -t TREV001 --message-file .orch/prompts/review.md
```

Do not proceed past a review gate until the exact task result returns. Do not use `ask --wait` for long/heavy implementation tasks that you could dispatch with `send` and check later.

## Async work with `send`

Use `send` like spawn: start independent worker work, keep helping the human, and retrieve the result later. Prefer this for long/heavy implementation, broad review, tests, or research. Do not immediately run a blocking wait unless the result now blocks your next action.

Async closeout invariant: `orch send` is not fire-and-forget. You own every async task ID until you read the exact result or explicitly hand the pending task back to the human.

Closeout discipline:

- After every async `orch send`, record the task ID in your working notes/plan and run `orch jobs --active` unless you are immediately doing independent Hermes-owned work.
- If your harness has a native TODO/checkpoint/reminder, you may record the task ID there as a secondary nudge. It does not replace resolving the task or handing it off.
- Do not set up cron or external timers as the Orchlink reporting mechanism.
- Do not use shell sleep, repeated timeout waits, or blind `orch jobs --wait --timeout ...` as the primary progress check.
- If the task remains active or may be slow, run `orch jobs --live <task_id>` before deciding it is stuck, blocked, or done.
- Before any human-facing completion or decision, read terminal async results with `orch jobs --result <task_id>`; use `orch jobs --wait <task_id>` only when you intentionally need to block because that result gates the next action.
- If an async task is still pending when you must stop or reply, tell the human the task ID, whether it blocks the answer, and the retrieval command.
- Do not claim dependent work is done while its async task is pending.
- Run `orch jobs --idle` before final claims or dependent full tests.

Rules:

- Stay responsive to unrelated human questions.
- Do not stack tasks on the same worker name. Each named worker is single-flight, while different names can run independent scoped work.
- Do not send a dependent task while another task or Talk conversation is active.
- If you no longer need active work, cancel it before assigning new work.
- Do not stop visible worker terminals from the lead. Stop only tracked background workers; a visible worker should be stopped by the human in its own terminal with Ctrl-C.
- Do not use async REVIEW as a gate. If a review is unrelated and truly non-blocking, use `orch send --allow-async-review`, then verify the exact result with `orch jobs --result TREV001` or `orch jobs --wait TREV001` before using it.

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

For async work, prefer reading the exact result when it is ready:

```bash
orch jobs --result T002
# or, only if the result now gates you:
orch jobs --wait T002
```

Do not use `orch jobs --idle` as a substitute for reading the exact result; idle only says no active work remains. Use `--result` later to reread or debug a completed result. If a visible Pi lead chat also receives the same result, treat matching task/project IDs as one result.

Trust only the exact task ID in the current project. Orchlink refuses cross-project or unscoped task results. If you see a stale-broker or cross-project warning, stop and repair before continuing.

## Talk Mode

For Hermes-as-lead, prefer `orch ask --wait` for discussion because the reply prints in the terminal.

Use Talk Mode only when a visible lead Pi session is running or the human explicitly wants a lead/work chat transcript.

```bash
orch talk work -m "one short question" -r 3
orch say C001 -m "answer or follow-up"
orch close C001 -m "Decision: ... Rationale: ... Dissent/risk accepted: ... Next step: ... Owner: ... Human approval needed: yes/no"
# Use --edit or --message-file for long/shell-sensitive Talk messages.
```

Talk Mode is a conversation, not a task order: no `MODE`, no `TASK_ID`, no scope boilerplate, one short question or idea per turn.
