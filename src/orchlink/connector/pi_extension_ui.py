"""Generated TypeScript for the Orchlink read-only Pi TUI monitor extension."""

from __future__ import annotations


ORCHLINK_PI_UI_EXTENSION = r'''
import { getMarkdownTheme, type ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Container, Key, Markdown, Text, matchesKey, truncateToWidth, visibleWidth } from "@earendil-works/pi-tui";
import { Type } from "typebox";
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
    // G019 AC-4: authoritative start timestamp captured once on the
    // first RUNNING transition and durable across restart. Used by the
    // inline worker tree to render ``elapsed`` locally without polling.
    startedAt?: string | null;
    // G019 AC-6: worker-side tool count surfaced from the telemetry
    // endpoint. Kept here for the above-editor widget so the render path
    // is single-source (no separate telemetry fetch).
    toolCount?: number | null;
    // G019 AC-7 + AC-8: worker session context usage published by the
    // worker through the telemetry endpoint and surfaced on the same
    // status poll. These are session-context fields, never task-token
    // spend; the widget renders them under the ``ctx`` label.
    tokens?: number | null;
    contextWindow?: number | null;
    percent?: number | null;
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

// G019 AC-6 + AC-7 + AC-8: coerce broker telemetry fields into safe
// nullable numbers. The broker already clamps tool_count to a
// non-negative int and treats absent tokens/context_window/percent as
// unknown (null); these helpers defend the render path against a
// malformed or partial record so the widget never renders ``NaN`` or a
// negative count. Returns ``null`` for any non-finite value.
function telemetryNumber(tel: JsonObject | null, field: string): number | null {
  if (!tel) return null;
  const raw = (tel as any)[field];
  if (raw === undefined || raw === null) return null;
  const num = Number(raw);
  return Number.isFinite(num) ? num : null;
}

function telemetryToolCount(tel: JsonObject | null): number | null {
  const value = telemetryNumber(tel, "tool_count");
  if (value === null) return null;
  // AC-6: publish only a non-negative integer.
  return Math.max(0, Math.floor(value));
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
  // G019 AC-4: surface the durable ``started_at`` from the broker's
  // active_messages, keyed by task_id, so the above-editor widget can
  // render ``elapsed`` locally. This keeps the widget in sync with the
  // authoritative broker capture on each 5s poll (no per-second broker
  // polling — elapsed is computed against the local clock in the widget).
  const startedAtByTask: Record<string, string | null | undefined> = {};
  const activeMessages = Array.isArray(body.active_messages) ? body.active_messages : [];
  for (const stored of activeMessages) {
    const taskKey = String((stored as any).task_id || "");
    if (taskKey && !(taskKey in startedAtByTask)) {
      startedAtByTask[taskKey] = (stored as any).started_at ?? null;
    }
  }
  // G019 AC-6 + AC-7 + AC-8: surface the latest worker telemetry from
  // the broker status poll so the above-editor widget can render tool
  // count and session-context usage without a second fetch. The broker
  // publishes one latest-state record per task (lease-fenced,
  // status-only — no bodies, args, or secrets). Keyed by task_id.
  const telemetryByTask: Record<string, JsonObject> = {};
  const telemetry = Array.isArray((body as any).telemetry) ? (body as any).telemetry : [];
  for (const t of telemetry) {
    const taskKey = String((t as any).task_id || "");
    if (taskKey && !(taskKey in telemetryByTask)) {
      telemetryByTask[taskKey] = t as JsonObject;
    }
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
        const taskIdStr = String(jobId(job));
        const tel = telemetryByTask[taskIdStr] || null;
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
          startedAt: startedAtByTask[taskIdStr] ?? null,
          toolCount: telemetryToolCount(tel),
          tokens: telemetryNumber(tel, "tokens"),
          contextWindow: telemetryNumber(tel, "context_window"),
          percent: telemetryNumber(tel, "percent"),
        };
      }
      return row;
    })
    .sort((a: WorkerRow, b: WorkerRow) => Number(Boolean(b.job)) - Number(Boolean(a.job)) || a.workerName.localeCompare(b.workerName));
}

// --------------------------------------------------------------------------
// Inline worker tree render helpers (G019 AC-7, AC-8).
//
// Pure functions kept at module scope so the per-worker field rendering is
// unit-testable without driving the poll loop. The widget delegates to
// ``renderWorkerTreeFromRows`` with the latest broker-poll rows.
// --------------------------------------------------------------------------

// G019 AC-4 + AC-8: format an elapsed duration from the authoritative
// ``started_at``. Returns ``—`` when no start timestamp is known so the
// row still renders the other fields.
function formatElapsed(startedAt: string | null | undefined, nowMs: number): string {
  if (!startedAt) return "—";
  const startMs = Date.parse(startedAt);
  if (!Number.isFinite(startMs)) return "—";
  const deltaSec = Math.max(0, Math.floor((nowMs - startMs) / 1000));
  if (deltaSec < 60) return `${deltaSec}s`;
  const min = Math.floor(deltaSec / 60);
  const sec = deltaSec % 60;
  if (min < 60) return `${min}m ${sec.toString().padStart(2, "0")}s`;
  const hr = Math.floor(min / 60);
  const minR = min % 60;
  return `${hr}h ${minR.toString().padStart(2, "0")}m`;
}

// G019 AC-8: map the broker's canonical uppercase status to the compact
// glyph + lowercase form the inline tree presents (``● running`` / ``✓
// complete`` / ``✗ failed``). Presentation projection only; the canonical
// status is what the broker publishes.
function statusGlyph(status: string): string {
  const key = String(status || "").toUpperCase();
  if (key === "RUNNING" || key === "IN_PROGRESS") return "●";
  if (key === "DONE" || key === "COMPLETE" || key === "COMPLETED" || key === "SUCCEEDED") return "✓";
  if (key === "FAILED" || key === "ERROR" || key === "CANCELLED" || key === "CANCELED" || key === "TIMEOUT") return "✗";
  return "○";
}

function statusLabel(status: string): string {
  return `${statusGlyph(status)} ${String(status || "unknown").toLowerCase()}`;
}

// G019 AC-7 + AC-8: render the session-context line. ``ctx N/M (P%)``
// when tokens + contextWindow are known and positive; the literal
// ``ctx —`` when either is unknown. The label is always ``ctx`` — a
// session-context metric, never task-token spend.
function renderContextLine(tokens: number | null, contextWindow: number | null, percent: number | null): string {
  if (
    tokens !== null &&
    contextWindow !== null &&
    contextWindow > 0 &&
    Number.isFinite(tokens) &&
    Number.isFinite(contextWindow)
  ) {
    const tokensK = (Number(tokens) / 1000).toFixed(0);
    const windowK = (Number(contextWindow) / 1000).toFixed(0);
    const pct = percent !== null && Number.isFinite(percent) ? ` (${Number(percent).toFixed(0)}%)` : "";
    return `ctx ${tokensK}k/${windowK}k${pct}`;
  }
  return "ctx —";
}

// G019 AC-8: render the full inline worker tree from a set of broker-poll
// rows. Pure: takes the rows + a clock value, returns the line array. The
// tree is one lead-to-worker level — no nesting is ever inferred. At most
// three detailed workers; the rest collapse to ``+N more`` (AC-9).
function renderWorkerTreeFromRows(rows: WorkerRow[], width: number, nowMs: number): string[] {
  const withJobs = rows.filter((row) => Boolean(row.job));
  if (!withJobs.length) return [];
  const visible = withJobs.slice(0, 3);
  const moreCount = Math.max(0, withJobs.length - visible.length);
  const maxContent = Math.max(20, width - 4);
  const lines: string[] = ["Active workers"];
  for (const row of visible) {
    const task = row.job!;
    // Lead-to-worker row: registered worker name, task id, canonical status.
    // Keep the row compact on narrow terminals by truncating the status area.
    const status = statusLabel(task.status);
    const header = `${row.workerName} · ${task.id} · ${status}`;
    lines.push(truncateToWidth(header, maxContent, ""));
    // One ``└─`` line per worker: tool count, ctx line, elapsed.
    const parts: string[] = [];
    const toolCount = task.toolCount ?? null;
    if (toolCount !== null && toolCount >= 0) {
      parts.push(`${toolCount} tool${toolCount === 1 ? "" : "s"}`);
    }
    parts.push(renderContextLine(task.tokens ?? null, task.contextWindow ?? null, task.percent ?? null));
    parts.push(formatElapsed(task.startedAt, nowMs));
    const detail = `└─ ${parts.join(" · ")}`;
    lines.push(truncateToWidth(detail, maxContent, ""));
  }
  if (moreCount > 0) {
    lines.push(`+${moreCount} more`);
  }
  lines.push("");
  lines.push("F8 open workers");
  return lines;
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

function panelLines(rows: WorkerRow[], offline = false, selected = 0, mode: ViewMode = "list", follow: FollowState | null = null): string[] {
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
    private _getActivityByItem: () => Record<string, string[]>,
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
      : panelLines(rows, this.getOffline(), this.selected, this.mode, follow);
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
  // Cross-process broker state cannot use Pi's in-process session subscribe
  // API, so keep the local status projection responsive with a 1s default.
  // Operators may tune it down to 500ms; transcript Follow uses long polling
  // separately and is not gated by this interval.
  const pollMs = Math.max(500, Number(env("ORCHLINK_MONITOR_POLL_SECONDS", "1")) * 1000 || 1000);
  let rows: WorkerRow[] = [];
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
  const delegateRenderTimers = new Set<ReturnType<typeof setInterval>>();


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
    refreshWorkerTree();
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
      render(ctx);
    } catch (_error: any) {
      offline = true;
      rows = [];
      render(ctx);
    } finally {
      schedule(ctx, pollMs);
    }
  }

  // --------------------------------------------------------------------------
  // Native worker delegation tool (G019 AC-1, lead-only).
  //
  // Submits a task to a registered worker through the canonical Python
  // envelope builder by spawning ``orch send --async-json``. The Python side
  // owns the envelope (project configuration, prompt policy, API key, broker
  // URL, task ID validation); this extension is a thin adapter that
  // surfaces a structured tracking handle, never the worker's answer.
  if (role === "lead" && typeof pi.registerTool === "function") {
    type DelegateArgs = {
      worker: string;
      task_id: string;
      message: string;
      thinking?: string;
      async?: boolean;
    };
    type DelegateHandle = {
      worker: string;
      task_id: string;
      correlation_id: string;
      conversation_id: string;
      status: string;
      accepted_at: string;
    };
    type DelegateSnapshot = {
      status: string;
      startedAt: string;
      toolCount: number | null;
      tokens: number | null;
      contextWindow: number | null;
      percent: number | null;
      transcript: string[];
      cursor: number;
      resultSummary: string;
      background: boolean;
    };
    type DelegateDetails = { handle: DelegateHandle; snapshot: DelegateSnapshot };
    type DelegateRenderAnimation = { timer?: ReturnType<typeof setInterval>; frame?: number };
    const DELEGATE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
    const TERMINAL_DELEGATE_STATUSES = new Set([
      "DONE", "COMPLETE", "COMPLETED", "SUCCEEDED", "FAILED", "ERROR",
      "CANCELLED", "CANCELED", "TIMEOUT", "EXPIRED",
    ]);

    function delegateHeaders(): Record<string, string> {
      return {
        "X-API-Key": apiKey,
        "X-Orchlink-Project-ID": projectId,
      };
    }

    function delegateResult(handle: DelegateHandle, snapshot: DelegateSnapshot): any {
      const terminal = TERMINAL_DELEGATE_STATUSES.has(snapshot.status);
      const text = snapshot.background
        ? `${handle.task_id} accepted in background on ${handle.worker}. Handle: ${handle.correlation_id}.`
        : terminal
          ? `${handle.task_id} ${snapshot.status.toLowerCase()} on ${handle.worker}.${snapshot.resultSummary ? `\n${snapshot.resultSummary}` : ""}`
          : `${handle.task_id} is ${snapshot.status.toLowerCase()} on ${handle.worker}.`;
      return {
        content: [{ type: "text", text }],
        details: {
          handle: { ...handle },
          snapshot: { ...snapshot, transcript: [...snapshot.transcript] },
        } as DelegateDetails,
      };
    }

    function waitDelay(ms: number, signal: AbortSignal | undefined): Promise<void> {
      return new Promise((resolve) => {
        if (signal?.aborted) return resolve();
        const timer = setTimeout(resolve, ms);
        signal?.addEventListener("abort", () => {
          clearTimeout(timer);
          resolve();
        }, { once: true });
      });
    }

    async function refreshDelegateSnapshot(
      handle: DelegateHandle,
      snapshot: DelegateSnapshot,
      signal: AbortSignal | undefined,
    ): Promise<void> {
      const request = { headers: delegateHeaders(), ...(signal ? { signal } : {}) };
      const [taskResponse, telemetryResponse, transcriptResponse] = await Promise.all([
        fetch(`${brokerUrl}/v1/tasks/${encodeURIComponent(handle.task_id)}`, request),
        fetch(`${brokerUrl}/v1/tasks/${encodeURIComponent(handle.task_id)}/telemetry`, request),
        fetch(`${brokerUrl}/v1/tasks/${encodeURIComponent(handle.task_id)}/transcript?after=${snapshot.cursor}&limit=100`, request),
      ]);
      if (taskResponse.ok) {
        const task = await taskResponse.json() as JsonObject;
        snapshot.status = String(task.status || snapshot.status).toUpperCase();
        const payload = task?.reply?.payload || {};
        snapshot.resultSummary = String(payload.summary || payload.stdout || "").trim();
      }
      if (telemetryResponse.ok) {
        const telemetry = await telemetryResponse.json() as JsonObject;
        snapshot.toolCount = telemetryToolCount(telemetry);
        snapshot.tokens = telemetryNumber(telemetry, "tokens");
        snapshot.contextWindow = telemetryNumber(telemetry, "context_window");
        snapshot.percent = telemetryNumber(telemetry, "percent");
      }
      if (transcriptResponse.ok) {
        const transcript = await transcriptResponse.json() as JsonObject;
        const events = Array.isArray(transcript.events) ? transcript.events : [];
        for (const event of events) {
          const seq = Number(event?.seq || 0);
          const kind = String(event?.kind || event?.event_kind || "");
          const text = String(event?.text || "");
          const isTruncationMarker = kind === "system"
            && text === "Earlier transcript history was dropped by retention. This is not the complete output.";
          if (isTruncationMarker) {
            if (!snapshot.transcript.includes(text)) snapshot.transcript.push(text);
            continue;
          }
          if (seq <= snapshot.cursor) continue;
          snapshot.cursor = seq;
          if (kind === "assistant_delta") snapshot.transcript.push(...text.replace(/\r/g, "").split("\n"));
        }
        if (snapshot.transcript.length > 50) snapshot.transcript.splice(0, snapshot.transcript.length - 50);
      }
    }

    async function waitForDelegate(
      handle: DelegateHandle,
      snapshot: DelegateSnapshot,
      signal: AbortSignal | undefined,
      onUpdate: ((result: any) => void) | undefined,
    ): Promise<any> {
      onUpdate?.(delegateResult(handle, snapshot));
      while (!TERMINAL_DELEGATE_STATUSES.has(snapshot.status)) {
        if (signal?.aborted) {
          snapshot.status = "DETACHED";
          snapshot.background = true;
          return delegateResult(handle, snapshot);
        }
        try {
          await refreshDelegateSnapshot(handle, snapshot, signal);
        } catch (_error: any) {
          if (signal?.aborted) continue;
          // Progress is best effort; retry without changing task authority.
        }
        onUpdate?.(delegateResult(handle, snapshot));
        if (!TERMINAL_DELEGATE_STATUSES.has(snapshot.status)) await waitDelay(1000, signal);
      }
      return delegateResult(handle, snapshot);
    }

    async function runOrchSend(
      args: string[],
      signal: AbortSignal | undefined,
    ): Promise<DelegateHandle> {
      // Argument-array execution so the user-controlled fields cannot inject
      // shell metacharacters. ``pi.exec`` propagates the AbortSignal so the
      // LLM can cancel the delegation by interrupting the tool call.
      const result = await pi.exec("orch", args, {
        ...(signal ? { signal } : {}),
        timeout: 30_000,
      });
      const stdout = String(result.stdout ?? "").trim();
      const lastLine = stdout ? stdout.split(/\r?\n/).filter(Boolean).pop()! : "";
      if (result.code !== 0) {
        const stderr = String(result.stderr ?? "").trim();
        throw new Error(`orch send failed (exit ${result.code}): ${stderr || lastLine || "no output"}`);
      }
      if (!lastLine) {
        throw new Error("orch send returned no tracking handle (empty stdout)");
      }
      let parsed: DelegateHandle;
      try {
        parsed = JSON.parse(lastLine) as DelegateHandle;
      } catch (err: any) {
        throw new Error(`orch send returned non-JSON handle: ${String(err?.message || err)}`);
      }
      for (const field of ["worker", "task_id", "correlation_id", "status"] as const) {
        if (!parsed[field]) {
          throw new Error(`orch send handle missing required field: ${field}`);
        }
      }
      return parsed;
    }

    pi.registerTool({
      name: "delegate_worker",
      label: "Delegate to worker",
      description:
        "Delegate a task to a registered Orchlink worker through the canonical broker. By default delegate_worker behaves like a native Pi foreground subagent: the tool call stays pending, streams broker-backed progress through partial results, and returns the authoritative worker result only after terminal completion. Set async=true only for background work; that mode returns a TRACKING HANDLE (worker, task_id, correlation_id, status) as acceptance metadata, never the worker's final answer or a deliverable. The lead must later reconcile the authoritative broker result through the delivered message, /orchlink (F8), or orch jobs --result <task_id>.",
      promptSnippet:
        "delegate_worker streams inline until completion by default; async=true returns only a tracking handle and requires later result reconciliation.",
      promptGuidelines: [
        "Use delegate_worker with its default async=false when the user should see native inline worker progress and the next step depends on the result.",
        "Use delegate_worker with async=true only for genuinely independent background work; its tracking handle is NOT the worker's final answer or a deliverable.",
        "After delegate_worker async=true accepts a task, reconcile the authoritative broker result via the delivered Orchlink message, /orchlink (F8), or orch jobs --result <task_id>; never end dependent work on acceptance alone.",
      ],
      parameters: Type.Object({
        worker: Type.String({ description: "Registered worker name (e.g. 'work', 'review', 'bg-test')." }),
        task_id: Type.String({ description: "Exact task ID to assign, such as 'UI-031'." }),
        message: Type.String({ description: "Task prompt for the worker." }),
        thinking: Type.Optional(
          Type.String({
            description: "Override worker thinking for this task: off, minimal, low, medium, high, xhigh.",
          }),
        ),
        async: Type.Optional(
          Type.Boolean({
            description: "Return a tracking handle immediately and continue in the background. Default false keeps the native Pi tool row pending and streams progress until completion.",
          }),
        ),
      }),
      executionMode: "parallel",
      async execute(_toolCallId, params, signal, onUpdate, _ctx) {
        const background = params.async === true;
        const args = [
          "send",
          String(params.worker),
          "--task-id",
          String(params.task_id),
          "--message",
          String(params.message),
          "--async-json",
        ];
        // Foreground mode still submits immediately through the canonical
        // client, but uses blocking delivery so the separate async notifier
        // does not duplicate the result that this pending tool will return.
        if (!background) args.push("--foreground-json");
        if (params.thinking) args.push("--thinking", String(params.thinking));
        const handle = await runOrchSend(args, signal);
        const snapshot: DelegateSnapshot = {
          status: String(handle.status || "PENDING").toUpperCase(),
          startedAt: String(handle.accepted_at || new Date().toISOString()),
          toolCount: null,
          tokens: null,
          contextWindow: null,
          percent: null,
          transcript: [],
          cursor: 0,
          resultSummary: "",
          background,
        };
        if (background) return delegateResult(handle, snapshot);
        return await waitForDelegate(handle, snapshot, signal, onUpdate);
      },
      renderCall(args, theme) {
        // Concise intent row: ``● Delegate → <worker>``. The renderCall row
        // only surfaces the worker name (an identifier the LLM already
        // supplied) and never inlines the task body, transcript text, CLI
        // output, or any tool result body.
        const worker = String(args.worker || "worker");
        const background = args.async === true ? " [background]" : "";
        const styled = theme?.fg
          ? theme.fg("accent", `● Delegate → ${worker}${background}`)
          : `● Delegate → ${worker}${background}`;
        return new Text(styled, 0, 0);
      },
      renderResult(result, options, theme, context) {
        // Native Pi foreground semantics: execute() remains pending and sends
        // partial AgentToolResult snapshots through onUpdate. The renderer is
        // pure apart from a lightweight spinner invalidation timer; it never
        // polls the broker after the tool has settled.
        const details = result?.details as DelegateDetails | undefined;
        const handle = details?.handle;
        const snapshot = details?.snapshot;
        const accent = (text: string) => (theme?.fg ? theme.fg("accent", text) : text);
        const dim = (text: string) => (theme?.fg ? theme.fg("dim", text) : text);
        const success = (text: string) => (theme?.fg ? theme.fg("success", text) : text);
        const error = (text: string) => (theme?.fg ? theme.fg("error", text) : text);
        if (!handle?.task_id || !handle?.worker || !snapshot) {
          return new Text(error(`delegate_worker: progress details missing`), 0, 0);
        }

        const animation = context.state as DelegateRenderAnimation;
        const terminal = TERMINAL_DELEGATE_STATUSES.has(snapshot.status);
        if (!snapshot.background && !terminal && !animation.timer) {
          animation.frame = 0;
          animation.timer = setInterval(() => {
            animation.frame = ((animation.frame ?? 0) + 1) % DELEGATE_FRAMES.length;
            context.invalidate();
          }, 80);
          animation.timer.unref?.();
          delegateRenderTimers.add(animation.timer);
        } else if ((terminal || snapshot.background) && animation.timer) {
          clearInterval(animation.timer);
          delegateRenderTimers.delete(animation.timer);
          animation.timer = undefined;
        }

        const failed = ["FAILED", "ERROR", "CANCELLED", "CANCELED", "TIMEOUT", "EXPIRED"].includes(snapshot.status);
        const glyph = snapshot.background
          ? "○"
          : terminal
            ? (failed ? "✗" : "✓")
            : DELEGATE_FRAMES[animation.frame ?? 0];
        const statusText = snapshot.background
          ? "background"
          : terminal && !failed
            ? "complete"
            : snapshot.status.toLowerCase();
        const statusStyled = failed
          ? error(`${glyph} ${statusText}`)
          : terminal
            ? success(`${glyph} ${statusText}`)
            : accent(`${glyph} ${statusText}`);
        const metrics: string[] = [];
        if (snapshot.toolCount !== null) metrics.push(`${snapshot.toolCount} tool${snapshot.toolCount === 1 ? "" : "s"}`);
        metrics.push(renderContextLine(snapshot.tokens, snapshot.contextWindow, snapshot.percent));
        metrics.push(formatElapsed(snapshot.startedAt, Date.now()));

        const component = (context.lastComponent as Container | undefined) ?? new Container();
        component.clear();
        component.addChild(new Text(`${statusStyled} · ${accent(handle.task_id)} · ${handle.worker}`, 0, 0));
        component.addChild(new Text(dim(`  ${metrics.join(" · ")}`), 0, 0));
        if (options.expanded && !snapshot.background) {
          const visible = snapshot.transcript.slice(-12);
          component.addChild(new Text(dim(visible.length ? `  ${visible.join("\n  ")}` : "  Waiting for visible worker output…"), 0, 0));
        } else if (snapshot.background) {
          component.addChild(new Text(dim(`  Tracking handle returned · F8 details`), 0, 0));
        } else if (!terminal) {
          component.addChild(new Text(dim(`  Expand tool row for live output · F8 details`), 0, 0));
        } else if (snapshot.resultSummary) {
          const summary = snapshot.resultSummary.replace(/\s+/g, " ").trim();
          component.addChild(new Text(dim(`  ${summary.length > 160 ? `${summary.slice(0, 159)}…` : summary}`), 0, 0));
        }
        return component;
      },
    });
  }

  // --------------------------------------------------------------------------
  // Inline worker tree widget (G019 AC-7, AC-8, AC-9).
  //
  // Below-editor widget that mirrors the same broker-driven ``rows`` data
  // the overlay consumes and renders a compact per-worker line. The
  // session-context metric is read from a worker-side telemetry record
  // (worker-published under the existing telemetry endpoint) and rendered
  // as ``ctx N/M (P%)`` when tokens + contextWindow are present, or the
  // literal ``ctx —`` when either is unknown. Hides automatically when no
  // relevant worker work remains.
  let widgetRows: WorkerRow[] = [];

  // G019 AC-4 + AC-8: a single shared UI tick drives the elapsed display
  // WITHOUT per-second broker polling. The widget reads `job.startedAt`
  // (set by AC-4's durable capture) and formats an elapsed string against
  // the local clock. The tick is paused when no relevant work remains.
  let widgetTick: ReturnType<typeof setInterval> | undefined;
  let widgetRequestRender: (() => void) | undefined;

  function renderWorkerTreeLines(width: number): string[] {
    // Delegates to the pure module-level renderer so the per-worker field
    // rendering (AC-8) is unit-tested directly without driving the poll.
    return renderWorkerTreeFromRows(widgetRows, width, Date.now());
  }

  function renderWorkerTreeWidget(tui: any, theme: any): any {
    // Pi invokes this factory once. The returned component must read live
    // broker-backed rows on every render; a static empty Container created at
    // session start would remain blank when work begins later.
    if (tui?.requestRender) widgetRequestRender = tui.requestRender.bind(tui);
    return {
      render(availableWidth: number) {
        // AC-9: an empty render hides the surface when no work remains.
        if (!widgetRows.length) return [];
        const width = Math.max(20, Number(availableWidth || tui?.terminal?.cols || 80));
        const lines = renderWorkerTreeLines(width);
        const accent = (text: string) => (theme?.fg ? theme.fg("accent", text) : text);
        const dim = (text: string) => (theme?.fg ? theme.fg("dim", text) : text);
        const statusColor = (text: string, status: string) => {
          if (!theme?.fg) return text;
          const s = String(status || "").toUpperCase();
          if (s === "RUNNING" || s === "IN_PROGRESS") return theme.fg("accent", text);
          if (s === "DONE" || s === "COMPLETE" || s === "COMPLETED" || s === "SUCCEEDED") return theme.fg("dim", text);
          if (s === "FAILED" || s === "ERROR" || s === "CANCELLED" || s === "CANCELED" || s === "TIMEOUT") {
            return theme.fg("error", text);
          }
          return theme.fg("warning", text);
        };
        return lines.map((line) => {
          if (line === "Active workers") return accent(line);
          if (line === "F8 open workers") return dim(line);
          if (line.startsWith("+")) return dim(line);
          // Lead-to-worker row: color the worker name and the status label.
          if (line.includes(" · ") && !line.startsWith("└─")) {
            const parts = line.split(" · ");
            if (parts.length >= 3) {
              const workerPart = parts[0];
              const statusPart = parts[parts.length - 1];
              const statusValue = statusPart.replace(/^[●✓✗○]\s*/, "");
              parts[0] = accent(workerPart);
              parts[parts.length - 1] = statusColor(statusPart, statusValue);
              return parts.join(" · ");
            }
          }
          return line;
        });
      },
      invalidate() {},
    };
  }

  function bindWorkerTreeWidget(ctx: any) {
    if (role !== "lead" || !isTui(ctx) || typeof ctx.ui?.setWidget !== "function") return;
    // AC-8: keep one compact worker activity tree fixed above the editor so
    // it remains visible while transcript and tool output scroll.
    ctx.ui.setWidget("orchlink-worker-tree", renderWorkerTreeWidget, { placement: "aboveEditor" });
  }

  // G019 AC-4 + AC-8: a single shared UI timer — NOT per-second broker
  // polling — drives the elapsed text. The timer is started on first
  // relevant work and stopped when the widget is idle.
  function ensureWidgetTick() {
    if (widgetTick) return;
    widgetTick = setInterval(() => {
      // Re-render only if the widget is currently visible.
      if (widgetRows.length) widgetRequestRender?.();
    }, 1000);
  }
  function clearWidgetTick() {
    if (!widgetTick) return;
    clearInterval(widgetTick);
    widgetTick = undefined;
  }

  // Refresh widget rows whenever the poll loop updates the live data.
  function refreshWorkerTree() {
    widgetRows = rows.filter((row) => Boolean(row.job));
    if (widgetRows.length) ensureWidgetTick();
    else clearWidgetTick();
    // Status transitions should paint immediately; the shared timer exists
    // only to advance elapsed time between broker polls.
    widgetRequestRender?.();
  }

  async function openOrchlinkOverlay(ctx: any) {
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
            () => ({}),
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
  }

  pi.registerCommand("orchlink", {
    description: "Show Orchlink worker status panel",
    handler: async (_args: string, ctx: any) => {
      await openOrchlinkOverlay(ctx);
    },
  });

  // G019 AC-11: configurable shortcut to open the /orchlink overlay. Default
  // ``F8`` has no Pi/Herdr conflict; ``ORCHLINK_WORKERS_KEY`` overrides it.
  const RESERVED_SHORTCUTS = new Set([
    "ctrl+b", "ctrl+o", "ctrl+g", "ctrl+l", "ctrl+p", "ctrl+r", "ctrl+t",
    "ctrl+w", "ctrl+j", "shift+tab", "ctrl+tab", "ctrl+c", "ctrl+d", "ctrl+z",
  ]);
  const workersShortcutKeyRaw = env("ORCHLINK_WORKERS_KEY", "f8");
  const workersShortcutKey = workersShortcutKeyRaw.toLowerCase().replace(/\s+/g, "");
  if (role === "lead" && typeof pi.registerShortcut === "function") {
    if (RESERVED_SHORTCUTS.has(workersShortcutKey)) {
      console.warn(`[orchlink] ORCHLINK_WORKERS_KEY "${workersShortcutKeyRaw}" conflicts with a reserved Pi/Herdr binding; falling back to /orchlink command.`);
    } else {
      pi.registerShortcut(workersShortcutKey, {
        description: "Open the Orchlink workers overlay",
        handler: async (ctx: any) => {
          await openOrchlinkOverlay(ctx);
        },
      });
    }
  }

  pi.on("session_start", async (_event, ctx) => {
    lastCtx = ctx;
    if (role !== "lead" || !isTui(ctx)) return;
    bindWorkerTreeWidget(ctx);
    render(ctx);
    schedule(ctx, 0);
  });

  pi.on("session_shutdown", async () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    abortController?.abort();
    for (const delegateTimer of delegateRenderTimers) clearInterval(delegateTimer);
    delegateRenderTimers.clear();
    // Abort any in-flight follow polling so the panel cleans up before Pi
    // tears down the session.
    panelTranscriber?.abortActive();
    panelTranscriber = null;
    setPanelMouseTracking(false);
    panelMouseTerminal = undefined;
    panelMouseEnabled = false;
    widgetRequestRender = undefined;
    clearWidgetTick();
    if (lastCtx && isTui(lastCtx) && typeof pi.setSessionName === "function") {
      pi.setSessionName("Orchlink Lead");
    }
  });
}
'''
