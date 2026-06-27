# Pi Orchlink

Loop engineering for local Pi coding agents.

Set a goal. Orchlink keeps a **lead Pi** and **work Pi** moving through scoped tasks until the acceptance criteria prove the work is done.

Pi Orchlink turns two visible Pi sessions into a local goal loop. You give lead a PRD, implementation plan, or plain-English goal. Lead derives acceptance criteria, prompts work for the next slice, checks the evidence, and continues until the goal is done, blocked, or needs your signoff.

No tmux. No Redis. No dashboard. No hosted workflow engine. Just two Pi sessions, a small local broker, and durable project files.

Think of it like this:

```text
you → lead Pi → work Pi → lead Pi → you
```

## Demo

This GIF shows the full demo at 1.5x speed.

![Pi Orchlink demo](media-demo.gif)

## A Pi-to-Pi goal loop

Most agent workflows stop at one prompt. Larger work needs a loop: remember the goal, choose the next slice, verify it, then continue.

Pi Orchlink gives two local Pi agents that loop:

1. Capture the source: a PRD, implementation plan, or short goal.
2. Turn it into concrete acceptance criteria.
3. Review the plan before work starts.
4. Send the next bounded slice to the worker.
5. Run checks and record evidence.
6. Repeat until the criteria pass.
7. Stop for human signoff when judgment is required.

The result is not “the agent said it finished.” The result is a goal with criteria, checks, evidence, blockers, and history.

## What you need to know

Most of the time, you only type in the lead Pi chat.

The lead agent knows how to use Orchlink commands. It can turn a spec into tracked work, ask the worker to inspect code, review changes, discuss a decision, or handle a scoped implementation slice.

You only need a few human-facing shell commands:

```bash
orch init      # set up this project
orch lead      # open the lead Pi session
orch work      # open the worker Pi session
orch goal      # run spec/plan work until criteria are verified
orch doctor    # check setup
orch sessions  # see active lead/work Pi sessions
orch jobs      # see recent/current work
orch stop      # stop the project broker
orch update    # update Orchlink
```

Commands like `orch ask`, `orch send`, `orch talk`, `orch say`, `orch close`, `orch wait`, `orch get`, `orch idle`, `orch peek`, and `orch cancel` are mostly for the lead agent. Debug commands like `orch status`, `orch watch`, `orch task`, and `orch broker run` are for troubleshooting.

## Install

You need:

- Python 3.11+
- `git`
- Pi installed as `pi`

Install Pi Orchlink:

```bash
curl -fsSL https://raw.githubusercontent.com/bakhshb/pi-orchlink/main/install.sh | bash
```

The installer puts Orchlink in `~/.local/share/orchlink` and links the `orch` command into `~/.local/bin`.

If your shell cannot find `orch`, run:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

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

## How lead should use work

Lead has one worker lane. That means work handles one thing at a time.

Good:

```text
lead asks work to review the plan
work replies
lead decides what to do next
```

Bad:

```text
lead sends three tasks before work finishes the first one
```

Pi Orchlink blocks that kind of stacking.

## Broker lifetime

`orch lead` and `orch work` keep a project-local broker alive while their Pi sessions are open, even if those sessions are idle. When the last lead/work session closes and no active jobs remain, the broker can stop automatically. Closing the worker session cancels active worker-owned work so lead does not wait forever for a reply that cannot arrive.

Use `orch stop` when you want to force-stop the broker manually.

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

Goal Mode still uses the existing lead/work lane. It does not add parallel workers, a scheduler, a dashboard, or a direct LLM client inside Orchlink core. Objective ACs need check commands for strongest unattended verification; subjective ACs stop at a signoff gate.

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

After an update, restart the broker and Pi sessions:

```bash
orch stop
orch lead --new
orch work --new
```

`orch lead` and `orch work` refresh stale generated skills automatically. Use `--new` when you want fresh Pi chats with the latest Orchlink instructions.

For real-session validation beyond unit tests, run the manual smoke plan in [`docs/manual-smoke-test.md`](docs/manual-smoke-test.md).

