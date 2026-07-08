# Pi Orchlink

Loop engineering for local Pi coding agents.

Set a goal. Prove it is done.

A single Pi chat can propose code. Real project work needs more: a loop that remembers the goal, delegates bounded slices, checks evidence, and stops when judgment needs a human. Orchlink gives Pi users that loop without a hosted platform.

The lead Pi orchestrates. Orchlink provides the wiring and accountability layer: a small local broker, generated lead/work skills, and project files under `.orch/`. Named worker Pis like `work`, `review`, and `bg-test` keep separate context and accept one task at a time.

```text
you → lead Pi → named worker Pi → lead Pi → you
```

No Redis. No dashboard. No hosted workflow engine. Just local Pi sessions, a local broker, and durable project files.

## The loop

Most agent workflows stop at one prompt. Larger work needs a loop: remember the goal, choose the next slice, verify it, then continue.

Pi Orchlink gives local Pi agents that loop:

1. Capture the source: a PRD, implementation plan, or short goal.
2. Turn it into concrete acceptance criteria.
3. Review the plan before work starts.
4. Send the next bounded slice to a named worker.
5. Run checks and record evidence.
6. Repeat until the criteria pass.
7. Stop for human signoff when judgment is required.

The result is not “the agent said it finished.” The result is a goal with criteria, checks, evidence, blockers, and history.

## What makes Orchlink different

- **Proof over claims.** Goal Mode tracks acceptance criteria, check commands, evidence, blockers, and signoff instead of trusting a final chat message.
- **Named workers you can see and stop.** `work`, `review`, and `bg-test` are durable Pi contexts, not an anonymous worker pool.
- **Single-flight by worker name.** Orchlink prevents you from stacking three tasks onto one worker context by accident.
- **Local and Pi-first.** The broker runs on your machine, the sessions are Pi sessions, and the project state lives under `.orch/`.
- **Lead-owned decisions.** The lead Pi chooses the next safe slice. Orchlink routes work and records state; it does not become an autonomous scheduler.

## What you need to know

Most of the time, you only type in the lead Pi chat.

The lead agent knows how to use Orchlink commands. It can turn a spec into tracked work, ask the worker to inspect code, review changes, discuss a decision, or handle a scoped implementation slice.

You only need a few human-facing shell commands:

```bash
orch init      # set up this project
orch lead      # open the lead Pi session
orch work      # open the default worker Pi session; add --name for another worker
orch goal      # run spec/plan work until criteria are verified
orch doctor    # check setup
orch sessions  # see active lead and named worker Pi sessions
orch jobs      # see recent/current work
orch stop      # stop this project's default background worker; add --name for another
orch update    # update Orchlink
```

Commands like `orch ask`, `orch send`, `orch talk`, `orch say`, `orch close`, `orch jobs --result`, `orch jobs --wait`, `orch jobs --idle`, `orch jobs --live`, and `orch jobs --cancel` are mostly for the lead agent. Debug commands like `orch broker status`, `orch broker watch`, and `orch broker run` are for troubleshooting.

## Install

You need:

- Python 3.11+
- `git`
- Pi installed as `pi`

Install Pi Orchlink on Linux or macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/bakhshb/pi-orchlink/main/install.sh | bash
```

The Linux/macOS installer puts Orchlink in `~/.local/share/orchlink` and links the `orch` command into `~/.local/bin`. In an interactive terminal, it can also offer to install optional Orchlink skills: the general skill to `~/.agents/skills/orchlink`, plus OpenClaw/Hermes skills only when those commands are detected.

If your shell cannot find `orch`, run:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Install Pi Orchlink on Windows PowerShell:

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/bakhshb/pi-orchlink/main/install.ps1 -OutFile install.ps1
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

The Windows installer puts Orchlink in `%LOCALAPPDATA%\orchlink`, creates `%LOCALAPPDATA%\orchlink\bin\orch.cmd`, and adds that `bin` directory to your user PATH. Open a new terminal if `orch` is not found immediately.

Useful Windows installer options:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Ref main -Force
powershell -ExecutionPolicy Bypass -File .\install.ps1 -SkillsOnly
powershell -ExecutionPolicy Bypass -File .\install.ps1 -NoSkills
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Uninstall
```

