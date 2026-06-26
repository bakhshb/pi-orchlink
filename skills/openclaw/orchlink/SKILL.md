---
name: orchlink
description: Use this skill whenever OpenClaw should coordinate with a local Pi worker through Orchlink. OpenClaw acts as the lead: check setup and sessions, choose ask/send/talk correctly, gate reviews, read exact results, inspect activity, cancel stale work, and recover from stale broker or cross-project problems.
version: 1.0.3
metadata:
  openclaw:
    emoji: "🔗"
    requires:
      bins: ["orch"]
---

# Orchlink Lead for OpenClaw

OpenClaw is the lead agent. Pi `work` is the visible worker agent. Use Orchlink as one local lead/work loop, not as a workflow engine or multi-agent dashboard.

Use shell commands when available. If OpenClaw has no shell access, tell the human the exact `orch ...` command to run and what output to paste back.

## Command map

Human daily commands:

- `orch init` creates `.orch/project.yaml` and generated lead/work skills for a project.
- `orch lead` starts or reopens the visible Pi lead session. Do not start it unless the human wants a visible Pi lead chat.
- `orch work` starts or reopens the visible Pi worker session. OpenClaw usually needs this running.
- `orch doctor` checks project config, broker compatibility, Pi command, and generated skills.
- `orch sessions` shows registered lead/work Pi sessions. Use `orch sessions --all` for released history and `--json` only when machine-readable output helps.
- `orch jobs` browses recent and active work in the current project.
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

Debug/reference commands:

- `orch status` prints raw broker JSON. Use it only for debugging.
- `orch watch` watches raw broker events. Use it only for troubleshooting routing/activity.
- `orch task T002` shows focused route/status/activity for one task.
- `orch broker run` runs the broker in the foreground for debugging.
- `orch --help` and `orch jobs --help` are safe when command behavior is unclear.

## Startup checklist

Run these from the target project directory:

```bash
command -v orch
orch doctor
orch sessions
orch idle
```

If `command -v orch` fails, stop and tell the human to install or update Orchlink, then restart OpenClaw. For local development, suggest:

```bash
cd /home/debian/projects/orchlink
./install.sh
```

Interpret the checks this way:

- `orch doctor` must show a valid project and compatible broker. If it reports stale skills, run `orch init --refresh-skills` or follow the printed instruction.
- `orch sessions` tells you whether visible Pi sessions exist. It answers a different question than `orch idle`.
- `orch idle` only says whether active/blocking work exists. It can pass when no worker session is running.

If the project is not initialized, ask the human to run:

```bash
orch init
```

If no worker session is active, ask the human to start one in a visible terminal:

```bash
orch work --new
```

Do not start `orch lead` by default. OpenClaw can act as lead through the CLI. Start `orch lead --new` only when the human wants a visible Pi lead chat to receive worker replies or Talk messages.

## Choosing the right command

Use this decision order:

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

Good uses:

- review gates
- architectural disagreement
- bug triage that affects the lead's next step
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

Good reply-shape requests:

```text
Reply in 3 bullets max.
```

```text
Return verdict, risks, files inspected, and tests run.
```

```text
Return files changed, tests run, and remaining risks.
```

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
```

`orch jobs --active` is not the same as `orch idle`: it shows details; it is not the safety gate. Use `--limit` when long sessions make the default recent list noisy.

Use activity tools for long-running work:

```bash
orch peek T002
orch task T002
```

`peek` and `task` do not replace `wait`, `get`, or `idle`. In `jobs`, trust `STATUS` over activity text. Heartbeat activity means the worker was alive then; terminal jobs should not be treated as active because of stale activity.

## Talk Mode

For OpenClaw-as-lead, prefer `orch ask --wait` for discussion because the reply prints in the terminal.

Use Talk Mode only when a visible lead Pi session is running or the human explicitly wants a lead/work chat transcript.

```bash
orch talk work -m "one short question" -r 3
orch say C001 -m "answer or follow-up"
orch close C001 -m "Decision: ... Rationale: ... Dissent/risk accepted: ... Next step: ... Owner: ... Human approval needed: yes/no"
```

Talk Mode is a conversation, not a task order:

- no `MODE`
- no `TASK_ID`
- no scope boilerplate
- one short question or idea per turn
- close when there is a decision, blocker, timeout, max rounds, or no new value

Do not use `orch get C001` as the normal way to follow Talk. Read the visible lead chat. Use `get` for reread/debug only if needed.

## Cancellation

Cancel stale or no-longer-needed work before assigning more work:

```bash
orch cancel T002 -m "reason"
```

Cancellation marks broker work `CANCELLED` and asks Pi to abort the current turn. Future tool calls should be blocked. Already-running shell commands are best-effort and may only stop if Pi's abort reaches them.

After cancellation:

```bash
orch jobs --id T002
orch idle
```

Do not assume cancelled shell commands stopped instantly.

## Debug and recovery

Use readable checks first:

```bash
orch doctor
orch sessions
orch jobs --active
orch idle
```

Use help when unsure:

```bash
orch --help
orch jobs --help
```

Use broker health only for deeper debugging:

```bash
curl -s http://127.0.0.1:8787/health
```

Healthy broker output should include capabilities such as:

```text
project_header_scope
task_activity_endpoint
scoped_task_results
status_filters
session_leases
```

If Orchlink reports stale broker, missing capabilities, cross-project results, or confusing state, restart cleanly:

```bash
orch stop
orch work --new
```

Start `orch lead --new` too only if using a visible Pi lead chat.

Raw debug commands:

```bash
orch status --task T002 --limit 20
orch watch --iterations 1 --limit 20
orch task T002
orch broker run --host 127.0.0.1 --port 8787
```

Do not use raw debug output for normal coordination or session checks. Use `orch sessions` for sessions and `orch jobs`/`idle` for work state.

## Safety rules

- Keep OpenClaw-owned work and worker-owned work separate.
- Do not expose API keys, tokens, secrets, or private logs in prompts.
- Do not ask the worker to edit outside the allowed scope.
- Do not run dependent full tests while worker work is active.
- Do not make final claims until blocking reviews and active work are resolved.
- Do not accept worker output blindly. Name the risk, disagreement, or assumption before deciding.
- If command output is stale, cross-project, or inconsistent, stop and repair instead of guessing.
