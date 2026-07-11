# Orchlink PRD

## 1. Product thesis

Orchlink coordinates a lead agent and one or more named Pi worker sessions inside a local project. A worker name is a durable context handle; each named worker can be visible or headless, and only one active runtime may own the same name at a time.

The product is not a platform, workflow engine, cloud service, or hidden agent swarm. It is a local lead/work loop that removes copy-paste while keeping the worker path explicit and accountable.

Secondary adapter mode: an external lead agent such as OpenClaw or Hermes may use the `orch` CLI to coordinate with the Pi worker, usually through `orch work --background`. That is supported adapter usage, but the core product remains the same local lead/work loop.

```text
human → lead agent → Orchlink broker → named Pi worker session → Orchlink broker → lead agent → human
```

Product language:

- **work** is the broad user-facing term: a delegated task or a Talk conversation.
- **job** is the status row shown by `orch jobs`.
- **message/envelope/protocol** are broker internals and should appear only in architecture or reference sections.

## 2. Core technical design principles

### 2.1 Start with a canonical Job/Event model

`Job` should be the main model, not raw messages.

Two job types:

```text
TaskJob
TalkJob
```

Target-state lifecycle for the canonical model:

```text
CREATED
QUEUED
DELIVERED
RUNNING
DONE
FAILED
TIMEOUT
CANCELLED
CLOSED
```

Current protocol/status names are close but not identical; do not treat this lifecycle as fully implemented until state-model cleanup reconciles the vocabulary.

Everything else should derive from that model:

- `orch jobs`
- `orch jobs --wait`
- `orch jobs --result`
- `orch jobs --idle`
- cancellation
- timeout handling
- activity
- result retrieval

Current code works, but state is spread across active messages, tasks, conversations, results, events, activity, sessions, and waiters. Future cleanup should simplify this around one canonical job state machine and an event stream.

### 2.2 Make the broker boring and strict

The broker should only coordinate:

- validate protocol
- register sessions
- enforce single-flight per named worker
- route jobs
- track state
- record events
- handle timeout/cancel
- return results

It should never:

- think
- plan
- summarize
- make product decisions
- perform agent behavior

Agent behavior belongs in visible Pi sessions and generated lead/work skills.

### 2.3 Keep storage intentionally ephemeral for now

The current in-memory broker is acceptable for the current local two-session product.

Accepted limitation:

- broker restart loses active work and recent history
- waiters cannot survive broker process restart
- events/activity are runtime diagnostics, not a durable audit log

Near-term reliability should come from:

- visible sessions
- session leases and expiry
- `orch jobs`
- `orch jobs --idle`
- `orch jobs --cancel`
- `orch doctor`
- simple restart guidance

Durable local history is deferred unless restart recovery becomes a real product pain. Do not add storage complexity now.

## 3. Current implementation baseline

The codebase already implements:

- installable Python package with `orch = "orchlink.cli.main:app"`
- project-local `.orch/project.yaml`
- generated lead/work skill templates
- FastAPI broker
- in-memory `MessageStore` abstraction and `MemoryMessageStore`
- API-key protected broker endpoints
- project scoping through project IDs and `X-Orchlink-Project-ID`
- Pi launcher for visible sessions and headless RPC worker mode, with generated Pi extension
- lead and worker session leases, heartbeats, release/expiry, and auto-stop support
- task jobs, Talk conversations, events, activity, result retrieval, waiters, cancellation, and status views
- single-flight per named worker enforcement
- separated human, agent-coordination, and debug command surfaces
- tests for broker, protocol, memory store, CLI, events, packaging, project init, state, and request/reply behavior

This PRD describes current state plus near-term cleanup direction. It is not an old MVP backlog.

## 4. Product goals

Orchlink should answer these questions clearly:

1. Is setup valid?
2. Is the broker reachable and compatible?
3. Is the lead session running?
4. Is the worker session running?
5. Is there active worker work?
6. Can lead safely assign another worker task or Talk turn?
7. What is the latest status of work?
8. What activity is the worker performing?
9. Did the result belong to this project?
10. Was work completed, cancelled, timed out, failed, or abandoned?
11. What should lead do next?

## 5. Non-goals

Do not turn Orchlink into:

- a cloud orchestration service
- a multi-user collaboration backend
- a general workflow engine
- a dashboard-first product
- a terminal-multiplexer automation layer
- a Redis/RabbitMQ/Postgres platform
- an automatic git merge system
- a hidden autonomous agent swarm

