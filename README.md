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

             job/result      .orch/goals/  .orch/loop/
             only            Gxxx/         state.md
             no verifier     evidence +     maker ≠ verifier
                             signoff       by default
                                            objective checks
                                            no auto-merge
```

**Ask/Send** — use this for one question, one review, or one implementation task. The broker records the job and result, but Orchlink does not create a goal, lifecycle state, or verifier step.

**Goal Mode** — use this for PRD-driven work. Orchlink stores acceptance criteria, a plan, evidence, blockers, and signoff under `.orch/goals/Gxxx/`.

**Loop Mode** — use this for recurring or parallel work. Orchlink stores loop items in `.orch/loop/state.md`, sends ready items to maker workers, requires a verifier by default, and can run objective checks before `done`.

## Install

You need Python 3.11+, `git`, and Pi installed as `pi`.

**Linux or macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/bakhshb/pi-orchlink/main/install.sh | bash
```

Install Pi Orchlink on Windows PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/bakhshb/pi-orchlink/main/install.ps1 -OutFile install.ps1
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Windows options:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Ref main -Force
powershell -ExecutionPolicy Bypass -File .\install.ps1 -SkillsOnly
powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoSkills
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
```

Windows installs into `%LOCALAPPDATA%\orchlink`, creates `%LOCALAPPDATA%\orchlink\bin\orch.cmd`, and can install the general skill at `%USERPROFILE%\.agents\skills\orchlink`.

Advanced overrides: `ORCHLINK_REPO_URL`, `ORCHLINK_REF`, `ORCHLINK_INSTALL_DIR`, `ORCHLINK_BIN_DIR`, `ORCHLINK_PYTHON`, `ORCHLINK_SOURCE_DIR`. Close running `orch lead` / `orch work` / Pi terminals before uninstalling.

If your shell cannot find `orch`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

> **Windows support is currently beta.** The installer supports basic install, update, uninstall, and command shims, but shell/PATH behavior can vary between PowerShell, CMD, Git Bash, and Pi's tool shell. Linux/macOS remain the primary tested paths.

## Start a project

```bash
cd /path/to/your/project
orch init
orch lead    # terminal 1
orch work    # terminal 2
```

For background use without blocking the current terminal:

```bash
orch work --background
```

This starts the headless Pi RPC worker named `work`, writes `.orch/run/orch-work.pid` and `.orch/run/orch-work.log`, waits for readiness, and returns. For a fresh task-scoped background worker that exits after one completed task reply, use:

```bash
orch work --background --new --replace --oneshot
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

### Loop Mode setup

1. Start a maker worker and a verifier worker. Loop Mode defaults to `maker` for implementation and `review` for verification.

```bash
orch work --background --name maker
orch work --background --name review
```

Visible workers are also valid; run the same commands without `--background` in separate terminals.

2. Configure objective checks in `.orch/loop/checks.yaml`.

```yaml
checks:
  - id: pytest
    command: "python3 -m pytest tests/ -q"
    required: true
  - id: ruff
    command: "ruff check src/"
    required: false
```

A failed required check forces `REJECTED` regardless of the verifier text.

3. Optional: configure a GitHub connector in `.orch/project.yaml`.

```yaml
loop:
  connectors:
    github:
      repo: owner/repo
      limit: 10
      default_branch: main
```

GitHub connector behavior:

- open pull requests become candidates
- open issues become candidates only when labeled `bug`, `enhancement`, `good first issue`, or `help wanted`
- failing commit status on the default branch becomes a CI-failure candidate

4. Put the GitHub token outside `.orch`.

```bash
export ORCHLINK_GITHUB_TOKEN="ghp_..."
```

Or use the external secrets directory:

```bash
mkdir -p ~/.config/orchlink/secrets
printf '%s' 'ghp_...' > ~/.config/orchlink/secrets/github.token
chmod 600 ~/.config/orchlink/secrets/github.token
```

Do not put tokens in `.orch/project.yaml`.

5. Run one triage tick and inspect the result.

```bash
orch loop tick
orch loop ls
orch loop show issue-123
```

New GitHub candidates are stored in `.orch/loop/state.md` as `triaged`. They are not dispatched until you mark them ready.

6. Approve a loop item for work.

```bash
orch loop ready issue-123
```

7. Run the bounded loop with checks.

```bash
orch loop tick --run-checks
```

The tick recovers stale state, triages new candidates, dispatches ready items to the maker, collects maker results, sends work to the verifier, runs objective checks when enabled, and exits.

For a foreground repeated run:

```bash
orch loop watch --run-checks --interval 60 --max-steps 10
```

For a scheduled run that fires one bounded process every 30 minutes:

```bash
orch loop schedule --every 30m --install
```

An item reaches `done` only through:

```text
ready → dispatching → running → awaiting_verdict → verifying → done
```

No auto-merge. No daemon. Each scheduled fire is a fresh bounded `orch loop tick` process.

Loop state lives in `.orch/loop/state.md` as human-readable markdown with a fenced YAML block.

## Workers

- Each worker name handles one task at a time. Different names run independent work.
- `orch work --background` starts a headless RPC worker. `--oneshot` exits after one reply.
- `orch work --worktree-create --base main` creates a `git worktree` for the worker.
- `orch work --model <model> --thinking <level>` pins a worker's model and thinking.
- Talk Mode (`orch talk`, `orch say`, `orch close`) is for lead↔worker discussion without file edits.

## Recovery

Use `orch resume` first when returning after an interruption, broker restart, cancelled task, or compacted conversation. It prints the active task or goal, lead/work sessions, the last broker checkpoint, drifted leases, and one recommended next command in a single plain-text report.

```bash
orch resume          # recovery report after interruption
orch jobs --idle     # exit 0 if no active work
orch jobs --active   # what is still busy
orch jobs --result T002  # read a completed result
orch jobs --cancel T002 -m "reason"  # cancel stale work
orch goal show Gxxx  # check a specific goal's acceptance evidence
```

Use narrower commands when you already know what you need: `orch jobs --idle` for a quick safe/unsafe check, `orch jobs` for recent task/talk rows, `orch sessions` for lead/work session leases, and `orch goal show Gxxx` for a specific goal's acceptance evidence.

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
| `orch loop next ITEM --maker NAME` | Reserve a ready item for a maker and mark it dispatched. |
| `orch loop verify ITEM --verifier NAME` | Verify with a separate worker. Use `tick --run-checks` or `watch --run-checks` for objective checks. |
| `orch loop tick` | Run one bounded loop invocation and exit. |
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