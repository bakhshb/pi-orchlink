# Orchlink lead command reference

Use this file for command details after the lead skill's quick chooser says Orchlink coordination is needed.

## Command map

Human daily commands:

- `orch init` creates `.orch/project.yaml`, generated lead/work skills, and reference files for a project.
- `orch lead` starts or reopens the visible Pi lead session.
- `orch work` starts or reopens a visible or background named Pi worker. Use `orch work --name review` for another visible worker, `orch work --background --name bg-test --new` for an isolated headless worker, or `orch work --background --test` as the shortcut. Add `--replace` to release/fence an active session with the same name. For a fresh task-scoped background worker, use `orch work --background --new --replace --oneshot`.
- `orch doctor` checks project config, broker compatibility, Pi command, and generated skills.
- `orch sessions` shows registered lead and named worker Pi sessions, readiness, runtime, backend, model/thinking, and lease heartbeat.
- `orch jobs` is the single work console: list, active filter, idle gate, live activity, result retrieval, waiting, and cancellation.
- `orch goal ...` runs PRD/plan-driven Goal Mode from source to verified completion. Read `goal-mode.md` before using it.
- `orch stop` stops tracked background workers and, only when safe, broker processes.
- `orch update` updates Orchlink. Treat it as a human/operator command unless the human asks you to update.

Lead coordination commands:

- `orch ask work --wait -t T001 -m "..."` sends a blocking task to a named worker (`work` by default; e.g. `orch ask review ...`). Use it for short reviews, decisions, discussions, and any answer that changes your next safe action. Use `--edit` or `--message-file` for long/shell-sensitive prompts. Add `--thinking` only when one task needs an explicit thinking override. `orch ask --no-wait` exists, but prefer `orch send` for async work so intent is obvious.
- `orch send work -t T002 -m "..."` sends async work to one named worker. Prefer it for long/heavy implementation, broad review, tests, or research when you can safely work on a different scope while Pi works. Different worker names can run independent tasks; the same name remains single-flight. Use `--edit` or `--message-file` for long/shell-sensitive prompts. Add `--thinking` only when one task needs an explicit thinking override.
- `orch jobs --active` shows currently active task and Talk jobs.
- `orch jobs --idle` is the safety gate. Run it before dependent tests, final conclusions, or assigning more worker work.
- `orch jobs --live T002` shows recent worker activity for long-running work. It does not return the final result.
- `orch jobs --result T002` prints a completed task result or Talk summary.
- `orch jobs --wait T002` waits for one exact task result. Use it only when that result now blocks your next action; a wait timeout does not cancel the task.
- `orch jobs --cancel T002 -m "reason"` cancels stale or no-longer-needed work before assigning something else. Read `recovery.md` for cancellation details.
- `orch talk work -m "one short question" -r 6` starts Talk Mode with the worker. Use `--edit` or `--message-file` for long/shell-sensitive messages.
- `orch say C001 -m "answer or follow-up"` sends the next Talk turn. Use `--edit` or `--message-file` for long/shell-sensitive messages.
- `orch close C001 -m "Decision: ..."` closes Talk with a decision record. Use `--edit` or `--message-file` for long/shell-sensitive messages.

Debug/reference commands:

- `orch broker status` prints raw broker JSON. Use it only for debugging.
- `orch broker watch` watches raw broker events. Use it only for troubleshooting routing/activity.
- `orch broker run` runs the broker in the foreground for debugging.
- `orch resume` shows one recovery report: active work, sessions, checkpoint drift, and next action.
- `orch --help` and `orch <command> --help` are safe when command behavior is unclear.

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

These commands sound similar, but answer different questions:

- `orch jobs --idle` answers "can I proceed?" with a yes/no exit code. It is intentionally boring and scriptable; trust its exit code.
- `orch jobs <id>` inspects one job's current status and route.
- `orch jobs --active` answers "what is still busy?" Use it immediately after async dispatch and before dependent decisions.
- `orch jobs --live <id>` answers "what has the worker been doing lately?" It is progress, not the final result.
- `orch jobs --result <id>` and `orch jobs --wait <id>` retrieve final results: prefer `--result` when the result is ready; use `--wait` only when you intentionally need to block because the result gates your next action.
- `orch jobs --cancel <id> -m "reason"` cancels work you no longer need before assigning something else.
- `orch broker status` is raw broker JSON for debugging only; do not use it as the normal human/agent status command.