Close running `orch lead` / `orch work` / Pi terminals before uninstalling so Windows can remove files from the virtual environment. In an interactive terminal, the installer can also offer optional skill installs: the general skill to `%USERPROFILE%\.agents\skills\orchlink`, plus OpenClaw/Hermes skills only when those commands are detected. Advanced overrides are available through `-Repo`, `-Ref`, `-Dir`, `-BinDir`, `-Python` or the matching environment variables: `ORCHLINK_REPO_URL`, `ORCHLINK_REF`, `ORCHLINK_INSTALL_DIR`, `ORCHLINK_BIN_DIR`, `ORCHLINK_PYTHON`, and `ORCHLINK_SOURCE_DIR`. If your project path contains spaces, quote it when passing arguments through `orch.cmd`.

> **Windows support is currently beta.** The installer supports basic install, update, uninstall, and command shims, but shell/PATH behavior can vary between PowerShell, CMD, Git Bash, and Pi's tool shell. Linux/macOS remain the primary tested paths; if a behavior differs on Windows, compare against the Linux/macOS flow before reporting it as a blocker.

## Start a project

Run this inside the project where lead and work should help you:

```bash
cd /path/to/your/project
orch init
```

Open terminal 1:

```bash
orch lead
```

Open terminal 2:

```bash
orch work
```

For external agents or background use, start the worker without blocking the current terminal:

```bash
orch work --background
```

This starts the headless Pi RPC worker named `work`, writes `.orch/run/orch-work.pid` and `.orch/run/orch-work.log`, waits for readiness, and returns. For a fresh task-scoped background worker that should exit after one completed task reply, use `orch work --background --new --replace --oneshot`. Use a visible worker terminal (`orch work --new`) if background readiness fails or you want to watch the Pi worker chat.

Named workers need no YAML setup. Start another durable worker context with `--name` and target it by that name:

```bash
orch work --name review
orch send review -t R001 -m "Review only, no edits."
```

To test background mode while a visible `orch work` terminal is already open, do not replace it. Start an isolated named worker instead:

```bash
orch work --background --name bg-test --new --replace --oneshot
orch ask bg-test --wait -t BG001 -m "Reply exactly: bg-ok"
# --oneshot exits after the reply; use orch stop --name bg-test only for persistent background workers.
```

`orch work --background --test` is the shortcut for that background test worker.

Headless and visible workers can also be started with an explicit Pi model and default thinking level:

```bash
orch work --background --name review --model openai/codex-max --thinking xhigh
```

Before starting a model-pinned worker, Orchlink checks `pi --list-models <model>`. If the model is not registered or available, it stops before launching the worker and prints available models to choose from.

Orchlink applies thinking per task automatically: review, planning, questioning, and Talk Mode default to `xhigh`; implementation work defaults to `medium`. Override one task by adding `--thinking <level>` to `orch ask ...` or `orch send ...`. `orch sessions` shows the worker model and reported thinking level when Pi reports them.

Now talk to the lead Pi session. For example:

```text
Review this repo and ask work for a second opinion before changing anything.
```

or:

```text
Improve the settings page UI. Ask work to review before you run full tests.
```

The worker reply appears inside the lead chat.

## Normal day-to-day use

1. Start `orch lead`.
2. Start `orch work`.
3. Talk to lead in plain English.
4. Lead sends focused work to the worker when useful.
5. Lead waits for worker review before risky next steps.
6. You read the final answer in the lead chat.

You should not need to watch the broker or copy messages between terminals.

### Checking whether it is safe to continue

Use `orch resume` first when returning after an interruption, broker restart, cancelled task, or compacted conversation. It prints the active task or goal, lead/work sessions, the last broker checkpoint, drifted leases, and one recommended next command in a single plain-text report.

Use the narrower commands when you already know what you need: `orch jobs --idle` for a quick safe/unsafe worker check, `orch jobs` for recent task/talk rows, `orch sessions` for lead/work session leases, and `orch goal show Gxxx` for a specific goal's acceptance evidence.