Multiple workers, remote brokers, dashboards, durable history, and non-Pi adapters may be future work only if the simple local lead/work loop remains clear.

## 6. Command surfaces

Keep one `orch` binary. Separate commands by audience in docs, generated skills, and help text. Do not create separate binaries or namespaces unless there is a strong usability reason later.

### 6.1 Human daily commands

Humans should mostly need:

```bash
orch init      # set up .orch for this project
orch lead      # start/reopen visible lead Pi
orch work      # start/reopen visible worker named work; add --name or --background/--test for named/headless workers
orch doctor    # check setup and compatibility
orch jobs      # see recent/current work
orch stop      # stop this project's tracked background worker; use --all for broker cleanup
orch update    # update the install
```

Humans should be able to use Orchlink without learning broker API details.

### 6.2 Agent coordination commands

Lead agents use these to coordinate with worker:

```bash
orch send work --wait -t T001 -m "Please review ..."
orch send work -t T002 -m "Implement ..."
orch talk work -m "one short question" -r 6
orch say C001 -m "follow-up"
orch close C001 -m "Decision: ..."
orch jobs --result T002
orch jobs --wait T002
orch jobs --idle
orch jobs --live T002
orch jobs --cancel T002 -m "reason"
```

Rules:

- `send --wait` is for short synchronous decisions and review gates.
- Async `send` is the default for long/heavy implementation, broad review, tests, or research when lead can work on a different scope.
- `talk` is for visible discussion, not automation glue.
- `jobs --result` reads completed async output; `jobs --wait` should be used only when the result now blocks the next safe action.
- `jobs --idle` is a safety check before dependent tests, final conclusions, or new worker assignment.
- `jobs --cancel` marks broker work cancelled immediately, but process interruption is best-effort.

### 6.3 Debug/reference commands

These exist for debugging and advanced inspection:

```bash
orch broker status
orch broker watch
orch jobs T001
orch broker run
```

Normal lead/work behavior should not depend on raw broker JSON.

## 7. Runtime experience

### 7.1 Set up a project

```bash
cd /path/to/project
orch init
```

Creates:

```text
.orch/
  project.yaml
  skills/
    lead.md
    work.md
  run/
```

`orch lead` and `orch work` refresh stale generated skills before launching Pi.

### 7.2 Start worker sessions

Terminal 1:

```bash
orch lead
```

Terminal 2, visible worker:

```bash
orch work
```

Or, for an external lead/background flow:

```bash
orch work --background
```

Expected behavior:

- broker starts when needed
- lead/work register with broker
- sessions acquire leases and heartbeat
- Pi sessions receive role instructions and the Orchlink extension
- worker listens for tasks and Talk turns
- lead receives worker replies in visible chat

### 7.3 Delegate a blocking review

```bash
orch send work --wait -t T001 -m "Review the plan. Reply with verdict, risks, files inspected, tests run."
```

Expected behavior:

- broker rejects the request if the named worker is offline or busy
- work appears in `orch jobs`
- worker receives a scoped prompt
- worker reply resolves the waiter
- lead receives the result in CLI and/or visible lead chat
- result is scoped to the current project

### 7.4 Delegate async work

```bash
orch send work -t T002 -m "Implement only the parser change. Run parser tests."
```

Expected behavior:

- broker enforces single-flight per named worker
- lead may continue only on a separate scope
- lead must reconcile the worker result before dependent final steps

### 7.5 Native delegation and background worker visibility

The lead Pi registers a lead-only `delegate_worker` tool. Both execution modes submit through the canonical `orch send` client and existing local broker.

Foreground is the default and follows Pi's native subagent contract: `execute()` remains pending, broker progress is emitted as partial `AgentToolResult` values through `onUpdate`, and the final tool result is returned only after terminal broker completion. Its inline row shows an animated state, task status, tool-call count, worker-session context usage, and elapsed time; expanded mode adds only privacy-filtered visible assistant transcript.

Explicit `async: true` is the background mode. It returns only an accepted tracking handle; the later broker result remains authoritative and must be reconciled separately through notification, `/orchlink`, or `orch jobs --result`.

While any task is active, a bounded activity tree stays fixed above the editor and summarizes each worker name, task, status, tool-call count, worker-session context usage, and elapsed time. It includes both foreground and background tasks so activity remains visible while native tool rows move through the transcript. Context is labeled `ctx` and is not task token spend. The widget is broker-driven, status-only, limited to three detailed workers plus overflow, hidden when no relevant work remains, and refreshed every second by default (`ORCHLINK_MONITOR_POLL_SECONDS`, minimum 0.5 seconds).

