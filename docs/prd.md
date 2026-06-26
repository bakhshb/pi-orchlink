Orchlink PRD

1. Product Name

Orchlink

Orchlink is a local broker and connector that lets two visible Pi coding-agent sessions communicate with each other through a structured request/reply protocol.

The purpose is simple:

Terminal 1: Pi lead session
Terminal 2: Pi worker session

The user talks to the lead.
The lead sends tasks to the worker.
The worker receives the task in its own Pi session.
The worker replies.
The lead receives the reply and continues.

Orchlink is the communication layer between the sessions.

2. Command Name

The user-facing CLI command must be:

orch

The commands must be short and easy to remember.

Primary commands:

orch init
orch lead
orch work
orch ask work --task T001 --msg "Inspect the project and return a plan."
orch watch
orch stop
orch doctor

Daily use should usually require only:

orch lead
orch work

The broker should be started automatically when needed.

Do not require the user to remember long commands like:

python -m ...
uvicorn ...
orchlink start worker-backend ...

Those are implementation details, not user commands.

3. Product Summary

Orchlink allows independent Pi coding-agent sessions to work together.

The user opens two terminals:

Terminal 1:
orch lead

Terminal 2:
orch work

Then the user talks only to the lead session.

The lead can delegate tasks to the worker using:

orch ask work --task T001 --msg "Task message"

The worker receives the task, processes it inside its own visible Pi session, and sends the final response back to the lead.

The experience should feel like two coding agents talking to each other, while still keeping each session separate.

4. Problem

Today, when using two Pi coding-agent sessions, the user must manually copy and paste work between them:

1. Ask lead session.
2. Copy lead task.
3. Paste task into worker session.
4. Wait for worker response.
5. Copy worker response.
6. Paste back into lead session.

This is slow and annoying.

Orchlink removes the copy-paste step by giving the sessions a shared local communication protocol.

5. Product Goal

Build a Python-based local system that allows two visible Pi coding-agent sessions to communicate:

lead session  →  Orchlink broker  →  worker session
worker session  →  Orchlink broker  →  lead session

The MVP must support:

- One lead session
- One worker session
- Simple CLI command: orch
- Python venv
- FastAPI broker
- In-memory message routing
- Request/reply communication
- Native Pi connector
- Visible Pi sessions
- Simple project-local config
- Automatic broker startup
- Worker listening for tasks
- Lead able to delegate tasks
- Message monitor
- Tests for the full request/reply loop

6. Non-Goals for MVP

Do not implement these in the first version:

- Database
- SQLite
- Redis
- RabbitMQ
- Web dashboard
- GitLab integration
- Telegram integration
- Slack/Discord integration
- Cloud deployment
- Multi-user auth
- Vector memory
- Automatic Git merge
- Complex workflow engine
- Multi-worker scheduler
- Tmux adapter

No tmux in the primary design.

The primary design must be a native Pi connector.

7. Core Runtime Experience

The desired daily flow:

Terminal 1

cd ~/projects/my-project
orch lead

This starts a visible Pi lead session.

The lead session should know:

- it is the lead
- a worker exists
- it can delegate using orch ask work
- it should ask for PLAN before risky implementation
- it should review worker replies before continuing

Terminal 2

cd ~/projects/my-project
orch work

This starts a visible Pi worker session.

The worker session should know:

- it is the worker
- it listens for tasks from the lead
- it must obey scope
- it must return PLAN, RESULT, or BLOCKER

Optional Terminal 3

orch watch

This shows the conversation:

[10:12:01] lead → work TASK T001
[10:14:42] work → lead PLAN T001

8. How Sessions Know Each Other

Orchlink uses a project-local config.

When the user runs:

orch init

Orchlink creates:

.orch/
├── project.yaml
├── skills/
│   ├── lead.md
│   └── work.md
└── run/

The project config defines:

project_id
broker_url
api_key
lead agent id
worker agent id
Pi command
project directory

By default:

lead agent id   = <project_id>.lead
worker agent id = <project_id>.work

Inside the project, the user does not need to type the full IDs.

The user can type:

orch ask work --task T001 --msg "..."

Orchlink expands "work" to:

<project_id>.work

This keeps commands simple.

9. Architecture

┌──────────────────────────────┐
│ Terminal 1                   │
│ orch lead                    │
│ Visible Pi lead session      │
└───────────────┬──────────────┘
                │
                │ sends task using orch ask
                ▼
┌──────────────────────────────┐
│ Orchlink Broker              │
│ FastAPI + in-memory store    │
└───────────────┬──────────────┘
                │
                │ routes task
                ▼
