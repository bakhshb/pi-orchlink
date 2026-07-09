from orchlink.connector import pi_connector
from orchlink.connector.pi_connector import PiConnector, PiSessionLease
from orchlink.project.config import load_project_config
from orchlink.project.init import init_project


class FakeThread:
    def __init__(self, target=None, args=(), daemon=None):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True

    def join(self, timeout=None):
        return None


class StopAfterOne:
    def __init__(self):
        self.calls = 0

    def wait(self, interval):
        self.calls += 1
        return self.calls > 1


def test_visible_worker_acquire_metadata_contains_configured_project_dir(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    config = load_project_config(tmp_path)
    config["work"] = {**config["work"], "project_dir": str(worktree)}
    connector = PiConnector(config)
    calls = []

    monkeypatch.setattr(pi_connector.threading, "Thread", FakeThread)
    monkeypatch.setattr(
        connector,
        "_post_broker",
        lambda path, body: calls.append((path, body)) or {"session": {"lease_id": "lease-work"}},
    )

    lease = PiSessionLease(connector, "work", 123).acquire()

    assert lease.lease_id == "lease-work"
    assert calls[0][0] == "/v1/sessions/acquire"
    assert calls[0][1]["project_dir"] == str(worktree)
    assert calls[0][1]["project_id"] == "demo"


def test_visible_worker_heartbeat_metadata_contains_project_dir(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    config = load_project_config(tmp_path)
    config["work"] = {**config["work"], "project_dir": str(worktree)}
    connector = PiConnector(config)
    calls = []

    monkeypatch.setattr(pi_connector, "broker_session_heartbeat_interval_seconds", lambda config: 1)
    monkeypatch.setattr(connector, "heartbeat_session", lambda lease_id, metadata=None: calls.append((lease_id, metadata)))

    connector._heartbeat_loop("lease-work", StopAfterOne(), {"project_dir": str(worktree)})

    assert calls == [("lease-work", {"project_dir": str(worktree)})]


def test_env_exposes_explicit_project_dir_override(tmp_path):
    init_project(tmp_path, project_id="demo")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    connector = PiConnector(load_project_config(tmp_path))

    env = connector._env("work", project_dir=worktree)

    assert env["ORCHLINK_PROJECT_ID"] == "demo"
    assert env["ORCHLINK_PROJECT_DIR"] == str(worktree)
