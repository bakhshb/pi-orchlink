from orchlink.connector.pi_extension import ORCHLINK_PI_UI_EXTENSION, ensure_orchlink_ui_extension
from orchlink.project.init import init_project


def test_generated_ui_extension_is_read_only_tui_monitor():
    assert "/v1/status?limit=200" in ORCHLINK_PI_UI_EXTENSION
    assert "/v1/activity?item_id=${encodeURIComponent(itemId)}&limit=30" in ORCHLINK_PI_UI_EXTENSION
    assert "X-Orchlink-Project-ID" in ORCHLINK_PI_UI_EXTENSION
    assert "ORCHLINK_BROKER_URL" in ORCHLINK_PI_UI_EXTENSION
    assert "ORCHLINK_API_KEY" in ORCHLINK_PI_UI_EXTENSION
    assert "ORCHLINK_MONITOR_POLL_SECONDS" in ORCHLINK_PI_UI_EXTENSION
    assert "pi.registerCommand(\"orchlink\"" in ORCHLINK_PI_UI_EXTENSION
    assert "ctx.mode === \"tui\"" in ORCHLINK_PI_UI_EXTENSION
    assert "pi.setSessionName(nextName)" in ORCHLINK_PI_UI_EXTENSION
    assert "ctx.ui.setStatus" not in ORCHLINK_PI_UI_EXTENSION
    assert "footerStatusText" not in ORCHLINK_PI_UI_EXTENSION
    assert "ctx.ui.setWidget" not in ORCHLINK_PI_UI_EXTENSION
    assert "ctx.ui.custom" in ORCHLINK_PI_UI_EXTENSION
    assert "worker - ${row.workerName}" in ORCHLINK_PI_UI_EXTENSION
    assert "Orchlink Lead · ${active} active · ${idle} idle" in ORCHLINK_PI_UI_EXTENSION
    assert "LEGACY_STATUS_KEYS" not in ORCHLINK_PI_UI_EXTENSION
    assert "\\u001b[90m${value}\\u001b[0m" not in ORCHLINK_PI_UI_EXTENSION
    assert "activityByItem" in ORCHLINK_PI_UI_EXTENSION
    assert "formatActivityRecord" in ORCHLINK_PI_UI_EXTENSION
    assert "ACTIVE_SESSION_STATUSES" in ORCHLINK_PI_UI_EXTENSION
    assert "activeSession(session)" in ORCHLINK_PI_UI_EXTENSION
    assert "overlayHandle?.requestRender?.()" in ORCHLINK_PI_UI_EXTENSION
    assert "onHandle: (handle: any)" in ORCHLINK_PI_UI_EXTENSION
    assert "spawn(\"orch\", [\"stop\", \"--name\", workerName]" in ORCHLINK_PI_UI_EXTENSION
    assert "Stop background worker ${result.workerName}?" in ORCHLINK_PI_UI_EXTENSION
    assert "Visible worker terminals are not stopped from this panel." in ORCHLINK_PI_UI_EXTENSION
    assert "stop it from its own terminal with Ctrl-C" in ORCHLINK_PI_UI_EXTENSION
    assert "isBackground ? \"stop\" : \"visible\"" in ORCHLINK_PI_UI_EXTENSION
    assert "s stop worker" in ORCHLINK_PI_UI_EXTENSION
    assert "q/Esc close" in ORCHLINK_PI_UI_EXTENSION
    assert "workers none" in ORCHLINK_PI_UI_EXTENSION
    assert "Orchlink Workers" not in ORCHLINK_PI_UI_EXTENSION
    assert "clearTimeout(timer)" in ORCHLINK_PI_UI_EXTENSION
    assert "abortController?.abort()" in ORCHLINK_PI_UI_EXTENSION
    assert "this.selected = Math.min(Math.max(0, rows.length - 1), this.selected + 1)" in ORCHLINK_PI_UI_EXTENSION
    assert "Math.min(this.maxOffset(), this.offset + 8)" in ORCHLINK_PI_UI_EXTENSION
    assert "minWidth: 32" in ORCHLINK_PI_UI_EXTENSION
    assert "anchor: \"center\"" in ORCHLINK_PI_UI_EXTENSION
    assert "visible: (termWidth" not in ORCHLINK_PI_UI_EXTENSION

    forbidden = [
        "pi.sendUserMessage",
        "pi.sendMessage",
        "deliverAs:",
        "ctx.compact",
        "session_before_compact",
        "session_compact",
        "ORCHLINK_POLL_WAIT_SECONDS",
        "compact-phase",
        "phaseCompactionInstructions",
        "pi.registerCommand(\"orch\",",
    ]
    for needle in forbidden:
        assert needle not in ORCHLINK_PI_UI_EXTENSION


def test_ensure_orchlink_ui_extension_writes_project_run_file(tmp_path):
    paths = init_project(tmp_path, project_id="demo")
    path = ensure_orchlink_ui_extension({"_project_root": str(tmp_path)})

    assert path == paths["run_dir"] / "orchlink-pi-ui-extension.ts"
    assert path.read_text(encoding="utf-8") == ORCHLINK_PI_UI_EXTENSION
