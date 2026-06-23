from pathlib import Path
from typing import Any

from orchlink.project.config import run_dir


ORCHLINK_PI_EXTENSION = r'''
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

type OrchMessage = Record<string, any>;

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
  return `You are the worker coding agent in a Talk Mode conversation with the lead.

This is a peer discussion, not a task assignment. If the lead's text contains TASK_ID, scope, or checklist language, treat it only as discussion context.

Conversation ID:
${message.conversation_id || ""}

Turn:
${message.turn || 1}/${message.max_turns || 6}

Discussion topic:
${payload.topic || ""}

Lead says:
${payload.message || payload.intent || ""}

Transcript preview:
${payload.transcript_preview || ""}

Guidance:
- Put TYPE: CHAT_REPLY first, then answer conversationally in 2-4 short lines. No big paragraph.
- Answer the lead's latest question first.
- Challenge weak assumptions. Do not agree by default.
- Name one risk, disagreement, or assumption before accepting the lead's view, unless there is truly no meaningful objection.
- Recommend a practical decision, or ask one direct follow-up question if the decision is not ready.
- If the topic is broad, large, or unclear, ask one direct clarifying question instead of guessing.
- Do not edit files, run implementation, expand scope, use headings, or write a long audit.
- For broad repo opinions, do not read every file; use current context and a few high-signal files if useful. Ask before a broad scan.
- If you hit a stop condition, say it plainly: clear decision, next task, blocker, max rounds, timeout, or no new value.

Required first line:

TYPE: CHAT_REPLY
`;
}

function renderWorkerTaskPrompt(message: OrchMessage): string {
  const payload = message.payload || {};
  const scope = payload.scope || {};
  return `You are the worker coding agent in an Orchlink pair.

MODE:
${payload.mode || "PLAN"}

TASK_ID:
${message.task_id || ""}

INTENT:
${payload.intent || payload.summary || ""}

ALLOWED SCOPE:
${formatList(asList(scope.allowed))}

FORBIDDEN SCOPE:
${formatList(asList(scope.forbidden))}

CONSTRAINTS:
${formatList(asList(payload.constraints))}

EXPECTED REPLY:
${formatList(asList(payload.expected_reply))}

DELIVERY:
${message.delivery || "async"}

Rules:
- Work only on this task. Never edit forbidden files or expand scope.
- If MODE is PLAN, inspect and propose only. No edits.
- If MODE is REVIEW, inspect and report only. No edits unless the lead explicitly allows them.
- If MODE is DO, implement only inside the allowed scope.
- Return BLOCKER with specific questions if the request is unclear, too broad, or too large to scope safely.
- If implementation is allowed, run relevant tests.
- Do not commit unless explicitly allowed.

Required response format:

TYPE: PLAN | RESULT | BLOCKER
MODE:
TASK_ID:
SUMMARY:
FILES_INSPECTED:
FILES_CHANGED:
TESTS_RUN:
FINDINGS:
RISKS:
OPEN_QUESTIONS:
RECOMMENDED_NEXT_STEP:
`;
}

function renderWorkerPrompt(message: OrchMessage): string {
  if (isChatRequest(message)) return renderWorkerTalkPrompt(message);
  return renderWorkerTaskPrompt(message);
}

function stripChatReplyMarker(value: any): string {
  return String(value || "").replace(/^\s*TYPE:\s*CHAT_REPLY\s*\r?\n?/i, "").trim();
}

function renderLeadPrompt(message: OrchMessage): string {
  const payload = message.payload || {};
  const rawSummary = payload.summary || payload.stdout || payload.message || "";
  const type = message.type || "RESULT";
  const summary = type === "CHAT_REPLY" ? stripChatReplyMarker(rawSummary) : rawSummary;
  if (type === "CHAT_REPLY") {
    return `[Orchlink] Message from ${message.from_agent || "work"}

Conversation: ${message.conversation_id || ""}
Turn: ${message.turn || "?"}/${message.max_turns || "?"}

Worker says:
${summary}

Next: if worker asked a direct question, answer it first with orch say ${message.conversation_id || "<conversation_id>"} -m "<your answer>". Otherwise continue only if useful, or close with orch close ${message.conversation_id || "<conversation_id>"} -m "Decision: ..." after a clear decision.`;
  }

  return `[Orchlink] Result from ${message.from_agent || "work"}

Task: ${message.task_id || ""}
Mode: ${payload.mode || type}
Status: ${message.status || "DONE"}

Worker result:
${summary}

Recommended next step:
Stop any unrelated work now and reconcile this with your current state before calling more tools. If it changes the plan, state what changed. If it leaves open questions, ask a follow-up. If it confirms the plan, continue with the agreed workload split.`;
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
  for (const line of output.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed.startsWith("TYPE:")) continue;
    const value = trimmed.slice("TYPE:".length).trim().split(/\s+/, 1)[0];
    if (["PLAN", "RESULT", "BLOCKER"].includes(value)) return value;
  }
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
    return /WebSocket error|provider_transport_failure|transport/i.test(errorText);
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
    pi.sendUserMessage(`[Orchlink] ${label} is ${status}. Stop this work now, do not make more edits, and briefly acknowledge the cancellation.`, { deliverAs: "steer" });
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
          pi.sendMessage({
            customType: "orchlink",
            content: `Orchlink received ${message?.type || "RESULT"} ${label} from ${message?.from_agent || "work"}`,
            display: true,
            details: message,
          }, { deliverAs: "steer" });
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
    if (!String(event.text || "").startsWith("You are the worker coding agent in")) return;
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
'''


def ensure_pi_extension(config: dict[str, Any]) -> Path:
    directory = run_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "orchlink-pi-extension.ts"
    if not path.exists() or path.read_text(encoding="utf-8") != ORCHLINK_PI_EXTENSION:
        path.write_text(ORCHLINK_PI_EXTENSION, encoding="utf-8")
    return path
