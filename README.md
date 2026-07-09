# Pi Orchlink

Orchlink is a local coordination layer for Pi coding agents. It connects one lead Pi session to named worker Pi sessions through a local broker, so a lead agent can delegate work, get reviews, run goals, and manage loops — all on your machine.

## What Orchlink does

- **Routes tasks between agents.** The lead Pi sends work to named workers (`work`, `review`, `bg-test`) via a local HTTP broker. Each worker is a separate Pi session with its own context.
- **Tracks work state.** Tasks, talks, sessions, and results are tracked by the broker. The lead reads results, checks whether work is idle, and cancels stale tasks.
- **Runs Goal Mode.** Create a goal from a PRD, derive acceptance criteria and a plan, dispatch bounded work slices, verify objective checks, record evidence, and sign off subjective criteria.
- **Runs Loop Mode.** Triage work items, dispatch each to a maker worker in an isolated worktree, verify with a separate verifier worker, run objective checks (tests, lint), and reach `done` only on an accepted verdict.
- **Schedules loop ticks.** Install a crontab or systemd timer that fires `orch loop tick` on a cadence. Each fire is a fresh bounded process, not a daemon.
- **Isolates parallel workers.** `orch work --worktree-create` creates a `git worktree` per worker so two makers don't touch the same files.
- **Connects to external tools.** GitHub and Linear connectors discover pull requests, issues, and CI failures as loop candidates. Tokens are loaded from env or external files, never stored in project state.
- **Generates project skills.** `orch init` writes lead and worker skills under `.orch/skills/` so agents know the project's conventions, commands, and recovery procedures.
- **Checks broker security.** `orch doctor` reports whether the broker is loopback-only, whether the API key is default, and whether the running broker accepts the project key.

## Three modes

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

**Ask/Send** — one task, one result. No lifecycle, no state, no verifier.

**Goal Mode** — PRD with acceptance criteria, plan, evidence, and human signoff.

**Loop Mode** — recurring or parallel work with maker/verifier separation, objective checks, and durable state.

## Install

You need Python 3.11+, `git`, and Pi installed as `pi`.

**Linux or macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/bakhshb/pi-orchlink/main/install.sh | bash
```

**Windows PowerShell:**

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/bakhshb/pi-orchlink/main/install.ps1 -OutFile install.ps1
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

If your shell cannot find `orch`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

> **Windows support is beta.** Linux/macOS are the primary tested paths.

## Start a project

```bash
cd /path/to/your/project
orch init
orch lead    # terminal 1
orch work    # terminal 2
```

For background use:

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

## Ask and Send

```bash
# Blocking: short review, decision, or blocker
orch ask work --wait -t T001 -m "Is this function safe to merge?"

# Async: long implementation, broad review, tests, or research
orch send work -t T002 -m "Implement the export endpoint."
orch jobs --result T002    # read result when ready
```

## Goal Mode

Goal Mode is for PRD/plan-driven work where the lead should not claim done until acceptance criteria are verified.

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

A failed required objective check forces `REJECTED` regardless of what the LLM says. No auto-merge. No daemon. Each scheduled fire is a fresh bounded `orch loop tick` process.

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

Loop state lives in `.orch/loop/state.md` as human-readable markdown with a fenced YAML block.

## Workers

- Each worker name handles one task at a time. Different names run independent work.
- `orch work --background` starts a headless RPC worker. `--oneshot` exits after one reply.
- `orch work --worktree-create --base main` creates a `git worktree` for the worker.
- `orch work --model <model> --thinking <level>` pins a worker's model and thinking.
- Talk Mode (`orch talk`, `orch say`, `orch close`) is for lead↔worker discussion without file edits.

## Recovery

```bash
orch resume          # recovery report after interruption
orch jobs --idle     # exit 0 if no active work
orch jobs --active   # what is still busy
orch jobs --result T002  # read a completed result
orch jobs --cancel T002 -m "reason"  # cancel stale work
```

## Project files

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
  loop/
    state.md            # loop items and lifecycle state
    checks.yaml         # objective check definitions
  goals/
    Gxxx/               # per-goal artifacts
  run/                  # broker, worker, and session runtime files
```

Do not commit `.orch/`. Refresh skills with `orch init --refresh-skills`.

## Command reference

| Command | Use |
| --- | --- |
| `orch init` | Create or refresh `.orch/`. |
| `orch lead` | Start or reopen the visible lead Pi session. |
| `orch work` | Start visible or background named workers. |
| `orch ask work --wait -t T001 -m "..."` | Ask short gates: review, decision, blocker. |
| `orch send work -t T002 -m "..."` | Dispatch async implementation, review, tests, or research. |
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

One broker can serve multiple projects. Orchlink scopes commands by `project_id` so results from another repo are refused.

## Security

- `orch init` generates a random per-project broker API key. The broker refuses to start with a missing or default `change-me` key.
- `orch doctor` reports broker bind exposure, API key state, and whether the running broker accepts the project key.
- `orch broker run` warns when binding to a non-loopback interface.
- Orchlink does not sandbox worker shell commands. Scope worker tasks clearly.
- Worktree isolation (`--worktree`) changes the working directory but is not a security boundary.
- Cancellation is best-effort once Pi has started a tool call.

## Development

```bash
pip install -e ".[dev]"
python3 -c "import pytest, sys; sys.exit(pytest.main(['tests', '-v']))"
python3 -m compileall src/orchlink
```