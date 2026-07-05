"""Generated TypeScript for the Orchlink read-only Pi TUI monitor extension."""

from __future__ import annotations


ORCHLINK_PI_UI_EXTENSION = r'''
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";

type JsonObject = Record<string, any>;
type PanelResult = { action: "stop"; workerName: string } | { action: "visible"; workerName: string } | null;

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
  const label = rows.length === 1 ? "worker" : "workers";
  return `Orchlink Lead · ${rows.length} ${label}`;
}

function formatActivityRecord(record: JsonObject): string {
  const type = String(record.activity_type || record.type || "activity");
  const tool = String(record.tool_name || "");
  const detail = String(record.detail || record.preview || "");
  const prefix = tool ? `${type}/${tool}` : type;
  return `${prefix}: ${detail || "working"}`;
}

function panelLines(rows: WorkerRow[], activityByItem: Record<string, string[]> = {}, offline = false, selected = 0): string[] {
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
    const heading = row.job ? `${marker} ${row.workerName}  ${row.job.status}  ${row.job.id}` : `${marker} ${row.workerName}  IDLE`;
    lines.push(heading);
    lines.push(`  agent: ${row.agentId || "-"}`);
    lines.push(`  model: ${row.model || "-"}${row.thinking ? ` · thinking ${row.thinking}` : ""}`);
    lines.push(`  runtime: ${row.runtime || "-"} / ${row.backend || "-"} · ready: ${row.ready ? "yes" : "no"}`);
    if (row.job) {
      const turn = row.job.turn && row.job.maxTurns ? ` · turn ${row.job.turn}/${row.job.maxTurns}` : "";
      lines.push(`  work: ${row.job.kind.toUpperCase()} ${row.job.mode}${turn}`);
      lines.push(`  last: ${truncate(row.job.tool ? `${row.job.tool}: ${row.job.activity}` : row.job.activity || "working", 160)}`);
      const activity = activityByItem[row.job.id] || [];
      if (activity.length) {
        lines.push("  activity:");
        for (const item of activity.slice(-12)) lines.push(`    ${truncate(item, 150)}`);
      }
    }
    lines.push("");
  });
  lines.push("↑/↓ select · s stop worker · PgUp/PgDn scroll · q/Esc close");
  return lines;
}

class OrchlinkWorkersPanel {
  private offset = 0;
  private selected = 0;
  constructor(
    private getRows: () => WorkerRow[],
    private getActivityByItem: () => Record<string, string[]>,
    private getOffline: () => boolean,
    private done: (value: PanelResult) => void,
  ) {}
  invalidate() {}
  private allLines(): string[] {
    return panelLines(this.getRows(), this.getActivityByItem(), this.getOffline(), this.selected);
  }
  private visibleHeight(): number {
    return Math.min(22, Math.max(6, this.allLines().length));
  }
  private maxOffset(): number {
    return Math.max(0, this.allLines().length - this.visibleHeight());
  }
  private clampOffset() {
    this.offset = Math.min(this.maxOffset(), Math.max(0, this.offset));
  }
  render(width: number): string[] {
    const rows = this.getRows();
    this.selected = Math.min(Math.max(0, this.selected), Math.max(0, rows.length - 1));
    const contentWidth = Math.max(20, width - 4);
    const all = this.allLines().map((line) => truncate(line, contentWidth));
    const height = this.visibleHeight();
    this.clampOffset();
    const visible = all.slice(this.offset, this.offset + height);
    const border = "─".repeat(Math.max(0, contentWidth));
    return [`┌${border}┐`, ...visible.map((line) => `│${line.padEnd(contentWidth).slice(0, contentWidth)}│`), `└${border}┘`];
  }
  handleInput(data: string) {
    const rows = this.getRows();
    if (data === "q" || data === "\u001b") this.done(null);
    else if (data === "s" && rows[this.selected]) {
      const row = rows[this.selected];
      const isBackground = row.backend === "rpc-supervisor" || row.runtime === "rpc";
      this.done({ action: isBackground ? "stop" : "visible", workerName: row.workerName });
    }
    else if (data === "\u001b[A") this.selected = Math.max(0, this.selected - 1);
    else if (data === "\u001b[B") this.selected = Math.min(Math.max(0, rows.length - 1), this.selected + 1);
    else if (data === "\u001b[5~") this.offset = Math.max(0, this.offset - 8);
    else if (data === "\u001b[6~") this.offset = Math.min(this.maxOffset(), this.offset + 8);
  }
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
      const result = await ctx.ui.custom<PanelResult>(
        (_tui: any, _theme: any, _keybindings: any, done: (value: PanelResult) => void) => new OrchlinkWorkersPanel(
          () => rows,
          () => activityByItem,
          () => offline,
          done,
        ),
        {
          overlay: true,
          overlayOptions: {
            width: "70%",
            minWidth: 32,
            maxHeight: "70%",
            anchor: "center",
            margin: 1,
          },
          onHandle: (handle: any) => {
            overlayHandle = handle;
          },
        },
      );
      overlayHandle = undefined;
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
    if (lastCtx && isTui(lastCtx) && typeof pi.setSessionName === "function") {
      pi.setSessionName("Orchlink Lead");
    }
  });
}
'''
