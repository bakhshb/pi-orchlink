# Pi Orchlink

Loop engineering for local Pi coding agents.

Set a goal. Prove it is done.

A single Pi chat can propose code. Real project work needs a loop that remembers the goal, delegates bounded slices, checks evidence, and stops when judgment needs a human. Orchlink gives Pi users that loop without a hosted platform.

The lead Pi orchestrates. Orchlink provides the wiring and accountability layer: a small local broker, generated lead/work skills, and project files under `.orch/`. Named worker Pi sessions like `work`, `review`, and `bg-test` keep separate context and accept one task at a time.

```text
you → lead Pi → named worker Pi → lead Pi → you
```

No Redis. No dashboard. No hosted workflow engine. Just local Pi sessions, a local broker, and durable project files.

## What makes Orchlink different

- **Proof over claims.** Goal Mode tracks acceptance criteria, check commands, evidence, blockers, and signoff instead of trusting a final chat message.
- **Named workers you can see and stop.** `work`, `review`, and `bg-test` are durable Pi contexts, not an anonymous worker pool.
- **Single-flight by worker name.** Orchlink prevents three tasks from landing on one worker context by accident.
- **Local and Pi-first.** The broker runs on your machine, the sessions are Pi sessions, and project state lives under `.orch/`.
- **Lead-owned decisions.** The lead Pi chooses the next safe slice. Orchlink routes work and records state; it does not become an autonomous scheduler.

## Daily model

Most of the time you type in the lead Pi chat. The lead uses Orchlink commands to ask named workers for implementation, review, discussion, or Goal Mode progress.

You only need a few shell commands:

```bash
orch init      # set up this project
orch lead      # open the lead Pi session
orch work      # open the default worker Pi session
orch jobs      # see recent/current work
orch goal      # run spec/plan work until criteria are verified
orch sessions  # see lead and worker sessions
orch doctor    # check setup
orch stop      # stop tracked background workers or broker processes
orch update    # update Orchlink
```

Commands like `orch ask`, `orch send`, `orch talk`, `orch say`, `orch close`, `orch jobs --result`, `orch jobs --wait`, `orch jobs --idle`, `orch jobs --live`, and `orch jobs --cancel` are mostly for the lead agent. Debug commands like `orch broker status`, `orch broker watch`, and `orch broker run` are for troubleshooting.

## Install

You need Python 3.11+, `git`, and Pi installed as `pi`.

Install Pi Orchlink on Linux or macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/bakhshb/pi-orchlink/main/install.sh | bash
```

The installer puts Orchlink in `~/.local/share/orchlink`, links `orch` into `~/.local/bin`, and can offer optional Orchlink skills.

If your shell cannot find `orch`, run:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Install Pi Orchlink on Windows PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/bakhshb/pi-orchlink/main/install.ps1 -OutFile install.ps1
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

Windows installs into `%LOCALAPPDATA%\orchlink`, creates `%LOCALAPPDATA%\orchlink\bin\orch.cmd`, and can offer the general skill at `%USERPROFILE%\.agents\skills\orchlink`.

Useful Windows options:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Ref main -Force
powershell -ExecutionPolicy Bypass -File .\install.ps1 -SkillsOnly
powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoSkills
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
```

Advanced overrides include `ORCHLINK_REPO_URL`, `ORCHLINK_REF`, `ORCHLINK_INSTALL_DIR`, `ORCHLINK_BIN_DIR`, `ORCHLINK_PYTHON`, and `ORCHLINK_SOURCE_DIR`. Close running `orch lead` / `orch work` / Pi terminals before uninstalling so Windows can remove files from the virtual environment.

> **Windows support is currently beta.** The installer supports basic install, update, uninstall, and command shims, but shell/PATH behavior can vary between PowerShell, CMD, Git Bash, and Pi's tool shell. Linux/macOS remain the primary tested paths.

## Start a project

Run this inside the repo where lead and work should help you:

```bash
cd /path/to/your/project
orch init
```

Open the lead and worker in separate terminals:

```bash
orch lead
orch work
```

For external agents or background use, start the worker without blocking the current terminal:

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

For a safe background smoke worker while a visible `work` terminal is open:

```bash
orch work --background --name bg-test --new --replace --oneshot
orch ask bg-test --wait -t BG001 -m "Reply exactly: bg-ok"
```

`orch work --background --test` is the shortcut for that background test worker.

Pin a worker model or default thinking level when needed:

```bash
orch work --background --name review --model openai/codex-max --thinking xhigh
```

Orchlink checks `pi --list-models <model>` before launching a model-pinned worker. It applies thinking per task automatically: review, planning, questioning, and Talk Mode default to `xhigh`; implementation defaults to `medium`. Override one task by adding `--thinking <level>` to `orch ask ...` or `orch send ...`.

Now talk to lead in plain English:

```text
Review this repo and ask work for a second opinion before changing anything.
```

The worker reply appears in the lead chat.

## Goal Mode

Goal Mode is for PRD/plan-driven work where lead should not claim done until acceptance criteria are verified.

```bash
orch goal start "Implement export feature" --prd docs/export-prd.md --derive
orch goal review G001
orch goal gate G001 approve
orch goal work G001 --until done --max-steps 20
orch goal show G001
```

Goal Mode writes durable state under `.orch/goals/Gxxx/`: source, acceptance criteria, plan, coverage, goal status, evidence, blockers, history, audits, and trials.

Useful commands:

