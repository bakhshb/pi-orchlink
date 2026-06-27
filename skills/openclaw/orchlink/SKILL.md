---
name: orchlink
description: Use this skill whenever OpenClaw should act as the lead agent for a local Pi worker through Orchlink. It covers safe setup checks, choosing ask/send/talk/goal mode, review gates, exact wait/get results, cancellation, stale-broker recovery, and when to load the bundled references.
version: 1.1.0
platforms: [linux, macos]
metadata:
  openclaw:
    tags: [coding, local-coordination, cli]
    category: coding
    requires_tools: [shell]
---

# Orchlink Lead for OpenClaw

OpenClaw is the lead agent. Pi `work` is the visible worker agent. Use Orchlink when a second local coding agent should inspect, review, test, implement, or challenge a scoped slice of work.

Treat Orchlink as one local lead/work loop, not as a workflow engine or agent platform. Use terminal commands when available. If OpenClaw has no terminal access, tell the human the exact `orch ...` command to run and what output to return.

## Reference files

Load bundled references only when the task needs that detail:

- `references/core.md` — read before non-trivial Orchlink coordination: startup checks, command choice, ask/send/wait/get, jobs/idle, Talk Mode, prompt shape, and worker replies.
- `references/goal-mode.md` — read before using `orch goal ...` or advising on PRD/plan-driven work.
- `references/review-gates.md` — read before review gates, expensive test/release steps, or phase compaction.
- `references/recovery.md` — read when sessions, broker state, cancellation, stale results, or debug output are involved.

## Startup checklist

From the target project directory:

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

If the project is not initialized, ask the human to run `orch init`. If no worker session is active, ask the human to start one in a visible terminal with `orch work --new`.

## Quick command chooser

1. Need a review, decision, critique, plan, or blocker answer before continuing? Use `orch ask work --wait`.
2. Need worker implementation while you can work on a separate scope? Use `orch send`, then `orch wait` later.
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
