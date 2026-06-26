# Orchlink Improvement Plan

## Guiding principle

Keep Orchlink simple:

```text
visible Pi sessions are the product surface
broker is local coordination
CLI should be small, predictable, and agent-friendly
skills should carry behavior policy before adding CLI complexity
```

## Phase 1: Skill and behavior cleanup first

Start with skill/docs improvements before adding more CLI surface.

### 1. External agent skill guidance

Update the OpenClaw and Hermes skills so external agents use Orchlink consistently.

Default workflow:

```bash
orch ask work --wait ...
```

Rules:

- Check setup first:
  ```bash
  command -v orch
  orch doctor
  orch idle
  ```
- If no visible worker is running, ask the human to start:
  ```bash
  orch work --new
  ```
- Do not silently start `orch work` inside the external agent unless the human explicitly asks.
- Use `orch wait T001` **or** `orch get T001`, not both. Use `get` later only to reread a completed result.
- Use `peek` only for long-running work. Short tasks may finish before activity is useful.
- Prefer `ask --wait` for external-agent workflows.
- Use Talk Mode only when the human explicitly wants visible lead/work discussion.
- The lead decides the reply shape. Do not force a universal template unless needed.

### 2. Pi lead skill guidance

Update generated `.orch/skills/lead.md`:

- The lead specifies the desired reply shape per task.
- Do not request full structured output every time.
- Do not call both `wait` and `get` routinely.
- Use Talk Mode for visible discussion, not automation.
- Use `ask --wait` for review gates and synchronous decisions.
- Use `idle` before dependent final actions.

Examples:

```text
For quick checks, ask for 3 bullets max.
For reviews, ask for verdict, risks, files inspected, tests run.
For implementation, ask for files changed, tests run, remaining risks.
```

### 3. Pi worker skill guidance

Update generated `.orch/skills/work.md`:

```text
Follow the lead's requested reply shape.
If no reply shape is requested, use a concise default.
```

Suggested default when no shape is given:

```text
summary:
changed/inspected:
tests:
risks/blockers:
next:
```

For Talk Mode:

- Reply naturally like a teammate.
- No template.
- No labels required.
- No task boilerplate.

For task modes, keep mode semantics:

- `PLAN`: propose; no edits.
- `REVIEW`: inspect/report; no edits unless allowed.
- `DO`: implement inside scope.
- `DISCUSS`: reason/recommend.

Do not force a large universal output template.

## Phase 2: Make `orch jobs` the main work browser

Keep `orch jobs` broad by default, but add filters.

### 4. Add filtered jobs view

Current:

```bash
orch jobs --limit 50
```

Add:

```bash
orch jobs --active
orch jobs --status STATUS
orch jobs --kind task
orch jobs --kind talk
orch jobs --id T001
orch jobs --id C001
orch jobs --json
```

Default behavior:

```bash
orch jobs
```

shows everything recent.

`--active` shows only blocking/open/running work.

Blocking statuses:

```text
PENDING
QUEUED
DELIVERED
RUNNING
IN_PROGRESS
OPEN
```

Kind mapping:

```text
task = job with task_id
talk = job with conversation_id and no task_id
```

### 5. Improve jobs output

Current output is useful but gets cluttered.

Target output:

```text
ID      KIND  MODE    STATUS      UPDATED        ROUTE              PREVIEW
T012    task  REVIEW  DELIVERED   12s ago        demo.lead → work   Review...
C003    talk  TALK    OPEN        1m ago         demo.lead → work   Should we...
T011    task  DO      DONE        4m ago         demo.lead → work   Implement...
```

Show last activity when present:

```text
  last activity: [12:03:44] tool_call read: src/foo.py
```

Optional later grouping:

```text
ACTIVE
...

RECENT DONE
...
```

### 6. Keep `idle` for now

Do not remove `orch idle` yet.

It has a useful exit-code contract:

```text
exit 0 = idle
exit 1 = active/blocking work exists
```

Later, if `jobs` supports check mode:

```bash
orch jobs --active --check
```

then `idle` can become a wrapper or alias.

### 7. Keep `task` for now

`orch task T001` currently shows focused route/activity/event status.

Do not remove it until:

```bash
orch jobs --id T001
```

can match or exceed its usefulness.

Later options:

- keep `task` as alias to `jobs --id`
- deprecate `task` only if `jobs --id` becomes better

### 8. Keep `status` as debug-only

`orch status` prints raw JSON and supports broker debugging:

```bash
orch status --task T001
orch status --all-projects
orch status --since-id 120
```

Keep it, but skills/docs should not encourage normal agents to use it.

Maybe later move it under:

```bash
orch broker status
```

## Phase 3: Talk Mode UX cleanup

Talk Mode should feel like visible teammate chat, not a protocol-rendered task.

### 9. Keep Talk messages plain

Worker receives:

```text
[Orchlink Talk] lead · C001 · 1/6

actual lead message
```

Lead receives:

```text
[Orchlink] work · C001 · 2/6

actual worker reply
```