┌──────────────────────────────┐
│ Terminal 2                   │
│ orch work                    │
│ Visible Pi worker session    │
└───────────────┬──────────────┘
                │
                │ returns reply
                ▼
┌──────────────────────────────┐
│ Orchlink Broker              │
└───────────────┬──────────────┘
                │
                │ returns reply
                ▼
┌──────────────────────────────┐
│ Lead receives worker result  │
└──────────────────────────────┘

10. Main Components

10.1 Broker

The broker is the local message router.

Responsibilities:

- Start automatically when needed.
- Register lead and worker agents.
- Store active messages in memory.
- Route messages to the correct session.
- Match replies using correlation_id.
- Enforce timeouts.
- Reject invalid messages.
- Protect endpoints with API key.

The broker does not think or code.

10.2 Lead Connector

The lead connector starts and configures the visible Pi lead session.

Responsibilities:

- Load .orch/project.yaml.
- Ensure broker is running.
- Register lead agent.
- Start Pi in lead mode.
- Provide lead skill/instructions.
- Make delegation command available.

10.3 Worker Connector

The worker connector starts and configures the visible Pi worker session.

Responsibilities:

- Load .orch/project.yaml.
- Ensure broker is running.
- Register worker agent.
- Start Pi in worker mode.
- Listen for tasks.
- Send incoming task into the worker Pi session.
- Wait for worker answer.
- Send worker answer back to broker.

10.4 Monitor

The monitor displays message flow.

Command:

orch watch

Responsibilities:

- Show messages between lead and worker.
- Show task ID.
- Show message type.
- Show status.
- Show short preview.

11. Native Pi Connector Requirement

Orchlink must not rely on tmux for the primary experience.

The preferred integration is a native Pi connector.

The connector should support named Pi sessions.

Desired conceptual behavior:

pi --session lead
pi --session work

However, the user should not have to run those directly.

The user should run:

orch lead
orch work

Internally, Orchlink may call Pi with the configured command.

Example from config:

pi:
  command: pi
  lead_args:
    - --session
    - lead
  work_args:
    - --session
    - work

If Pi supports native listen/connect flags, Orchlink should use them.

Conceptual worker behavior:

Start visible Pi worker session.
Register with broker.
Wait for broker task.
Inject task into worker session.
Wait for answer.
Return answer to broker.
Continue waiting.

If Pi does not support native task injection, the MVP must clearly return an error explaining that the Pi connector needs a send/listen capability.

Do not silently pretend the worker is connected if it is not.

12. Simple Command UX

12.1 "orch init"

Initializes Orchlink for the current project.

Command:

orch init

Creates:

.orch/project.yaml
.orch/skills/lead.md
.orch/skills/work.md
.orch/run/

It should ask only minimal questions or use defaults.

Default project ID should be based on the current folder name.

Example:

Current folder: ~/projects/test
project_id: test

12.2 "orch lead"

Starts the visible lead Pi session.

Command:

orch lead

Behavior:

1. Load .orch/project.yaml.
2. Ensure broker is running.
3. Register <project_id>.lead.
4. Load .orch/skills/lead.md.
5. Start Pi lead session.
6. Show available worker.
7. Keep user inside the lead session.

Expected output before Pi starts:

[Orch] Broker online
[Orch] Registered: test.lead
[Orch] Worker available: work
[Orch] Starting Pi lead session...

12.3 "orch work"

Starts the visible worker Pi session and listens for tasks.

Command:

orch work

Behavior:

1. Load .orch/project.yaml.
2. Ensure broker is running.
3. Register <project_id>.work.
4. Load .orch/skills/work.md.
5. Start Pi worker session.
6. Start task listener.
7. Keep worker visible.

Expected output:

[Orch] Broker online
[Orch] Registered: test.work
[Orch] Starting Pi worker session...
[Orch] Waiting for tasks...

When task arrives:

[Orch] Received TASK T001 from lead
[Orch] Sending task to Pi worker session...
[Orch] Worker replied
[Orch] Reply sent to lead

12.4 "orch ask"

Sends a task to the worker and waits for reply.

Command:

orch ask work --task T001 --msg "Inspect the project and return PLAN only."

Short alias may also be supported:

orch ask work -t T001 -m "Inspect the project and return PLAN only."

Behavior:

1. Resolve work to <project_id>.work.
2. Build protocol message.
3. Send message to broker.
4. Wait for reply.
5. Print reply.

12.5 "orch watch"

Shows message flow.

Command:

orch watch

Example output:

[10:12:01] test.lead → test.work TASK T001
Inspect the project and return PLAN only.

