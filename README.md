# Orchlink

Orchlink lets two visible Pi coding-agent sessions talk through a local broker.

```text
Terminal 1: visible lead Pi session
Terminal 2: visible worker Pi session
```

You talk mainly to the lead. The lead can ask, send, or talk to the worker. The worker receives messages inside its own Pi chat and replies back into the lead chat.

No tmux, database, Redis, or dashboard is required.

## Requirements

- Python 3.11+
- `git`
- Pi installed as `pi`

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/bakhshb/orchlink/main/install.sh | bash
```

The installer creates a venv in `~/.local/share/orchlink` and links `orch` into `~/.local/bin`.

If your shell cannot find `orch`:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## First run

Run this in the project where the two Pi sessions should work:

```bash
cd /path/to/your/project
orch init
```

Start the lead in terminal 1:

```bash
orch lead
```

Start the worker in terminal 2:

```bash
orch work
```

Both sessions listen through the broker. Worker task results and Talk Mode replies appear in the lead Pi chat.

## Command model

| Command | Use |
| --- | --- |
| `orch talk work -m "..." -r 6` | Think together. The worker can challenge assumptions and recommend a decision. No file edits. |
| `orch ask work --wait -t T001 -m "..."` | Blocking decision gate. Use when the lead cannot continue without the worker answer. |
| `orch send work -t T002 -m "..."` | Async worker task. Use when the lead can continue elsewhere. |
| `orch say C001 -m "..."` | Send the next turn in an open Talk Mode conversation. |
| `orch close C001 -m "..."` | Close a conversation with a final decision. |
| `orch jobs` | List recent tasks and conversations. |
| `orch get T002` | Read an async task result if ready. |
| `orch wait T002` | Block until an async task finishes or times out. |
| `orch watch` | Observe task and chat events. |

## Talk Mode example

```bash
orch lead
orch work
orch talk work -m "Should we add SQLite now or later?" -r 6
orch say C001 -m "Challenge the restart risk."
orch close C001 -m "Decision: memory only for MVP, SQLite later behind MessageStore."
```

Talk Mode is for reasoning only. The worker should compare options, identify risks, disagree when useful, and recommend a practical decision. It must not edit files.

`orch talk` starts the conversation; it is not meant to be a one-shot answer. Continue with `orch say` until Talk Mode reaches a stop condition, then use `orch close` before summarizing.

Talk Mode stops when it has produced one of: clear decision, next task, blocker, max rounds, timeout, or no new value.

`C001` is a conversation ID. Use it with `orch say` and `orch close`. `orch get` and `orch wait` are for task IDs like `T010`.

Write Talk Mode messages like a conversation, not a task spec. Avoid `TASK_ID`, scope, permission, and expected-reply boilerplate in `orch talk` / `orch say` messages.

For broad prompts like "what do you think about the repo?", Talk Mode should stay high-level and conversational. The worker should use current context and a few high-signal files if useful, not read every file unless you ask for an exhaustive audit.

A good Talk Mode exchange uses short turns:

```text
lead: What is your high-level take on this repo?
work: It looks broad, but the plugin boundary seems like the main design bet.
lead: Let's break that down. Which part worries you first?
work: Persistence ownership. I would check whether plugins leak too much into core migrations.
```

## Async task example

```bash
orch send work -t T010 -m "MODE: PLAN. Inspect tests. Do not edit files."
orch jobs
orch get T010
```

Use `orch wait T010` when you want the shell to block until the result arrives.

## Blocking ask example

```bash
orch ask work --wait -t T001 -m "Review this plan and tell me whether to proceed."
```

Use this for decision gates. Use `orch send` for independent work.

Worker REVIEW is a gate by default. If a review can change the next action, use `orch ask --wait`; do not start full tests or release steps until the review result arrives. `orch send` rejects `MODE: REVIEW` unless you pass `--allow-async-review` for an unrelated, non-gating review.

## Skills and project files

`orch init` creates:

```text
.orch/project.yaml
.orch/skills/lead.md
.orch/skills/work.md
.orch/run/
```

You do not run the skill files. Orchlink loads them into Pi so the lead and worker know their roles.

Refresh role instructions without changing project config:

```bash
orch init --refresh-skills
```

## Fresh Pi sessions

By default, `orch lead` and `orch work` reopen saved Pi histories named `lead` and `work`.

Start clean histories when needed:

```bash
orch lead --new
orch work --new
```

## Other useful commands

| Command | Use |
| --- | --- |
| `orch stop` | Stop the project broker. |
| `orch doctor` | Check setup. |
| `orch update` | Pull latest Orchlink code and reinstall it. |
| `orch work --no-pi` | Run the worker listener without opening Pi. Debug only. |
| `orch broker run --host 127.0.0.1 --port 8787` | Run the broker manually. Debug only. |

## Configuration

Project settings live in:

```text
.orch/project.yaml
```

Change the broker port by updating both `broker.url` and `broker.port`:

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

Change the Pi executable if needed:

```yaml
pi:
  command: pi
```

## Security

- The broker binds to `127.0.0.1` by default.
- `/v1/*` endpoints require `X-API-Key`.
- Orchlink stores the broker API key in `.orch/project.yaml`.
- `.env` files are not required.
- Do not commit `.orch/`.
- `change-me` is for local development only.
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
