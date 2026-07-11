"""Generated TypeScript for the Orchlink read-only Pi TUI monitor extension."""

from __future__ import annotations


ORCHLINK_PI_UI_EXTENSION = r'''
import { getMarkdownTheme, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Key, Markdown, matchesKey, truncateToWidth, visibleWidth } from "@earendil-works/pi-tui";
import { spawn } from "node:child_process";

type JsonObject = Record<string, any>;
type PanelResult = { action: "stop"; workerName: string } | { action: "visible"; workerName: string } | null;
type ViewMode = "list" | "follow";
type FollowState = {
  workerName: string;
  taskId: string;
  status: string;
  lines: string[];
  offset: number;
  live: boolean;
  truncated: boolean;
  // Broker sequence last received for this task. The transcript poll uses
  // ``?after=cursor`` so duplicates are skipped and a stored cursor lets the
  // lead panel resume after broker/lead restart without gaps.
  cursor: number;
  // Broker-provided truncation watermark. When the cursor the panel kept
  // predates retained history, the broker returns a synthetic marker that
  // mirrors the truncation here so the UI renders the same label.
  truncatedBeforeSeq: number;
  lastEventKind?: string;
};
const FOLLOW_LINES_LIMIT = 500;

type WorkerRow = {
  workerName: string;
  agentId: string;
  ready: boolean;
  model: string;
  thinking: string;
  runtime: string;
  backend: string;
  heartbeat: string;
  job?: {
    kind: string;
    id: string;
    mode: string;
    status: string;
    activity: string;
    tool: string;
    updated: string;
    turn?: number;
    maxTurns?: number;
  };
};

const ACTIVE_JOB_STATUSES = new Set(["PENDING", "QUEUED", "DELIVERED", "RUNNING", "IN_PROGRESS", "RECLAIMABLE", "OPEN"]);
const ACTIVE_SESSION_STATUSES = new Set(["ACTIVE"]);

function env(name: string, fallback = ""): string {
  return process.env[name] || fallback;
}

function truncate(value: any, maxLength = 80): string {
  const text = String(value === undefined || value === null ? "" : value).replace(/\s+/g, " ").trim();
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function wrapDisplayLine(value: any, width: number): string[] {
  const logicalLines = String(value === undefined || value === null ? "" : value)
    .replace(/\r/g, "")
    .split("\n");
  const wrapped: string[] = [];
  for (const line of logicalLines) {
    if (!line.length) {
      wrapped.push("");
      continue;
    }
    for (let start = 0; start < line.length; start += width) {
      wrapped.push(line.slice(start, start + width));
    }
  }
  return wrapped;
}

function isTui(ctx: any): boolean {
  return ctx.mode === "tui" && ctx?.ui && ctx?.hasUI !== false;
}

function jobId(job: JsonObject): string {
  return String(job.task_id || job.conversation_id || job.id || "-");
}

function jobKind(job: JsonObject): string {
  if (job.conversation_id || String(job.kind || "").toLowerCase() === "talk") return "talk";
  return String(job.kind || "task");
}

function workerNameFromSession(session: JsonObject): string {
  return String(session.worker_name || session.name || session.agent_id || "worker");
}

function workerNameFromJob(job: JsonObject): string {
  return String(job.worker_name || job.to_agent || "worker");
}

function activeJob(job: JsonObject): boolean {
  return ACTIVE_JOB_STATUSES.has(String(job.status || "").toUpperCase());
}

function activeSession(session: JsonObject): boolean {
  return ACTIVE_SESSION_STATUSES.has(String(session.status || "ACTIVE").toUpperCase());
}

function summarizeStatus(body: JsonObject): WorkerRow[] {
  if (body.broker !== "ok") return [];
  const sessions = Array.isArray(body.sessions) ? body.sessions : [];
  const jobs = (Array.isArray(body.jobs) ? body.jobs : []).filter(activeJob);
  const jobsByWorker = new Map<string, JsonObject>();
  const jobsByAgent = new Map<string, JsonObject>();
  for (const job of jobs) {
    const workerName = workerNameFromJob(job);
    if (workerName && !jobsByWorker.has(workerName)) jobsByWorker.set(workerName, job);
    const target = String(job.to_agent || "");
    if (target && !jobsByAgent.has(target)) jobsByAgent.set(target, job);
  }
  return sessions
    .filter((session: JsonObject) => String(session.role || "") === "work" && activeSession(session))
    .map((session: JsonObject) => {
      const workerName = workerNameFromSession(session);
      const agentId = String(session.agent_id || "");
      const job = jobsByWorker.get(workerName) || jobsByAgent.get(agentId);
      const row: WorkerRow = {
        workerName,
        agentId,
        ready: Boolean(session.ready),
        model: String(session.model || ""),
        thinking: String(session.thinking || ""),
        runtime: String(session.runtime_mode || ""),
        backend: String(session.backend || ""),
        heartbeat: String(session.last_heartbeat_at || ""),
      };
      if (job) {
        row.job = {
          kind: jobKind(job),
          id: jobId(job),
          mode: String(job.mode || (job.conversation_id ? "TALK" : "DO")),
          status: String(job.status || "UNKNOWN"),
          activity: String(job.last_activity_preview || ""),
          tool: String(job.last_activity_tool || ""),
          updated: String(job.last_activity_at || ""),
          turn: job.turn === undefined || job.turn === null ? undefined : Number(job.turn),
          maxTurns: job.max_turns === undefined || job.max_turns === null ? undefined : Number(job.max_turns),
        };
      }
      return row;
    })
    .sort((a: WorkerRow, b: WorkerRow) => Number(Boolean(b.job)) - Number(Boolean(a.job)) || a.workerName.localeCompare(b.workerName));
}

function rowInlineText(row: WorkerRow): string {
  const state = row.job ? `${row.job.id} ${row.job.status.toLowerCase()}` : row.ready ? "idle" : "starting";
  const model = row.model || row.backend || "unknown-model";
  const activity = row.job?.activity ? ` · ${truncate(row.job.tool ? `${row.job.tool}: ${row.job.activity}` : row.job.activity, 48)}` : "";
  return `worker - ${row.workerName} ${model} ${state}${activity}`;
}

function sessionNameText(rows: WorkerRow[], offline = false): string {
  if (offline) return "Orchlink Lead · workers offline";
  if (!rows.length) return "Orchlink Lead · no workers";
  const active = rows.filter((r) => r.job).length;
  const idle = rows.length - active;
  return `Orchlink Lead · ${active} active · ${idle} idle`;
}

function formatActivityRecord(record: JsonObject): string {
  const type = String(record.activity_type || record.type || "activity");
  const tool = String(record.tool_name || "");
  const detail = String(record.detail || record.preview || "");
  const prefix = tool ? `${type}/${tool}` : type;
  return `${prefix}: ${detail || "working"}`;
}

function panelLines(rows: WorkerRow[], _activityByItem: Record<string, string[]> = {}, offline = false, selected = 0, mode: ViewMode = "list", follow: FollowState | null = null): string[] {
  if (mode === "follow" && follow) {
    return followLines(follow);
  }
  if (offline) return ["workers offline", "", "q/Esc close"];
  const lines: string[] = [];
  if (!rows.length) {
    lines.push("workers none");
    lines.push("");
    lines.push("q/Esc close");
    return lines;
  }
  rows.forEach((row, index) => {
    const marker = index === selected ? ">" : " ";
    const model = row.model || row.backend || "unknown-model";
    const state = row.job ? `${row.job.status} · ${row.job.id}` : row.ready ? "IDLE" : "STARTING";
    lines.push(`${marker} ${row.workerName} · ${state} · ${model}`);
  });
  lines.push("");
  const selectHint = rows.length > 1 ? "↑/↓ select · " : "";
  lines.push(`${selectHint}Enter/f follow active · s stop · q/Esc close`);
  return lines;
}

function followStateKey(row: WorkerRow): string | null {
  return row.job ? `${row.workerName}:${row.job.id}` : null;
}

function followHeader(follow: FollowState): string {
  const liveTag = follow.live ? "LIVE" : "PAUSED";
  const truncatedTag = follow.truncated ? " · TRUNCATED" : "";
  return `FOLLOW · ${follow.workerName} · ${follow.taskId} · ${follow.status} · ${liveTag}${truncatedTag}`;
}

function followLines(follow: FollowState, canSwitch = false, canScroll = false, contentLines = follow.lines): string[] {
  const header = followHeader(follow);
  const lines: string[] = [];
  lines.push(header);
  lines.push("");
  if (follow.lines.length === 0) {
    lines.push("(no visible output yet)");
    lines.push("");
  } else {
    lines.push(...contentLines);
  }
  lines.push("");
  const controls: string[] = [];
  if (!follow.live) controls.push("-- PAUSED --");
  if (canSwitch) controls.push("Tab switch");
  if (canScroll) controls.push("Wheel/keys scroll", "End live");
  controls.push("Esc workers", "q close");
  lines.push(controls.join(" · "));
  return lines;
}

function mouseWheelDirection(data: string): -1 | 0 | 1 {
  const match = data.match(/^\x1b\[<(\d+);\d+;\d+M$/);
  if (!match) return 0;
  const button = Number(match[1]);
  if ((button & 64) === 0) return 0;
  const direction = button & 3;
  if (direction === 0) return -1;
  if (direction === 1) return 1;
  return 0; // horizontal wheel events must not move the transcript vertically
}

function trimFollowLines(follow: FollowState) {
  while (follow.lines.length > FOLLOW_LINES_LIMIT) {
    follow.lines.shift();
    follow.truncated = true;
  }
  if (follow.live) {
    follow.offset = Math.max(0, follow.lines.length - 1);
  }
}

function pushFollowLine(follow: FollowState, text: string) {
  follow.lines.push(text);
  follow.lastEventKind = "line";
  trimFollowLines(follow);
}

function appendAssistantDelta(follow: FollowState, text: string) {
  const parts = text.replace(/\r/g, "").split("\n");
  if (follow.lastEventKind !== "assistant_delta" || follow.lines.length === 0) {
    follow.lines.push("");
  }
  follow.lines[follow.lines.length - 1] += parts[0] || "";
  for (const part of parts.slice(1)) follow.lines.push(part);
  follow.lastEventKind = "assistant_delta";
  trimFollowLines(follow);
}

function syncFollowStatus(follow: FollowState | null, rows: WorkerRow[]) {
  if (!follow) return;
  const row = rows.find((r) => r.workerName === follow.workerName && r.job?.id === follow.taskId);
  if (row?.job) {
    follow.status = row.job.status;
  }
}

class OrchlinkWorkersPanel {
  private offset = 0;
  private selected = 0;
  private contentWidth = 80;
  private mode: ViewMode = "list";
  private followByKey: Record<string, FollowState> = {};
  private currentFollowKey: string | null = null;
  private previousFollowKey: string | null = null;
  private notice = "";
  private getOverlayHandle: () => any = () => undefined;
  // Lifecycle bridge: the lead extension subscribes to enter/switch/exit/close
  // events so it can drive an AbortController-backed long-poll for transcripts
  // and abort stale fetches when the user switches or leaves the panel.
  private followLifecycleListener:
    | ((event: { type: string; followKey?: string | null; taskId?: string; workerName?: string }) => void)
    | null = null;
  constructor(
    private getRows: () => WorkerRow[],
    private getActivityByItem: () => Record<string, string[]>,
    private getOffline: () => boolean,
    private done: (value: PanelResult) => void,
    private terminalRows = 35,
    private markdownTheme?: any,
    private uiTheme?: any,
  ) {}
  setOverlayHandle(handle: () => any) {
    this.getOverlayHandle = handle;
  }
  setFollowLifecycleListener(
    listener: (event: { type: string; followKey?: string | null; taskId?: string; workerName?: string }) => void,
  ) {
    this.followLifecycleListener = listener;
  }
  requestRender() {
    this.getOverlayHandle()?.requestRender?.();
  }
  invalidate() {}
  private getRowsForInput(): WorkerRow[] {
    return this.getRows();
  }
  private activeRowIndices(): number[] {
    const rows = this.getRowsForInput();
    return rows.map((row, index) => (row.job ? index : -1)).filter((index) => index >= 0);
  }
  getFollowState(followKey: string): FollowState | undefined {
    return this.followByKey[followKey];
  }
  private enterFollow(index: number) {
    const row = this.getRowsForInput()[index];
    if (!row?.job) return;
    // Preserve the current follow's scroll offset before switching so a later
    // Tab/Shift-Tab return resumes at the same line. ``follow.offset`` is the
    // per-follow saved scroll; ``this.offset`` is the panel-level slice index.
    if (this.currentFollowKey && this.followByKey[this.currentFollowKey]) {
      this.followByKey[this.currentFollowKey].offset = this.offset;
    }
    const key = followStateKey(row);
    if (!key) return;
    if (!this.followByKey[key]) {
      this.followByKey[key] = {
        workerName: row.workerName,
        taskId: row.job.id,
        status: row.job.status,
        lines: [],
        offset: 0,
        live: true,
        truncated: false,
        cursor: 0,
        truncatedBeforeSeq: 0,
      };
    }
    this.currentFollowKey = key;
    this.mode = "follow";
    const next = this.followByKey[key];
    if (next.live) {
      // Live mode: snap to the bottom so newly arriving transcript lines show.
      this.offset = Math.max(0, this.displayLines().length - this.visibleHeight());
    } else {
      // Paused mode: restore the saved scroll position for this follow.
      this.offset = Math.min(this.maxOffset(), Math.max(0, next.offset));
    }
    this.getOverlayHandle()?.requestRender?.();
    this.followLifecycleListener?.({
      type: this.previousFollowKey && this.previousFollowKey !== key ? "follow-switch" : "follow-enter",
      followKey: key,
      taskId: next.taskId,
      workerName: next.workerName,
    });
    this.previousFollowKey = key;
  }
  private cycleFollow(direction: 1 | -1) {
    const active = this.activeRowIndices();
    if (!active.length || !this.currentFollowKey) return;
    const rows = this.getRowsForInput();
    const currentIndex = rows.findIndex((r) => {
      const follow = this.currentFollowKey ? this.followByKey[this.currentFollowKey] : null;
      return !!follow && r.workerName === follow.workerName && r.job?.id === follow.taskId;
    });
    let position = active.findIndex((index) => index === currentIndex);
    if (position === -1) position = 0;
    const nextPosition = (position + direction + active.length) % active.length;
    const nextIndex = active[nextPosition] ?? 0;
    this.enterFollow(nextIndex);
  }
  private returnToList() {
    if (this.mode === "follow") {
      const exitedKey = this.currentFollowKey;
      this.mode = "list";
      this.offset = 0;
      this.currentFollowKey = null;
      this.getOverlayHandle()?.requestRender?.();
      this.followLifecycleListener?.({ type: "follow-exit", followKey: exitedKey });
      return;
    }
    this.mode = "list";
    this.offset = 0;
    this.getOverlayHandle()?.requestRender?.();
  }
  private closePanel() {
    const exitedKey = this.currentFollowKey;
    this.followLifecycleListener?.({ type: "follow-close", followKey: exitedKey });
    this.done(null);
  }
  private follow(): FollowState | null {
    return this.currentFollowKey ? this.followByKey[this.currentFollowKey] ?? null : null;
  }
  private pageSize(): number {
    return Math.max(1, this.visibleHeight() - 4);
  }
  private allLines(renderedFollowLines?: string[]): string[] {
    const rows = this.getRows();
    const follow = this.follow();
    syncFollowStatus(follow, rows);
    const transcriptRows = follow
      ? (renderedFollowLines || follow.lines.flatMap((line) => wrapDisplayLine(line, this.contentWidth))).length
      : 0;
    const canScroll = !!follow && transcriptRows + 5 > this.visibleHeight();
    const lines = this.mode === "follow" && follow
      ? followLines(follow, rows.filter((row) => row.job).length > 1, canScroll, renderedFollowLines || follow.lines)
      : panelLines(rows, this.getActivityByItem(), this.getOffline(), this.selected, this.mode, follow);
    if (this.mode === "list" && this.notice && lines.length > 0) {
      lines.splice(Math.max(0, lines.length - 1), 0, `! ${this.notice}`);
    }
    return lines;
  }
  private displayLines(): string[] {
    const follow = this.follow();
    if (!follow) return this.allLines().flatMap((line) => wrapDisplayLine(line, this.contentWidth));
    const rendered = this.markdownTheme
      ? new Markdown(follow.lines.join("\n"), 0, 0, this.markdownTheme).render(this.contentWidth)
      : follow.lines.flatMap((line) => wrapDisplayLine(line, this.contentWidth));
    const lines = this.allLines(rendered);
    if (this.uiTheme && lines.length > 0) {
      lines[0] = this.uiTheme.fg("accent", lines[0]);
      lines[lines.length - 1] = this.uiTheme.fg("dim", lines[lines.length - 1]);
    }
    return lines;
  }
  private visibleHeight(): number {
    // Keep the overlay useful even with only idle workers while leaving room
    // for Pi/Herdr chrome. Pi still clamps the overlay on tiny terminals.
    return Math.min(38, Math.max(6, Math.floor(this.terminalRows * 0.80)));
  }
  private maxOffset(): number {
    return Math.max(0, this.displayLines().length - this.visibleHeight());
  }
  private clampOffset() {
    this.offset = Math.min(this.maxOffset(), Math.max(0, this.offset));
  }
  render(width: number): string[] {
    const rows = this.getRows();
    this.selected = Math.min(Math.max(0, this.selected), Math.max(0, rows.length - 1));
    const contentWidth = Math.max(20, width - 4);
    this.contentWidth = contentWidth;
    const follow = this.follow();
    if (follow) {
      follow.offset = Math.min(Math.max(0, follow.offset), this.maxOffset());
    }
    const all = this.displayLines();
    const height = this.visibleHeight();
    this.clampOffset();
    // Keep follow controls visible while transcript content scrolls beneath it.
    const stickyFooter = this.mode === "follow" ? all.pop() : undefined;
    const bodyHeight = Math.max(1, height - (stickyFooter === undefined ? 0 : 1));
    const visible = all.slice(this.offset, this.offset + bodyHeight);
    while (visible.length < bodyHeight) visible.push("");
    if (stickyFooter !== undefined) visible.push(stickyFooter);
    const border = "─".repeat(Math.max(0, contentWidth));
    return [`┌${border}┐`, ...visible.map((line) => {
      const fitted = truncateToWidth(line, contentWidth, "");
      const padding = " ".repeat(Math.max(0, contentWidth - visibleWidth(fitted)));
      return `│${fitted}${padding}│`;
    }), `└${border}┘`];
  }
  handleInput(data: string) {
    const rows = this.getRowsForInput();
    const wheel = mouseWheelDirection(data);
    if (wheel !== 0) {
      const previous = this.offset;
      this.offset = Math.min(this.maxOffset(), Math.max(0, this.offset + wheel * 3));
      if (this.offset !== previous) {
        const follow = this.follow();
        if (follow) {
          follow.live = false;
          follow.offset = this.offset;
        }
        this.getOverlayHandle()?.requestRender?.();
      }
      return;
    }
    if (this.mode === "list") {
      if (matchesKey(data, "q") || matchesKey(data, Key.escape)) this.closePanel();
      else if (matchesKey(data, "s") && rows[this.selected]) {
        const row = rows[this.selected];
        const isBackground = row.backend === "rpc-supervisor" || row.runtime === "rpc";
        this.done({ action: isBackground ? "stop" : "visible", workerName: row.workerName });
      }
      else if (matchesKey(data, Key.enter) || matchesKey(data, "f")) {
        if (rows[this.selected]?.job) {
          this.notice = "";
          this.enterFollow(this.selected);
        } else if (rows[this.selected]) {
          this.notice = `${rows[this.selected].workerName} is idle; there is no active task to follow.`;
          this.getOverlayHandle()?.requestRender?.();
        }
      }
      else if (matchesKey(data, Key.up)) {
        this.notice = "";
        this.selected = Math.max(0, this.selected - 1);
        this.getOverlayHandle()?.requestRender?.();
      }
      else if (matchesKey(data, Key.down)) {
        this.notice = "";
        this.selected = Math.min(Math.max(0, rows.length - 1), this.selected + 1);
        this.getOverlayHandle()?.requestRender?.();
      }
      else if (matchesKey(data, Key.pageUp)) this.offset = Math.max(0, this.offset - 8);
      else if (matchesKey(data, Key.pageDown)) this.offset = Math.min(this.maxOffset(), this.offset + 8);
      return;
    }
    const follow = this.follow();
    if (!follow) {
      this.returnToList();
      return;
    }
    if (matchesKey(data, "q")) {
      this.closePanel();
    } else if (matchesKey(data, Key.escape)) {
      this.returnToList();
    } else if (matchesKey(data, Key.tab)) {
      this.cycleFollow(1);
    } else if (matchesKey(data, Key.shift("tab"))) {
      this.cycleFollow(-1);
    } else if (matchesKey(data, Key.pageUp)) {
      follow.live = false;
      this.offset = Math.max(0, this.offset - this.pageSize());
      follow.offset = this.offset;
      this.getOverlayHandle()?.requestRender?.();
    } else if (matchesKey(data, Key.pageDown)) {
      follow.live = false;
      this.offset = Math.min(this.maxOffset(), this.offset + this.pageSize());
      follow.offset = this.offset;
      this.getOverlayHandle()?.requestRender?.();
    } else if (matchesKey(data, Key.up)) {
      follow.live = false;
      this.offset = Math.max(0, this.offset - 1);
      follow.offset = this.offset;
      this.getOverlayHandle()?.requestRender?.();
    } else if (matchesKey(data, Key.down)) {
      follow.live = false;
      this.offset = Math.min(this.maxOffset(), this.offset + 1);
      follow.offset = this.offset;
      this.getOverlayHandle()?.requestRender?.();
    } else if (matchesKey(data, Key.end)) {
      follow.live = true;
      this.offset = Math.max(0, this.displayLines().length - this.visibleHeight());
      follow.offset = this.offset;
      this.getOverlayHandle()?.requestRender?.();
    }
  }
  appendTranscript(taskId: string, text: string, kind = "line") {
    for (const follow of Object.values(this.followByKey)) {
      if (follow.taskId === taskId) {
        if (kind === "assistant_delta") appendAssistantDelta(follow, text);
        else pushFollowLine(follow, text);
      }
    }
    if (this.mode === "follow" && this.currentFollowKey) {
      // While the follow view is live (user at the end), snap the panel
      // scroll to the bottom so newly arriving transcript lines surface
      // immediately. When the user has scrolled away (live=false), keep
      // their position so they can keep reading history while new events
      // continue buffering in ``follow.lines``.
      const follow = this.followByKey[this.currentFollowKey];
      if (follow && follow.live) {
        this.offset = Math.max(0, this.displayLines().length - this.visibleHeight());
        follow.offset = this.offset;
      }
      this.getOverlayHandle()?.requestRender?.();
    }
  }
}

// --------------------------------------------------------------------------
// Transcript follow transcriber
//
// Wires the panel's lifecycle events (follow-enter / follow-switch /
// follow-exit / follow-close) to a per-poll AbortController and a generation
// token so a late response from an interrupted poll cannot mutate or rerender
// a view the user has already moved past.
//
// Privacy boundary (G018):
//   * Only ``assistant_delta`` and the broker's exact retention marker are
//     surfaced as line text in the follow view.
//   * ``tool`` events arrive with a ``tool_name`` but their raw output or
//     raw exception bodies are intentionally NOT mirrored into the panel.
//   * ``thinking_delta`` is never published by the worker / broker and so
//     never arrives here.
//
// The fetch is bounded by ``limit`` and a ``wait_seconds`` long-poll so the
// UI never blocks indefinitely and the broker is never overwhelmed.
const FOLLOW_FETCH_LIMIT = 200;
const FOLLOW_WAIT_SECONDS = 2;
const FOLLOW_RETRY_MS = 500;

type FollowDeps = {
  fetchImpl: typeof fetch;
  brokerUrl: string;
  apiKey: string;
  projectId: string;
  isStopped: () => boolean;
};

function createFollowTranscriber(panel: OrchlinkWorkersPanel, deps: FollowDeps) {
  let activeAbort: AbortController | undefined;
  let activeGeneration = 0;
  let currentFollowKey: string | null = null;

  function abortActive(): void {
    // Fence even fetch implementations that ignore AbortSignal and resolve
    // after the user has left the follow view.
    activeGeneration += 1;
    if (activeAbort) {
      try {
        activeAbort.abort();
      } catch (_err: any) {
        // ignore
      }
      activeAbort = undefined;
    }
    currentFollowKey = null;
  }

  async function runPoll(followKey: string, taskId: string): Promise<void> {
    const controller = new AbortController();
    activeAbort?.abort();
    activeAbort = controller;
    currentFollowKey = followKey;
    const generation = ++activeGeneration;

    while (!controller.signal.aborted && !deps.isStopped()) {
      const follow = panel.getFollowState(followKey);
      if (!follow) break;
      const cursor = Number(follow.cursor || 0);
      try {
        const url = `${deps.brokerUrl}/v1/tasks/${encodeURIComponent(taskId)}/transcript?after=${cursor}&limit=${FOLLOW_FETCH_LIMIT}&wait_seconds=${FOLLOW_WAIT_SECONDS}`;
        const response = await deps.fetchImpl(url, {
          signal: controller.signal,
          headers: {
            "X-API-Key": deps.apiKey,
            "X-Orchlink-Project-ID": deps.projectId,
          },
        });
        // Generation guard: a newer poll replaced us (user switched away).
        // Drop the response so a stale fetch cannot mutate or rerender
        // the now-different follow view.
        if (generation !== activeGeneration) break;
        if (!response.ok) {
          await new Promise((r) => setTimeout(r, FOLLOW_RETRY_MS));
          continue;
        }
        const body = await response.json() as JsonObject;
        if (generation !== activeGeneration) break;
        const events = Array.isArray(body.events) ? body.events : [];
        let mutated = false;
        for (const event of events) {
          const kind = String(event.kind || "");
          const text = String(event.text || "");
          const seq = Number(event.seq || 0);
          // Synthetic retention markers can share the first retained event's
          // sequence. Render the marker without consuming that real event.
          const isTruncationMarker = kind === "system"
            && text === "Earlier transcript history was dropped by retention. This is not the complete output.";
          if (isTruncationMarker) {
            if (!follow.truncated || seq > follow.truncatedBeforeSeq) {
              panel.appendTranscript(taskId, text, kind);
              mutated = true;
            }
            follow.truncated = true;
            follow.truncatedBeforeSeq = Math.max(follow.truncatedBeforeSeq, seq);
            continue;
          }
          // Advance past every real broker event, including fail-closed event
          // kinds, so a dropped tool/thinking event cannot replay forever.
          if (seq <= follow.cursor) continue;
          follow.cursor = seq;
          mutated = true;
          // Privacy boundary: arbitrary system/status/tool payloads fail
          // closed; only visible assistant text reaches the lead panel.
          if (kind === "assistant_delta") {
            panel.appendTranscript(taskId, text, kind);
          }
        }
        // ``next_seq`` is the broker's next assignable sequence, not a read
        // cursor. Advancing to it would skip retained events when a page hits
        // the limit, so the cursor is derived only from returned event seqs.
        if (mutated) panel.requestRender();
        // Yield even when a test/mock fetch resolves immediately. Production
        // long polls naturally block, but this prevents a hot microtask loop.
        await new Promise((r) => setTimeout(r, 0));
      } catch (err: any) {
        if (controller.signal.aborted) break;
        if (generation !== activeGeneration) break;
        await new Promise((r) => setTimeout(r, FOLLOW_RETRY_MS));
      }
    }
  }

  panel.setFollowLifecycleListener((event) => {
    if (event.type === "follow-enter" || event.type === "follow-switch") {
      if (!event.followKey || !event.taskId) return;
      void runPoll(event.followKey, event.taskId);
    } else if (event.type === "follow-exit" || event.type === "follow-close") {
      abortActive();
    }
  });

  return {
    abortActive,
    get currentFollowKey(): string | null {
      return currentFollowKey;
    },
    get activeGeneration(): number {
      return activeGeneration;
    },
  };
}

export default function (pi: ExtensionAPI) {
  const role = env("ORCHLINK_PI_ROLE");
  const brokerUrl = env("ORCHLINK_BROKER_URL", "http://127.0.0.1:8787").replace(/\/$/, "");
  const apiKey = env("ORCHLINK_API_KEY");
  const projectId = env("ORCHLINK_PROJECT_ID", "default");
  const pollMs = Math.max(2000, Number(env("ORCHLINK_MONITOR_POLL_SECONDS", "5")) * 1000 || 5000);
  let rows: WorkerRow[] = [];
  let activityByItem: Record<string, string[]> = {};
  let offline = false;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let abortController: AbortController | undefined;
  let overlayHandle: any;
  let lastSessionName = "";
  let lastCtx: any;
  // Live follow transcriber for the current /orchlink session panel. A new
  // transcriber replaces the prior one so listener bindings cannot leak
  // across panel re-acquisitions.
  let panelTranscriber: ReturnType<typeof createFollowTranscriber> | null = null;
  let panelMouseTerminal: any;
  let panelMouseEnabled = false;

  function setPanelMouseTracking(enabled: boolean) {
    if (!panelMouseTerminal || panelMouseEnabled === enabled) return;
    panelMouseTerminal.write(enabled
      ? "\x1b[?1000h\x1b[?1006h"
      : "\x1b[?1000l\x1b[?1006l");
    panelMouseEnabled = enabled;
  }

  async function getStatus(): Promise<JsonObject> {
    abortController?.abort();
    abortController = new AbortController();
    const response = await fetch(`${brokerUrl}/v1/status?limit=200`, {
      signal: abortController.signal,
      headers: {
        "X-API-Key": apiKey,
        "X-Orchlink-Project-ID": projectId,
      },
    });
    if (!response.ok) throw new Error(`status ${response.status}`);
    return await response.json() as JsonObject;
  }

  async function getActivity(itemId: string): Promise<string[]> {
    const response = await fetch(`${brokerUrl}/v1/activity?item_id=${encodeURIComponent(itemId)}&limit=30`, {
      headers: {
        "X-API-Key": apiKey,
        "X-Orchlink-Project-ID": projectId,
      },
    });
    if (!response.ok) return [];
    const body = await response.json() as JsonObject;
    const activity = Array.isArray(body.activity) ? body.activity : [];
    return activity.filter((item: any) => item && typeof item === "object").map(formatActivityRecord);
  }

  async function refreshActivity() {
    const activeIds = rows.map((row) => row.job?.id).filter((id): id is string => Boolean(id));
    const next: Record<string, string[]> = {};
    await Promise.all(activeIds.map(async (itemId) => {
      next[itemId] = await getActivity(itemId);
    }));
    activityByItem = next;
  }

  function runOrchStop(workerName: string): Promise<string> {
    return new Promise((resolve, reject) => {
      const child = spawn("orch", ["stop", "--name", workerName], { stdio: ["ignore", "pipe", "pipe"] });
      let output = "";
      child.stdout.on("data", (chunk) => { output += String(chunk); });
      child.stderr.on("data", (chunk) => { output += String(chunk); });
      child.on("error", reject);
      child.on("close", (code) => {
        if (code === 0) resolve(output.trim());
        else reject(new Error(output.trim() || `orch stop exited ${code}`));
      });
    });
  }

  function render(ctx: any) {
    if (!isTui(ctx)) return;
    const nextName = sessionNameText(rows, offline);
    if (nextName !== lastSessionName && typeof pi.setSessionName === "function") {
      pi.setSessionName(nextName);
      lastSessionName = nextName;
    }
    overlayHandle?.requestRender?.();
  }

  function schedule(ctx: any, delayMs = pollMs) {
    if (stopped || role !== "lead") return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => void poll(ctx), delayMs);
  }

  async function poll(ctx: any) {
    if (stopped || role !== "lead") return;
    try {
      const body = await getStatus();
      offline = body.broker !== "ok";
      rows = summarizeStatus(body);
      activityByItem = {};
      if (!offline) await refreshActivity();
      render(ctx);
    } catch (_error: any) {
      offline = true;
      rows = [];
      activityByItem = {};
      render(ctx);
    } finally {
      schedule(ctx, pollMs);
    }
  }

  pi.registerCommand("orchlink", {
    description: "Show Orchlink worker status panel",
    handler: async (_args: string, ctx: any) => {
      lastCtx = ctx;
      if (!isTui(ctx)) {
        ctx.ui?.notify?.("Orchlink worker panel is available only in Pi TUI mode.", "info");
        return;
      }
      await poll(ctx);
      let panelInstance: OrchlinkWorkersPanel | null = null;
      let result: PanelResult = null;
      try {
        result = await ctx.ui.custom<PanelResult>(
        (_tui: any, _theme: any, _keybindings: any, done: (value: PanelResult) => void) => {
          panelMouseTerminal = _tui?.terminal;
          panelMouseEnabled = false;
          setPanelMouseTracking(true);
          if (panelInstance) {
            // Ensure stale listeners from any previous turn aren't reused.
            panelInstance.setFollowLifecycleListener(() => undefined);
          }
          panelInstance = new OrchlinkWorkersPanel(
            () => rows,
            () => activityByItem,
            () => offline,
            done,
            Number(_tui?.terminal?.rows || 35),
            getMarkdownTheme(),
            _theme,
          );
          panelInstance.setOverlayHandle(() => overlayHandle);
          // Inject the test fetch implementation if the harness set one before
          // the panel was constructed (production keeps the default global
          // fetch).
          // Track a per-panel transcriber so its polling lifecycle ends
          // when the panel is replaced or the user leaves follow mode.
          panelTranscriber?.abortActive();
          panelTranscriber = createFollowTranscriber(panelInstance, {
            fetchImpl: fetch.bind(globalThis),
            brokerUrl,
            apiKey,
            projectId,
            isStopped: () => stopped,
          });
          return panelInstance;
        },
        {
          overlay: true,
          overlayOptions: {
            width: "92%",
            minWidth: 60,
            maxHeight: "88%",
            anchor: "center",
            margin: 1,
          },
          onHandle: (handle: any) => {
            overlayHandle = handle;
          },
        },
        );
      } finally {
        setPanelMouseTracking(false);
        panelMouseTerminal = undefined;
        panelMouseEnabled = false;
        panelTranscriber?.abortActive();
        panelTranscriber = null;
        overlayHandle = undefined;
      }
      if (result?.action === "visible") {
        ctx.ui.notify(`Worker ${result.workerName} is visible; stop it from its own terminal with Ctrl-C.`, "info");
        return;
      }
      if (result?.action === "stop") {
        const confirmed = await ctx.ui.confirm(`Stop background worker ${result.workerName}?`, "This stops the tracked background supervisor. Visible worker terminals are not stopped from this panel.");
        if (!confirmed) return;
        try {
          const output = await runOrchStop(result.workerName);
          ctx.ui.notify(output || `Stopped worker ${result.workerName}.`, "info");
          await poll(ctx);
        } catch (error: any) {
          ctx.ui.notify(`Failed to stop worker ${result.workerName}: ${error?.message || error}`, "error");
        }
      }
    },
  });

  pi.on("session_start", async (_event, ctx) => {
    lastCtx = ctx;
    if (role !== "lead" || !isTui(ctx)) return;
    render(ctx);
    schedule(ctx, 0);
  });

  pi.on("session_shutdown", async () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    abortController?.abort();
    // Abort any in-flight follow polling so the panel cleans up before Pi
    // tears down the session.
    panelTranscriber?.abortActive();
    panelTranscriber = null;
    setPanelMouseTracking(false);
    panelMouseTerminal = undefined;
    panelMouseEnabled = false;
    if (lastCtx && isTui(lastCtx) && typeof pi.setSessionName === "function") {
      pi.setSessionName("Orchlink Lead");
    }
  });
}
'''
