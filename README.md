# Pi Orchlink

Loop engineering for local Pi coding agents.

A single Pi chat can propose code. Real project work needs a loop that remembers the goal, delegates bounded slices, checks evidence, and stops when judgment needs a human. Orchlink gives Pi users that loop without a hosted platform.

The lead Pi orchestrates. Orchlink provides the wiring and accountability layer: a small local broker, generated lead/work skills, and project files under `.orch/`. Named worker Pi sessions like `work`, `review`, and `bg-test` keep separate context and accept one task at a time.

```text
you → lead Pi → named worker Pi → lead Pi → you
```

No Redis. No dashboard. No hosted workflow engine. Just local Pi sessions, a local broker, and durable project files.

## Three ways to work

```text
                          ┌─────────────────────┐
                          │   What do you need?  │
                          └─────────┬───────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
             one task         formal PRD      recurring or
             one answer       with ACs         parallel work
                    │               │               │
                    ▼               ▼               ▼
             ┌──────────┐  ┌────────────┐  ┌──────────────┐
             │orch ask  │  │orch goal   │  │orch loop     │
             │orch send │  │            │  │              │
             └──────────┘  └────────────┘  └──────────────┘
             quick gate     PRD → AC →     triage → maker →
             or async       plan → work →  verifier → checks
             dispatch       signoff        → done

             no state        .orch/goals/  .orch/loop/
             no lifecycle    Gxxx/         state.md
             no verifier     evidence +     maker ≠ verifier
                             signoff       by default
                                            objective checks
                                            no auto-merge
```

**Ask/Send** — one task, one result. No lifecycle, no state, no verifier. Use it for quick gates, async implementation, reviews, or anything where you read the result and move on.

**Goal Mode** — PRD with acceptance criteria, plan, evidence, and human signoff. Use it when the work needs formal proof of completion before you trust it.

**Loop Mode** — recurring or parallel work with maker/verifier separation, objective checks, and durable state. Use it when work should be triaged, dispatched, verified by a separate agent, and checked against tests before `done`.

## What makes Orchlink different

- **Proof over claims.** Goal Mode tracks acceptance criteria, check commands, evidence, blockers, and signoff. Loop Mode refuses `done` without an accepted verifier verdict and a passed objective check.
- **Maker ≠ verifier by default.** Loop Mode structurally separates the agent that writes from the agent that checks. A failed required check forces `REJECTED` regardless of what the LLM says.
- **Named workers you can see and stop.** `work`, `review`, and `bg-test` are durable Pi contexts, not an anonymous worker pool.
- **Single-flight by worker name.** Orchlink prevents three tasks from landing on one worker context by accident.
- **Worktree isolation.** `orch work --worktree-create` gives each maker its own `git worktree` so parallel workers don't collide.
- **No daemon, no cron, no auto-merge.** The loop runs as foreground ticks you control. A schedule fires discrete `orch loop tick` processes, not a long-running daemon.
- **Local and Pi-first.** The broker runs on your machine, the sessions are Pi sessions, and project state lives under `.orch/`.
- **Lead-owned decisions.** The lead Pi chooses the next safe slice. Orchlink routes work and records state; it does not become an autonomous scheduler.

## Install

You need Python 3.11+, `git`, and Pi installed as `pi`.

**Linux or macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/bakhshb/pi-orchlink/main/install.sh | bash
```

The installer puts Orchlink in `~/.local/share/orchlink`, links `orch` into `~/.local/bin`, and can offer optional Orchlink skills.

If your shell cannot find `orch`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

**Windows PowerShell:**

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/bakhshb/pi-orchlink/main/install.ps1 -OutFile install.ps1
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Advanced overrides: `ORCHLINK_REPO_URL`, `ORCHLINK_REF`, `ORCHLINK_INSTALL_DIR`, `ORCHLINK_BIN_DIR`, `ORCHLINK_PYTHON`, `ORCHLINK_SOURCE_DIR`. Close running `orch lead` / `orch work` / Pi terminals before uninstalling.

> **Windows support is beta.** Linux/macOS remain the primary tested paths.

## Start a project

```bash
cd /path/to/your/project
orch init
orch lead    # terminal 1
orch work    # terminal 2
```

For background or external-agent use:

```bash
orch work --background
```

Named workers need no YAML setup:

```bash
orch work --name review
orch send review -t R001 -m "Review only, no edits."
```

Pin a worker model or thinking level:

```bash
orch work --background --name review --model openai/codex-max --thinking xhigh
```

Now talk to lead in plain English:

```text
Review this repo and ask work for a second opinion before changing anything.
```

The worker reply appears in the lead chat.

## Ask and Send

```bash
# Blocking: short review, decision, or blocker
orch ask work --wait -t T001 -m "Is this function safe to merge?"