## How lead should use work

Each worker name handles one thing at a time. The default worker is `work`; additional names like `review` or `bg-test` are separate contexts that must be targeted explicitly.

Good:

```text
lead asks work to review the plan
work replies
lead decides what to do next

lead sends an unrelated test-only task to bg-test
bg-test replies without stealing work's context
```

Bad:

```text
lead sends three tasks to the same worker before that worker finishes the first one
```

Pi Orchlink blocks that kind of same-worker stacking.

## Broker lifetime

`orch lead` and `orch work` keep a project-local broker alive while their Pi sessions are open, even if those sessions are idle. When the last lead/work session closes and no active jobs remain, the broker can stop automatically. Closing the worker session cancels active worker-owned work so lead does not wait forever for a reply that cannot arrive.

Use `orch stop --broker` or `orch stop --all` when you want to force-stop the broker manually. Plain `orch stop` only stops this project's tracked background worker so it does not disrupt other projects sharing the default broker.

## Talk Mode

Talk Mode is for short discussion.

Use it when lead wants a second opinion, a challenge, or a decision. Work should not edit files in Talk Mode.

Example in normal words:

```text
Should we keep this behavior simple or split it into a new command? Ask work to challenge the decision.
```

Lead will start the talk, read the worker reply, ask a follow-up if needed, then close the conversation when there is a decision. Talk replies are plain teammate chat, not a required template.

Talk Mode stops when there is:

- a clear decision
- a next task
- a blocker
- no useful next question
- max rounds reached
- a timeout

## Review Mode

Review Mode is for checking work before lead continues.

Use it when you want work to say one of these:

```text
proceed
fix this first
ask a follow-up
avoid full tests for now
```

Example:

```text
Ask work to review these changes before you run the full test suite.
```

Lead should not run big tests, final summaries, release steps, or cleanup that depends on the review until work replies.

## Goal Mode

Goal Mode is for PRD/plan-driven work where lead should not claim "done" until acceptance criteria are verified.

Typical flow:

```bash
orch goal start "Implement export feature" --prd docs/export-prd.md --derive
orch goal review G001
# inspect/edit .orch/goals/G001/acceptance.md, plan.md, and coverage.md if needed
orch goal gate G001 approve
orch goal work G001 --until done --max-steps 20
orch goal show G001
```

You can also start from a plan or short inline goal:

```bash
orch goal start "Implement export feature" --plan docs/export-plan.md --derive
orch goal start "Small cleanup" --text "Refactor export validation with tests" --derive
```

If the plan exists only in chat/context, the lead should first write it to a normal markdown file, then use `--plan`. There is no special chat-plan CLI:

```bash
mkdir -p .orch/goals/inbox
# lead writes .orch/goals/inbox/export-plan.md from the conversation plan
orch goal start "Implement export feature" --plan .orch/goals/inbox/export-plan.md --derive
```

Goal Mode writes durable state under `.orch/goals/Gxxx/`:

```text
source.md      captured PRD/plan/text source
acceptance.md  editable ACs with status/checks/dependencies
plan.md        editable execution plan
coverage.md    optional source/AC/plan coverage report
goal.yaml      goal status, evidence, blockers, deferrals
history.jsonl  append-only goal events
audit.md       optional audit result
trials.jsonl   optional real-PRD trial records
```

Useful commands:

| Command | What it does |
| --- | --- |
| `orch goal review G001` | Show source summary, ACs, plan, coverage, and warnings before approval. |
| `orch goal derive G001` | Ask worker to derive acceptance, plan, and optional coverage artifacts. |
| `orch goal gate G001 approve` | Approve the current AC/plan gate. |
| `orch goal work G001 --until done` | Keep dispatching bounded worker slices until done, gated, blocked, cancelled, or capped. |
| `orch goal audit G001` | Ask worker to audit artifacts/evidence without editing or closing the goal. |
| `orch goal signoff G001 AC-4` | Human-approve a subjective core AC. |
| `orch goal trial G001 ...` / `orch goal trials G001` | Record and list real PRD trial metrics. |