| Command | What it does |
| --- | --- |
| `orch goal review G001` | Show source, ACs, plan, coverage, and warnings before approval. |
| `orch goal derive G001` | Ask worker to derive acceptance criteria and a plan. |
| `orch goal gate G001 approve` | Approve the combined AC/plan gate. |
| `orch goal work G001 --until done` | Dispatch bounded worker slices until done, gated, blocked, cancelled, or capped. |
| `orch goal audit G001` | Ask worker to audit artifacts and evidence without editing. |
| `orch goal signoff G001 AC-4` | Human-approve a subjective core AC. |
| `orch goal trial G001 ...` / `orch goal trials G001` | Record and list real PRD trial metrics. |

Objective ACs need check commands for strongest unattended verification. Subjective ACs stop at a signoff gate.

## Worker modes

- **Implementation:** lead sends a scoped task to a worker. Prefer async `orch send` for long work.
- **Review Mode:** work checks a change before lead runs expensive tests, final summaries, release steps, or cleanup.
- **Talk Mode:** lead and work discuss a decision. Work should not edit files in Talk Mode.
- **Background workers:** external agents can run headless workers with `orch work --background`.

Each worker name handles one thing at a time. Different names can run independent work. The same name stays single-flight.

## Recovery and safety

Use `orch resume` first when returning after an interruption, broker restart, cancelled task, or compacted conversation. It prints the active task or goal, lead/work sessions, the last broker checkpoint, drifted leases, and one recommended next command in a single plain-text report.

Use narrower commands when you already know what you need: `orch jobs --idle` for a quick safe/unsafe worker check, `orch jobs` for recent task/talk rows, `orch sessions` for lead/work session leases, and `orch goal show Gxxx` for a specific goal's acceptance evidence.

Before final claims, dependent full tests, packaging, or release notes, make sure worker work is resolved:

```bash
orch jobs --idle
```

If it is not idle, inspect and resolve the exact job:

```bash
orch jobs --active
orch jobs --live T002
orch jobs --result T002
# or only if the result now gates you:
orch jobs --wait T002
```

Cancellation is cooperative. `orch jobs --cancel T002 -m "reason"` marks broker work cancelled and asks Pi to abort the turn. Already-running shell commands may finish.

## Project files

`orch init` creates:

```text
.orch/
  project.yaml
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

Do not commit `.orch/`. Orchlink refreshes stale generated skills and references when you run `orch lead` or `orch work`. You can refresh manually:

```bash
orch init --refresh-skills
```

## Agent skills

This repo includes a general Orchlink lead skill plus adapter skills for OpenClaw and Hermes while Pi runs named worker sessions such as `work`, `review`, or `bg-test`.

External leads should use `orch ask --wait` for short synchronous gates, prefer async `orch send` for long/heavy implementation, broad review, tests, or research, read completed output with `orch jobs --result`, and use `orch jobs --wait` only when a result now blocks the next safe action. Reserve Talk Mode for visible lead/work discussion.

Skill files live here:

```text
skills/general/orchlink/
skills/openclaw/orchlink/
skills/hermes/orchlink/
```

Install skills only:

```bash
./install.sh --skills-only
powershell -ExecutionPolicy Bypass -File .\install.ps1 -SkillsOnly
```

For local development, symlink adapter skills if needed:

```bash
mkdir -p ~/.openclaw/skills ~/.hermes/skills
ln -sfn "$PWD/skills/openclaw/orchlink" ~/.openclaw/skills/orchlink
ln -sfn "$PWD/skills/hermes/orchlink" ~/.hermes/skills/orchlink
```

The general skill is the source of truth for shared content. Keep adapters synchronized with:

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
| `orch jobs T002` | Inspect one job. |
| `orch jobs --active` | Show active/open work. |
| `orch jobs --idle` | Exit 0 only when no active/blocking worker work remains. |
| `orch jobs --live T002` | Show recent activity for one job. |
| `orch jobs --result T002` | Print a terminal result or Talk summary. |
| `orch jobs --wait T002` | Block for one exact result only when it gates your next action. |
| `orch jobs --cancel T002 -m "..."` | Cancel stale or unneeded work. |
| `orch sessions` | Show lead/worker sessions, model, runtime, readiness, and leases. |
| `orch talk`, `orch say`, `orch close` | Manage short Talk Mode discussions. |
| `orch doctor` | Check setup, broker compatibility, Pi command, and generated skills. |
| `orch resume` | Show recovery state and recommended next action. |
| `orch update` | Update Orchlink and print restart guidance. |
| `orch goal ...` | Run Goal Mode. |
| `orch broker status`, `orch broker watch`, `orch broker run` | Raw broker diagnostics and foreground broker run. |

`orch jobs --result` and `orch jobs --wait` refuse cross-project or unscoped task results. If you see a stale-broker error, check no other project needs the broker, then restart fresh sessions:

```bash
orch stop --all
orch lead --new
orch work --new
```

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

- The local broker uses an API key. The default `change-me` value is for local development only.
- Orchlink does not sandbox worker shell commands. Scope worker tasks clearly.
- Scope guardrails are prompt and skill guidance, not an OS sandbox.
- Cancellation is best-effort once Pi has started a tool call.

## Development

Install from a checkout:

```bash
pip install -e ".[dev]"
```

Run tests:

```bash
python3 -c "import pytest, sys; sys.exit(pytest.main(['tests', '-v']))"
```

Run compile checks:

```bash
python3 -m compileall src/orchlink
```