If you are unsure, run `orch jobs --idle` first. If it says busy, run `orch jobs --active`; if one task is taking long, run `orch jobs --live <task_id>`.

## Startup and session checks

Use readable checks first:

```bash
orch doctor
orch sessions
orch jobs --idle
```

If the user asks whether lead/work sessions exist, use `orch sessions`, `orch sessions --name <worker>`, or `orch sessions --all` before raw broker checks or ad hoc JSON parsing.

If Orchlink reports stale broker, missing capabilities, cross-project results, or confusing state, restart cleanly:

```bash
orch stop --all
orch lead --new
orch work --new
```

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

Use `send` when the worker has an independent scope and you can work elsewhere. Prefer it for long/heavy implementation, broad review, tests, or research.

```bash
orch send work -t TDO001 -m "Add one parser edge case. Edit parser.py and tests/test_parser.py only; do not touch docs or unrelated files. Implementation is allowed. Run python3 -m pytest tests/test_parser.py -v and reply with files changed, tests run, and remaining risks."
orch jobs --active
orch jobs --live TDO001
# Later, when the result is ready or blocks you:
orch jobs --result TDO001  # use --wait only if the result now gates you
```

Progress discipline:

- After every async `orch send`, run `orch jobs --active` unless you are immediately doing independent lead-owned work.
- Do not use shell sleep, repeated timeout waits, or blind `orch jobs --wait --timeout ...` as the primary progress check.
- If the task remains active or may be slow, run `orch jobs --live <task_id>` before deciding it is stuck, blocked, or done.
- Use `orch jobs --result <task_id>` when `jobs` shows the task is terminal; use `orch jobs --wait <task_id>` only when you intentionally need to block because that result gates the next action.

Rules:

- Each named worker is single-flight. Do not stack worker tasks on the same name.
- Named workers preserve their own Pi context. Use `--new` only when a fresh context is intended.
- Do not send a dependent task while another task or Talk conversation is active.
- If you no longer need active work, cancel it before assigning new work.
- Do not stop visible worker terminals from the lead. Stop only tracked background workers; a visible worker should be stopped by the human in its own terminal with Ctrl-C.
- Do not use async REVIEW as a gate. If a review is unrelated and truly non-blocking, use `orch send --allow-async-review`, then verify the exact result with `orch jobs --result TREV001` or `orch jobs --wait TREV001` before using it.

## Reading results safely

For blocking work, read the worker reply injected into the lead chat or the exact `orch ask --wait` output if using an external shell.

For async work, prefer reading the exact result when it is ready:

```bash
orch jobs --result T002
# or, only if the result now gates you:
orch jobs --wait T002
```

Use `--result` later to reread or debug a completed result. If both CLI output and the lead chat show the same result, treat matching task/project IDs as one result.

Trust only the exact task ID in the current project. Orchlink refuses cross-project or unscoped task results. If you see a stale-broker or cross-project warning, stop and repair before continuing.

## Talk Mode

Talk Mode is a short peer conversation with the worker. Use it for discussion, second opinion, tradeoff analysis, or challenge. Do not use Talk as automation glue.

```bash
orch talk work -m "one short question" -r 6
orch say C001 -m "answer or follow-up"
orch close C001 -m "Decision: ... Rationale: ... Dissent/risk accepted: ... Next step: ... Owner: ... Human approval needed: yes/no"
```

Talk Mode rules:

- one short question or idea per turn
- no `MODE`
- no `TASK_ID`
- no scope boilerplate
- no expected reply checklist
- no "I will wait" line
- write like a peer, usually 1-3 sentences
- answer direct worker questions before closing
- close when there is a decision, blocker, timeout, max rounds, or no new value

Do not use `orch jobs --result C001` as the normal way to follow Talk. Read the worker reply in this lead chat. Use `--result` for reread/debug only if needed.

When reporting a Talk outcome to the human, preserve the substance of the discussion. A good Talk summary should be natural prose, not a mandatory template, but it should account for the main options considered, the strongest reasons on each side, any disagreement or uncertainty, the practical decision, and the next action. For deep discussions, do not summarize only the final turn.
