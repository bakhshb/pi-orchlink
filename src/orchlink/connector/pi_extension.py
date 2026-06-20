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

function renderWorkerPrompt(message: OrchMessage): string {
  const payload = message.payload || {};
  const scope = payload.scope || {};
  return `You are the worker coding agent.

You received a task from the lead through Orchlink.

TASK ID:
${message.task_id || ""}

FROM:
${message.from_agent || "lead"}

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

Rules:
- Work only on this task.
- Do not expand scope.
- Do not edit forbidden files.
- If implementation is not explicitly allowed, inspect only and return PLAN.
- If the task is unclear, return BLOCKER.
- If implementation is allowed, run relevant tests.
- Do not commit unless explicitly allowed.

Required response format:

TYPE: PLAN | RESULT | BLOCKER
TASK_ID:
SUMMARY:
FILES_INSPECTED:
FILES_CHANGED:
TESTS_RUN:
FINDINGS:
RISKS:
RECOMMENDED_NEXT_STEP:
`;
}

function renderLeadPrompt(message: OrchMessage): string {
  const payload = message.payload || {};
  const summary = payload.summary || payload.stdout || "";
  return `You received a worker reply through Orchlink.

TASK ID:
${message.task_id || ""}

FROM:
${message.from_agent || "work"}

TYPE:
${message.type || "RESULT"}

SUMMARY:
${summary}

Review the worker reply and decide the next step.`;
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
  return {
    protocol: task.protocol || "orch-a2a-v1",
    message_id: `reply-${crypto.randomUUID()}`,
    correlation_id: task.correlation_id,
    project_id: task.project_id || env("ORCHLINK_PROJECT_ID", "default"),
    conversation_id: task.conversation_id || `${env("ORCHLINK_PROJECT_ID", "default")}-default`,
    task_id: task.task_id,
    from_agent: env("ORCHLINK_AGENT_ID", task.to_agent || "work"),
    to_agent: task.from_agent,
    type: failed ? "BLOCKER" : detectReplyType(output),
    status: failed ? "FAILED" : "COMPLETED",
    turn: Math.min(Number(task.turn || 1) + 1, Number(task.max_turns || 6)),
    max_turns: task.max_turns || 6,
    requires_reply: false,
    timeout_seconds: 1,
    payload: {
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
      capabilities: role === "work" ? ["inspection", "implementation", "tests"] : ["delegation", "review"],
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
        const taskId = message?.task_id || "task";
        if (role === "work") {
          currentTask = message;
          pi.sendMessage({
            customType: "orchlink",
            content: `Orchlink received TASK ${taskId} from ${message?.from_agent || "lead"}`,
            display: true,
            details: message,
          }, { deliverAs: "nextTurn" });
          pi.sendUserMessage(renderWorkerPrompt(message), { deliverAs: "followUp" });
        } else {
          pi.sendMessage({
            customType: "orchlink",
            content: `Orchlink received ${message?.type || "RESULT"} ${taskId} from ${message?.from_agent || "work"}`,
            display: true,
            details: message,
          }, { deliverAs: "nextTurn" });
          pi.sendUserMessage(renderLeadPrompt(message), { deliverAs: "followUp" });
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
      ctx.ui.notify(`Orchlink reply sent for ${task.task_id}`, "info");
      pi.sendMessage({
        customType: "orchlink",
        content: `Orchlink sent ${reply.type} for ${task.task_id} to ${task.from_agent}`,
        display: true,
        details: reply,
      }, { deliverAs: "nextTurn" });
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
