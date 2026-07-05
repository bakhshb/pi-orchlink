import json
from pathlib import Path
from typing import Any

from orchlink.connector.pi_extension_pure import interpolation_replacements
from orchlink.core.prompt_policy import TaskPromptPolicy
from orchlink.project.config import run_dir


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
  const workerModel = env("ORCHLINK_WORKER_MODEL").trim();
  const workerThinking = normalizeThinking(env("ORCHLINK_WORKER_THINKING"));
  const supervisorPid = Number(env("ORCHLINK_SUPERVISOR_PID", "0")) || undefined;
  const readyHeartbeatMs = Math.max(1000, Number(env("ORCHLINK_READY_HEARTBEAT_MS", "5000")) || 5000);
  // M3 job lease: the worker captures the lease epoch at pickup and renews it
  // via /v1/jobs/{id}/heartbeat. On a 409 (stale/reclaimed) it stops and steers.
  let currentLeaseEpoch: number = 0;
  let leaseLost = false;
  let workerCtx: any;
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

  function rememberAbortContext(ctx: any) {
    if (typeof ctx?.abort === "function") {
      abortCurrentTurn = () => ctx.abort();
    }
  }

  function clearAbortContext() {
    abortCurrentTurn = undefined;
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
    try {
      const reply = replyEnvelope(task, assistantMessage);
      const leaseHeaders: Record<string, string> = currentLeaseEpoch ? {
        "x-orchlink-lease-epoch": String(currentLeaseEpoch),
        "x-orchlink-lease-holder": String(agentId || ""),
      } : {};
      if (sessionLeaseId) leaseHeaders["x-orchlink-session-lease-id"] = sessionLeaseId;
      await postJson(`/v1/messages/${encodeURIComponent(task.message_id)}/reply`, reply, leaseHeaders);
      const label = task.task_id || task.conversation_id;
      ctx.ui.notify(`Orchlink reply sent for ${label}`, "info");
    } catch (error: any) {
      ctx.ui.notify(`Orchlink reply failed: ${error?.message || error}`, "error");
    } finally {
      schedule(0);
    }
  }

  function deferRecoverableFailure(task: OrchMessage, assistantMessage: any, ctx: any) {
    clearRecoveryTimer();
    const label = task.task_id || task.conversation_id || "current work";
    ctx.ui.notify(`Orchlink saw a transient provider error for ${label}; waiting for Pi recovery.`, "info");
    recoveryTimer = setTimeout(() => {
      if (!currentTask || currentTask.message_id !== task.message_id) return;
      currentTask = undefined;
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

  pi.on("message_end", async (event, ctx) => {
    if (role !== "work" || !currentTask) return;
    if (event.message.role !== "assistant") return;
    if ((event.message as any).stopReason === "toolUse") return;

    const task = currentTask;
    if (isRecoverableAssistantError(event.message)) {
      void postCurrentActivity("recovering", "Provider transport error; waiting for Pi recovery.", { phase: "recovering" });
      deferRecoverableFailure(task, event.message, ctx);
      return;
    }

    clearRecoveryTimer();
    currentTask = undefined;
    clearCancelCheck();
    clearActivityHeartbeat();
    clearAbortContext();
    await sendReply(task, event.message, ctx);
  });

  pi.on("session_shutdown", async () => {
    stopped = true;
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


ORCHLINK_PI_UI_EXTENSION = r'''
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";

type JsonObject = Record<string, any>;
type PanelResult = { action: "stop"; workerName: string } | null;

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
    else if (data === "s" && rows[this.selected]) this.done({ action: "stop", workerName: rows[this.selected].workerName });
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
      if (result?.action === "stop") {
        const confirmed = await ctx.ui.confirm(`Stop worker ${result.workerName}?`, "This releases the worker session and stops its background supervisor when tracked.");
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


def _write_extension_file(directory: Path, filename: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    if not path.exists() or path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")
    return path


def ensure_pi_extension(config: dict[str, Any]) -> Path:
    return _write_extension_file(run_dir(config), "orchlink-pi-extension.ts", ORCHLINK_PI_EXTENSION)


def ensure_orchlink_ui_extension(config: dict[str, Any]) -> Path:
    return _write_extension_file(run_dir(config), "orchlink-pi-ui-extension.ts", ORCHLINK_PI_UI_EXTENSION)