[10:14:22] test.work → test.lead PLAN T001
Found repeated code in two files. Recommend limited cleanup.

12.6 "orch stop"

Stops local Orchlink broker and any background Orchlink processes for the current user.

Command:

orch stop

It must not kill unrelated Pi sessions unless explicitly confirmed.

12.7 "orch doctor"

Checks setup.

Command:

orch doctor

Checks:

- Python package installed
- .orch/project.yaml exists
- broker reachable or startable
- API key configured
- Pi command exists
- lead skill exists
- worker skill exists
- worker can register

12.8 "orch jobs"

Shows recent work for the current project. This is the main work browser.

Commands:

orch jobs
orch jobs --active
orch jobs --status STATUS
orch jobs --kind task
orch jobs --kind talk
orch jobs --id T001
orch jobs --id C001
orch jobs --json

Behavior:

- Default `orch jobs` shows recent work, including terminal and active rows.
- `--active` shows only pending/running/open/blocking work.
- Blocking statuses are PENDING, QUEUED, DELIVERED, RUNNING, IN_PROGRESS, and OPEN.
- `--status` filters by broker status.
- `--kind task` means rows with a task_id.
- `--kind talk` means rows with a conversation_id and no task_id.
- `--id` focuses one task/conversation/message ID.
- `--json` prints machine-readable output.
- Show ID, kind, mode, status, route, updated time, and preview.
- Status is authoritative for agents.
- Last activity may show heartbeat/tool activity while work is active.
- Stale heartbeat activity such as "Worker still active" must not be shown after a job is terminal.
- Terminal jobs must still appear as rows; only stale heartbeat metadata is suppressed.

12.8.1 "orch idle"

Keep `orch idle` as a script/check command while it has a useful exit-code contract:

- exit 0 = idle
- exit 1 = active/blocking work exists

12.8.2 Focused/debug status commands

Keep `orch task T001` as focused route/activity status until `orch jobs --id T001` matches or exceeds it.

Keep `orch status` as raw broker JSON for debugging only. Normal agents should not use it for coordination.

12.8.3 Cancellation honesty

`orch cancel T001` marks broker work CANCELLED immediately and sends a steering cancellation message to Pi when possible. Future tool calls should be blocked. Already-running shell commands are best-effort and may only stop if Pi's abort reaches them.

12.9 CLI help

`orch --help` must explain each top-level command in plain language.

Command-specific help, such as `orch jobs --help`, must explain the command and its options.

13. Python Packaging

Orchlink must be a real installable Python package.

Use "pyproject.toml".

Required structure:

orchlink/
├── pyproject.toml
├── README.md
├── src/
│   └── orchlink/
│       ├── __init__.py
│       ├── broker/
│       │   ├── __init__.py
│       │   ├── main.py
│       │   ├── protocol.py
│       │   ├── settings.py
│       │   └── storage/
│       │       ├── __init__.py
│       │       ├── base.py
│       │       └── memory.py
│       ├── cli/
│       │   ├── __init__.py
│       │   └── main.py
│       ├── connector/
│       │   ├── __init__.py
│       │   ├── pi_connector.py
│       │   ├── lead.py
│       │   └── worker.py
│       ├── bridge/
│       │   ├── __init__.py
│       │   ├── ask.py
│       │   ├── listener.py
│       │   └── monitor.py
│       └── project/
│           ├── __init__.py
│           ├── init.py
│           └── config.py
└── tests/
    ├── test_protocol.py
    ├── test_memory_store.py
    ├── test_broker.py
    ├── test_cli.py
    └── test_request_reply.py

"pyproject.toml" must expose:

[project.scripts]
orch = "orchlink.cli.main:app"

Install:

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

The command must then work:

orch --help

14. Dependencies

Use Python 3.11+.

Dependencies:

fastapi
uvicorn[standard]
pydantic
pydantic-settings
httpx
typer
rich
pyyaml
pytest

No DB dependency in MVP.

15. Storage Strategy

MVP uses in-memory storage only.

The broker stores:

- registered agents
- inbox queues
- active messages
- pending reply futures
- recent events for watch command

The implementation must still use a storage interface:

MessageStore
└── MemoryMessageStore

Do not allow FastAPI routes to manipulate raw dictionaries directly.

Future storage can add:

SQLiteMessageStore
RedisMessageStore

But not in MVP.

16. Broker Auto-Start

The user should not need to manually start the broker.

When the user runs:

orch lead
orch work
orch ask
orch watch

Orchlink should:

1. Check broker health.
2. If broker is not running, start it locally in the background.
3. Write PID/log files under .orch/run or ~/.orchlink/run.
4. Continue command.

For debugging, this command may also exist:

orch broker

But daily usage should not require it.

17. Project Config

"orch init" creates ".orch/project.yaml".

Example:

project_id: test

broker:
  url: http://127.0.0.1:8787
  api_key: change-me
  auto_start: true
  host: 127.0.0.1
  port: 8787

pi:
  command: pi

lead:
  agent_id: test.lead
  session_id: lead
  project_dir: .

work:
  agent_id: test.work
  session_id: work
  project_dir: .
  poll_wait_seconds: 5

scope:
  allowed:
    - "**/*"
  forbidden:
    - ".git/**"
    - ".orch/**"
    - "node_modules/**"
    - ".venv/**"

No project-specific product name should be hardcoded.

The project ID should come from the folder name unless the user overrides it.

18. Message Protocol

Every message must use this envelope:

{
  "protocol": "orch-a2a-v1",
  "message_id": "msg-0001",
  "correlation_id": "req-0001",
  "project_id": "test",
  "conversation_id": "test-default",
  "task_id": "T001",
  "from_agent": "test.lead",
  "to_agent": "test.work",
  "type": "TASK",
  "status": "PENDING",
  "turn": 1,
  "max_turns": 6,
  "requires_reply": true,
  "timeout_seconds": 1800,
  "payload": {
    "intent": "Inspect the project and return PLAN only.",
    "scope": {
      "allowed": [
        "**/*"
      ],
      "forbidden": [
        ".git/**",
        ".orch/**",
        "node_modules/**",
        ".venv/**"
      ]
    },
    "constraints": [
      "Do not edit files.",
      "Return PLAN only."
    ],
    "expected_reply": [
      "summary",
      "files inspected",
      "findings",
      "risks",
      "recommended next step"
    ]
  }
}

19. Message Types

MVP message types:

TASK
PLAN
RESULT
BLOCKER
REVIEW
CLOSE
STOP

The broker must reject unknown message types.

20. Broker API

20.1 Health

GET /health

Response:

{
  "status": "ok",
  "service": "orchlink",
  "version": "0.1.0"
}

20.2 Register Agent

POST /v1/agents/register

Request:

{
  "project_id": "test",
  "agent_id": "test.work",
  "role": "worker",
  "display_name": "Worker",
  "capabilities": [
    "inspection",
    "implementation",
    "tests"
  ]
}

20.3 Send and Wait

POST /v1/messages/send-and-wait

Used by:

orch ask work ...

Must send message and wait for worker reply.

20.4 Get Next Message

GET /v1/agents/{agent_id}/next?wait_seconds=5

Used by the worker listener.

20.5 Reply

POST /v1/messages/{message_id}/reply

Used by the worker to return a reply.

20.6 Events

GET /v1/events

Used by:

orch watch

MVP may keep events in memory.

21. Lead Skill

"orch init" creates:

.orch/skills/lead.md

Content:

# Lead Role

You are the lead coding agent.

You can delegate work to the worker through Orchlink.

Use this command:

orch ask work --task <TASK_ID> --msg "<TASK_MESSAGE>"

Rules:
- Send small tasks.
- Prefer `orch ask work --wait` for review gates and synchronous decisions.
- Use Talk Mode for visible discussion, not automation.
- Ask for PLAN before risky implementation.
- Include clear scope and constraints.
- Decide the desired worker reply shape per task; do not force a universal template every time.
- Use `orch wait T001` or `orch get T001`, not both routinely. Use `get` later only to reread/debug a completed result.
- Use `peek` only for long-running work.
- Review worker replies before continuing.
- Do not let the worker edit forbidden files.
- If worker returns BLOCKER, decide the next step.

When "orch lead" starts Pi, it must provide or display this skill so the lead session knows how to use Orchlink.

22. Worker Skill

"orch init" creates:

.orch/skills/work.md

Content:

# Worker Role

You are the worker coding agent.

You receive tasks from the lead through Orchlink.

Rules:
- Work only on the assigned task.
- Obey allowed scope.
- Never edit forbidden files.
- If implementation is not explicitly allowed, inspect only and return PLAN.
- If the task is unclear, return BLOCKER.
- If implementation is allowed, run relevant tests.
- Do not commit unless explicitly allowed.
- Follow the lead's requested reply shape.

For task replies, prefer starting with:

TYPE: PLAN | RESULT | BLOCKER

If the lead requests no shape, use a concise default:

summary:
changed/inspected:
tests:
risks/blockers:
next:

23. Worker Task Prompt

