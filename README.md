# Pi Orchlink

Pi Orchlink lets two local coding agents talk, debate, and review each other inside your terminal — no tmux, Redis, database, or dashboard required.

You run two visible Pi sessions:

```text
Terminal 1: lead
Terminal 2: work
```

You talk to **lead**. Lead can ask **work** for help. Work replies back into the lead chat.

Think of it like this:

```text
you → lead Pi → work Pi → lead Pi → you
```

## Demo

This GIF shows the full demo at 1.5x speed.

![Pi Orchlink demo](media-demo.gif)

## What you need to know

Most of the time, you only type in the lead Pi chat.

The lead agent knows how to use Orchlink commands. It can ask work to inspect code, review changes, discuss a decision, or do a small scoped task.

You only need a few shell commands:

```bash
orch init      # set up this project
orch lead      # open the lead Pi session
orch work      # open the worker Pi session
orch doctor    # check setup
orch update    # update Orchlink
```

Commands like `orch ask`, `orch send`, `orch talk`, `orch say`, and `orch close` are mostly for the lead agent. You can learn them later, but you do not need them to start.

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

## Talk Mode

Talk Mode is for short discussion.

Use it when lead wants a second opinion, a challenge, or a decision. Work should not edit files in Talk Mode.

Example in normal words:

```text
Should we add SQLite now or later? Ask work to challenge the decision.
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

## Project files

`orch init` creates this folder:

```text
.orch/
  project.yaml
  skills/
    lead.md
    work.md
  run/
```

You do not run these files. Orchlink gives them to Pi so lead and work know their roles.

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

After an update, refresh the project instructions and restart:

```bash
orch init --refresh-skills
orch stop
orch lead --new
orch work --new
```

Use `--new` when you want fresh Pi chats with the latest Orchlink instructions.

For real-session validation beyond unit tests, run the manual smoke plan in [`docs/manual-smoke-test.md`](docs/manual-smoke-test.md).

## OpenClaw and Hermes adapter skills

This repo includes adapter skills for using OpenClaw or Hermes as the Orchlink lead while Pi runs the visible `work` session. External leads should prefer `orch ask --wait` for synchronous decisions/reviews, use `orch wait` or `orch get` but not both unless rereading, and reserve Talk Mode for visible lead/work discussion.

```text
skills/openclaw/orchlink/SKILL.md
skills/hermes/orchlink/SKILL.md
```

### OpenClaw install

OpenClaw does not install directly from a raw `SKILL.md` URL. Paste this prompt into OpenClaw instead:

```text
Install the Orchlink skill for this OpenClaw workspace.

Skill URL:
https://raw.githubusercontent.com/bakhshb/pi-orchlink/main/skills/openclaw/orchlink/SKILL.md

Use shell commands to:
1. Create a temporary directory.
2. Download that URL into the temporary directory as SKILL.md.
3. Run: openclaw skills install <temporary-directory> --as orchlink --force
4. Remove the temporary directory.
5. Tell me to start a new OpenClaw session after installation.

Do not install globally unless I explicitly ask for all local OpenClaw agents.
```

If you want a global OpenClaw install, add this line to the prompt:

```text
Install globally by adding --global to the openclaw skills install command.
```

### Hermes install

Hermes supports direct `SKILL.md` URL installs:

```bash
hermes skills install https://raw.githubusercontent.com/bakhshb/pi-orchlink/main/skills/hermes/orchlink/SKILL.md --name orchlink --force --yes
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

## Advanced commands

You normally do not need these. The lead agent uses them when it coordinates with work.

| Command | What it means |
| --- | --- |
| `orch ask work --wait -t T001 -m "..."` | Ask work and wait. Use for external/synchronous decisions and reviews. |
| `orch send work -t T002 -m "..."` | Send work an independent task only when you can work on another scope. |
| `orch talk work -m "..." -r 6` | Start a visible lead/work discussion for up to 6 lead↔worker rounds. Do not use Talk as automation glue. |
| `orch say C001 -m "..."` | Continue a Talk Mode conversation. |
| `orch close C001 -m "..."` | Close Talk Mode with a decision. |
| `orch cancel T002 -m "..."` | Mark broker work CANCELLED immediately and ask Pi to abort the current turn. Future tool calls are blocked; already-running shell commands are best-effort. |
| `orch jobs` | Main browser for recent work in the current project ID. Status is authoritative; active jobs can show last heartbeat/tool activity, but stale heartbeat activity is hidden after terminal jobs. |
| `orch jobs --active` | Show active/open/blocking work. |
| `orch jobs --status STATUS` | Filter by broker status. |
| `orch jobs --kind task\|talk` | Show only task or Talk conversation rows. |
| `orch jobs --id T002` | Focus on one task/conversation/message ID. |
| `orch jobs --json` | Print machine-readable jobs output. |
| `orch idle` | Script/check idle state; exit 0 means idle, exit 1 means active/blocking work exists. |
| `orch peek T002` | Show recent worker heartbeat/tool activity for a long-running task. Short tasks may finish before activity is useful. |
| `orch task T002` | Show focused route/activity status until `orch jobs --id` fully replaces it. |
| `orch get T002` | Read or reread a completed task result. |
| `orch wait T002` | Wait for that exact task result and print worker activity while waiting. This does not cancel the task if the wait times out. Use `wait` or `get`, not both, unless rereading/debugging. |

For big tasks, give work more time when sending the task:

```bash
orch send work -t T010 --timeout 7200 -m "MODE: DO. Implement chunk 1 only."
```

Debug-only commands:

| Command | Use |
| --- | --- |
| `orch status --task T010 --since-id 120 --limit 20` | Print raw broker JSON for debugging; normal agents should not use it for coordination. |
| `orch watch` | Watch broker events, including worker activity heartbeats/tool calls. Lifecycle lines are labeled QUEUED, DELIVERED, or SETTLED. |
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
