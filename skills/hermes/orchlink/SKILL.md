---
name: orchlink
description: Use this skill whenever Hermes should act as the lead agent for a local Pi worker through Orchlink. It covers sending scoped worker tasks, blocking review gates, async wait/get flows, worker activity checks, blockers, project scoping, stale broker recovery, and safe cancellation semantics.
version: 1.0.1
platforms: [linux, macos]
metadata:
  hermes:
    tags: [coding, orchestration, agents, cli]
    category: coding
    requires_toolsets: [terminal]
---

# Orchlink Lead for Hermes

You are the lead agent using Orchlink to coordinate with a visible local Pi worker session. Use Orchlink when a second local coding agent should inspect, review, test, implement, or challenge a scoped slice of work.

Use terminal commands when available. If the current Hermes surface does not provide terminal access, tell the human exactly which `orch ...` command to run and what output to return.

## Mental model

- Hermes is the lead brain.
- Pi `work` is the visible worker agent.
- Orchlink's local broker usually runs at `http://127.0.0.1:8787`.
- Multiple projects can share one broker; isolation comes from `.orch/project.yaml` and the current `project_id`.
- A task ID like `T001` is read with `orch wait` or `orch get` for results, and `orch jobs --id`, `orch peek`, or `orch task` for status/activity.
- A conversation ID like `C001` is for Talk Mode and is best used when a visible Pi lead chat exists.

## Start/check workflow

First verify the Orchlink CLI exists. Do this before any other `orch` command so you do not dead-end halfway through the workflow:

```bash
command -v orch
```

If this prints nothing or fails, stop. Tell the human: "Orchlink CLI is not installed or not on PATH. Install/update Orchlink first, then restart this Hermes session." If the human is developing this repo locally, suggest:

```bash
cd /home/debian/projects/orchlink
./install.sh
```

From the target project directory:

```bash
orch doctor
orch idle
```

If `orch doctor` reports stale skills, missing project config, missing Pi, or incompatible broker, follow its instruction before continuing.

If the worker is not running, ask the human to start a visible worker session:

```bash
orch work --new
```

Do not start `orch lead` by default. Hermes can be the lead through CLI commands. Start `orch lead --new` only when the human wants a visible Pi lead chat to receive injected replies.

If `orch doctor` or any command reports a stale/incompatible broker, stop and restart sessions before trusting results:

```bash
orch stop
orch work --new
```

Add `orch lead --new` only if using a visible Pi lead session.

## Best commands for Hermes-as-lead

Use blocking ask for reviews, decisions, and discussions that affect your next action:

```bash
orch ask work --wait -t T001 -m "MODE: REVIEW. TASK_ID: T001. ..."
```

Use async send for independent worker work:

```bash
orch send work -t T002 -m "MODE: DO. TASK_ID: T002. ..."
orch jobs --active
orch wait T002
```

Use `orch peek T002` only for long-running work where heartbeat/tool activity would help. Use `orch get T002` later only to reread or debug a completed result.

Check the lane before dependent work:

```bash
orch idle
```

Cancel stale/no-longer-needed work:

```bash
orch cancel T002 -m "reason"
```

Cancellation marks broker work `CANCELLED` immediately and asks Pi to abort the current turn. Future tool calls should be blocked. Already-running shell commands are best-effort and may only stop if Pi's abort reaches them.

## Prefer ask/wait over Talk Mode unless needed

Because Hermes is not a visible Pi lead chat, prefer:

```bash
orch ask work --wait ...
```

for discussion, critique, and review. This prints the worker answer directly in the terminal.

Use Talk Mode only if the human explicitly wants a lead/work conversation or has started `orch lead --new`:

```bash
orch talk work -m "one short question" -r 3
orch say C001 -m "answer or follow-up"
orch close C001 -m "Decision: ... Rationale: ... Dissent/risk accepted: ... Next step: ... Owner: ... Human approval needed: yes/no"
```

Talk Mode messages should be short and conversational: no required template, no `MODE`, no `TASK_ID`, no task boilerplate.

## Review gates

Treat review as a gate if it can change your next action.

Use:

```bash
orch ask work --wait -t TREV001 -m "MODE: REVIEW. TASK_ID: TREV001. ..."
```

Do not use `orch send --allow-async-review` unless the review is unrelated and you will not act on it until it returns. If async review is explicitly allowed, verify exact task identity before proceeding:

```bash
orch wait TREV001
# or: orch get TREV001, if it already completed
```

## Task prompt checklist

Every `orch ask` or `orch send` task should include:

- `MODE: DISCUSS | PLAN | DO | REVIEW`
- `TASK_ID: ...`
- current context
- exact allowed files/paths
- forbidden files/paths
- whether edits are allowed
- commands/tests the worker may run
- desired reply shape, chosen for this task

Keep scopes narrow. The worker lane is single-flight; do not stack multiple tasks.

## Expected worker replies

Choose the reply shape that fits the task. Do not force the full structured template for every request.

Examples:

```text
Reply in 3 bullets max.
```

```text
Return verdict, risks, files inspected, and tests run.
```

For task work, prefer asking the worker to start with `TYPE: PLAN | RESULT | BLOCKER` when practical, then follow your requested shape. If the request is too broad, unclear, or unsafe, the worker should return `BLOCKER` with one concrete question. Answer worker questions before moving on.

## Reading results safely

For blocking tasks, read the `orch ask --wait` output.

For async tasks, use one result command routinely:

```bash
orch wait T002
# or: orch get T002, if it already completed
```

Use `wait` or `get`, not both, unless rereading/debugging. If a visible Pi lead is running, the same result may also be injected into lead chat; that duplication is expected.

Current Orchlink refuses cross-project/unscoped results. If you see a warning about stale broker, cross-project result, or missing capabilities, stop and repair rather than trying to reason around it.

Do not rely on `orch jobs` as the final result. It is a status view. Use `orch wait` or `orch get` for task output.

In `orch jobs`, trust the job `STATUS` over activity text. Heartbeat activity means "worker was alive then"; it is shown only for active jobs and hidden after terminal jobs when stale.

## Progress and activity

Use `orch jobs` as the main work browser, with filters when useful:

```bash
orch jobs --active
orch jobs --status STATUS
orch jobs --kind task
orch jobs --kind talk
orch jobs --id T002
orch jobs --json
```

Use activity checks for long tasks:

```bash
orch peek T002
orch task T002
```

`peek` is observational only and is most useful for long-running work. It does not replace `wait`, `get`, or `idle`. Treat `orch task T002` as focused status/activity until `orch jobs --id T002` fully replaces it. Treat `orch status` as raw debug JSON, not normal coordination output.

## Safety rules

- Run `orch idle` before dependent tests, final conclusions, or new worker assignments.
- Keep Hermes-owned work and worker-owned work separate.
- Do not print or send real API keys/secrets.
- Do not ask the worker to scan the whole repo unless necessary.
- Do not ignore a `BLOCKER` or direct worker question.
- If results disagree (`jobs` vs `get`/`wait`), trust `get`/`wait` for terminal task output and inspect stale-broker warnings.

## Recovery commands

Use CLI help when command behavior is unclear:

```bash
orch --help
orch jobs --help
```

Then check setup:

```bash
orch doctor
curl -s http://127.0.0.1:8787/health
orch idle
```

Healthy broker output should include capabilities such as:

```text
project_header_scope
task_activity_endpoint
scoped_task_results
status_filters
```

If not, restart fresh:

```bash
orch stop
orch work --new
```

Start `orch lead --new` too only if using a visible Pi lead chat.