`F8` opens the existing read-only `/orchlink` TUI overlay. `ORCHLINK_WORKERS_KEY` may override the shortcut, and `/orchlink` remains the universal fallback. The overlay shows a compact worker list, then follows visible assistant output for a selected active task.

Requirements:

- use 92% terminal width, a 60-column minimum when possible, 88% maximum height, and at least a 36-line component budget
- Enter or `f` follows the selected worker
- the mouse wheel scrolls inside an overflowing transcript and is released when the panel closes
- Up/Down scroll one line; Page Up/Page Down scroll one page
- manual scrolling pauses auto-scroll; End jumps to the latest event and resumes live mode
- Tab/Shift-Tab switch active workers while preserving task-specific transcript cursors and scroll positions; unavailable controls are hidden
- Escape returns to the list; `q` closes the overlay without cancelling work
- persist bounded transcript events separately from broker message snapshots
- render visible assistant output with Pi's Markdown theme and syntax-highlighted code blocks
- display only visible assistant text plus bounded status/tool summaries; exclude thinking, unknown stream events, provider data, secrets, and raw tool output
- abort stale long polls on worker switch, list return, panel close, and Pi shutdown

### 7.6 Talk Mode

```bash
orch talk work -m "Should we keep this behavior simple or split it into a new command?" -r 3
orch say C001 -m "Follow-up question"
orch close C001 -m "Decision: ..."
```

Expected behavior:

- Talk messages stay conversational
- no task prompt boilerplate is injected
- each turn is tracked as work in `orch jobs`
- max turns are enforced
- open Talk conversations block new worker-bound work
- closing records a compact decision

## 8. Core concepts

### 8.1 Project

A project is a repository or working directory with `.orch/project.yaml`.

The project defines:

- `project_id`
- broker URL, host, port, API key, auto-start, auto-stop, peer-session policy
- Pi command and optional session args
- lead agent ID and session ID
- worker agent ID and session ID
- allowed and forbidden scope defaults

All normal work, result, event, activity, and session queries must be project scoped.

### 8.2 Agent

An agent is a registered participant:

- lead: coordinates with worker and talks to the human
- work: receives scoped work and replies to lead

Default IDs:

```text
<project_id>.lead
<project_id>.work
```

### 8.3 Visible session

A visible session is a running Pi process managed by `orch lead` or `orch work`. A worker session has a configless name such as `work`, `review`, or `bg-test` that maps deterministically to `<project_id>.<name>`.

Sessions have:

- lease ID
- role
- agent ID
- project ID
- process ID when known
- session ID
- heartbeat timestamp
- active/released/expired status
- grace period

If a worker session exits or expires, active worker-owned work should be settled as cancelled so lead does not wait forever.

### 8.4 Job

A job is a status row for work.

Kinds:

- `task`: delegated task with a task ID
- `talk`: open or closed Talk conversation with a conversation ID

Fields:

- ID (`task_id` or `conversation_id`)
- kind
- mode
- status
- route
- project ID
- preview
- created/updated timestamps
- optional latest activity
- optional result/reply

`orch jobs` is the main browser for jobs.

### 8.5 Event

An event is a runtime observation used by `orch broker watch`, status views, and debugging.

Examples:

- agent registered
- session acquired/released/expired
- message queued
- message delivered
- worker activity
- reply received
- timeout
- cancellation
- conversation closed

### 8.6 Activity

Activity is worker telemetry while work is active.

Examples:

- heartbeat
- tool call
- tool result
- phase/status update

Activity answers “is the worker doing anything?” It is not a durable audit log.

### 8.7 Message

A message is a broker envelope used to route a work turn or reply.

Messages are implementation details behind jobs. They still need strict validation because the broker must route, correlate, and reject invalid work.

## 9. Modes

Modes are product behavior, not only prompt text.

| Mode | Purpose | Edits allowed? | Typical command |
| --- | --- | --- | --- |
| TALK | visible discussion, challenge, tradeoff, decision | no | `orch talk`, `orch say`, `orch close` |
| DISCUSS | reason and recommend inside a task envelope | no | `orch send` |
| PLAN | inspect if useful, then propose | no | `orch send` |
| REVIEW | inspect and gate next step | no, unless explicitly allowed | `orch send --wait` |
| DO | implement scoped work | yes, only inside scope | `orch send` |

Mode requirements:

- TALK should be concise teammate chat with no task boilerplate.
- REVIEW is a gate when its result can change lead’s next action.
- DO requires explicit implementation permission.
- If DO is requested without implementation permission, worker should inspect only and return PLAN/BLOCKER.
- Worker should follow the lead’s requested reply shape.
- Task replies should be natural by default; do not require `TYPE:` labels or a fixed result schema unless the lead asks for one.

## 10. Architecture

```text
Typer CLI
  ├─ human commands: init/lead/work/doctor/jobs/stop/update
  ├─ agent coordination commands: send/talk/say/close/wait/get/idle/peek/cancel
  └─ debug/reference commands: status/watch/task/broker run

Project config
  ├─ .orch/project.yaml
  ├─ generated lead/work skills
  └─ run directory for PID/log/extension files

FastAPI broker
  ├─ protocol validation
  ├─ API-key auth
  ├─ session leases
  ├─ job routing and status
  ├─ single-flight enforcement
  ├─ events/activity
  ├─ result waiters
  └─ cancellation/timeout handling

Storage interface
  └─ MemoryMessageStore today

Pi connector
  ├─ launches visible Pi sessions and the headless RPC worker
  ├─ injects generated skills
  ├─ injects Orchlink Pi extension
  ├─ sets runtime env vars
  └─ maintains session lease heartbeats

Pi extension
  ├─ registers role with broker
  ├─ polls for broker messages
  ├─ renders worker task/Talk prompts
  ├─ captures assistant replies
  ├─ posts results back to broker
  ├─ records activity telemetry
  ├─ delivers worker results into lead chat
  └─ attempts best-effort cancellation/abort
```

## 11. Broker API requirements

Public:

- `GET /health`

Protected by `X-API-Key`:

- `POST /v1/agents/register`
- `POST /v1/sessions/acquire`
- `POST /v1/sessions/{lease_id}/heartbeat`
- `POST /v1/sessions/{lease_id}/release`
- `GET /v1/sessions`
- `POST /v1/messages/send`
- `POST /v1/messages/send-and-wait`
- `GET /v1/agents/{agent_id}/next`
- `POST /v1/messages/{message_id}/reply`
- `POST /v1/messages/{message_id}/status`
- `POST /v1/jobs/{item_id}/cancel`
- `POST /v1/conversations/{conversation_id}/close`
- `GET /v1/events`
- `POST /v1/activity`
- `GET /v1/activity`
- `GET /v1/tasks/{task_id}/activity`
- `GET /v1/jobs`
- `GET /v1/tasks/{task_id}`
- `GET /v1/tasks/{task_id}/wait`
- `GET /v1/status`

API requirements:

- reject invalid API keys except on `/health`
- reject unsupported protocol versions
- reject unknown message types
- reject invalid mode/delivery combinations
- reject turns above `max_turns`
- reject worker-bound work when peer session is required and offline
- reject worker-bound work when the named worker is busy
- preserve project scoping on jobs, tasks, events, activity, sessions, and results
- never print or log API keys

## 12. Protocol reference

Every routed message uses an envelope like:

```json
{
  "protocol": "orch-a2a-v1",
  "message_id": "msg-...",
  "correlation_id": "req-...",
  "project_id": "demo",
  "conversation_id": "demo-tasks",
  "task_id": "T001",
  "from_agent": "demo.lead",
  "to_agent": "demo.work",
  "type": "TASK",
  "status": "PENDING",
  "turn": 1,
  "max_turns": 6,
  "requires_reply": true,
  "timeout_seconds": 1800,
  "delivery": "async",
  "payload": {
    "mode": "PLAN",
    "intent": "Inspect and propose only.",
    "scope": {
      "allowed": ["**/*"],
      "forbidden": [".git/**", ".orch/**", "node_modules/**", ".venv/**"]
    },
    "constraints": [],
    "expected_reply": []
  }
}
```

Supported task/result message types:

- `TASK`
- `PLAN`
- `RESULT`
- `BLOCKER`
- `REVIEW`
- `CLOSE`
- `STOP`

Supported Talk message types:

- `CHAT_START`
- `CHAT_TURN`
- `CHAT_REPLY`
- `CHAT_CLOSE`

Status vocabulary:

- active/busy: `PENDING`, `QUEUED`, `DELIVERED`, `RUNNING`, `IN_PROGRESS`, `OPEN`
- terminal/settled: `DONE`, `COMPLETED`, `FAILED`, `TIMEOUT`, `CANCELLED`, `CLOSED`

