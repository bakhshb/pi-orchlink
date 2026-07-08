---
name: orchlink
description: "Use when this agent is the Orchlink lead for local named Pi workers (`work`, `review`, `bg-test`): coordinate ask/send/talk/goal tasks, review gates, jobs results, cancellation, and stale-state recovery. Must use whenever the user mentions Orchlink, orch commands, Pi workers, background/oneshot workers, review gates, stale sessions, or broker/session recovery."
version: 1.1.4
platforms: [linux, macos, windows]
metadata:
  tags: [coding, local-coordination, cli]
  category: coding
  requires_tools: [shell]
---

# Orchlink Lead

You are the lead coding agent. Named Pi workers such as `work`, `review`, or `bg-test` are worker agents, either visible terminals or headless background workers. Use Orchlink when a second local coding agent should inspect, review, test, implement, or challenge a scoped slice of work.

Treat Orchlink as one local lead/work loop, not as a workflow engine or agent platform. Use terminal commands when available. If you have no terminal access, tell the human the exact `orch ...` command to run and what output to return.

Do not substitute native subagents or other agent-platform delegation for named Pi workers. Orchlink's own `orch work --background` and `orch work --background --name ...` are the approved background workers; platform-native background sessions are not. If the human asks for Orchlink, work must flow through `orch work` plus `orch ask`/`orch send`; otherwise stop and ask whether a non-Orchlink substitute is acceptable.

As an external agent, do not run plain `orch work` yourself; it opens an interactive Pi chat and blocks until the session ends. Use `orch work --background` for a persistent default worker, `orch work --background --new --replace --oneshot` for a fresh task-scoped worker, or `orch work --background --name bg-test --new`/`--test` for isolated background testing while a visible worker is already open.

## Reference files

Load bundled references only when the task needs that detail:

- `references/core.md` — read before non-trivial Orchlink coordination: startup checks, command choice, ask/send/jobs, Talk Mode, prompt shape, and worker replies.
- `references/goal-mode.md` — read before using `orch goal ...` or advising on PRD/plan-driven work.
- `references/review-gates.md` — read before review gates or expensive test/release steps.
- `references/recovery.md` — read when sessions, broker state, cancellation, stale results, or debug output are involved.

## Startup checklist

From the target project directory, use Orchlink commands as the source of truth. Do not inspect `ps`, PID lists, raw broker URLs, or ad hoc HTTP checks for normal coordination.

```bash
command -v orch
orch doctor
orch sessions
orch jobs --idle
```

If `command -v orch` fails, stop and tell the human to install or update Orchlink. For local development, suggest:

```bash
cd /home/debian/projects/orchlink
./install.sh
```

If the project is not initialized, ask the human to run `orch init`. If no worker session is active, this is a mandatory branch before any Orchlink task:

1. Start the worker in the background, recommended for external agents: run `orch work --background`. For a fresh task-scoped background worker that exits after one reply, use `orch work --background --new --replace --oneshot`; `--new --replace` avoids stale context or an active same-name session, and `--oneshot` exits after one completed task reply. It returns only after the headless RPC worker becomes ready or fails with `.orch/run/orch-work.log` diagnostics.
2. If a visible `work` terminal is already active and you only need to test background mode, do not replace `work`; use `orch work --background --name bg-test --new --replace --oneshot` if `bg-test` may already exist, or `orch work --background --test` as the shortcut, and target `bg-test` explicitly.
3. If the background worker fails or the human wants a visible worker terminal instead, ask them to run `orch work --new` in a separate terminal. Visible terminals are more reliable for long sessions.

If neither option is available, stop and tell the human Orchlink cannot proceed yet. Do not silently use native subagents as a substitute.

## Quick command chooser

1. Need a short review, decision, critique, plan, or blocker answer before continuing safely? Use `orch ask work --wait` or target a specific active worker name such as `review`.
2. Need long/heavy implementation, broad review, tests, or research? Prefer async `orch send <name>`. Record the task ID, continue only on non-conflicting lead-owned work, and keep ownership until you read the exact result with `orch jobs --result <task_id>` or report it pending. Use `orch jobs --wait <task_id>` only when the result now blocks your next safe action. Do not use `orch ask --wait` just to make heavy work synchronous; that blocks the lead and encourages rushed conclusions.
3. Need PRD/plan-driven completion with acceptance criteria? Read `references/goal-mode.md`, then use `orch goal ...`.
4. Need short peer discussion in a visible lead/work chat? Use Talk Mode.
5. Need to know whether it is safe to continue? Use `orch jobs --idle`.
6. Need active work details? Use `orch jobs --active`.
7. Need final output? Prefer `orch jobs --result T002` once terminal; use `orch jobs --wait T002` only if you must block now. Do not rely on the plain jobs list as the result.

## Safety rules

- Keep lead-owned work and worker-owned work separate.
- Do not expose API keys, tokens, secrets, or private logs in prompts.
- Do not ask the worker to edit outside the allowed scope.
- Do not stop visible worker terminals from the lead. Stop only tracked background workers; a visible worker should be stopped by the human in its own terminal with Ctrl-C.
- Do not run dependent full tests while worker work is active.
- Do not use blocking waits to rush long worker tasks; dispatch async and resolve the task ID at a natural checkpoint.
- Async closeout: `orch send` is not fire-and-forget. Before any human-facing completion or decision, read the exact result with `orch jobs --result <task_id>`/`--wait <task_id>`, or state that the task ID is still pending, whether it blocks, and how to retrieve it. Do not claim dependent work is done while it is pending.
- Do not make final claims until blocking reviews and active work are resolved.
- Do not accept worker output blindly. Name the risk, disagreement, or assumption before deciding.
- Trust only exact task IDs in the current project.
- If command output is stale, cross-project, or inconsistent, read `references/recovery.md` and repair before guessing.