## OpenClaw and Hermes adapter skills

This repo includes adapter skills for using OpenClaw or Hermes as the Orchlink lead while Pi runs the visible `work` session. External leads should prefer `orch ask --wait` for synchronous decisions/reviews, use `orch wait` or `orch get` but not both unless rereading, and reserve Talk Mode for visible lead/work discussion.

```text
skills/openclaw/orchlink/SKILL.md
skills/openclaw/orchlink/references/*.md
skills/hermes/orchlink/SKILL.md
skills/hermes/orchlink/references/*.md
```

The adapter `SKILL.md` files are intentionally small routers. Detailed command, Goal Mode, and recovery guidance lives in bundled `references/` files so external agents only load the extra context when needed.

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
| `orch work` | Start or reopen the visible worker Pi session. |
| `orch doctor` | Check local setup, broker compatibility, Pi command, and generated skills. |
| `orch sessions` | Show active lead/work Pi sessions for this project. Use `--all` to include released session history. |
| `orch jobs` | Main browser for recent work in the current project ID. Status is authoritative. |
| `orch goal ...` | Run PRD/plan-driven Goal Mode from source → ACs/plan/coverage → verified work. |
| `orch stop` | Stop the project broker. |
| `orch update` | Update Orchlink and print restart/refresh guidance. |

### Agent coordination commands

You normally do not type these yourself. The lead agent uses them when it coordinates with work.

| Command | What it means |
| --- | --- |
| `orch ask work --wait -t T001 -m "..."` | Ask work and wait. Use for synchronous decisions and reviews. |
| `orch send work -t T002 -m "..."` | Send work an independent task only when lead can work on another scope. |
| `orch talk work -m "..." -r 6` | Start a visible lead/work discussion for up to 6 lead↔worker rounds. Do not use Talk as automation glue. |
| `orch say C001 -m "..."` | Continue a Talk Mode conversation. |
| `orch close C001 -m "..."` | Close Talk Mode with a decision. |
| `orch wait T002` | Wait for that exact task result and print worker activity while waiting. This does not cancel the task if the wait times out. |
| `orch get T002` | Read or reread a completed task result. Use `wait` or `get`, not both routinely. |
| `orch idle` | Script/check idle state; exit 0 means idle, exit 1 means active/blocking work exists. |
| `orch peek T002` | Show recent worker heartbeat/tool activity for long-running work. Short tasks may finish before activity is useful. |
| `orch cancel T002 -m "..."` | Mark broker work CANCELLED immediately and ask Pi to abort the current turn. Future tool calls are blocked when possible; already-running shell commands are best-effort. |

Useful `jobs` filters:

| Command | What it means |
| --- | --- |
| `orch jobs --active` | Show active/open/blocking work. |
| `orch jobs --status STATUS` | Filter by broker status. |
| `orch jobs --kind task\|talk` | Show only task or Talk conversation rows. |
| `orch jobs --id T002` | Focus on one task/conversation/message ID. |
| `orch jobs --json` | Print machine-readable jobs output. |

For big tasks, give work more time when sending the task:

```bash
orch send work -t T010 --timeout 7200 -m "Implement chunk 1 only."
```

### Debug/reference commands

| Command | Use |
| --- | --- |
| `orch status --task T010 --since-id 120 --limit 20` | Print raw broker JSON for debugging; normal agents should not use it for coordination. |
| `orch watch` | Watch broker events, including worker activity heartbeats/tool calls. Lifecycle lines are labeled QUEUED, DELIVERED, or SETTLED. |
| `orch task T010` | Show focused route/activity status until `orch jobs --id` fully replaces it. |
| `orch broker run --host 127.0.0.1 --port 8787` | Run the broker by hand. |

`orch get` and `orch wait` refuse cross-project or unscoped task results. If you see a stale-broker error, stop the old broker and restart fresh sessions:

```bash
orch stop
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
orch status --task T010 --since-id 120 --limit 20
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

Then restart:

```bash
orch stop
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