Goal Mode still uses the existing lead/work route by default. It does not add a scheduler, a dashboard, or a direct LLM client inside Orchlink core. Objective ACs need check commands for strongest unattended verification; subjective ACs stop at a signoff gate.

## Project files

`orch init` creates this folder:

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

You do not run these files. Orchlink gives the small lead/work skills to Pi, and the skills load reference files only when a task needs detailed command, Goal Mode, review, or recovery guidance. `orch lead` and `orch work` refresh stale or missing generated skill files and references from the installed templates before starting Pi.

Do not commit `.orch/`.

## Check setup

Run:

```bash
orch doctor
```

It checks the project-local `.orch` files and tells you if they are old.

If you see this:

```text
Project .orch files: stale
Run: orch init --refresh-skills
```

run:

```bash
orch init --refresh-skills
```

Then restart the Pi sessions.

## Update

Run:

```bash
orch update
```

After an update, restart Pi sessions; stop the broker only if no other project is using it:

```bash
orch stop --all
orch lead --new
orch work --new
```

`orch lead` and `orch work` refresh stale generated skills automatically. Use `--new` when you want fresh Pi chats with the latest Orchlink instructions.

For real-session validation beyond unit tests, run the manual smoke plan in [`docs/manual-smoke-test.md`](docs/manual-smoke-test.md).

## Agent skills

This repo includes a general Orchlink lead skill plus adapter skills for OpenClaw and Hermes while Pi runs named worker sessions such as `work`, `review`, or `bg-test`. External leads should use `orch ask --wait` for short synchronous gates, prefer async `orch send` for long/heavy implementation, broad review, tests, or research, read completed output with `orch jobs --result`, and use `orch jobs --wait` only when a result now blocks the next safe action. Reserve Talk Mode for visible lead/work discussion.

```text
skills/general/orchlink/SKILL.md
skills/general/orchlink/references/*.md
skills/openclaw/orchlink/SKILL.md
skills/openclaw/orchlink/references/*.md
skills/hermes/orchlink/SKILL.md
skills/hermes/orchlink/references/*.md
```

Run the installer with `--skills-only` / `-SkillsOnly` to install skills without reinstalling Orchlink. The installer always offers the general skill target `~/.agents/skills/orchlink`; OpenClaw and Hermes options appear only when their commands are detected.

The skill `SKILL.md` files are intentionally small routers. Detailed command, Goal Mode, and recovery guidance lives in bundled `references/` files so external agents only load the extra context when needed. The general skill is the canonical source for shared reference content; keep adapter references synchronized with `python3 skills/sync_orchlink_skills.py` or check drift with `python3 skills/sync_orchlink_skills.py --check`.

### OpenClaw install

OpenClaw needs the whole skill directory so bundled `references/` are installed with `SKILL.md`. Paste this prompt into OpenClaw:

```text
Install the Orchlink skill for this OpenClaw workspace.

Use shell commands to:
1. Create a temporary directory.
2. Run: git clone --depth 1 https://github.com/bakhshb/pi-orchlink.git <temporary-directory>/pi-orchlink
3. Run: openclaw skills install <temporary-directory>/pi-orchlink/skills/openclaw/orchlink --as orchlink --force
4. Remove the temporary directory.
5. Tell me to start a new OpenClaw session after installation.

Do not install globally unless I explicitly ask for all local OpenClaw agents.
```

If you want a global OpenClaw install, add this line to the prompt:

```text
Install globally by adding --global to the openclaw skills install command.
```

### Hermes install

Hermes should also install the whole skill directory so bundled `references/` are available:

```bash
tmpdir=$(mktemp -d)
git clone --depth 1 https://github.com/bakhshb/pi-orchlink.git "$tmpdir/pi-orchlink"
hermes skills install "$tmpdir/pi-orchlink/skills/hermes/orchlink" --name orchlink --force --yes
rm -rf "$tmpdir"
```

### Local checkout install

If you already have a local Orchlink checkout, you can install from disk instead:

