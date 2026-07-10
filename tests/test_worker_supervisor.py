import json
import os
import threading

import httpx
import pytest

from orchlink.project.init import init_project
from orchlink.worker import supervisor


def test_lost_session_error_detection_includes_missing_and_conflict_statuses():
    for status_code in (404, 409):
        request = httpx.Request("POST", "http://127.0.0.1:8787/v1/sessions/lease-worker/heartbeat")
        response = httpx.Response(status_code, request=request)
        error = httpx.HTTPStatusError("session lease failed", request=request, response=response)
        assert supervisor._is_lost_session_error(error) is True


def test_worker_supervisor_launches_pi_rpc_child_and_writes_status(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    calls = {"popen": [], "acquire": [], "release": []}

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def acquire_session(self, role, pid, lease_id=None, metadata=None):
            calls["acquire"].append((role, pid, lease_id, metadata))
            return "lease-worker"

        def heartbeat_session(self, lease_id, metadata=None):
            return None

        def release_session(self, lease_id, reason):
            calls["release"].append((lease_id, reason))

        def _release_session(self, lease_id, reason):
            # Compatibility wrapper mirroring PiConnector.
            self.release_session(lease_id, reason)

        def env(self, role, extra=None):
            return {"PATH": "", **(extra or {})}

        def _env(self, role, extra=None):
            # Compatibility wrapper mirroring PiConnector.
            return self.env(role, extra=extra)

        def work_rpc_argv(self):
            return ["pi", "--mode", "rpc", "--no-extensions", "--extension", "orchlink-pi-extension.ts"]

    class FakeProcess:
        pid = 2468
        stdout = ['{"type":"response","success":true}\n']

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def fake_popen(command, **kwargs):
        calls["popen"].append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(supervisor, "PiConnector", FakePiConnector)
    monkeypatch.setattr(supervisor.subprocess, "Popen", fake_popen)
    (tmp_path / ".orch" / "run" / "orch-work.pid").write_text(str(os.getpid()), encoding="utf-8")

    result = supervisor.run_supervisor(tmp_path, model="openai/codex-max", thinking="xhigh", oneshot=True)

    status = json.loads((tmp_path / ".orch" / "run" / "orch-work-status.json").read_text(encoding="utf-8"))
    assert result == 0
    assert calls["acquire"][0][0] == "work"
    assert calls["acquire"][0][3]["backend"] == "rpc-supervisor"
    assert calls["acquire"][0][3]["model"] == "openai/codex-max"
    assert calls["acquire"][0][3]["thinking"] == "xhigh"
    assert calls["acquire"][0][3]["project_dir"] == str(tmp_path)
    assert calls["popen"][0][0] == ["pi", "--mode", "rpc", "--no-extensions", "--extension", "orchlink-pi-extension.ts"]
    assert calls["popen"][0][1]["cwd"] == tmp_path
    assert calls["popen"][0][1]["stdin"] is supervisor.subprocess.PIPE
    assert calls["popen"][0][1]["stdout"] is supervisor.subprocess.PIPE
    assert calls["popen"][0][1]["env"]["ORCHLINK_SESSION_LEASE_ID"] == "lease-worker"
    assert calls["popen"][0][1]["env"]["ORCHLINK_ONESHOT"] == "true"
    assert calls["release"] == [("lease-worker", "Background worker supervisor exited.")]
    assert not (tmp_path / ".orch" / "run" / "orch-work.pid").exists()
    assert status["status"] == "exited"
    assert status["model"] == "openai/codex-max"
    assert status["thinking"] == "xhigh"
    assert status["oneshot"] is True
    assert status["pi_pid"] == 2468
    assert status["project_dir"] == str(tmp_path)


def test_worker_supervisor_project_dir_argument_sets_child_cwd_and_metadata(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    calls = {"popen": [], "acquire": []}

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def acquire_session(self, role, pid, lease_id=None, metadata=None):
            calls["acquire"].append((role, pid, lease_id, metadata, self.config))
            return "lease-worker"

        def heartbeat_session(self, lease_id, metadata=None):
            return None

        def release_session(self, lease_id, reason):
            return None

        def _release_session(self, lease_id, reason):
            # Compatibility wrapper mirroring PiConnector.
            return self.release_session(lease_id, reason)

        def env(self, role, extra=None):
            return {"PATH": "", **(extra or {})}

        def _env(self, role, extra=None):
            # Compatibility wrapper mirroring PiConnector.
            return self.env(role, extra=extra)

        def work_rpc_argv(self):
            return ["pi", "--mode", "rpc"]

    class FakeProcess:
        pid = 2468
        stdout = []

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def fake_popen(command, **kwargs):
        calls["popen"].append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(supervisor, "PiConnector", FakePiConnector)
    monkeypatch.setattr(supervisor.subprocess, "Popen", fake_popen)

    result = supervisor.main([
        "--project-root",
        str(tmp_path),
        "--worker-name",
        "maker-1",
        "--project-dir",
        str(worktree),
    ])

    status = json.loads((tmp_path / ".orch" / "run" / "workers" / "maker-1" / "orch-work-status.json").read_text(encoding="utf-8"))
    assert result == 0
    assert calls["popen"][0][1]["cwd"] == worktree.resolve()
    assert calls["acquire"][0][3]["project_dir"] == str(worktree.resolve())
    assert calls["acquire"][0][4]["_project_root"] == str(tmp_path)
    assert calls["acquire"][0][4]["work"]["project_dir"] == str(worktree.resolve())
    assert status["project_dir"] == str(worktree.resolve())


def test_worker_supervisor_heartbeat_metadata_includes_project_dir(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    worktree = tmp_path / "wt"
    worktree.mkdir()
    heartbeat_seen = threading.Event()
    calls = {"heartbeat": []}

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def acquire_session(self, role, pid, lease_id=None, metadata=None):
            return "lease-worker"

        def heartbeat_session(self, lease_id, metadata=None):
            calls["heartbeat"].append((lease_id, metadata))
            heartbeat_seen.set()

        def release_session(self, lease_id, reason):
            return None

        def _release_session(self, lease_id, reason):
            # Compatibility wrapper mirroring PiConnector.
            return self.release_session(lease_id, reason)

        def env(self, role, extra=None):
            return {"PATH": "", **(extra or {})}

        def _env(self, role, extra=None):
            # Compatibility wrapper mirroring PiConnector.
            return self.env(role, extra=extra)

        def work_rpc_argv(self):
            return ["pi", "--mode", "rpc"]

    class FakeProcess:
        pid = 2468

        def __init__(self):
            self.stdout = self

        def __iter__(self):
            return self

        def __next__(self):
            if heartbeat_seen.wait(3):
                raise StopIteration
            raise AssertionError("heartbeat did not fire")

        def poll(self):
            return 0 if heartbeat_seen.is_set() else None

        def wait(self, timeout=None):
            heartbeat_seen.wait(timeout or 3)
            return 0

    monkeypatch.setattr(supervisor, "PiConnector", FakePiConnector)
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(supervisor, "broker_session_heartbeat_interval_seconds", lambda config: 1)

    result = supervisor.run_supervisor(tmp_path, project_dir=worktree)

    assert result == 0
    assert calls["heartbeat"]
    assert calls["heartbeat"][0][1]["project_dir"] == str(worktree.resolve())


def test_worker_supervisor_cli_rejects_invalid_project_dir_before_launch(monkeypatch, tmp_path, capsys):
    init_project(tmp_path, project_id="demo")
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        supervisor,
        "run_supervisor",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not launch child")),
    )

    with pytest.raises(SystemExit) as missing:
        supervisor.main(["--project-root", str(tmp_path), "--project-dir", str(tmp_path / "missing")])
    assert missing.value.code != 0
    assert "--project-dir path does not exist" in capsys.readouterr().err

    with pytest.raises(SystemExit) as file_error:
        supervisor.main(["--project-root", str(tmp_path), "--project-dir", str(file_path)])
    assert file_error.value.code != 0
    assert "--project-dir path is not a directory" in capsys.readouterr().err


def test_worker_supervisor_stops_rpc_child_when_session_lease_is_lost(monkeypatch, tmp_path):
    init_project(tmp_path, project_id="demo")
    calls = {"release": [], "terminated": False}
    created = {}

    class FakePiConnector:
        def __init__(self, config):
            self.config = config

        def acquire_session(self, role, pid, lease_id=None, metadata=None):
            return "lease-worker"

        def heartbeat_session(self, lease_id, metadata=None):
            request = httpx.Request("POST", "http://127.0.0.1:8787/v1/sessions/lease-worker/heartbeat")
            response = httpx.Response(404, request=request, json={"detail": "Session not found: lease-worker"})
            raise httpx.HTTPStatusError("session missing", request=request, response=response)

        def release_session(self, lease_id, reason):
            calls["release"].append((lease_id, reason))

        def _release_session(self, lease_id, reason):
            # Compatibility wrapper mirroring PiConnector.
            self.release_session(lease_id, reason)

        def env(self, role, extra=None):
            return {"PATH": "", **(extra or {})}

        def _env(self, role, extra=None):
            # Compatibility wrapper mirroring PiConnector.
            return self.env(role, extra=extra)

        def work_rpc_argv(self):
            return ["pi", "--mode", "rpc"]

    class FakeProcess:
        pid = 2468

        def __init__(self):
            self._stopped = threading.Event()
            self.stdout = self

        def __iter__(self):
            return self

        def __next__(self):
            if self._stopped.wait(3):
                raise StopIteration
            raise AssertionError("stale session heartbeat did not stop the RPC child")

        def poll(self):
            return -15 if self._stopped.is_set() else None

        def wait(self, timeout=None):
            self._stopped.wait(timeout or 3)
            return -15 if self._stopped.is_set() else 0

        def terminate(self):
            calls["terminated"] = True
            self._stopped.set()

    def fake_popen(command, **kwargs):
        process = FakeProcess()
        created["process"] = process
        return process

    def fake_terminate_process_tree(process, timeout=5.0):
        process.terminate()

    monkeypatch.setattr(supervisor, "PiConnector", FakePiConnector)
    monkeypatch.setattr(supervisor.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(supervisor, "_terminate_process_tree", fake_terminate_process_tree)
    monkeypatch.setattr(supervisor, "broker_session_heartbeat_interval_seconds", lambda config: 1)

    result = supervisor.run_supervisor(tmp_path)

    status = (tmp_path / ".orch" / "run" / "orch-work-status.json").read_text(encoding="utf-8")
    assert result == -15
    assert calls["terminated"] is True
    assert calls["release"] == [("lease-worker", "Background worker supervisor exited.")]
    assert '"status": "exited"' in status
    assert '"stopped_reason": "session_lost"' in status
    assert '"session_lost_error":' in status
    assert created["process"].poll() == -15


def test_default_process_terminator_delegates_to_monkeypatched_terminate_tree(monkeypatch):
    """The supervisor's process boundary must still honor the legacy
    ``supervisor._terminate_process_tree`` monkeypatch seam."""
    invoked = []

    def fake_terminate_tree(process, timeout=5.0):
        invoked.append((process, timeout))

    monkeypatch.setattr(supervisor, "_terminate_process_tree", fake_terminate_tree)

    sentinel = object()
    supervisor.DefaultProcessTerminator().terminate(sentinel, timeout=2.5)

    assert invoked == [(sentinel, 2.5)]


def test_build_launch_spec_and_runtime_paths_resolve_worker_locations(tmp_path):
    init_project(tmp_path, project_id="demo")

    spec = supervisor.build_launch_spec(
        tmp_path,
        worker_name="maker-1",
        model="openai/codex-max",
        thinking="xhigh",
        oneshot=True,
    )

    assert spec.project_root == tmp_path
    assert spec.worker_name == "maker-1"
    assert spec.role == "work"
    assert spec.oneshot is True
    assert spec.config["work"]["model"] == "openai/codex-max"
    assert spec.config["work"]["thinking"] == "xhigh"
    assert spec.project_dir == supervisor.project_root(spec.config)
    with pytest.raises(TypeError):
        spec.config["project_id"] = "mutated"
    with pytest.raises(TypeError):
        spec.config["work"]["model"] = "mutated"

    paths = supervisor.build_runtime_paths(spec)
    run_root = supervisor.run_dir(spec.config)
    assert paths.run_dir == run_root
    assert paths.worker_dir == run_root / "workers" / "maker-1"
    assert paths.pid_path == paths.worker_dir / "orch-work.pid"
    assert paths.child_pid_path == paths.worker_dir / "orch-work-child.pid"
    assert paths.status_path == paths.worker_dir / "orch-work-status.json"

    # Default "work" worker uses the run root directly as its worker dir.
    work_spec = supervisor.build_launch_spec(tmp_path)
    assert work_spec.worker_name == "work"
    assert supervisor.build_runtime_paths(work_spec).worker_dir == supervisor.run_dir(work_spec.config)


def test_build_launch_spec_records_project_dir_override(tmp_path):
    init_project(tmp_path, project_id="demo")
    worktree = tmp_path / "wt"
    worktree.mkdir()

    spec = supervisor.build_launch_spec(tmp_path, project_dir=worktree)

    assert spec.project_dir == worktree.resolve()
    assert spec.config["work"]["project_dir"] == str(worktree.resolve())


def test_injected_process_controller_receives_acquired_lease_env(monkeypatch, tmp_path):
    """An injected controller receives the acquired lease as explicit spawn data."""
    init_project(tmp_path, project_id="demo")
    env_calls = []
    spawned = {}

    class RecordingConnector:
        def __init__(self, config):
            self.config = config

        def acquire_session(self, role, pid, lease_id=None, metadata=None):
            return "lease-injected"

        def heartbeat_session(self, lease_id, metadata=None):
            return None

        def release_session(self, lease_id, reason):
            return None

        def _release_session(self, lease_id, reason):
            return self.release_session(lease_id, reason)

        def env(self, role, extra=None):
            env_calls.append((role, dict(extra or {})))
            return {"PATH": "", **(extra or {})}

        def _env(self, role, extra=None):
            return self.env(role, extra=extra)

        def work_rpc_argv(self):
            return ["pi", "--mode", "rpc"]

    class FakeProcess:
        pid = 1357
        stdout = []

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    def fake_popen(command, **kwargs):
        spawned["env"] = kwargs["env"]
        return FakeProcess()

    monkeypatch.setattr(supervisor, "PiConnector", RecordingConnector)
    monkeypatch.setattr(supervisor.subprocess, "Popen", fake_popen)

    controller = supervisor.ProcessController(
        RecordingConnector({}),
        env_extras={"ORCHLINK_FROBNICATE": "1"},
    )

    result = supervisor.run_supervisor(tmp_path, process_controller=controller)

    assert result == 0
    # The injected controller's own extra survives...
    assert spawned["env"]["ORCHLINK_FROBNICATE"] == "1"
    # ...and the explicit acquired lease id is included without mutating the controller.
    assert spawned["env"]["ORCHLINK_SESSION_LEASE_ID"] == "lease-injected"
    assert controller.child_env("work", "lease-injected")["ORCHLINK_SESSION_LEASE_ID"] == "lease-injected"
    assert env_calls and env_calls[-1][1]["ORCHLINK_SESSION_LEASE_ID"] == "lease-injected"


def test_injected_boundaries_are_used_for_lease_status_and_metadata(monkeypatch, tmp_path):
    """Injected lease gateway, status writer, and the controller plumbing must
    all be honored, and the status payload must carry the prior field set."""
    init_project(tmp_path, project_id="demo")
    events = {"gateway": [], "writes": []}

    class FakeGateway:
        def acquire(self, role, pid, metadata):
            events["gateway"].append(("acquire", role, pid, metadata))
            return "lease-gw"

        def heartbeat(self, lease_id, metadata):
            events["gateway"].append(("heartbeat", lease_id, metadata))

        def release(self, lease_id, reason):
            events["gateway"].append(("release", lease_id, reason))

    class FakeWriter:
        def __init__(self, paths):
            self.paths = paths

        def write(self, status, **extra):
            events["writes"].append((status, extra))

    class FakeConnector:
        def __init__(self, config):
            self.config = config

        def acquire_session(self, role, pid, lease_id=None, metadata=None):
            return "lease-worker"

        def heartbeat_session(self, lease_id, metadata=None):
            return None

        def release_session(self, lease_id, reason):
            return None

        def _release_session(self, lease_id, reason):
            return self.release_session(lease_id, reason)

        def env(self, role, extra=None):
            return {"PATH": "", **(extra or {})}

        def _env(self, role, extra=None):
            return self.env(role, extra=extra)

        def work_rpc_argv(self):
            return ["pi", "--mode", "rpc"]

    class FakeProcess:
        pid = 4242
        stdout = []

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(supervisor, "PiConnector", FakeConnector)
    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    paths = supervisor.build_runtime_paths(supervisor.build_launch_spec(tmp_path, model="m", thinking="high", oneshot=True))

    result = supervisor.run_supervisor(
        tmp_path,
        model="m",
        thinking="high",
        oneshot=True,
        lease_gateway=FakeGateway(),
        status_writer=FakeWriter(paths),
    )

    assert result == 0
    acquire_event = events["gateway"][0]
    assert acquire_event[0] == "acquire"
    assert acquire_event[1] == "work"
    assert acquire_event[2] == os.getpid()
    assert any(event[0] == "release" and event[1] == "lease-gw" for event in events["gateway"])

    by_status = {name: extra for name, extra in events["writes"]}
    assert set(["starting", "running", "exited"]).issubset(by_status)
    running = by_status["running"]
    # Prior status field set must remain present.
    for field in (
        "backend",
        "runtime_mode",
        "project_id",
        "agent_id",
        "worker_name",
        "session_id",
        "model",
        "thinking",
        "supervisor_pid",
        "oneshot",
        "project_dir",
        "lease_id",
        "pi_pid",
        "updated_at",
    ):
        assert field in running, f"running status missing prior field {field}"
    assert running["lease_id"] == "lease-gw"
    assert running["pi_pid"] == 4242
    assert running["model"] == "m"
    assert running["oneshot"] is True
