# Orchlink lead command reference

Use this file for command details after the lead skill's quick chooser says Orchlink coordination is needed.

## Command map

Human daily commands:

- `orch init` creates `.orch/project.yaml`, generated lead/work skills, and reference files for a project.
- `orch lead` starts or reopens the visible Pi lead session.
- `orch work` starts or reopens the default visible Pi worker named `work`. Use `orch work --name review` for another configless named worker, `orch work --background --name bg-test --new` for an isolated headless test worker, or `orch work --background --test` as the shortcut. Use `--model` and `--thinking` to pin a worker session's Pi model/default thinking; Orchlink validates model availability with `pi --list-models` before launching.
- `orch doctor` checks project config, broker compatibility, Pi command, and generated skills.
- `orch sessions` shows registered lead and named worker Pi sessions with worker name, model, reported thinking, runtime, backend, ready state, and lease heartbeat. Use `orch sessions --name review`, `--all`, or `--json` when useful.
- `orch jobs` browses recent and active work in the current project.
- `orch goal ...` runs PRD/plan-driven Goal Mode from source to verified completion. Read `goal-mode.md` before using it.
- `orch stop` stops this project's tracked default background worker and leaves the shared broker running. Use `orch stop --name bg-test` for a named worker, or `orch stop --broker`/`--all` only when no other project needs that broker.
- `orch update` updates Orchlink. Treat it as a human/operator command unless the human asks you to update.

Lead coordination commands:

- `orch ask work --wait -t T001 -m "..."` sends a blocking task to a named worker (`work` by default; e.g. `orch ask review ...`). Use it for reviews, decisions, discussions, and any answer that changes your next action. Use `--edit` or `--message-file` for long/shell-sensitive prompts. Add `--thinking` only when one task needs an explicit thinking override. `orch ask --no-wait` exists, but prefer `orch send` for async work so intent is obvious.
- `orch send work -t T002 -m "..."` sends async work to one named worker only when you can safely work on a different scope while Pi works. Different worker names can run independent tasks; the same name remains single-flight. Use `--edit` or `--message-file` for long/shell-sensitive prompts. Add `--thinking` only when one task needs an explicit thinking override.
- `orch wait T002` waits for one exact task result. A wait timeout does not cancel the task.
- `orch get T002` rereads a completed task result. Use `wait` or `get` routinely, not both.
- `orch idle` is the safety gate. Run it before dependent tests, final conclusions, or assigning more worker work.
- `orch peek T002` shows recent activity for long-running work. It does not return the final result.
- `orch cancel T002 -m "reason"` cancels stale or no-longer-needed work before assigning something else. Read `recovery.md` for cancellation details.
- `orch talk work -m "one short question" -r 6` starts Talk Mode with the worker. Use `--edit` or `--message-file` for long/shell-sensitive messages.
- `orch say C001 -m "answer or follow-up"` sends the next Talk turn. Use `--edit` or `--message-file` for long/shell-sensitive messages.
- `orch close C001 -m "Decision: ..."` closes Talk with a decision record. Use `--edit` or `--message-file` for long/shell-sensitive messages.

Debug/reference commands:

- `orch status` prints raw broker JSON. Use it only for debugging.
- `orch watch` watches raw broker events. Use it only for troubleshooting routing/activity.
- `orch task T002` shows focused route/status/activity for one task.
- `orch broker run` runs the broker in the foreground for debugging.
- `orch --help` and `orch jobs --help` are safe when command behavior is unclear.

## Startup and session checks

Use readable checks first:

```bash
orch doctor
orch sessions
orch idle
```

If the user asks whether lead/work sessions exist, use `orch sessions`, `orch sessions --name <worker>`, or `orch sessions --all` before raw `orch status` or ad hoc JSON parsing.

If Orchlink reports stale broker, missing capabilities, cross-project results, or confusing state, restart cleanly:

```bash
orch stop --all
orch lead --new
orch work --new
```

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

Good uses:

- review gates
- architectural disagreement
- bug triage that affects your next step
- deciding whether to run full tests
- unclear user requests where worker may return `BLOCKER`

Do not proceed past a review gate until the exact task result returns.

## Async work with `send`

Use `send` only when the worker has an independent scope and you can work elsewhere.

```bash
orch send work -t TDO001 -m "Add one parser edge case. Edit parser.py and tests/test_parser.py only; do not touch docs or unrelated files. Implementation is allowed. Run python3 -m pytest tests/test_parser.py -v and reply with files changed, tests run, and remaining risks."
orch jobs --active
orch wait TDO001
```

Rules:

- Each named worker is single-flight. Do not stack worker tasks on the same name.
- Named workers preserve their own Pi context. Use `--new` only when a fresh context is intended.
- Do not send a dependent task while another task or Talk conversation is active.
- If you no longer need active work, cancel it before assigning new work.
- Do not use async REVIEW as a gate. If a review is unrelated and truly non-blocking, use `orch send --allow-async-review`, then verify the exact result with `orch wait TREV001` before using it.

## Reading results safely

For blocking work, read the worker reply injected into the lead chat or the exact `orch ask --wait` output if using an external shell.

For async work, use one result command routinely:

```bash
orch wait T002
# or, if it already completed:
orch get T002
```

Use `get` later only to reread or debug a completed result. If both CLI output and the lead chat show the same result, treat matching task/project IDs as one result.

Trust only the exact task ID in the current project. Orchlink refuses cross-project or unscoped task results. If you see a stale-broker or cross-project warning, stop and repair before continuing.

## Jobs, idle, and activity

Use `orch idle` as the gate:

```bash
orch idle
```

Exit code `0` means no active/blocking worker work. Exit code `1` means do not run dependent tests, final conclusions, or new worker assignments yet.

Use `jobs` to inspect work:

```bash
orch jobs
orch jobs --active
orch jobs --status DONE
orch jobs --kind task
orch jobs --kind talk
orch jobs --id T002
orch jobs --limit 20
orch jobs --json
orch jobs --name review
```

`orch jobs --active` is not the same as `orch idle`: it shows details; it is not the safety gate. Use `--name` for one worker and `--limit` when long sessions make the default recent list noisy.

Use activity tools for long-running work:

```bash
orch peek T002
orch peek --name review
orch task T002
```

`peek` and `task` do not replace `wait`, `get`, or `idle`. In `jobs`, trust `STATUS` over activity text. Heartbeat activity means the worker was alive then; terminal jobs should not be treated as active because of stale activity.

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

Do not use `orch get C001` as the normal way to follow Talk. Read the worker reply in this lead chat. Use `get` for reread/debug only if needed.

When reporting a Talk outcome to the human, preserve the substance of the discussion. A good Talk summary should be natural prose, not a mandatory template, but it should account for the main options considered, the strongest reasons on each side, any disagreement or uncertainty, the practical decision, and the next action. For deep discussions, do not summarize only the final turn.
