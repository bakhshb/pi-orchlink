# Orchlink manual smoke test

Use this when validating a real visible lead/work Pi pair. It covers the main collaboration paths that unit tests cannot prove: conversational follow-up, BLOCKER handling, worker edits, disagreement, and skill visibility.

Run these from a small throwaway project after updating Orchlink and starting fresh sessions:

```bash
orch update
orch init --refresh-skills
orch stop
orch lead --new
orch work --new
```

Before starting, verify the broker is current:

```bash
orch doctor
curl -s http://127.0.0.1:8787/health
```

Expected health includes `version: "0.4.0"` or newer and these capabilities:

```text
project_header_scope
task_activity_endpoint
scoped_task_results
status_filters
```

## 1. Talk follow-up with `orch say`

Goal: prove Talk Mode can run a real second turn, not only open/close.

```bash
orch talk work -m "Ask me one direct clarifying question before recommending how parse_flags should handle unknown flags." -r 2
```

Wait for the worker reply in the lead Pi chat. It should ask one direct question.

Then answer it:

```bash
orch say C001 -m "This is a teaching toy parser, not production CLI behavior. Prefer simple, explicit errors."
```

Close with a decision record:

```bash
orch close C001 -m "Decision: reject unknown flags explicitly. Rationale: easier teaching/debugging. Dissent/risk accepted: less permissive than argparse. Next step: implement one focused behavior if needed. Owner: lead. Human approval needed: no"
```

Pass criteria:

- Worker receives only the short Talk header plus the lead message.
- Lead receives only the short Talk header plus the worker reply.
- Worker reply arrives in the lead Pi chat after `talk`.
- Worker reply arrives again after `say`.
- Worker answers the new context rather than repeating the first answer.
- `orch idle` reports idle after close.

## 2. BLOCKER path

Goal: prove worker asks for scope instead of guessing on broad/unclear work.

```bash
orch ask work --wait -t TBLOCK001 -m "MODE: DO. TASK_ID: TBLOCK001. Improve the parser broadly. No specific files, behavior, or acceptance criteria are provided. Return BLOCKER if this is too unclear."
```

Pass criteria:

- Reply type is `BLOCKER`, or the payload clearly says it cannot proceed safely.
- Worker asks one concrete clarifying question.
- No files are edited.
- `orch idle` reports idle afterward.

## 3. Edit-producing worker task plus review gate

Goal: prove worker can make a tiny scoped edit, then lead can gate on review.

Use a throwaway project with these files:

```text
orch_task.py
tests/test_orch_task.py
```

Send one narrow implementation task:

```bash
orch send work -t TEDIT001 -m "MODE: DO. TASK_ID: TEDIT001. Add one tiny parser behavior and one focused test. Only edit orch_task.py and tests/test_orch_task.py. Run only the focused test file. Expected reply: RESULT with files changed and tests run."
```

Observe progress:

```bash
orch jobs --active
orch jobs --id TEDIT001
# Use peek only if the task runs long enough for activity to be useful.
orch peek TEDIT001
orch wait TEDIT001 --timeout 300
# Optional deliberate reread/debug check; do not use both wait and get routinely.
orch get TEDIT001
```

Then run a blocking review gate:

```bash
orch ask work --wait -t TREV001 -m "MODE: REVIEW. TASK_ID: TREV001. Review only the TEDIT001 changes in orch_task.py and tests/test_orch_task.py. Do not edit. Return REVIEW/RESULT with findings, risks, and whether lead can proceed."
```

Pass criteria:

- Worker edits only the allowed files.
- Worker reports tests run.
- `orch wait TEDIT001` returns the exact project/task result.
- Optional `orch get TEDIT001` rereads the same completed result.
- Review completes before lead proceeds.
- `orch idle` reports idle afterward.

## 4. Disagreement / critical collaborator path

Goal: prove the worker does not agree by default.

```bash
orch talk work -m "I think duplicate flags should be accepted silently in parse_flags. Push back if that is risky, and recommend a better behavior." -r 3
```

If useful, continue once:

```bash
orch say C002 -m "Assume beginners will use this parser in tests and need clear failures."
```

Close:

```bash
orch close C002 -m "Decision: ... Rationale: ... Dissent/risk accepted: ... Next step: ... Owner: ... Human approval needed: no"
```

Pass criteria:

- Worker names at least one risk or tradeoff.
- Worker recommends a concrete behavior.
- Lead closes with a decision record.

## 5. Skill visibility check

Goal: prove worker skill instructions are active enough to affect behavior.

```bash
orch ask work --wait -t TSKILL001 -m "MODE: PLAN. TASK_ID: TSKILL001. Inspect no files unless needed. In your reply, briefly state which Orchlink worker rules you followed: scope, mode, no-edits, and critical-thinking. Do not reveal hidden reasoning."
```

Pass criteria:

- Worker references the expected worker rules at a safe, high level.
- Worker does not expose hidden chain-of-thought.
- Worker does not edit files.

## 6. Jobs filters and CLI help

Goal: prove `jobs` is the main browser and help text is discoverable.

```bash
orch jobs
orch jobs --active
orch jobs --status DONE
orch jobs --kind task
orch jobs --kind talk
orch jobs --id TEDIT001
orch jobs --json
orch jobs --help
```

Pass criteria:

- Default `orch jobs` shows recent terminal and active rows for the current project.
- `--active` shows only open/running/blocking work.
- `--status` filters to the requested broker status.
- `--kind task` and `--kind talk` separate task and Talk rows.
- `--id` focuses the expected item.
- `--json` returns machine-readable jobs output.
- Help describes the command and its options.
- If activity is shown, job `STATUS` is treated as authoritative; stale heartbeat text is not shown as proof that terminal work is still running.

## 7. Cancellation drill

Goal: document the real cancellation boundary without claiming stronger interruption than measured.

```bash
orch send work -t TCANCEL001 -m "MODE: DO. TASK_ID: TCANCEL001. Wait 30 seconds, then reply RESULT. Do not edit files."
orch cancel TCANCEL001 -m "manual cancellation drill"
orch jobs --id TCANCEL001
```

Pass criteria:

- Broker state becomes `CANCELLED` quickly.
- A steering cancellation message is delivered to Pi if the task reached the worker.
- Future tool calls are blocked after cancellation.
- Any already-running shell command behavior is recorded as best-effort, not a guaranteed immediate stop.

## Final checks

```bash
orch jobs
orch jobs --active
orch idle
orch status --task TEDIT001 --limit 20
```

Pass criteria:

- No pending/active jobs.
- No cross-project jobs appear.
- `orch status --task ...` is scoped to the current project and treated as debug-only raw JSON.

Record failures with the exact command, task/conversation ID, broker health output, and relevant `orch jobs`/`orch get` output.
