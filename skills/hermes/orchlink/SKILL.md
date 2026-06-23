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
- A task ID like `T001` is read with `orch wait`, `orch get`, `orch peek`, or `orch task`.
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
orch peek T002
orch wait T002
orch get T002
```

Check the lane before dependent work:

```bash
orch idle
```

Cancel stale/no-longer-needed work:

```bash
orch cancel T002 -m "reason"
```

Cancellation marks broker work `CANCELLED` and asks Pi to abort the current turn. It can prevent later tool calls. A shell command already running inside Pi may only stop if Pi's abort reaches it.

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

Talk Mode messages should be short and conversational: no `MODE`, no `TASK_ID`, no task boilerplate.

## Review gates

Treat review as a gate if it can change your next action.

Use:

```bash
orch ask work --wait -t TREV001 -m "MODE: REVIEW. TASK_ID: TREV001. ..."
```

Do not use `orch send --allow-async-review` unless the review is unrelated and you will not act on it until it returns. If async review is explicitly allowed, verify exact task identity before proceeding:

```bash
orch wait TREV001
orch get TREV001
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
- expected reply structure

Keep scopes narrow. The worker lane is single-flight; do not stack multiple tasks.

## Expected worker replies

Ask for concise structured output:

```text
TYPE: PLAN | RESULT | BLOCKER
mode: PLAN | DO | REVIEW | DISCUSS
summary: ...
files inspected: ...
files changed: ...
tests run: ...
findings: ...
risks: ...
open questions: ...
recommended next step: ...
```

If the request is too broad, unclear, or unsafe, the worker should return `BLOCKER` with one concrete question. Answer worker questions before moving on.

## Reading results safely

For blocking tasks, read the `orch ask --wait` output.

For async tasks:

```bash
orch wait T002
orch get T002
```

Current Orchlink refuses cross-project/unscoped results. If you see a warning about stale broker, cross-project result, or missing capabilities, stop and repair rather than trying to reason around it.

Do not rely on `orch jobs` as the final result. It is a status view. Use `orch wait` or `orch get` for task output.

## Progress and activity

Use activity checks for long tasks:

```bash
orch peek T002
orch task T002
orch jobs
```

`peek` is observational only. It does not replace `wait`, `get`, or `idle`.

## Safety rules

- Run `orch idle` before dependent tests, final conclusions, or new worker assignments.
- Keep Hermes-owned work and worker-owned work separate.
- Do not print or send real API keys/secrets.
- Do not ask the worker to scan the whole repo unless necessary.
- Do not ignore a `BLOCKER` or direct worker question.
- If results disagree (`jobs` vs `get`/`wait`), trust `get`/`wait` for terminal task output and inspect stale-broker warnings.

## Recovery commands

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