No injected guidance:

```text
Next:
Worker says:
Guidance:
Conversation ID:
```

### 10. Validate Talk in real sessions

Manual test:

```bash
orch lead --new
orch work --new
orch talk work -m "One short opinion question." -r 3
```

Expected:

- Worker receives only short Talk header plus lead message.
- Worker reply is captured.
- Lead receives only short Talk header plus worker reply.
- `orch say` works.
- `orch close` works.
- No stuck delivered messages.

Regression to protect:

```text
plain Talk prompts must still be tracked as worker turns
```

### 11. Talk Mode remains visible-discussion-first

Keep docs/skills clear:

```text
Talk Mode is for visible discussion.
For scripts/external agents, prefer ask --wait.
```

Do not add full transcript dumping by default.

`orch get C001` stays preview/summary only.

If a full transcript is needed later, add an explicit command/flag:

```bash
orch transcript C001
# or
orch get C001 --full
```

## Phase 4: Result structure flexibility

### 12. Lead decides result shape

Current task prompts are too biased toward a full fixed template.

New direction:

- Orchlink defines mode behavior.
- Lead defines desired reply shape.
- Worker follows the lead’s requested shape.
- If no shape is requested, worker uses a concise default.

Examples:

```text
Reply in 3 bullets max.
```

```text
Return only JSON with keys: verdict, risks, tests.
```

```text
Use a review checklist with proceed/blocker/retry.
```

### 13. Keep minimal machine-readable type carefully

The extension can detect result type from:

```text
TYPE: PLAN | RESULT | BLOCKER
```

If missing, it defaults to `RESULT`.

Plan:

```text
For task replies, prefer starting with TYPE: PLAN | RESULT | BLOCKER.
Then follow the lead's requested reply shape.
If the lead requested no shape, be concise.
```

This preserves protocol clarity without forcing verbose output.

## Phase 5: Cancellation clarity

### 14. Verify steering cancellation

Expected behavior:

- `orch cancel T001` marks broker work `CANCELLED`.
- The extension notices cancellation.
- The extension sends a steering message to Pi:
  ```text
  [Orchlink] T001 is CANCELLED. Stop this work now...
  ```
- The extension aborts if the Pi context supports abort.
- Future tool calls are blocked.

Needed drills:

1. Cancel queued task.
2. Cancel delivered task before tool call.
3. Cancel during normal assistant response.
4. Cancel during long-running shell command.

Document behavior honestly:

```text
Already-running shell commands are best-effort.
Future tool calls are blocked.
Broker state cancels immediately.
```

Do not claim stronger interruption until measured.

## Phase 6: Duplicate result clarity

### 15. Reduce confusion through skills first

A result can appear through multiple channels:

```text
orch wait
orch get
visible lead chat injection
```

This is expected when visible lead is running.

Skill guidance:

```text
If using visible Pi lead, read the injected chat.
If using external agent, use ask --wait or wait/get.
Do not use both wait and get unless rereading/debugging.
```

Avoid code changes unless duplication remains a major problem.

Possible later CLI hint:

```text
Note: this result may also be delivered to visible lead chat.
```

## Phase 7: Public docs cleanup

### 16. README command guidance

Update README to reflect the refined model:

- `jobs`: main work browser.
- `jobs --active`: active/open work after filters are implemented.
- `idle`: script/check idle state.
- `task`: focused task activity until `jobs --id` replaces it.
- `status`: raw debug JSON.
- `peek`: long-task activity only.
- Talk: visible discussion.
- `ask --wait`: external/synchronous result.

### 17. Adapter skill docs

After jobs filters exist, update OpenClaw/Hermes skills:

- Use `jobs --active` when available.
- Do not overuse `peek`.
- Do not use Talk for automation.
- Lead chooses reply shape.
- Use `wait` or `get`, not both.

## Recommended execution order

### Step 1: Skill updates only

Update:

- generated lead skill
- generated worker skill
- OpenClaw skill
- Hermes skill
- README minor guidance

No CLI risk.

### Step 2: Real Talk smoke test

Validate the latest plain Talk behavior in visible Pi sessions.

### Step 3: Add `orch jobs` filters

Implement:

```bash
orch jobs --active
orch jobs --status STATUS
orch jobs --kind task|talk
orch jobs --id ID
orch jobs --json
```

Keep existing commands.

### Step 4: Update skills to prefer filtered jobs

After jobs filters exist.

### Step 5: Re-evaluate command surface

After actual usage:

- keep `idle` if exit code is useful
- keep or alias `task`
- keep `status` debug-only
- do not remove commands prematurely

### Step 6: Cancellation drill

Run measured manual tests and document behavior.

## What not to do now

Do not:

- remove `idle` yet
- remove `task` yet
- remove `status` yet
- add full transcript dumping
- add model selection unless real need returns
- auto-start `orch work` from external agents
- overbuild dashboard/state DB
- make Talk Mode automation-first

Keep the product visible, local, and simple.