When the worker receives a task, Orchlink must generate a prompt:

You are the worker coding agent.

You received a task from the lead through Orchlink.

TASK ID:
{task_id}

INTENT:
{intent}

ALLOWED SCOPE:
{allowed_scope}

FORBIDDEN SCOPE:
{forbidden_scope}

CONSTRAINTS:
{constraints}

EXPECTED REPLY:
{expected_reply}

Rules:
- Work only on this task.
- Do not expand scope.
- Do not edit forbidden files.
- If implementation is not explicitly allowed, inspect only and return PLAN.
- If the task is unclear, return BLOCKER.
- If implementation is allowed, run relevant tests.
- Do not commit unless explicitly allowed.

Response guidance:

For task replies, prefer starting with:

TYPE: PLAN | RESULT | BLOCKER

Then follow the lead's EXPECTED REPLY shape. If no useful shape is provided, be concise with:

summary:
changed/inspected:
tests:
risks/blockers:
next:

24. Security

MVP security:

- API key required for broker endpoints except /health.
- API key stored in .orch/project.yaml or environment.
- Do not print secrets in logs.
- Broker defaults to 127.0.0.1.
- Do not expose broker to the public internet.

25. Timeouts and Loop Protection

Default timeout:

1800 seconds

Every message includes:

turn
max_turns

Defaults:

turn = 1
max_turns = 6

Rules:

- Reject messages where turn > max_turns.
- Reject max_turns > 12 unless explicitly configured.
- Return timeout if worker does not reply.

26. Logging

Logs must include:

- broker started
- broker stopped
- agent registered
- message queued
- message delivered
- worker started task
- worker reply received
- timeout
- invalid request

Logs must include where relevant:

project_id
task_id
message_id
correlation_id
from_agent
to_agent
status

Do not log API keys.

27. Tests

Tests must cover:

- project init creates config and skills
- protocol validation
- invalid message type rejection
- agent registration
- send-and-wait success
- send-and-wait timeout
- worker next-message polling
- reply handling
- orch ask command
- orch watch events
- broker auto-start check

Minimum test command:

pytest

28. Acceptance Criteria

MVP is complete when:

1. `orch init` creates project config and skills.
2. `orch lead` starts or prepares a visible Pi lead session.
3. `orch work` starts or prepares a visible Pi worker session and listens for tasks.
4. Broker starts automatically when needed.
5. Lead and worker register with the broker.
6. `orch ask work --task T001 --msg "..."`
   sends a task to the worker.
7. Worker receives the task.
8. Worker processes the task through Pi connector.
9. Worker reply returns to the lead.
10. `orch watch` shows the exchange.
11. Timeout works.
12. Unknown message types are rejected.
13. API key protection works.
14. Tests pass.
15. README explains daily usage clearly.
16. `orch --help` explains top-level commands and `orch jobs --help` explains the jobs command/options.
17. `orch jobs` keeps terminal job rows visible but hides stale terminal heartbeat activity.

29. Implementation Phases

Phase 1: Clean Python Package

- Add pyproject.toml.
- Add src/orchlink package.
- Add orch console command.
- Add basic CLI help.

Phase 2: Project Init

- Implement orch init.
- Create .orch/project.yaml.
- Create .orch/skills/lead.md.
- Create .orch/skills/work.md.

Phase 3: Broker

- FastAPI broker.
- Health endpoint.
- API key protection.
- Protocol models.
- MemoryMessageStore.

Phase 4: Request/Reply Loop

- Register agent.
- Send-and-wait.
- Get next message.
- Reply endpoint.
- Timeout behavior.

Phase 5: CLI Ask and Watch

- orch ask work.
- orch watch.
- event stream in memory.

Phase 6: Pi Connector

- Implement configurable Pi command.
- Implement lead startup.
- Implement worker startup/listen mode.
- Send task into worker Pi session using native connector behavior.
- Return worker result.

Phase 7: Smooth Daily UX

- orch lead.
- orch work.
- broker auto-start.
- clear logs and errors.
- orch doctor.

Phase 8: Tests and Docs

- Add tests.
- Add README.
- Document simple commands.
- Document limitations.

30. Future Enhancements

Future versions may add:

- Multiple workers
- Multiple project sessions at the same time
- SQLite persistence
- Redis Streams
- Web dashboard
- Git integration
- Reviewer role
- Human approval gates
- Remote broker

Do not implement these in MVP.

31. Final Principle

Orchlink must stay simple.

The user should remember:

orch init
orch lead
orch work
orch ask work
orch watch

The lead is the brain.
The worker is the hand.
Orchlink is the link.