# Async: long implementation, broad review, tests, or research
orch send work -t T002 -m "Implement the export endpoint."
orch jobs --result T002    # read result when ready
```

Async work is not fire-and-forget. The lead keeps the task ID and, before any completion or decision, reads the exact result or reports it pending with blocking status and retrieval command.

## Goal Mode

Goal Mode is for PRD/plan-driven work where lead should not claim done until acceptance criteria are verified.

```bash
orch goal start "Implement export feature" --prd docs/export-prd.md --derive
orch goal review G001
orch goal gate G001 approve
orch goal work G001 --until done --max-steps 20
orch goal show G001
```

Goal Mode writes durable state under `.orch/goals/Gxxx/`: source, acceptance criteria, plan, coverage, goal status, evidence, blockers, and history.

| Command | What it does |
| --- | --- |
| `orch goal review G001` | Show source, ACs, plan, coverage, and warnings before approval. |
| `orch goal derive G001` | Ask worker to derive acceptance criteria and a plan. |
| `orch goal gate G001 approve` | Approve the combined AC/plan gate. |
| `orch goal work G001 --until done` | Dispatch bounded worker slices until done, gated, blocked, or capped. |
| `orch goal audit G001` | Ask worker to audit artifacts and evidence without editing. |
| `orch goal signoff G001 AC-4` | Human-approve a subjective core AC. |

Objective ACs need check commands for strongest unattended verification. Subjective ACs stop at a signoff gate.

## Loop Mode

Loop Mode is for recurring or parallel work that needs a maker, a separate verifier, and objective checks before `done`.

```bash
# See loop state
orch loop ls

# Move a triaged item to ready, dispatch it, verify it
orch loop ready L001
orch loop next L001 --maker work --worktree-create --base main
orch loop verify L001 --verifier review --run-checks

# Run one bounded tick (recover, triage, dispatch, advance, verify, exit)
orch loop tick --run-checks

# Run a foreground watch loop
orch loop watch --run-checks --interval 60 --max-steps 10

# Install a crontab that fires orch loop tick every 30 minutes
orch loop schedule --every 30m --install
```

An item reaches `done` only through:

```text
ready → dispatching → running → awaiting_verdict → verifying → done
```

…and only on an accepted verifier verdict. No auto-merge. No cron daemon. Each scheduled fire is a fresh bounded `orch loop tick` process.

Objective checks are configured in `.orch/loop/checks.yaml`:

```yaml
checks:
  - id: pytest
    command: "python3 -m pytest tests/ -q"
    required: true
  - id: ruff
    command: "ruff check src/"
    required: false
```

A failed **required** check forces `REJECTED` regardless of what the LLM says. Non-required failures are evidence only.

Loop state lives in `.orch/loop/state.md` as human-readable markdown with a fenced YAML block. The human can read it, edit notes, and override any decision.

## Worker modes

- **Implementation:** lead sends a scoped task to a worker. Prefer async `orch send` for long work.
- **Review:** work checks a change before lead runs expensive tests, final summaries, or release steps.
- **Talk Mode:** lead and work discuss a decision. Work should not edit files in Talk Mode.
- **Background workers:** external agents can run headless workers with `orch work --background`.
- **Worktree isolation:** `orch work --worktree-create --base main` gives each maker its own `git worktree` so parallel workers don't collide.

Each worker name handles one thing at a time. Different names can run independent work. The same name stays single-flight.

## Recovery and safety

Use `orch resume` first when returning after an interruption, broker restart, cancelled task, or compacted conversation. It prints active work, sessions, checkpoint drift, and one recommended next command.

```bash
orch resume
orch jobs --idle       # quick safe/unsafe check
orch jobs --active     # what is still busy
orch jobs --live T002  # recent activity for one job
orch jobs --result T002  # read a completed result
```

Cancellation is cooperative. `orch jobs --cancel T002 -m "reason"` marks broker work cancelled and asks Pi to abort the turn.

## Project files

`orch init` creates:

```text
.orch/
  project.yaml          # project config (auto-generated random API key)
  skills/
    lead.md
    work.md
    references/
      goal-mode.md
      lead-commands.md
      recovery.md
      review-gates.md
  run/
