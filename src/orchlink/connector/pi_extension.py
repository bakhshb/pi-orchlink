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
- Put TYPE: CHAT_REPLY first, then answer conversationally in 2-5 short chat sentences.
- Answer the lead's latest question first.
- Challenge weak assumptions. Do not agree by default.
- Name one risk, disagreement, or assumption before accepting the lead's view, unless there is truly no meaningful objection.
- Recommend a practical decision, or ask one direct follow-up question if the decision is not ready.
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
- Return BLOCKER with specific questions if the request is unclear.
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

function renderLeadPrompt(message: OrchMessage): string {
  const payload = message.payload || {};
  const summary = payload.summary || payload.stdout || payload.message || "";
  const type = message.type || "RESULT";
  if (type === "CHAT_REPLY") {
    return `[Orchlink] Message from ${message.from_agent || "work"}

Conversation: ${message.conversation_id || ""}
Type: CHAT_REPLY
Turn: ${message.turn || "?"}/${message.max_turns || "?"}

Worker says:
${summary}

Talk Mode should stop only when it has produced one of these: clear decision, next task, blocker, max rounds, timeout, or no new value.

Be a critical thinker. Decide whether to accept, reject, or challenge the worker's point. Do not agree just to move on.

If the worker asked a direct question, answer it explicitly in your next orch say before moving to another point. Do not ignore worker questions.

If a stop condition has not been reached and Turn is less than Max turns, send a short follow-up, one question or one idea:
orch say ${message.conversation_id || "<conversation_id>"} -m "<answer the worker question, then one short follow-up>"

If a stop condition has been reached, close it explicitly with a compact record:
orch close ${message.conversation_id || "<conversation_id>"} -m "Decision: ... Rationale: ... Dissent/risk accepted: ... Next step: ... Owner: ... Human approval needed: yes/no"

Only summarize to the user after you close the conversation or have a clear reason not to continue.`;
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
      summary: output,
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
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json();
}

async function getJson(path: string): Promise<any> {
  const baseUrl = env("ORCHLINK_BROKER_URL", "http://127.0.0.1:8787").replace(/\/$/, "");
  const response = await fetch(`${baseUrl}${path}`, {
    headers: { "x-api-key": env("ORCHLINK_API_KEY", "change-me") },
  });
  if (!response.ok) throw new Error(`${response.status} ${await response.text()}`);
  return response.json();
}

export default function (pi: ExtensionAPI) {
  const role = env("ORCHLINK_PI_ROLE");
  const agentId = env("ORCHLINK_AGENT_ID");
  const projectId = env("ORCHLINK_PROJECT_ID", "default");
  const pollWaitSeconds = Number(env("ORCHLINK_POLL_WAIT_SECONDS", "5"));
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let currentTask: OrchMessage | undefined;

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

  async function poll() {
    if (stopped || !["lead", "work"].includes(role)) return;
    if (role === "work" && currentTask) return;
    try {
      const body = await getJson(`/v1/agents/${encodeURIComponent(agentId)}/next?wait_seconds=${pollWaitSeconds}`);
      if (body.status === "message") {
        const message = body.message;
        const label = message?.task_id || message?.conversation_id || "message";
        if (role === "work") {
          pi.sendMessage({
            customType: "orchlink",
            content: `Orchlink received ${message?.type || "MESSAGE"} ${label} from ${message?.from_agent || "lead"}`,
            display: true,
            details: message,
          }, { deliverAs: "steer" });
          if (message?.type === "CHAT_CLOSE") {
            return;
          }
          currentTask = message;
          pi.sendUserMessage(renderWorkerPrompt(message), { deliverAs: "followUp" });
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

  pi.on("message_end", async (event, ctx) => {
    if (role !== "work" || !currentTask) return;
    if (event.message.role !== "assistant") return;
    if ((event.message as any).stopReason === "toolUse") return;

    const task = currentTask;
    currentTask = undefined;
    try {
      const reply = replyEnvelope(task, event.message);
      await postJson(`/v1/messages/${encodeURIComponent(task.message_id)}/reply`, reply);
      const label = task.task_id || task.conversation_id;
      ctx.ui.notify(`Orchlink reply sent for ${label}`, "info");
      pi.sendMessage({
        customType: "orchlink",
        content: `Orchlink sent ${reply.type} for ${label} to ${task.from_agent}`,
        display: true,
        details: reply,
      }, { deliverAs: "steer" });
    } catch (error: any) {
      ctx.ui.notify(`Orchlink reply failed: ${error?.message || error}`, "error");
    } finally {
      schedule(0);
    }
  });

  pi.on("session_shutdown", async () => {
    stopped = true;
    if (timer) clearTimeout(timer);
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
