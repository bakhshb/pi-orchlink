# Orchlink

Orchlink lets two local Pi coding-agent sessions talk through a small Python broker.

Daily flow:

```bash
orch init          # once per project
orch lead          # terminal 1
orch work          # terminal 2
orch ask work -t T001 -m "Inspect the project and return PLAN only."
orch watch         # optional terminal 3
```

The broker starts automatically for `lead`, `work`, `ask`, and `watch` when `.orch/project.yaml` has `broker.auto_start: true`.

## Install

### One-line install

Install with:

```bash
curl -fsSL https://raw.githubusercontent.com/bakhshb/orchlink/main/install.sh | bash
```

The installer:

- clones/updates Orchlink under `~/.local/share/orchlink`
- creates an isolated Python venv at `~/.local/share/orchlink/.venv`
- installs the package into that venv
- links `orch` and `orchlink` into `~/.local/bin`

If your shell cannot find `orch`, add this to your shell profile:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Advanced install options:

```bash
# Install from a specific repo/ref
curl -fsSL https://raw.githubusercontent.com/bakhshb/orchlink/main/install.sh | \
  bash -s -- --repo https://github.com/bakhshb/orchlink.git --ref main

# Custom install location
curl -fsSL https://raw.githubusercontent.com/bakhshb/orchlink/main/install.sh | \
  bash -s -- --dir ~/.local/share/orchlink --bin-dir ~/.local/bin

# Remove the installed copy
curl -fsSL https://raw.githubusercontent.com/bakhshb/orchlink/main/install.sh | bash -s -- --uninstall
```

### Developer install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

`pyproject.toml` exposes both `orch` and the legacy `orchlink` command.

## Project setup

Run inside the project you want the two Pi sessions to work on:

```bash
orch init
```

This creates:

```text
.orch/project.yaml
.orch/skills/lead.md
.orch/skills/work.md
.orch/run/
```

The default project id is the current folder name. The default agents are `<project_id>.lead` and `<project_id>.work`.

## Commands

```bash
orch lead
```

Registers the lead and launches a visible Pi lead session with the lead instructions.

```bash
orch work
```

Registers the worker and opens the visible Pi worker session. Incoming tasks are posted directly into that Pi chat; the worker's visible assistant response is returned to the lead.

For debugging without opening Pi:

```bash
orch work --no-pi
```

```bash
orch ask work --task T001 --msg "Return PLAN only."
```

Sends a TASK to the worker and waits for PLAN, RESULT, or BLOCKER.

```bash
orch watch
```

Shows broker events such as queued tasks, delivered tasks, replies, and timeouts.

```bash
orch stop
orch doctor
```

Stops the project broker PID or checks local setup.

For debugging:

```bash
orch broker run --host 127.0.0.1 --port 8787
```

## Pi connector

By default Orchlink calls:

```bash
pi --session-id lead ...
pi --session-id work ...              # visible worker session with Orchlink extension
pi --print --session-id work ...      # fallback/debug worker task execution
```

You can change the executable in `.orch/project.yaml`:

```yaml
pi:
  command: pi
```

If the command is missing, the worker returns a BLOCKER explaining that the Pi command must be installed or configured.

## Security notes

- `/health` is public.
- `/v1/*` endpoints require `X-API-Key`.
- The default `change-me` key is only for local development.
- The broker binds to `127.0.0.1` by default.
- API keys are not printed by the CLI.

## Tests

```bash
python3 -c "import pytest, sys; sys.exit(pytest.main(['tests', '-v']))"
python3 -m compileall src/orchlink
```
