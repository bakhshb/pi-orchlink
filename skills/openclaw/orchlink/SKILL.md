---
name: orchlink
description: "Use when OpenClaw is the Orchlink lead for local named Pi workers (`work`, `review`, `bg-test`): coordinate ask/send/talk/goal tasks, review gates, wait/get results, cancellation, and stale-state recovery."
version: 1.1.1
platforms: [linux, macos, windows]
metadata:
  openclaw:
    tags: [coding, local-coordination, cli]
    category: coding
    requires_tools: [shell]
---

# Orchlink Lead for OpenClaw

OpenClaw is the lead agent. Named Pi workers such as `work`, `review`, or `bg-test` are worker agents, either visible terminals or headless background workers. Use Orchlink when a second local coding agent should inspect, review, test, implement, or challenge a scoped slice of work.

Treat Orchlink as one local lead/work loop, not as a workflow engine or agent platform. Use terminal commands when available. If OpenClaw has no terminal access, tell the human the exact `orch ...` command to run and what output to return.

Do not substitute OpenClaw subagents, `sessions_spawn`, or other OpenClaw delegation for named Pi workers. Orchlink's own `orch work --background` and `orch work --background --name ...` are the approved background workers; OpenClaw-native background sessions are not. If the human asks for Orchlink, work must flow through `orch work` plus `orch ask`/`orch send`; otherwise stop and ask whether a non-Orchlink substitute is acceptable.

As an external agent, do not run plain `orch work` yourself; it opens an interactive Pi chat and blocks until the session ends. Use `orch work --background` for the default worker, or `orch work --background --name bg-test --new`/`--test` for isolated background testing while a visible worker is already open.

## Reference files

Load bundled references only when the task needs that detail:

- `references/core.md` — read before non-trivial Orchlink coordination: startup checks, command choice, ask/send/wait/get, jobs/idle, Talk Mode, prompt shape, and worker replies.
- `references/goal-mode.md` — read before using `orch goal ...` or advising on PRD/plan-driven work.
- `references/review-gates.md` — read before review gates, expensive test/release steps, or phase compaction.
- `references/recovery.md` — read when sessions, broker state, cancellation, stale results, or debug output are involved.

## Startup checklist

From the target project directory, use Orchlink commands as the source of truth. Do not inspect `ps`, PID lists, raw broker URLs, or ad hoc HTTP checks for normal coordination.

```bash
command -v orch
orch doctor
orch sessions
orch idle
```

If `command -v orch` fails, stop and tell the human to install or update Orchlink. For local development, suggest:

```bash
cd /home/debian/projects/orchlink
./install.sh
```

If the project is not initialized, ask the human to run `orch init`. If no worker session is active, this is a mandatory branch before any Orchlink task:

1. Start the worker in the background, recommended for external agents: run `orch work --background`. It returns only after the headless RPC worker becomes ready or fails with `.orch/run/orch-work.log` diagnostics.
2. If a visible `work` terminal is already active and you only need to test background mode, use `orch work --background --name bg-test --new` or `orch work --background --test` and target `bg-test` explicitly.
3. If the background worker fails or the human wants a visible worker terminal instead, ask them to run `orch work --new` in a separate terminal. Visible terminals are more reliable for long sessions.

If neither option is available, stop and tell the human Orchlink cannot proceed yet. Do not silently use OpenClaw subagents as a substitute.

## Quick command chooser

1. Need a review, decision, critique, plan, or blocker answer before continuing? Use `orch ask work --wait` or target a specific active worker name such as `review`.
2. Need worker implementation while you can work on a separate scope? Use `orch send <name>`, then `orch wait` later.
3. Need PRD/plan-driven completion with acceptance criteria? Read `references/goal-mode.md`, then use `orch goal ...`.
4. Need short peer discussion in a visible lead/work chat? Use Talk Mode.
5. Need to know whether it is safe to continue? Use `orch idle`.
6. Need active work details? Use `orch jobs --active`.
7. Need final output? Use `orch wait T002` or `orch get T002`, not `orch jobs`.

## Safety rules

- Keep OpenClaw-owned work and worker-owned work separate.
- Do not expose API keys, tokens, secrets, or private logs in prompts.
- Do not ask the worker to edit outside the allowed scope.
- Do not run dependent full tests while worker work is active.
- Do not make final claims until blocking reviews and active work are resolved.
- Do not accept worker output blindly. Name the risk, disagreement, or assumption before deciding.
- Trust only exact task IDs in the current project.
- If command output is stale, cross-project, or inconsistent, read `references/recovery.md` and repair before guessing.
