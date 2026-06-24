---
name: orchlink
description: Use this skill whenever OpenClaw should coordinate with a local Pi worker through Orchlink. This is for acting as the lead agent: start/check the worker lane, send scoped tasks, wait for results, run review gates, inspect worker activity, handle blockers, and avoid cross-project/stale-broker mistakes.
version: 1.0.2
metadata:
  openclaw:
    emoji: "🔗"
    requires:
      bins: ["orch"]
---

# Orchlink Lead for OpenClaw

You are the lead agent using Orchlink to coordinate with a visible local Pi worker session. Treat Orchlink as a project-local worker lane, not as a dashboard or database.

Use shell commands when available. If you do not have shell access, tell the human the exact `orch ...` command to run and what output to paste back.

## Mental model

- OpenClaw is the lead brain.
- Pi `work` is the visible worker agent.
- The local broker usually runs at `http://127.0.0.1:8787` and can serve multiple projects.
- Current-project isolation comes from `.orch/project.yaml` and the `project_id` sent by the CLI.
- `T001` means task ID. Use it with `orch wait`, `orch get`, `orch peek`, and `orch task`.
- `C001` means Talk conversation ID. Use it with `orch say` and `orch close` only when a visible lead Pi session is part of the workflow.

## Before using Orchlink

First verify the Orchlink CLI exists. Do this before any other `orch` command so you do not dead-end halfway through the workflow:

```bash
command -v orch
```

If this prints nothing or fails, stop. Tell the human: "Orchlink CLI is not installed or not on PATH. Install/update Orchlink first, then restart this OpenClaw session." If the human is developing this repo locally, suggest:

```bash
cd /home/debian/projects/orchlink
./install.sh
```

From the target project directory, check setup:

```bash
orch doctor
orch idle
```

If `orch doctor` reports stale skills, missing project config, missing Pi, or incompatible broker, follow its instruction. A stale broker can leak old cross-project state; do not continue until fixed.

If no worker session is running, ask the human to start it in a visible terminal:

```bash
orch work --new
```

Do not start `orch lead` unless the human explicitly wants a visible Pi lead chat. OpenClaw can act as lead through the CLI.

## Preferred command patterns

Use blocking ask for gates and decisions:

```bash
orch ask work --wait -t T001 -m "MODE: REVIEW. TASK_ID: T001. ..."
```

Use async send only when you can safely work on a different scope:

```bash
orch send work -t T002 -m "MODE: DO. TASK_ID: T002. ..."
orch peek T002
orch wait T002
orch get T002
```

Check worker lane before dependent work:

```bash
orch idle
```

Cancel stale or no-longer-needed broker work before assigning new work:

```bash
orch cancel T002 -m "reason"
```

Cancellation marks broker work `CANCELLED` and asks Pi to abort the current turn. It may stop before the next tool call. A shell command already running inside Pi may only stop if Pi's abort reaches it.

## Review gates

Treat `MODE: REVIEW` as a gate when the answer can change your next action.

Use:

```bash
orch ask work --wait -t TREV001 -m "MODE: REVIEW. TASK_ID: TREV001. ..."
```

Do not use async review unless the review is unrelated and you will not act on it until it returns. If async review is explicitly allowed, verify the exact task ID before using the result:

```bash
orch wait TREV001
orch get TREV001
```

## Task message checklist

Every `orch ask` or `orch send` message should include:

- `MODE: DISCUSS | PLAN | DO | REVIEW`
- `TASK_ID: ...`
- current context
- exact worker scope
- forbidden scope
- whether edits are allowed
- expected reply shape
- tests/checks the worker may run

Keep worker scopes narrow. Prefer file/path limits. Do not ask the worker to inspect the whole repository unless necessary.

## Reply handling

For blocking work, read the JSON from `orch ask --wait`.

For async work, use:

```bash
orch wait T002
orch get T002
```

Trust only results matching the current project and exact task ID. Current Orchlink refuses cross-project/unscoped results; if you see that warning, stop and restart the broker/sessions before continuing.

If the worker returns `BLOCKER` or asks a direct question, answer it before proceeding. Do not ignore worker questions.

## Talk Mode guidance

For OpenClaw-as-lead, prefer `orch ask --wait` for discussion because replies are visible in the CLI.

Use Talk Mode only when a visible lead Pi session is running or the human explicitly wants a lead/work chat transcript:

```bash
orch talk work -m "one short question" -r 3
orch get C001
orch say C001 -m "answer or follow-up"
orch close C001 -m "Decision: ... Rationale: ... Dissent/risk accepted: ... Next step: ... Owner: ... Human approval needed: yes/no"
orch get C001
```

`orch get C001` prints the full Talk transcript. Talk Mode is a conversation, not a task order. No `TASK_ID`, no `MODE`, no scope boilerplate.

## Worker result expectations

Ask the worker to use concise structured replies:

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

For unclear or broad work, prefer a `BLOCKER` with one concrete question over guessing.

## Safety rules

- The worker lane is single-flight. Do not stack worker tasks.
- Run `orch idle` before dependent tests, final conclusions, or assigning more worker work.
- Keep lead and worker scopes separate.
- Do not expose API keys or secrets in prompts, outputs, or logs.
- Do not rely on `orch jobs` alone for final results; use `orch wait` or `orch get`.
- If a command reports stale broker or cross-project result, stop and repair before continuing.

## Quick recovery

If Orchlink looks confused, run:

```bash
orch doctor
curl -s http://127.0.0.1:8787/health
orch idle
```

Expected broker health includes capabilities like:

```text
project_header_scope
task_activity_endpoint
scoped_task_results
status_filters
```

If stale/incompatible:

```bash
orch stop
orch lead --new   # only if using visible Pi lead
orch work --new
```