```bash
mkdir -p ~/.agents/skills
cp -R ./skills/general/orchlink ~/.agents/skills/orchlink
openclaw skills install ./skills/openclaw/orchlink --as orchlink --force
hermes skills install ./skills/hermes/orchlink --name orchlink --force --yes
```

### Developer symlink install

For development, use symlinks so edits in this repo are picked up without reinstalling:

```bash
mkdir -p ~/.openclaw/skills ~/.hermes/skills
ln -sfn "$PWD/skills/openclaw/orchlink" ~/.openclaw/skills/orchlink
ln -sfn "$PWD/skills/hermes/orchlink" ~/.hermes/skills/orchlink
```

Start a new OpenClaw/Hermes session after installing or changing skill files.

## Command reference

Orchlink has one `orch` command, but the commands are meant for different audiences.

### Human daily commands

| Command | What it means |
| --- | --- |
| `orch init` | Set up `.orch/` for the current project. |
| `orch lead` | Start or reopen the visible lead Pi session. |
| `orch work` | Start or reopen the visible worker named `work`; use `--name review` for another named worker, `--background --test` for isolated background smoke, `--new --replace --oneshot` for fresh task-scoped background work, and `--model`/`--thinking` to pin worker runtime defaults. |
| `orch doctor` | Check local setup, broker compatibility, Pi command, and generated skills. |
| `orch sessions` | Show active lead and named worker Pi sessions, model, reported thinking, runtime/backend, ready state, and leases. Use `--name` or `--all` as needed. |
| `orch jobs` | Main browser for recent work in the current project ID. Status is authoritative. |
| `orch goal ...` | Run PRD/plan-driven Goal Mode from source → ACs/plan/coverage → verified work. |
| `orch stop` | Stop this project's tracked default background worker; use `--name bg-test` for a named worker. |
| `orch stop --broker` / `orch stop --all` | Stop the broker, or both worker and broker, when no other project needs it. |
| `orch update` | Update Orchlink and print restart/refresh guidance. |

### Agent coordination commands

You normally do not type these yourself. The lead agent uses them when it coordinates with work.

| Command | What it means |
| --- | --- |
| `orch ask work --wait -t T001 -m "..."` | Ask a named worker and wait for short reviews, decisions, or blockers that gate the next safe action. Replace `work` with `review`, `test`, etc. when targeting another worker. Do not use it for long/heavy implementation that can be dispatched with `send`. Use `--edit` or `--message-file` for long prompts; add `--thinking xhigh` to override one task. |
| `orch send work -t T002 -m "..."` | Send one named worker an independent task when lead can work on another scope. Prefer this for implementation, broad review, tests, or research. Different names can run in parallel; one name stays single-flight. Record the task ID, continue only on non-conflicting lead-owned work, and retrieve the result later with `orch jobs --result`. Use `orch jobs --wait` only when that result now blocks the next step. Use `--edit` or `--message-file` for long prompts; add `--thinking medium` to override one task. |
| `orch talk work -m "..." -r 6` | Start a visible lead/work discussion for up to 6 lead↔worker rounds. Use `--edit` or `--message-file` for long messages. Do not use Talk as automation glue. |
| `orch say C001 -m "..."` | Continue a Talk Mode conversation. Use `--edit` or `--message-file` for long messages. |
| `orch close C001 -m "..."` | Close Talk Mode with a decision. Use `--edit` or `--message-file` for long messages. |
| `orch jobs --result T002` | Read or reread a completed task result. Prefer this when the async task is already terminal. |
| `orch jobs --wait T002` | Wait for that exact task result and print worker activity while waiting. Use only when the result now gates your next action. This does not cancel the task if the wait times out. |
| `orch jobs --idle` | Script/check idle state across workers; add `--name review` for one named worker. |
| `orch jobs --live T002` | Show recent worker heartbeat/tool activity for long-running work; add `--name review` to focus on one worker. |
| `orch jobs --cancel T002 -m "..."` | Mark broker work CANCELLED immediately and ask Pi to abort the current turn. Future tool calls are blocked when possible; already-running shell commands are best-effort. |

