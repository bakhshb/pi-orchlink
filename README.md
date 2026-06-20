# Orchlink

Orchlink connects two visible Pi coding-agent sessions through a local broker.

- Terminal 1 runs the lead Pi session.
- Terminal 2 runs the worker Pi session.
- The lead sends a task with `orch ask`.
- The worker receives the task inside its Pi chat and replies from that chat.

## Requirements

- Python 3.11+
- `git`
- Pi installed and available as `pi`

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/bakhshb/orchlink/main/install.sh | bash
```

The installer creates an isolated venv under `~/.local/share/orchlink` and links these commands into `~/.local/bin`:

```text
orch
orchlink
```

If your shell cannot find `orch`, add this to your shell profile:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Installer options:

```bash
INSTALL_URL="https://raw.githubusercontent.com/bakhshb/orchlink/main/install.sh"

# Install a specific branch, tag, or commit
curl -fsSL "$INSTALL_URL" | bash -s -- --ref main

# Use a custom install or bin directory
curl -fsSL "$INSTALL_URL" | bash -s -- --dir ~/.local/share/orchlink --bin-dir ~/.local/bin

# Uninstall
curl -fsSL "$INSTALL_URL" | bash -s -- --uninstall
```

## Quick start

Run these commands inside the project where you want the two Pi sessions to work.

```bash
orch init
```

Open the lead session:

```bash
orch lead
```

Open the worker session in another terminal:

```bash
orch work
```

Send a task from the lead session, or from any shell in the same project:

```bash
orch ask work -t T001 -m "Inspect the project and return PLAN only."
```

`orch ask` queues the task and returns immediately. The worker reply appears in the lead Pi chat. If you want the shell command to block until the reply arrives, add `--wait`.

Watch broker events if you want a third terminal:

```bash
orch watch
```

Stop the project broker:

```bash
orch stop
```

## Project files

`orch init` creates:

```text
.orch/project.yaml
.orch/skills/lead.md
.orch/skills/work.md
.orch/run/
```

The default project id comes from the folder name. The default agent ids are:

```text
<project_id>.lead
<project_id>.work
```

## Commands

| Command | Purpose |
| --- | --- |
| `orch init` | Create `.orch/` config and role instructions. |
| `orch lead` | Start the visible lead Pi session. |
| `orch work` | Start the visible worker Pi session. |
| `orch work --no-pi` | Run the worker listener without opening Pi. |
| `orch ask work -t T001 -m "..."` | Queue a task for the worker. |
| `orch ask work --wait -t T001 -m "..."` | Send a task and block until the reply arrives. |
| `orch watch` | Show queued tasks, delivered tasks, replies, and timeouts. |
| `orch stop` | Stop the project broker and worker listener. |
| `orch doctor` | Check local setup. |

For broker debugging:

```bash
orch broker run --host 127.0.0.1 --port 8787
```

## Configuration

Edit project settings in:

```text
.orch/project.yaml
```

To change the broker port, update both `broker.url` and `broker.port`:

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

To change the Pi executable:

```yaml
pi:
  command: pi
```

By default, Orchlink starts Pi with named sessions:

```bash
pi --session-id lead ...
pi --session-id work ...
```

The worker session loads an Orchlink Pi extension, so incoming tasks appear in the visible worker chat.

## Security

- The broker binds to `127.0.0.1` by default.
- `/health` is public.
- `/v1/*` endpoints require `X-API-Key`.
- Orchlink stores the broker API key in `.orch/project.yaml`.
- `.env` files are not required.
- `.orch/` contains project-local runtime config and should not be committed.
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
