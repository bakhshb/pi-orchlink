# Orchlink Agent Notes

Use these notes when working in this repository.

- Runtime code lives under `src/orchlink`; import modules as `orchlink.*`.
- The CLI entry points are declared in `pyproject.toml` as `orch = "orchlink.cli.main:app"` and legacy `orchlink = "orchlink.cli.main:app"`.
- Install locally with `pip install -e ".[dev]"`; do not rely on top-level `broker`, `bridge`, or `cli` packages.
- The user installer is `install.sh`; it installs into `~/.local/share/orchlink`, creates a venv, and links `orch`/`orchlink` into `~/.local/bin`.
- Start the broker with `orch broker run` or app path `orchlink.broker.main:app`.
- Run tests through Python in this environment: `python3 -c "import pytest, sys; sys.exit(pytest.main(['tests', '-v']))"`.
- Run compile checks with `python3 -m compileall src/orchlink`.
- This workspace may not be a git repository; do not run commit, branch, merge, or PR steps unless `.git/` exists or the user asks.
- Do not log or print real API keys. The `change-me` key is for local development only.