```

Do not commit `.orch/`. Refresh skills manually:

```bash
orch init --refresh-skills
```

## Agent skills

This repo includes a general Orchlink lead skill plus adapter skills for OpenClaw and Hermes.

```text
skills/general/orchlink/
skills/openclaw/orchlink/
skills/hermes/orchlink/
```

Install skills only:

```bash
./install.sh --skills-only
```

Keep adapters synchronized:

```bash
python3 skills/sync_orchlink_skills.py
python3 skills/sync_orchlink_skills.py --check
```

## Command reference

| Command | Use |
| --- | --- |
| `orch init` | Create or refresh `.orch/`. |
| `orch lead` | Start or reopen the visible lead Pi session. |
| `orch work` | Start visible or background named workers. |
| `orch ask work --wait -t T001 -m "..."` | Ask short gates: review, decision, blocker. |
| `orch send work -t T002 -m "..."` | Dispatch async implementation, broad review, tests, or research. |
| `orch jobs` | List recent task and Talk jobs. |
| `orch jobs --active` | Show active/open work. |
| `orch jobs --idle` | Exit 0 only when no active/blocking worker work remains. |
| `orch jobs --result T002` | Print a terminal result or Talk summary. |
| `orch jobs --wait T002` | Block for one exact result only when it gates your next action. |
| `orch jobs --cancel T002 -m "..."` | Cancel stale or unneeded work. |
| `orch sessions` | Show lead/worker sessions, model, runtime, readiness, and leases. |
| `orch talk`, `orch say`, `orch close` | Manage short Talk Mode discussions. |
| `orch doctor` | Check setup, broker compatibility, Pi command, and generated skills. |
| `orch resume` | Show recovery state and recommended next action. |
| `orch goal ...` | Run Goal Mode. |
| `orch loop ls` | List loop items and their states. |
| `orch loop next ITEM --maker NAME` | Dispatch a ready item to a maker worker. |
| `orch loop verify ITEM --verifier NAME --run-checks` | Verify with a separate worker; run objective checks. |
| `orch loop tick` | Run one bounded loop tick and exit. |
| `orch loop watch` | Run a foreground loop watch. |
| `orch loop schedule --every 30m --install` | Install a crontab/systemd timer that fires `orch loop tick`. |
| `orch broker status`, `orch broker watch`, `orch broker run` | Raw broker diagnostics and foreground broker run. |
| `orch update` | Update Orchlink and print restart guidance. |

## Configuration

Project settings live in `.orch/project.yaml`. You usually do not need to edit it.

If you change the broker port, update both `broker.url` and `broker.port`:

```yaml
broker:
  url: http://127.0.0.1:8788
  host: 127.0.0.1
  port: 8788
```

One broker can serve multiple projects. Orchlink scopes normal commands by `project_id` so results from another repo are refused.

## Security

- `orch init` generates a random per-project broker API key. The broker refuses to start with a missing or default `change-me` key.
- `orch doctor` reports broker bind exposure (loopback vs network), API key state, and whether the running broker accepts the project key.
- `orch broker run` warns when binding to a non-loopback interface.
- Orchlink does not sandbox worker shell commands. Scope worker tasks clearly.
- Worktree isolation (`--worktree`) changes the working directory but is not a security boundary. A worker can still touch absolute paths.
- Cancellation is best-effort once Pi has started a tool call.

## Development

```bash
pip install -e ".[dev]"
python3 -c "import pytest, sys; sys.exit(pytest.main(['tests', '-v']))"
python3 -m compileall src/orchlink
```