Useful `jobs` filters:

| Command | What it means |
| --- | --- |
| `orch jobs T002` | Inspect one job's current status and route. |
| `orch jobs --active` | Show active/open/blocking work. |
| `orch jobs --idle` | Exit 0 only when no active/blocking worker work remains. |
| `orch jobs --live T002` | Show recent activity for one task/conversation. |
| `orch jobs --result T002` | Print a terminal result or Talk summary. |
| `orch jobs --wait T002` | Block for one exact task result only when it gates your next action. |
| `orch jobs --cancel T002 -m "..."` | Cancel stale or unneeded work. |
| `orch jobs --status STATUS` | Filter by broker status. |
| `orch jobs --kind task\|talk` | Show only task or Talk conversation rows. |
| `orch jobs --id T002` | Focus on one task/conversation/message ID. |
| `orch jobs --json` | Print machine-readable jobs output. |
| `orch jobs --name review` | Filter jobs for one named worker. |

If the status commands feel similar, use this order:

1. `orch jobs --idle` answers "can I proceed?" with a yes/no exit code.
2. `orch jobs --active` answers "what is currently busy?"
3. `orch jobs --live <task_id>` answers "what progress has that busy task reported?"
4. `orch jobs --result <task_id>` retrieves the final result; `orch jobs --wait <task_id>` blocks only when that result now gates you.
5. `orch broker status` is raw broker JSON for debugging, not the normal human status command.

For big tasks, give work more time when sending the task. For long prompts, use the editor or a prompt file so shell quoting stays clean:

```bash
orch send work -t T010 --timeout 7200 -m "Implement chunk 1 only."
orch send work -t T011 --edit
orch send work -t T012 --message-file .orch/prompts/chunk-2.md
```

### Debug/reference commands

| Command | Use |
| --- | --- |
| `orch broker status --task T010 --since-id 120 --limit 20` | Print raw broker JSON for debugging; normal agents should not use it for coordination. |
| `orch broker watch` | Watch broker events, including worker activity heartbeats/tool calls. Lifecycle lines are labeled QUEUED, DELIVERED, or SETTLED. |
| `orch broker run --host 127.0.0.1 --port 8787` | Run the broker by hand. |

`orch jobs --result` and `orch jobs --wait` refuse cross-project or unscoped task results. If you see a stale-broker error, stop the old broker only after checking no other project needs it, then restart fresh sessions:

```bash
orch stop --all
orch lead --new
orch work --new
```

Use built-in help to see command and option descriptions:

```bash
orch --help
orch jobs --help
```

For checking visible Pi sessions, prefer the readable session command:

```bash
orch sessions
orch sessions --all
```

For broker debugging in long sessions, filter raw status output instead of dumping everything:

```bash
orch broker status --task T010 --since-id 120 --limit 20
# use --all-projects only for broker debugging
```

## Configuration

Project settings live in:

```text
.orch/project.yaml
```

You usually do not need to edit it.

If you change the broker port, update both `broker.url` and `broker.port`:

```yaml
broker:
  url: http://127.0.0.1:8788
  host: 127.0.0.1
  port: 8788
```

Then restart that project's sessions. Stop the old broker only if no other project needs it:

```bash
orch stop --all
orch lead
orch work
```

Optional local JSONL snapshots can be enabled for broker state recovery:

```yaml
broker:
  store_backend: jsonl
  store_path: .orch/run/orchlink-journal.jsonl
```

The default store is `memory`. The `jsonl` store is still intentionally simple:
it writes full local snapshots, may grow over time, and can redeliver in-flight
non-terminal work after a broker restart. Completed task results and queued work
are restored.

## Security

- The broker listens on `127.0.0.1` by default.
- Broker API calls require `X-API-Key`.
- Orchlink stores the local broker key in `.orch/project.yaml`.
- `.env` files are not needed.
- Do not commit `.orch/`.
- The CLI does not print API keys.

## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Run checks:

```bash
python3 -c "import pytest, sys; sys.exit(pytest.main(['tests', '-v']))"
python3 -m compileall src/orchlink
```
