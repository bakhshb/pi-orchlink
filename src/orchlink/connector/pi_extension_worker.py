"""Generated TypeScript for the Orchlink Pi worker/lead connector extension."""

from __future__ import annotations

import json

from orchlink.connector.pi_extension_pure import interpolation_replacements
from orchlink.core.prompt_policy import TaskPromptPolicy


_TASK_PROMPT_POLICY = TaskPromptPolicy()


_EXTENSION_TEMPLATE = r'''
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

type OrchMessage = Record<string, any>;

const ORCHLINK_WORKER_TASK_GUIDANCE = __ORCHLINK_WORKER_TASK_GUIDANCE__;

// Pure detection rules (single source: orchlink.connector.pi_extension_pure).
const RECOVERABLE_ERROR_REGEX = new RegExp(__ORCH_RECOVERABLE_ERROR_PATTERN__, "i");

function env(name: string, fallback = ""): string {
  return process.env[name] || fallback;
}

const THINKING_LEVELS = new Set(__ORCH_THINKING_LEVELS__);
const MODE_THINKING_DEFAULTS: Record<string, string> = __ORCH_MODE_THINKING_DEFAULTS__;

function normalizeThinking(value: any): string {
  const text = String(value || "").trim().toLowerCase();
  return THINKING_LEVELS.has(text) ? text : "";
}

function asList(value: any): string[] {
  if (Array.isArray(value)) return value.map((item) => String(item));
  if (value === undefined || value === null || value === "") return [];
  return [String(value)];
}

function formatList(values: string[]): string {
  return values.length ? values.map((value) => `- ${value}`).join("\n") : "- None";
}

function isChatRequest(message: OrchMessage): boolean {
  return ["CHAT_START", "CHAT_TURN"].includes(String(message?.type || ""));
}

function renderWorkerTalkPrompt(message: OrchMessage): string {
  const payload = message.payload || {};
  const speaker = message.from_agent || "lead";
  const conversation = message.conversation_id || "";
  const turn = `${message.turn || 1}/${message.max_turns || 6}`;
  const text = payload.message || payload.intent || payload.topic || "";
  return `[Orchlink Talk] ${speaker} · ${conversation} · ${turn}\n\n${text}`;
}

function renderWorkerTaskPrompt(message: OrchMessage): string {
  const payload = message.payload || {};
  const scope = payload.scope || {};
  const constraints = asList(payload.constraints);
  const expectedReply = asList(payload.expected_reply);
  const optionalConstraints = constraints.length ? `\n\nExtra constraints from lead:\n${formatList(constraints)}` : "";
  const optionalReply = expectedReply.length ? `\n\nLead-requested reply shape:\n${formatList(expectedReply)}` : "";
  return `You are the worker coding agent in an Orchlink pair. Handle only task ${message.task_id || ""}.

Lead request:
${payload.intent || payload.summary || ""}

Project scope guardrails:
Allowed:
${formatList(asList(scope.allowed))}

Forbidden:
${formatList(asList(scope.forbidden))}${optionalConstraints}${optionalReply}

${ORCHLINK_WORKER_TASK_GUIDANCE}
`;
}

function renderWorkerPrompt(message: OrchMessage): string {
  if (isChatRequest(message)) return renderWorkerTalkPrompt(message);
  return renderWorkerTaskPrompt(message);
}

function isOrchlinkWorkerPrompt(text: any): boolean {
  const value = String(text || "");
  return value.startsWith("You are the worker coding agent in") || value.startsWith("[Orchlink Talk]");
}

function stripChatReplyMarker(value: any): string {
  let text = String(value || "").trim();
  let previous = "";
  while (text !== previous) {
    previous = text;
    text = text.replace(/^\s*TYPE:\s*CHAT_REPLY\s*\r?\n?/i, "").trim();
    text = text.replace(/^\s*MODE:\s*TALK\s*\r?\n?/i, "").trim();
  }
  return text;
}

function renderLeadPrompt(message: OrchMessage): string {
  const payload = message.payload || {};
  const rawSummary = payload.summary || payload.stdout || payload.message || "";
  const type = message.type || "RESULT";
  const summary = type === "CHAT_REPLY" ? stripChatReplyMarker(rawSummary) : rawSummary;
  if (type === "CHAT_REPLY") {
    return `[Orchlink] ${message.from_agent || "work"} · ${message.conversation_id || ""} · ${message.turn || "?"}/${message.max_turns || "?"}

${summary}`;
  }

  return `[Orchlink] Result from ${message.from_agent || "work"}

Task: ${message.task_id || ""}
Mode: ${payload.mode || type}
Status: ${message.status || "DONE"}

Worker result:
${summary}`;
}

function messageText(message: any): string {
  const content = message?.content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter((part) => part && part.type === "text")
      .map((part) => String(part.text || ""))
      .join("\n");
  }
  return "";
}

function detectReplyType(output: string): string {
  const firstLine = output.split(/\r?\n/).map((line) => line.trim()).find((line) => line.length > 0) || "";
  if (!firstLine.startsWith("TYPE:")) return "RESULT";
  const value = firstLine.slice("TYPE:".length).trim().split(/\s+/, 1)[0];
  if (["PLAN", "RESULT", "BLOCKER"].includes(value)) return value;
  return "RESULT";
}

function replyEnvelope(task: OrchMessage, assistantMessage: any): OrchMessage {
  const output = messageText(assistantMessage);
  const failed = assistantMessage?.stopReason === "error" || assistantMessage?.stopReason === "aborted";
  const chat = isChatRequest(task);
  const replyType = chat ? "CHAT_REPLY" : (failed ? "BLOCKER" : detectReplyType(output));
  const summary = chat ? stripChatReplyMarker(output) : output;
  return {
    protocol: task.protocol || "orch-a2a-v1",
    message_id: `reply-${crypto.randomUUID()}`,
    correlation_id: task.correlation_id,
    project_id: task.project_id || env("ORCHLINK_PROJECT_ID", "default"),
    conversation_id: task.conversation_id || `${env("ORCHLINK_PROJECT_ID", "default")}-default`,
    task_id: task.task_id || null,
    from_agent: env("ORCHLINK_AGENT_ID", task.to_agent || "work"),
    to_agent: task.from_agent,
    type: replyType,
    status: failed ? "FAILED" : "DONE",
    turn: Math.min(Number(task.turn || 1) + 1, Number(task.max_turns || 6)),
    max_turns: task.max_turns || 6,
    requires_reply: false,
    timeout_seconds: 1,
    delivery: chat ? "conversation" : (task.delivery || "async"),
    payload: {
      mode: chat ? "TALK" : (task.payload || {}).mode,
      summary,
      stdout: output,
      stderr: failed ? assistantMessage?.errorMessage || "Pi assistant stopped before completing the task." : "",
      exit_code: failed ? 1 : 0,
      timed_out: false,
    },
  };
}

async function postJson(path: string, body: any, extraHeaders: Record<string, string> = {}): Promise<any> {
  const baseUrl = env("ORCHLINK_BROKER_URL", "http://127.0.0.1:8787").replace(/\/$/, "");
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": env("ORCHLINK_API_KEY", "change-me"),
      "x-orchlink-project-id": env("ORCHLINK_PROJECT_ID", "default"),
      ...extraHeaders,
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json();
}

async function getJson(path: string): Promise<any> {
  const baseUrl = env("ORCHLINK_BROKER_URL", "http://127.0.0.1:8787").replace(/\/$/, "");
  const response = await fetch(`${baseUrl}${path}`, {
    headers: {
      "x-api-key": env("ORCHLINK_API_KEY", "change-me"),
      "x-orchlink-project-id": env("ORCHLINK_PROJECT_ID", "default"),
    },
  });
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json();
}

async function markMessageStatus(messageId: string, status: string): Promise<void> {
  if (!messageId) return;
  const sessionLeaseId = env("ORCHLINK_SESSION_LEASE_ID");
  await postJson(`/v1/messages/${encodeURIComponent(messageId)}/status`, { status, session_lease_id: sessionLeaseId || undefined });
}

async function renewJobLease(taskId: string, epoch: number): Promise<{ ok: boolean; status: number }> {
  if (!taskId) return { ok: false, status: 0 };
  const baseUrl = env("ORCHLINK_BROKER_URL", "http://127.0.0.1:8787").replace(/\/$/, "");
  try {
    const response = await fetch(`${baseUrl}/v1/jobs/${encodeURIComponent(taskId)}/heartbeat`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "x-api-key": env("ORCHLINK_API_KEY", "change-me"),
        "x-orchlink-project-id": env("ORCHLINK_PROJECT_ID", "default"),
      },
      body: JSON.stringify({
        holder: env("ORCHLINK_AGENT_ID"),
        epoch,
        heartbeat_ms: Math.max(5000, Number(env("ORCHLINK_ACTIVITY_HEARTBEAT_MS", "15000")) || 15000),
      }),
    });
    return { ok: response.ok, status: response.status };
  } catch (error: any) {
    // Transient broker error: do not treat as lease loss. Keep working.
    return { ok: false, status: 0 };
  }
}

function truncate(value: any, maxLength = 180): string {
  const text = String(value === undefined || value === null ? "" : value).replace(/\s+/g, " ").trim();
  return text.length > maxLength ? `${text.slice(0, maxLength - 1)}…` : text;
}

function summarizeToolInput(toolName: string, input: any): string {
  if (!input || typeof input !== "object") return "";
  if (toolName === "bash") return truncate(input.command || "bash", 220);
  if (toolName === "read") return truncate(input.path || "read", 220);
  if (toolName === "edit" || toolName === "write") return truncate(input.path || toolName, 220);
  if (toolName === "web_fetch") return truncate(input.url || "web_fetch", 220);
  if (toolName === "web_search") return truncate(input.query || "web_search", 220);
  return truncate(JSON.stringify(input), 220);
}

function hasActiveStatus(value: any): boolean {
  return ["PENDING", "QUEUED", "DELIVERED", "RUNNING", "IN_PROGRESS", "OPEN"].includes(String(value || "").toUpperCase());
}

export default function (pi: ExtensionAPI) {
  const role = env("ORCHLINK_PI_ROLE");
  const agentId = env("ORCHLINK_AGENT_ID");
  const projectId = env("ORCHLINK_PROJECT_ID", "default");
  const pollWaitSeconds = Number(env("ORCHLINK_POLL_WAIT_SECONDS", "5"));
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let cancelTimer: ReturnType<typeof setTimeout> | undefined;
  let recoveryTimer: ReturnType<typeof setTimeout> | undefined;
  let activityTimer: ReturnType<typeof setTimeout> | undefined;
  let pendingTask: OrchMessage | undefined;
  let currentTask: OrchMessage | undefined;
  let abortCurrentTurn: (() => void) | undefined;
  let cancelNoticeSent = false;
  let activityUnsupported = false;
  let readyTimer: ReturnType<typeof setTimeout> | undefined;
  const sessionLeaseId = env("ORCHLINK_SESSION_LEASE_ID");
  const runtimeMode = env("ORCHLINK_RUNTIME_MODE");
  const backgroundBackend = env("ORCHLINK_BACKGROUND_BACKEND");
  const oneshot = env("ORCHLINK_ONESHOT").toLowerCase() === "true";
  const workerModel = env("ORCHLINK_WORKER_MODEL").trim();
  const workerThinking = normalizeThinking(env("ORCHLINK_WORKER_THINKING"));
  const supervisorPid = Number(env("ORCHLINK_SUPERVISOR_PID", "0")) || undefined;
  const readyHeartbeatMs = Math.max(1000, Number(env("ORCHLINK_READY_HEARTBEAT_MS", "5000")) || 5000);
  // G018 visible-transcript buffering limits.
  const TRANSCRIPT_FLUSH_MS = Math.max(50, Math.min(500, Number(env("ORCHLINK_TRANSCRIPT_FLUSH_MS", "150")) || 150));
  const TRANSCRIPT_MAX_BYTES = Math.max(256, Math.min(8192, Number(env("ORCHLINK_TRANSCRIPT_MAX_BYTES", "2048")) || 2048));
  // M3 job lease: the worker captures the lease epoch at pickup and renews it
  // via /v1/jobs/{id}/heartbeat. On a 409 (stale/reclaimed) it stops and steers.
  let currentLeaseEpoch: number = 0;
  let leaseLost = false;
  let workerCtx: any;
  // G018 visible-assistant transcript buffering.
  let transcriptBuffer = "";
  let transcriptFlushTimer: ReturnType<typeof setTimeout> | undefined;
  let transcriptBatchId = 0;
  const recoveryGraceMs = Math.max(1000, Number(env("ORCHLINK_RECOVERABLE_ERROR_GRACE_MS", "180000")) || 180000);
  const activityHeartbeatMs = Math.max(5000, Number(env("ORCHLINK_ACTIVITY_HEARTBEAT_MS", "15000")) || 15000);

  async function register() {
    if (!agentId || !role) return;
    await postJson("/v1/agents/register", {
      project_id: projectId,
      agent_id: agentId,
      role: role === "work" ? "worker" : role,
      display_name: role === "work" ? "Worker" : "Lead",
      capabilities: role === "work" ? ["inspection", "implementation", "tests", "talk"] : ["delegation", "review", "talk"],
    });
  }

  function currentModelName(ctx?: any): string | undefined {
    if (role === "work" && workerModel) return workerModel;
    const model = ctx?.model;
    if (!model) return undefined;
    const provider = String(model.provider || "").trim();
    const id = String(model.id || model.name || "").trim();
    if (provider && id && !id.startsWith(`${provider}/`)) return `${provider}/${id}`;
    return id || provider || undefined;
  }

  function currentThinkingLevel(): string | undefined {
    if (role === "work" && workerThinking) return workerThinking;
    if (typeof pi.getThinkingLevel === "function") {
      const level = pi.getThinkingLevel();
      return level ? String(level) : undefined;
    }
    return undefined;
  }

  async function sendReadyHeartbeat(ctx?: any) {
    if (!sessionLeaseId || !["lead", "work"].includes(role)) return;
    await postJson(`/v1/sessions/${encodeURIComponent(sessionLeaseId)}/heartbeat`, {
      project_id: projectId,
      ready: true,
      runtime_mode: runtimeMode || ctx?.mode || "tui",
      backend: backgroundBackend || (ctx?.mode === "rpc" ? "rpc-supervisor" : "interactive"),
      model: currentModelName(ctx),
      thinking: currentThinkingLevel(),
      supervisor_pid: supervisorPid,
      pi_pid: typeof process?.pid === "number" ? process.pid : undefined,
    });
  }

  function scheduleReadyHeartbeat(ctx?: any, delayMs = readyHeartbeatMs) {
    if (readyTimer) clearTimeout(readyTimer);
    if (stopped || !sessionLeaseId || !["lead", "work"].includes(role)) return;
    readyTimer = setTimeout(() => {
      void sendReadyHeartbeat(ctx)
        .catch((error) => console.error(`[orchlink] ready heartbeat failed: ${error?.message || error}`))
        .finally(() => scheduleReadyHeartbeat(ctx, readyHeartbeatMs));
    }, delayMs);
  }

  function schedule(delayMs = 0) {
    if (stopped || !["lead", "work"].includes(role)) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => void poll(), delayMs);
  }

  function taskThinkingLevel(message: OrchMessage): string {
    const payload = message?.payload || {};
    const mode = String(payload.mode || "").toUpperCase();
    return workerThinking || normalizeThinking(payload.thinking) || normalizeThinking(MODE_THINKING_DEFAULTS[mode]) || "";
  }

  async function applyWorkerThinking(message: OrchMessage) {
    if (role !== "work") return;
    const level = taskThinkingLevel(message);
    if (!level || typeof pi.setThinkingLevel !== "function") return;
    if (typeof pi.getThinkingLevel === "function" && pi.getThinkingLevel() === level) return;
    pi.setThinkingLevel(level as any);
  }

  function clearCancelCheck() {
    if (cancelTimer) clearTimeout(cancelTimer);
    cancelTimer = undefined;
  }

  function clearRecoveryTimer() {
    if (recoveryTimer) clearTimeout(recoveryTimer);
    recoveryTimer = undefined;
  }

  function clearActivityHeartbeat() {
    if (activityTimer) clearTimeout(activityTimer);
    activityTimer = undefined;
  }

  function clearTranscriptFlush() {
    if (transcriptFlushTimer) clearTimeout(transcriptFlushTimer);
    transcriptFlushTimer = undefined;
  }

  function flushTranscriptBuffer(force = false) {
    const text = transcriptBuffer;
    transcriptBuffer = "";
    clearTranscriptFlush();
    if (!text || role !== "work" || !currentTask || leaseLost) return;
    const task = currentTask;
    const batchId = ++transcriptBatchId;
    const headers: Record<string, string> = {
      "x-orchlink-project-id": task.project_id || projectId,
    };
    if (currentLeaseEpoch) headers["x-orchlink-lease-epoch"] = String(currentLeaseEpoch);
    if (agentId) headers["x-orchlink-lease-holder"] = agentId;
    if (sessionLeaseId) headers["x-orchlink-session-lease-id"] = sessionLeaseId;
    const body = {
      project_id: task.project_id || projectId,
      task_id: task.task_id || null,
      agent_id: agentId,
      worker_name: env("ORCHLINK_WORKER_NAME") || agentId || "work",
      batch_id: `batch-${batchId}`,
      events: [{ kind: "assistant_delta", text }],
    };
    postJson(`/v1/tasks/${encodeURIComponent(String(task.task_id || ""))}/transcript`, body, headers).catch((error: any) => {
      console.error(`[orchlink] transcript post failed: ${error?.message || error}`);
    });
  }

  function appendTranscriptDelta(delta: string) {
    if (!delta || role !== "work" || !currentTask) return;
    transcriptBuffer += delta;
    if (Buffer.byteLength(transcriptBuffer, "utf8") >= TRANSCRIPT_MAX_BYTES) {
      flushTranscriptBuffer(true);
      return;
    }
    clearTranscriptFlush();
    transcriptFlushTimer = setTimeout(() => flushTranscriptBuffer(), TRANSCRIPT_FLUSH_MS);
  }

  function finalizeTranscript(reason: string) {
    flushTranscriptBuffer(true);
    if (!currentTask) return;
    const task = currentTask;
    const headers: Record<string, string> = {
      "x-orchlink-project-id": task.project_id || projectId,
    };
    if (currentLeaseEpoch) headers["x-orchlink-lease-epoch"] = String(currentLeaseEpoch);
    if (agentId) headers["x-orchlink-lease-holder"] = agentId;
    if (sessionLeaseId) headers["x-orchlink-session-lease-id"] = sessionLeaseId;
    const body = {
      project_id: task.project_id || projectId,
      task_id: task.task_id || null,
      agent_id: agentId,
      worker_name: env("ORCHLINK_WORKER_NAME") || agentId || "work",
      batch_id: `finalize-${++transcriptBatchId}`,
      events: [{ kind: "system", text: reason }],
    };
    postJson(`/v1/tasks/${encodeURIComponent(String(task.task_id || ""))}/transcript`, body, headers).catch((error: any) => {
      console.error(`[orchlink] transcript finalize failed: ${error?.message || error}`);
    });
  }

  function stopAfterOneshotReply(task: OrchMessage) {
    if (!oneshot || role !== "work" || backgroundBackend !== "rpc-supervisor" || isChatRequest(task)) return false;
    stopped = true;
    resetTranscriptState();
    if (timer) clearTimeout(timer);
    if (readyTimer) clearTimeout(readyTimer);
    clearCancelCheck();
    clearRecoveryTimer();
    clearActivityHeartbeat();
    clearAbortContext();
    setTimeout(() => process.exit(0), 50);
    return true;
  }

  function rememberAbortContext(ctx: any) {
    if (typeof ctx?.abort === "function") {
      abortCurrentTurn = () => ctx.abort();
    }
  }

  function clearAbortContext() {
    abortCurrentTurn = undefined;
  }

  function resetTranscriptState() {
    transcriptBuffer = "";
    clearTranscriptFlush();
  }

  function abortIfPossible(ctx?: any) {
    try {
      if (typeof ctx?.abort === "function") ctx.abort();
      else abortCurrentTurn?.();
    } catch (error: any) {
      console.error(`[orchlink] abort failed: ${error?.message || error}`);
    }
  }

  async function postCurrentActivity(activityType: string, detail: string, extra: OrchMessage = {}) {
    if (activityUnsupported || role !== "work" || !currentTask) return;
    const payload = currentTask.payload || {};
    try {
      await postJson("/v1/activity", {
        project_id: currentTask.project_id || projectId,
        agent_id: agentId,
        task_id: currentTask.task_id || null,
        conversation_id: currentTask.conversation_id || null,
        message_id: currentTask.message_id || null,
        mode: isChatRequest(currentTask) ? "TALK" : payload.mode,
        activity_type: activityType,
        phase: extra.phase || activityType,
        tool_name: extra.tool_name || null,
        detail,
        status: "RUNNING",
        session_lease_id: sessionLeaseId || undefined,
      });
    } catch (error: any) {
      if (String(error?.message || error).startsWith("404")) activityUnsupported = true;
      console.error(`[orchlink] activity update failed: ${error?.message || error}`);
    }
  }

  function scheduleActivityHeartbeat(delayMs = activityHeartbeatMs) {
    clearActivityHeartbeat();
    if (stopped || role !== "work" || !currentTask) return;
    activityTimer = setTimeout(() => {
      void postCurrentActivity("heartbeat", "Worker still active.", { phase: "working" })
        .then(() => renewJobLease(String(currentTask?.task_id || ""), currentLeaseEpoch))
        .then((lease) => {
          if (lease && lease.status === 409) {
            leaseLost = true;
            workerCtx?.ui?.notify?.("Orchlink lease lost; stopping work.", "error");
            abortIfPossible(workerCtx);
            pi.sendUserMessage("[Orchlink] Job lease lost (reclaimed or stale heartbeat). Stop working now. Do not make more edits, do not call more tools, and briefly acknowledge the cancellation.", { deliverAs: "steer" });
            return;
          }
          if (currentTask && !leaseLost) scheduleActivityHeartbeat(activityHeartbeatMs);
        })
        .catch((error) => {
          console.error(`[orchlink] lease heartbeat failed: ${error?.message || error}`);
          if (currentTask && !leaseLost) scheduleActivityHeartbeat(activityHeartbeatMs);
        });
    }, delayMs);
  }

  function isRecoverableAssistantError(assistantMessage: any): boolean {
    if (assistantMessage?.stopReason !== "error") return false;
    const errorText = `${assistantMessage?.errorMessage || ""} ${JSON.stringify(assistantMessage?.diagnostics || [])}`;
    return RECOVERABLE_ERROR_REGEX.test(errorText);
  }

  async function sendReply(task: OrchMessage, assistantMessage: any, ctx: any) {
    flushTranscriptBuffer(true);
    let sent = false;
    try {
      const reply = replyEnvelope(task, assistantMessage);
      const leaseHeaders: Record<string, string> = currentLeaseEpoch ? {
        "x-orchlink-lease-epoch": String(currentLeaseEpoch),
        "x-orchlink-lease-holder": String(agentId || ""),
      } : {};
      if (sessionLeaseId) leaseHeaders["x-orchlink-session-lease-id"] = sessionLeaseId;
      await postJson(`/v1/messages/${encodeURIComponent(task.message_id)}/reply`, reply, leaseHeaders);
      sent = true;
      const label = task.task_id || task.conversation_id;
      ctx.ui.notify(`Orchlink reply sent for ${label}`, "info");
    } catch (error: any) {
      ctx.ui.notify(`Orchlink reply failed: ${error?.message || error}`, "error");
    } finally {
      if (!sent || !stopAfterOneshotReply(task)) schedule(0);
    }
  }

  function deferRecoverableFailure(task: OrchMessage, assistantMessage: any, ctx: any) {
    clearRecoveryTimer();
    flushTranscriptBuffer(true);
    const label = task.task_id || task.conversation_id || "current work";
    ctx.ui.notify(`Orchlink saw a transient provider error for ${label}; waiting for Pi recovery.`, "info");
    recoveryTimer = setTimeout(() => {
      if (!currentTask || currentTask.message_id !== task.message_id) return;
      currentTask = undefined;
      resetTranscriptState();
      clearCancelCheck();
      clearActivityHeartbeat();
      clearAbortContext();
      void sendReply(task, assistantMessage, ctx);
    }, recoveryGraceMs);
  }

  async function currentWorkStatus(task: OrchMessage): Promise<string> {
    const projectQuery = `project_id=${encodeURIComponent(String(task.project_id || projectId || "default"))}`;
    if (task.task_id) {
      const body = await getJson(`/v1/tasks/${encodeURIComponent(String(task.task_id))}?${projectQuery}`);
      return String(body.status || body.job?.status || "").toUpperCase();
    }
    const body = await getJson(`/v1/jobs?limit=500&${projectQuery}`);
    const conversation = (body.jobs || []).find((job: any) => job.conversation_id === task.conversation_id);
    return String(conversation?.status || "").toUpperCase();
  }

  async function checkCurrentTaskCancellation(ctx?: any): Promise<boolean> {
    if (stopped || !currentTask || cancelNoticeSent) return false;
    const status = await currentWorkStatus(currentTask);
    if (!["CANCELLED", "TIMEOUT"].includes(status)) return false;
    cancelNoticeSent = true;
    flushTranscriptBuffer(true);
    finalizeTranscript(`task ${status.toLowerCase()}`);
    const label = currentTask.task_id || currentTask.conversation_id || "current work";
    void postCurrentActivity("cancelled", `Broker marked ${label} ${status}; aborting current Pi turn.`, { phase: "cancelled" });
    abortIfPossible(ctx);
    pi.sendUserMessage(`[Orchlink] ${label} is ${status}. Stop working now. Do not make more edits, do not call more tools, and briefly acknowledge the cancellation.`, { deliverAs: "steer" });
    return true;
  }

  function scheduleCancelCheck(delayMs = 5000, ctx?: any) {
    clearCancelCheck();
    if (stopped || role !== "work" || !currentTask || cancelNoticeSent) return;
    cancelTimer = setTimeout(() => {
      void checkCurrentTaskCancellation(ctx)
        .catch((error) => console.error(`[orchlink] cancel check failed: ${error?.message || error}`))
        .finally(() => {
          if (currentTask && !cancelNoticeSent) scheduleCancelCheck(1000, ctx);
        });
    }, delayMs);
  }

  async function poll() {
    if (stopped || !["lead", "work"].includes(role)) return;
    if (role === "work" && (pendingTask || currentTask)) return;
    try {
      const params = new URLSearchParams({ wait_seconds: String(pollWaitSeconds) });
      if (sessionLeaseId) params.set("lease_id", sessionLeaseId);
      if (projectId) params.set("project_id", projectId);
      const body = await getJson(`/v1/agents/${encodeURIComponent(agentId)}/next?${params.toString()}`);
      if (body.status === "message") {
        const message = body.message;
        const label = message?.task_id || message?.conversation_id || "message";
        if (role === "work") {
          if (message?.type === "CHAT_CLOSE") {
            return;
          }
          pendingTask = message;
          try {
            await applyWorkerThinking(message);
            pi.sendUserMessage(renderWorkerPrompt(message), { deliverAs: "followUp" });
          } catch (error) {
            pendingTask = undefined;
            throw error;
          }
        } else {
          pi.sendUserMessage(renderLeadPrompt(message), { deliverAs: "steer" });
        }
      }
    } catch (error: any) {
      // Avoid spamming the transcript on transient broker errors.
      console.error(`[orchlink] poll failed: ${error?.message || error}`);
    } finally {
      if (role === "lead" || !currentTask) schedule(250);
    }
  }

  pi.on("session_start", async (_event, ctx) => {
    if (!role || !agentId) return;
    try {
      await register();
      await sendReadyHeartbeat(ctx);
      scheduleReadyHeartbeat(ctx);
      ctx.ui.notify(`Orchlink ${role} connected as ${agentId}`, "info");
      if (["lead", "work"].includes(role)) schedule(0);
    } catch (error: any) {
      ctx.ui.notify(`Orchlink connection failed: ${error?.message || error}`, "error");
    }
  });

  pi.on("input", async (event, ctx) => {
    if (role !== "work" || !pendingTask) return;
    if (event.source !== "extension") return;
    if (!isOrchlinkWorkerPrompt(event.text)) return;
    currentTask = pendingTask;
    pendingTask = undefined;
    resetTranscriptState();
    transcriptBatchId = 0;
    rememberAbortContext(ctx);
    workerCtx = ctx;
    cancelNoticeSent = false;
    leaseLost = false;
    currentLeaseEpoch = Number((currentTask as any)?.lease?.epoch) || 0;
    void markMessageStatus(String(currentTask.message_id || ""), "RUNNING").catch((error) => {
      console.error(`[orchlink] status update failed: ${error?.message || error}`);
    });
    void postCurrentActivity("started", "Worker accepted the task.", { phase: "started" });
    scheduleActivityHeartbeat(1000);
    scheduleCancelCheck(1000, ctx);
  });

  pi.on("tool_call", async (event, ctx) => {
    if (role !== "work" || !currentTask) return;
    rememberAbortContext(ctx);
    flushTranscriptBuffer(true);
    if (await checkCurrentTaskCancellation(ctx)) {
      return { block: true, reason: "Orchlink cancelled this work before the tool call started." };
    }
    const toolName = String((event as any).toolName || "tool");
    void postCurrentActivity("tool_call", summarizeToolInput(toolName, (event as any).input), {
      phase: "tool_call",
      tool_name: toolName,
    });
  });

  pi.on("tool_result", async (event, ctx) => {
    if (role !== "work" || !currentTask) return;
    rememberAbortContext(ctx);
    const toolName = String((event as any).toolName || "tool");
    const failed = Boolean((event as any).isError);
    void postCurrentActivity("tool_result", failed ? "Tool finished with error." : "Tool finished.", {
      phase: failed ? "tool_error" : "tool_result",
      tool_name: toolName,
    });
    void checkCurrentTaskCancellation(ctx).catch((error) => console.error(`[orchlink] cancel check failed: ${error?.message || error}`));
  });

  pi.on("message_update", async (event, _ctx) => {
    if (role !== "work" || !currentTask) return;
    const assistantEvent = event.assistantMessageEvent;
    if (!assistantEvent || assistantEvent.type !== "text_delta") return;
    const delta = typeof assistantEvent.delta === "string" ? assistantEvent.delta : "";
    appendTranscriptDelta(delta);
  });

  pi.on("message_end", async (event, ctx) => {
    if (role !== "work" || !currentTask) return;
    if (event.message.role !== "assistant") return;
    flushTranscriptBuffer(true);
    if ((event.message as any).stopReason === "toolUse") return;

    const task = currentTask;
    if (isRecoverableAssistantError(event.message)) {
      void postCurrentActivity("recovering", "Provider transport error; waiting for Pi recovery.", { phase: "recovering" });
      deferRecoverableFailure(task, event.message, ctx);
      return;
    }

    clearRecoveryTimer();
    currentTask = undefined;
    resetTranscriptState();
    clearCancelCheck();
    clearActivityHeartbeat();
    clearAbortContext();
    await sendReply(task, event.message, ctx);
  });

  pi.on("session_shutdown", async () => {
    stopped = true;
    resetTranscriptState();
    if (timer) clearTimeout(timer);
    if (readyTimer) clearTimeout(readyTimer);
    clearCancelCheck();
    clearRecoveryTimer();
    clearActivityHeartbeat();
    clearAbortContext();
  });
}
'''


def _render_extension() -> str:
    rendered = _EXTENSION_TEMPLATE.replace(
        "__ORCHLINK_WORKER_TASK_GUIDANCE__",
        json.dumps(_TASK_PROMPT_POLICY.worker_task_guidance()),
    )
    for placeholder, replacement in interpolation_replacements().items():
        rendered = rendered.replace(placeholder, replacement)
    return rendered


ORCHLINK_PI_EXTENSION = _render_extension()
