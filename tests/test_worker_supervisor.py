from orchlink.project.init import init_project
from orchlink.worker import supervisor


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

    result = supervisor.run_supervisor(tmp_path)

    status = (tmp_path / ".orch" / "run" / "orch-work-status.json").read_text(encoding="utf-8")
    assert result == 0
    assert calls["acquire"][0][0] == "work"
    assert calls["acquire"][0][3]["backend"] == "rpc-supervisor"
    assert calls["popen"][0][0] == ["pi", "--mode", "rpc", "--no-extensions", "--extension", "orchlink-pi-extension.ts"]
    assert calls["popen"][0][1]["cwd"] == tmp_path
    assert calls["popen"][0][1]["stdin"] is supervisor.subprocess.PIPE
    assert calls["popen"][0][1]["stdout"] is supervisor.subprocess.PIPE
    assert calls["popen"][0][1]["env"]["ORCHLINK_SESSION_LEASE_ID"] == "lease-worker"
    assert calls["release"] == [("lease-worker", "Background worker supervisor exited.")]
    assert '"status": "exited"' in status
    assert '"pi_pid": 2468' in status