## 13. Single-flight per named worker

Each named worker is single-flight per project and worker agent.

A new worker-bound task or Talk turn must be rejected when:

- a task is queued/delivered/running/in-progress
- a Talk conversation is open and waiting on the worker
- peer sessions are required and the target worker session is offline

The error must include enough context for lead to decide whether to wait, inspect jobs, or cancel stuck work.

## 14. Cancellation and timeout semantics

Cancellation is coordination cancellation, not process isolation.

Expected behavior:

- `orch jobs --cancel <id>` marks matching broker work `CANCELLED` immediately.
- pending `wait` calls resolve with cancellation.
- task/conversation/job status changes to cancelled.
- an event is recorded.
- Pi extension attempts to steer the current assistant turn to stop.
- Pi extension attempts to abort if supported by Pi.
- future tool calls should be blocked when the extension can enforce it.

Honest limitation:

- already-running shell commands or tool calls may continue if Pi cannot interrupt them in time.
- broker state cancellation is stronger than process cancellation.
- docs must not claim hard sandboxing or OS-level termination.

Timeout behavior:

- wait timeout does not cancel the underlying task
- task timeout settles broker work as `TIMEOUT`
- late replies after timeout/cancel should be ignored or clearly marked as late

## 15. Pi connector contract

The Pi connector is the riskiest integration point and must be treated as a maintained adapter.

Required capabilities:

- launch or reopen named visible Pi worker sessions, or start named headless RPC workers
- append role-specific system prompts
- load the Orchlink extension
- pass broker URL, API key, project ID, agent ID, role, and polling settings through env vars
- inject worker task and Talk prompts into the worker session
- capture assistant outputs as worker replies
- deliver worker replies into the visible lead session
- record activity telemetry where supported
- attempt cancellation/abort where supported

Compatibility requirements:

- `orch doctor` should check Pi command availability and generated skill freshness.
- The extension should degrade clearly when a required Pi API is missing.
- Connector/version assumptions should be documented.
- If Pi extension APIs change, Orchlink should fail loudly with actionable guidance instead of pretending worker is connected.

## 16. Security

Security model:

- broker binds to `127.0.0.1` by default
- all `/v1` endpoints require `X-API-Key`
- API key is stored in `.orch/project.yaml` or supplied by environment
- CLI must not print real keys
- logs must not include API keys
- `.orch/` should not be committed
- project scoping prevents stale cross-project result reads

Out of scope:

- multi-user auth
- public internet exposure
- remote broker trust model
- sandboxing of worker tool execution

## 17. Generated skills and behavior policy

`orch init` creates generated skills for lead and work.

Lead skill must teach:

- coordinate with worker, do not merely delegate
- keep scopes separate
- distinguish human daily, agent coordination, and debug commands
- use `send --wait` for gates
- use async `send` only for independent work
- use Talk for discussion
- use `idle` before dependent final steps or another assignment
- do not stack worker tasks
- decide reply shape per task
- reconcile worker replies before continuing

Worker skill must teach:

- obey mode and scope
- no edits for TALK/DISCUSS/PLAN/REVIEW unless explicitly allowed
- DO only inside scope
- return BLOCKER when unclear or too broad
- follow lead’s requested reply shape
- keep Talk conversational
- avoid universal verbose templates unless requested

Generated skills are part of product behavior and should stay aligned with this PRD.

## 18. CLI command requirements

### Human daily commands

#### `orch init`

Creates project config, skills, and run directory. Supports refreshing generated skills without overwriting config.

#### `orch lead`

Ensures broker, registers lead, acquires session lease, launches visible Pi lead with lead skill and extension.

#### `orch work`

Ensures broker, registers a named worker, acquires a session lease, and launches the visible Pi worker with worker skill and extension. `--name review` starts/reopens a separate configless worker context. With `--background`, starts the named headless RPC supervisor and waits for an extension-owned ready heartbeat; `--background --test` starts a fresh isolated `bg-test` worker.

#### `orch doctor`

Checks project setup, broker reachability/compatibility, API key presence, Pi command, generated skills, and recommended recovery steps.

#### `orch jobs`

Main work browser. Supports:

```bash
orch jobs
orch jobs --active
orch jobs --status STATUS
orch jobs --kind task
orch jobs --kind talk
orch jobs --id T001
orch jobs --json
```

Output should show ID, kind, mode, status, updated age, route, preview, and latest non-stale activity.

#### `orch stop`

