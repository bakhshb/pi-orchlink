# Orchlink Agent Notes

Use these notes when working in this repository.

- Runtime code lives under `src/orchlink`; import modules as `orchlink.*`.
- The CLI entry point is declared in `pyproject.toml` as `orch = "orchlink.cli.main:app"`.
- Install locally with `pip install -e ".[dev]"`; do not rely on top-level `broker`, `bridge`, or `cli` packages.
- The user installer is `install.sh`; it installs into `~/.local/share/orchlink`, creates a venv, and links `orch` into `~/.local/bin`.
- Start the broker with `orch broker run` or app path `orchlink.broker.main:app`.
- Run tests through Python in this environment: `python3 -c "import pytest, sys; sys.exit(pytest.main(['tests', '-v']))"`.
- Run compile checks with `python3 -m compileall src/orchlink`.
- Use `ctx` for local agent-history lookup when prior decisions, previous attempts, or conversation context may matter: start with `ctx status`, `ctx sources`, then `ctx search "<topic>"`; cite relevant ctx session/event IDs when relying on history.
- When bumping the Orchlink version, update only `pyproject.toml`; runtime package and broker versions read from package metadata via `orchlink.version`. Do not add separate hardcoded version constants.
- For release bumps, verify tests/compile/sync as appropriate, then commit, push, and move/create the requested git tag only when the user explicitly asks for git operations.
- This workspace may not be a git repository; do not run commit, branch, merge, or PR steps unless `.git/` exists or the user asks.
- Do not log or print real API keys. The `change-me` key is for local development only.
