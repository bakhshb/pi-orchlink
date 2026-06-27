import json
from pathlib import Path
from typing import Any

from orchlink.core.prompt_policy import TaskPromptPolicy
from orchlink.project.config import run_dir


_TASK_PROMPT_POLICY = TaskPromptPolicy()


ORCHLINK_PI_EXTENSION = r'''
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

type OrchMessage = Record<string, any>;

const ORCHLINK_WORKER_TASK_GUIDANCE = __ORCHLINK_WORKER_TASK_GUIDANCE__;

function env(name: string, fallback = ""): string {
  return process.env[name] || fallback;
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

async function postJson(path: string, body: any): Promise<any> {
  const baseUrl = env("ORCHLINK_BROKER_URL", "http://127.0.0.1:8787").replace(/\/$/, "");
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": env("ORCHLINK_API_KEY", "change-me"),
      "x-orchlink-project-id": env("ORCHLINK_PROJECT_ID", "default"),
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
  await postJson(`/v1/messages/${encodeURIComponent(messageId)}/status`, { status });
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

function phaseCompactionInstructions(note: string): string {
  const phaseNote = note.trim() || "Phase reviewed and closed.";
  return `This is an Orchlink phase boundary. Compact old context while preserving the state needed for the next phase.

Preserve:
- completed phase summary
- review verdict
- files changed
- tests run
- current task ID
- current goal ID, if any
- scope guardrails and forbidden paths
- unresolved blockers
- next exact step
- pointers to durable .orch/ state files
- cumulative readFiles and modifiedFiles

Phase note:
${phaseNote}`;
}

function orchlinkCompactionSummary(instructions: string, role: string, projectId: string, currentTask: OrchMessage | undefined): string {
  const taskId = currentTask?.task_id || "none";
  const conversationId = currentTask?.conversation_id || "none";
  return `## Orchlink state

## Goal
Continue the current Orchlink project with compact context and reload details from durable state instead of old chat.

## Critical Context
- Project ID: ${projectId}
- Role: ${role || "unknown"}
- Current task ID: ${taskId}
- Current conversation ID: ${conversationId}
- Durable state roots: .orch/goals/, .orch/run/, .orch/project.yaml
- Preserve task/goal scope guardrails and forbidden paths from the latest Orchlink task prompt.

## Phase compaction instructions
${instructions}

## Next Steps
1. Reload relevant .orch goal artifacts before making claims about goal status.
2. Continue only from verified task results, checks, and goal state files.
3. If scope or blocker details are unclear, ask one specific unblock question.`;
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
  let phaseCompactionRequested = false;
  let phaseCompactionCustomInstructions = "";
  const recoveryGraceMs = Math.max(1000, Number(env("ORCHLINK_RECOVERABLE_ERROR_GRACE_MS", "180000")) || 180000);
  const activityHeartbeatMs = Math.max(5000, Number(env("ORCHLINK_ACTIVITY_HEARTBEAT_MS", "15000")) || 15000);

  pi.registerCommand("orch", {
    description: "Orchlink helpers. Use: /orch compact-phase <phase summary>",
    handler: async (args, ctx) => {
      const text = String(args || "").trim();
      const match = text.match(/^compact-phase\b\s*(.*)$/s);
      if (!match) {
        ctx.ui.notify("Usage: /orch compact-phase <reviewed phase summary>", "info");
        return;
      }
      const note = match[1] || "";
      phaseCompactionRequested = true;
      phaseCompactionCustomInstructions = phaseCompactionInstructions(note);
      ctx.compact({
        customInstructions: phaseCompactionCustomInstructions,
        onComplete: () => {
          phaseCompactionRequested = false;
          phaseCompactionCustomInstructions = "";
          ctx.ui.notify("Orchlink phase compaction completed.", "info");
        },
        onError: (error: any) => {
          phaseCompactionRequested = false;
          phaseCompactionCustomInstructions = "";
          ctx.ui.notify(`Orchlink phase compaction failed: ${error?.message || error}`, "error");
        },
      });
      ctx.ui.notify("Orchlink phase compaction started.", "info");
    },
  });

  pi.on("session_before_compact", async (event: any) => {
    const instructions = String(event?.customInstructions || phaseCompactionCustomInstructions || "");
    if (!phaseCompactionRequested) return;
    phaseCompactionRequested = false;
    phaseCompactionCustomInstructions = "";
    const preparation = event?.preparation || {};
    if (!preparation.firstKeptEntryId) return;
    return {
      compaction: {
        summary: orchlinkCompactionSummary(instructions, role, projectId, currentTask),
        firstKeptEntryId: preparation.firstKeptEntryId,
        tokensBefore: preparation.tokensBefore || 0,
        details: {
          orchlink: true,
          projectId,
          role,
          taskId: currentTask?.task_id || null,
          conversationId: currentTask?.conversation_id || null,
          readFiles: preparation.fileOps?.readFiles || [],
          modifiedFiles: preparation.fileOps?.modifiedFiles || [],
        },
      },
    };
  });

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

  function schedule(delayMs = 0) {
    if (stopped || !["lead", "work"].includes(role)) return;
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => void poll(), delayMs);
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
        .finally(() => {
          if (currentTask) scheduleActivityHeartbeat(activityHeartbeatMs);
        });
    }, delayMs);
  }

  function isRecoverableAssistantError(assistantMessage: any): boolean {
    if (assistantMessage?.stopReason !== "error") return false;
    const errorText = `${assistantMessage?.errorMessage || ""} ${JSON.stringify(assistantMessage?.diagnostics || [])}`;
    return /WebSocket error|provider_transport_failure|transport|Request timed out|timed out|timeout/i.test(errorText);
  }

  async function sendReply(task: OrchMessage, assistantMessage: any, ctx: any) {
    try {
      const reply = replyEnvelope(task, assistantMessage);
      await postJson(`/v1/messages/${encodeURIComponent(task.message_id)}/reply`, reply);
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
      const body = await getJson(`/v1/agents/${encodeURIComponent(agentId)}/next?wait_seconds=${pollWaitSeconds}`);
      if (body.status === "message") {
        const message = body.message;
        const label = message?.task_id || message?.conversation_id || "message";
        if (role === "work") {
          if (message?.type === "CHAT_CLOSE") {
            return;
          }
          pendingTask = message;
          try {
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
    cancelNoticeSent = false;
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
    clearCancelCheck();
    clearRecoveryTimer();
    clearActivityHeartbeat();
    clearAbortContext();
  });
}
'''.replace(
    "__ORCHLINK_WORKER_TASK_GUIDANCE__",
    json.dumps(_TASK_PROMPT_POLICY.worker_task_guidance()),
)


def ensure_pi_extension(config: dict[str, Any]) -> Path:
    directory = run_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "orchlink-pi-extension.ts"
    if not path.exists() or path.read_text(encoding="utf-8") != ORCHLINK_PI_EXTENSION:
        path.write_text(ORCHLINK_PI_EXTENSION, encoding="utf-8")
    return path