Stops this project's tracked default background worker without killing unrelated Pi sessions or the shared broker. Use `orch stop --name <worker>` for a named worker, or `orch stop --broker`/`--all` for broker cleanup.

#### `orch update`

Updates the installed package and tells the user to refresh skills/restart sessions.

### Agent coordination commands

#### `orch send`

Canonical task command. Sends asynchronously by default and blocks with `--wait`. Reviews may start asynchronously; the lead must reconcile the result before crossing any dependent gate.

#### `orch talk`, `orch say`, `orch close`

Manage visible Talk Mode conversations. Must preserve plain conversational UX and max-turn protection.

#### `orch jobs --idle`

Exit-code safety check:

- 0: no active/blocking work
- 1: active/blocking work exists

#### `orch jobs --wait`

Waits for one exact task result. A wait timeout does not cancel the task.

#### `orch jobs --result`

Reads or rereads a task result, or conversation summary when supported.

#### `orch jobs --live`

Shows recent activity for long-running work only.

#### `orch jobs --cancel`

Marks work cancelled and asks Pi to stop. Must be honest about best-effort interruption.

### Debug/reference commands

#### `orch broker status`

Prints raw broker status JSON for debugging.

#### `orch broker watch`

Watches broker events for debugging worker activity and routing.

#### `orch jobs <id>`

Shows focused route/status details for one job. Use `orch jobs --live <id>` for recent activity.

#### `orch broker run`

Runs the broker foreground server for debugging/development.

## 19. Acceptance criteria for current v1

Orchlink is acceptable for v1 when:

1. `orch init` creates valid config and generated skills.
2. `orch lead` and `orch work` start visible Pi sessions with the Orchlink extension; `orch work --background` starts a ready-checked headless RPC worker.
3. Broker auto-start works for normal commands.
4. Lead and worker register and maintain session leases.
5. Named worker offline is detected when peer sessions are required.
6. `orch send --wait` completes a blocking request/reply loop.
7. `orch send` supports async work without stacking worker work.
8. `orch talk/say/close` supports visible Talk conversations.
9. `orch jobs` is the main current-project work browser.
10. `orch jobs --idle` correctly reports active/blocking work through its exit code.
11. `orch jobs --wait` and `orch jobs --result` return project-scoped results and reject stale cross-project data.
12. Activity telemetry appears for active work when supported by Pi.
13. Cancellation marks broker state immediately and sends best-effort stop/abort steering to Pi.
14. Timeout behavior is explicit and tested.
15. Unknown message types and invalid protocol envelopes are rejected.
16. API-key protection works.
17. Generated skills teach the intended lead/worker behavior.
18. README documents human, agent coordination, and debug commands separately.
19. Unit tests pass.
20. Manual smoke tests cover real visible Pi sessions and the headless background worker.

## 20. Near-term roadmap

### Product/docs cleanup

- keep one product concept: coordinated visible lead/work sessions
- use “work” for user-facing explanation
- use “job” for `orch jobs` status rows
- keep message/protocol details in architecture/reference only
- separate commands by audience in PRD, README, and skills

### State model cleanup

- introduce a canonical Job/Event model in code when changing state logic
- reduce duplicated status derivation across tasks/conversations/messages
- keep the broker boring and strict
- do not add storage complexity now

### Connector hardening

- document required Pi extension APIs
- add connector compatibility/version checks where practical
- improve actionable errors when Pi APIs are missing or changed
- keep graceful degradation honest

### Later enhancements, only if needed

- multiple workers
- reviewer role
- richer activity views
- remote broker mode
- web dashboard
- additional agent adapters
- durable local history if restart recovery becomes painful

## 21. If rebuilding the app from scratch

Build it in this order:

1. Define the canonical Job/Event state machine.
2. Build the local broker around jobs, sessions, events, results, and single-flight enforcement.
3. Add strict protocol envelopes only where needed for routing and validation.
4. Add project config and generated behavior skills.
5. Add the small human CLI.
6. Add the agent coordination CLI.
7. Add the debug/reference CLI.
8. Add the Pi connector and extension contract.
9. Add activity, cancellation, and recovery UX.
10. Add tests around state transitions and failure modes.

The rebuild should improve internal clarity, not broaden the product too early.

## 22. Final principle

Keep Orchlink boring and trustworthy.

```text
lead is the coordinator
work is the scoped collaborator
broker is the local source of coordination truth
jobs are what humans and agents inspect
messages are only how the broker moves turns around
```
