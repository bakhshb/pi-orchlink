# Orchlink

Orchlink lets two visible Pi coding-agent sessions talk to each other through a local broker.

You use two terminals:

```text
Terminal 1: lead Pi session
Terminal 2: worker Pi session
```

You talk to the lead. The lead can send messages to the worker. The worker receives those messages inside its own Pi chat and replies back to the lead chat.

## Requirements

- Python 3.11+
- `git`
- Pi installed as `pi`

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/bakhshb/orchlink/main/install.sh | bash
```

The installer creates its own venv in `~/.local/share/orchlink` and links `orch` into `~/.local/bin`.

If your shell cannot find `orch`, add this to your shell profile:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## First run

Go to the project where you want the two Pi sessions to work:

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

Now talk to the lead Pi session. Ask it to collaborate with the worker when useful.

Example message to the lead:

```text
Inspect this project. Discuss the workload with the worker first, then propose a plan.
```

The lead can send the worker a message with:

```bash
orch ask work -t PLAN-001 -m "Review the workload, identify risks, and propose how lead and worker should split this. Return PLAN only."
```

The worker reply appears back in the lead Pi chat.

## Daily commands

Use these most of the time:

| Command | Use |
| --- | --- |
| `orch init` | Set up Orchlink in the current project. Run once. |
| `orch lead` | Open the lead Pi session. |
| `orch work` | Open the worker Pi session. |
| `orch ask work -t T001 -m "..."` | Queue a message for the worker. |
| `orch task T001` | Check whether a task is queued, in progress, or complete. |
| `orch stop` | Stop the project broker. |

## How `orch ask` works

Default mode is async:

```bash
orch ask work -t T001 -m "Inspect auth and return PLAN."
```

Use this when the lead can work on a different scope while the worker thinks. The lead should treat the worker's scope as pending until the reply arrives.

Check progress without sleeping or guessing:

```bash
orch task T001
```

Use `--wait` when the next lead decision depends on the worker reply:

```bash
orch ask work --wait -t T001 -m "Inspect auth and return PLAN."
```

## Fresh Pi sessions

By default, `orch lead` and `orch work` reopen the saved Pi histories named `lead` and `work`.

Start clean histories when needed:

```bash
orch lead --new
orch work --new
```

## Skills and project files

`orch init` creates:

```text
.orch/project.yaml
.orch/skills/lead.md
.orch/skills/work.md
.orch/run/
```

You do not run the skill files. Orchlink loads them into Pi so the lead and worker know their roles.

Refresh the role instructions without changing project config:

```bash
orch init --refresh-skills
```

## Other useful commands

| Command | Use |
| --- | --- |
| `orch watch` | Show broker events in a third terminal. |
| `orch doctor` | Check setup. |
| `orch update` | Pull the latest Orchlink code and reinstall it. |
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
