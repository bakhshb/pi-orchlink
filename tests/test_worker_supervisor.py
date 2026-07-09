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

        def _release_session(self, lease_id, reason):
            calls["release"].append((lease_id, reason))

        def _env(self, role, extra=None):
            return {"PATH": "", **(extra or {})}

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

        def _release_session(self, lease_id, reason):
            return None

        def _env(self, role, extra=None):
            return {"PATH": "", **(extra or {})}

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

        def _release_session(self, lease_id, reason):
            return None

        def _env(self, role, extra=None):
            return {"PATH": "", **(extra or {})}

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

        def _release_session(self, lease_id, reason):
            calls["release"].append((lease_id, reason))

        def _env(self, role, extra=None):
            return {"PATH": "", **(extra or {})}

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
