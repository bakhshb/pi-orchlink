# Orchlink full manual smoke test

Use this plan when validating a real visible lead/work Pi pair before a release or after changing coordination behavior. It is intentionally broader than a quick smoke: it exercises every documented `orch` command surface, Goal Mode, generated skill references, compaction hooks, the main broker/session/storage paths, and the real Pi handoff paths that unit tests cannot prove.

This is still not a mathematical proof of every timing race. Treat the full validation as:

1. automated checks for internal edge cases, plus
2. required manual smoke for real sessions, terminals, broker process behavior, and operator workflows, plus
3. optional extended drills for slower or brittle edge cases.

Run **required** sections before calling a release candidate smoke-tested. Run **extended** sections when the related code changed, before larger releases, or when chasing regressions. Run from the Orchlink repository unless a section tells you to switch into a throwaway project.

## 0. Automated baseline

Goal: prove the package imports, compiles, and the unit suite covers protocol, storage, broker, CLI, project init, sessions, cancellation, and packaging edge cases.

```bash
python3 -m compileall src/orchlink
python3 -c "import pytest, sys; sys.exit(pytest.main(['tests', '-v']))"
```

Pass criteria:

- `compileall` succeeds.
- All tests pass.
- If either fails, stop. Do not trust the manual smoke until the baseline is green.

## 1. CLI help and command registration

Goal: prove every public command is installed and help is discoverable.

```bash
orch --help
orch broker --help
orch broker run --help

for cmd in \
  init lead work ask send task talk say close jobs sessions idle peek get wait \
  cancel update watch stop status doctor goal; do
  orch "$cmd" --help >/tmp/orch-help-$cmd.txt || exit 1
done
```

Pass criteria:

- Every command prints help and exits successfully.
- Help text distinguishes human commands from debug/agent coordination commands where relevant.
- `orch wait --help` documents `--timeout`, `--progress/--no-progress`, and `--poll-seconds`.
- `orch jobs --help` documents `--active`, `--status`, `--kind`, `--id`, and `--json`.
- `orch goal --help` lists `start`, `list`, `derive`, `review`, `approve`, `gate`, `work`, `resume`, `show`, `audit`, `signoff`, `trial`, `trials`, and `cancel`.

## 2. Create a throwaway project

Goal: keep real-session validation isolated from this repository.

```bash
export ORCH_SMOKE_ROOT="$(mktemp -d /tmp/orchlink-smoke-XXXXXX)"
cd "$ORCH_SMOKE_ROOT"

cat > orch_task.py <<'PY'
def parse_flags(args):
    return {arg.removeprefix('--'): True for arg in args if arg.startswith('--')}
PY

mkdir -p tests
cat > tests/test_orch_task.py <<'PY'
from orch_task import parse_flags


def test_parse_flags_accepts_simple_flag():
    assert parse_flags(['--verbose']) == {'verbose': True}
PY

orch init --project-id smoke-full
orch doctor
```

Pass criteria:

- `.orch/project.yaml`, `.orch/skills/lead.md`, `.orch/skills/work.md`, and `.orch/skills/references/*.md` exist.
- `orch doctor` reports the project config and generated skills.
- `orch doctor` reports `lead.md: current`, `work.md: current`, `references/: current`, and `Project .orch files: current`.
- Broker compatibility is current. Expected health includes version `0.5.0` or newer and capabilities including:

```text
project_header_scope
task_activity_endpoint
scoped_task_results
status_filters
session_leases
```

Also verify skill refresh paths:

```bash
printf 'stale\n' > .orch/skills/lead.md
printf 'stale\n' > .orch/skills/references/goal-mode.md
orch doctor
orch init --refresh-skills
orch doctor
orch init --force --project-id smoke-full
orch doctor
```

Pass criteria:

- `doctor` detects stale generated skills or references.
- `init --refresh-skills` repairs skills and references without changing the project identity.
- `init --force --project-id smoke-full` completes cleanly.

## 3. Update/install lifecycle

Extended for normal feature work; required for installer/update changes.

Goal: prove the installed CLI can reinstall itself and reports the post-update session steps.

Safe local reinstall check:

```bash
orch update --reinstall-only
```

Release/full install checks, when appropriate:

```bash
# From the Orchlink checkout, not the throwaway project:
./install.sh
orch update --ref main
```

Pass criteria:

- `orch update --reinstall-only` completes and prints the refresh/restart instructions.
- Fresh install creates or updates `~/.local/share/orchlink` and `~/.local/bin/orch`.
- No real API keys are printed.

## 4. Broker process and health

Goal: prove broker start/stop behavior and public/private API behavior.

```bash
orch stop || true
orch doctor
curl -s http://127.0.0.1:8787/health || true

# Auto-start through a normal command.
orch jobs
curl -s http://127.0.0.1:8787/health

# Authenticated status succeeds with the development key.
curl -s -H 'X-API-Key: change-me' 'http://127.0.0.1:8787/v1/status?project_id=smoke-full'

# Missing API key is rejected.
curl -i -s 'http://127.0.0.1:8787/v1/status?project_id=smoke-full' | head
```

Extended foreground broker flag check from a second terminal:

```bash
cd "$ORCH_SMOKE_ROOT"
orch stop || true
orch broker run --store-backend jsonl --store-path .orch/run/smoke-journal.jsonl
# Ctrl-C after /health works from another terminal.
```

Pass criteria:

- `orch jobs` auto-starts a reachable broker if needed.
- `/health` is public.
- `/v1/status` rejects missing API keys.
- `orch broker run` accepts memory/jsonl store flags.

## 5. Start visible Pi sessions

Goal: prove real lead/work sessions register with the broker and stale skills auto-refresh when sessions start.

Before starting sessions, deliberately stale one generated skill and one generated reference:

```bash
printf 'stale\n' > .orch/skills/work.md
printf 'stale\n' > .orch/skills/references/review-gates.md
```

Terminal A:

```bash
cd "$ORCH_SMOKE_ROOT"
orch lead --new
```

Terminal B:

```bash
cd "$ORCH_SMOKE_ROOT"
orch work --new
```

Terminal C:

```bash
cd "$ORCH_SMOKE_ROOT"
orch sessions
orch sessions --all
orch sessions --json
orch doctor
```

Pass criteria:

- Lead and worker visible Pi sessions start.
- Starting `lead` or `work` refreshes stale/missing generated skills and references.
- `orch sessions` shows active lead/work registrations for `smoke-full`.
- `orch sessions --all` includes released history if any exists.
- `orch sessions --json` is machine-readable.

## 6. Blocking task, async task, and wait/get paths

Goal: prove `ask`, `send`, `wait`, `get`, progress polling, and exact task IDs.

Blocking ask:

```bash
orch ask work --wait -t TASK-BLOCKING-001 -m "Smoke test only. Inspect no files and make no edits. Reply in one sentence confirming you received TASK-BLOCKING-001."
orch get TASK-BLOCKING-001
```

Async send and wait:

```bash
orch send work -t TASK-ASYNC-001 -m "Smoke test only. Inspect no files and make no edits. Reply with a short acknowledgement for TASK-ASYNC-001."
orch jobs --id TASK-ASYNC-001
orch wait TASK-ASYNC-001 --timeout 300 --poll-seconds 1
orch get TASK-ASYNC-001
```

Ask without waiting:

```bash
orch ask work --no-wait -t TASK-ASK-NOWAIT-001 -m "Smoke test for ask --no-wait. Inspect no files and make no edits. Reply with a short acknowledgement for TASK-ASK-NOWAIT-001."
orch wait TASK-ASK-NOWAIT-001 --timeout 300 --no-progress
```

Wait timeout does not cancel:

```bash
orch send work -t TASK-WAIT-TIMEOUT-001 -m "Smoke test wait timeout. Do not edit files. Wait about 10 seconds, then reply that TASK-WAIT-TIMEOUT-001 completed."
orch wait TASK-WAIT-TIMEOUT-001 --timeout 2 --poll-seconds 1
orch jobs --active
orch wait TASK-WAIT-TIMEOUT-001 --timeout 60
```

Missing-result and failed-result display:

```bash
orch get TASK-DOES-NOT-EXIST || true
orch wait TASK-DOES-NOT-EXIST --timeout 1 --no-progress || true

orch ask work --wait -t TASK-FAILED-STDERR-001 -m "Smoke test failed-result display. Do not edit files. Run python3 -c 'import sys; sys.stderr.write(\"smoke-stderr\\n\"); sys.exit(3)' and report the command failure, including stderr if your harness exposes it."
orch get TASK-FAILED-STDERR-001 || true
```

Pass criteria:

- `ask --wait` prints a terminal result.
- `send` returns immediately with guidance.
- `wait` returns the exact requested task result.
- `get` rereads the completed result.
- `ask --no-wait` behaves like async task submission.
- Timeout output clearly says the task is still pending unless cancelled or expired.
- Missing task lookups are explicit and do not return another task's result.
- Failed task output preserves a useful failure summary; stderr is shown when the worker/harness includes it.

## 7. Worker BLOCKER and review-gate behavior

Goal: prove unclear work is blocked and review defaults are safe.

BLOCKER path:

```bash
orch ask work --wait -t TASK-BLOCKER-001 -m "Improve the parser broadly. I am not giving specific files, behavior, or acceptance criteria. Return BLOCKER if this is too unclear to scope safely."
```

Review rejection through async send:

```bash
orch send work -t TASK-REVIEW-REJECT-001 -m "Please review the previous change. Do not edit."
```

Explicit async review, only for non-gating checks:

```bash
orch send work --allow-async-review -t TASK-REVIEW-ASYNC-001 -m "Non-gating smoke review. Inspect no files and make no edits. Reply with no findings for TASK-REVIEW-ASYNC-001."
orch wait TASK-REVIEW-ASYNC-001 --timeout 300
```

Blocking review gate:

```bash
orch ask work --wait -t TASK-REVIEW-GATE-001 -m "Please review only orch_task.py and tests/test_orch_task.py. Do not edit. Return verdict, risks, files inspected, and whether I can proceed."
```

Pass criteria:

- BLOCKER result asks one concrete clarifying question and edits no files.
- `orch send` rejects REVIEW by default with guidance to use blocking `ask --wait`.
- `--allow-async-review` works only when explicitly requested.
- Blocking review completes before lead proceeds.

## 8. Edit-producing task plus real test run

Goal: prove worker can make a scoped edit and report verification.

```bash
orch send work -t TASK-EDIT-001 -m "In this throwaway parser project, add one tiny parser behavior and one focused test. Edit only orch_task.py and tests/test_orch_task.py. Do not edit .orch, docs, install files, or unrelated tests. Run python3 -m pytest tests/test_orch_task.py -v. Return files changed, tests run, and remaining risks."
orch jobs --active
orch jobs --id TASK-EDIT-001
orch task TASK-EDIT-001
orch peek TASK-EDIT-001 || true
orch wait TASK-EDIT-001 --timeout 300
python3 -m pytest tests/test_orch_task.py -v
```

Then gate the change:

```bash
orch ask work --wait -t TASK-EDIT-REVIEW-001 -m "Please review TASK-EDIT-001. Inspect only orch_task.py and tests/test_orch_task.py. Do not edit. You may run python3 -m pytest tests/test_orch_task.py -v. Return findings, risks, files inspected, tests run, and whether I can proceed."
```

Pass criteria:

- Worker edits only the allowed files.
- Focused test passes for worker and lead.
- `orch task` reports route/status/activity while the task exists.
- `peek` reports activity if activity was recorded; no activity is acceptable for very short tasks.
- Review result arrives before the lead treats the edit as accepted.

## 8A. Goal Mode: goal loop to verified done

Goal: prove a real goal can move from source artifact to approved acceptance criteria, worker execution, objective check evidence, and `done` status. This is the key smoke for the loop-engineering promise: set a goal, work until acceptance criteria prove it is done.

Create a small goal source and deterministic check:

```bash
cat > goal-prd.md <<'MD'
# Parser goal

Implement support for --name=value flags in the teaching parser.

Acceptance:
- parse_flags(['--mode=fast']) returns {'mode': 'fast'}.
- Existing boolean flag behavior keeps working.
MD

cat > check_goal.py <<'PY'
from orch_task import parse_flags

assert parse_flags(['--mode=fast']) == {'mode': 'fast'}
assert parse_flags(['--verbose']) == {'verbose': True}
PY

orch goal start "Support equals flags" --prd goal-prd.md
```

Write concrete artifacts for the smoke goal. This avoids relying on LLM-derived AC wording for the required path while still exercising the real goal runner:

````bash
cat > .orch/goals/G001/acceptance.md <<'MD'
# Acceptance

```yaml
acceptance:
  - id: AC-1
    text: --name=value flags return the value string while existing boolean flags still work
    type: objective
    priority: core
    depends_on: []
    check: python3 check_goal.py
    source: goal-prd.md
    confidence: high
    status: pending
```
MD

cat > .orch/goals/G001/plan.md <<'MD'
# Plan

1. Update `parse_flags` in `orch_task.py` to split `--name=value` arguments.
2. Preserve existing boolean flag behavior.
3. Run `python3 check_goal.py` and `python3 -m pytest tests/test_orch_task.py -v`.
MD

cat > .orch/goals/G001/coverage.md <<'MD'
# Coverage

- Source requirement `--name=value` -> AC-1 -> plan step 1 -> `python3 check_goal.py`.
- Source requirement existing boolean behavior -> AC-1 -> plan step 2 -> `python3 check_goal.py`.
- Uncovered: none.
MD
````

Review, list, approve, and run the goal loop. This path intentionally uses the standalone `approve` command for both gates; the combined `gate approve` path is exercised by the subjective goal below.

```bash
orch goal list
orch goal review G001
orch goal approve G001 ac
orch goal approve G001 plan
orch goal show G001
orch goal work G001 --until done --max-steps 3 --timeout 600
orch goal show G001
python3 check_goal.py
python3 -m pytest tests/test_orch_task.py -v
```

Record trial metadata and audit evidence:

```bash
orch goal trial G001 --baseline 4 --outcome done --caught-gap AC-1 --deferrals 0 --evidence-quality good --note "manual smoke goal loop"
orch goal trials G001
orch goal audit G001 --timeout 600
orch goal show G001
```

Subjective signoff path, using a separate goal. This path exercises the combined `gate approve` command:

````bash
orch goal start "Subjective docs signoff" --text "Confirm the README wording is clear enough for a human."
cat > .orch/goals/G002/acceptance.md <<'MD'
# Acceptance

```yaml
acceptance:
  - id: AC-1
    text: Human agrees the wording is clear enough for this smoke test
    type: subjective
    priority: core
    depends_on: []
    check: ""
    source: inline
    confidence: high
    status: pending
```
MD
cat > .orch/goals/G002/plan.md <<'MD'
# Plan

1. Ask for human signoff.
MD
orch goal gate G002 approve
orch goal work G002 --until done --max-steps 1
orch goal signoff G002 AC-1 --note "manual smoke signoff"
orch goal show G002
````

Blocked work, `resume`, and cancellation paths, using separate goals:

````bash
orch goal start "Resume recovery smoke" --text "Verify a blocked goal can resume after a failed check is repaired."
cat > check_resume.py <<'PY'
raise SystemExit(1)
PY
cat > .orch/goals/G003/acceptance.md <<'MD'
# Acceptance

```yaml
acceptance:
  - id: AC-1
    text: Resume verifies the repaired check and marks the goal done
    type: objective
    priority: core
    depends_on: []
    check: python3 check_resume.py
    source: inline
    confidence: high
    status: pending
```
MD
cat > .orch/goals/G003/plan.md <<'MD'
# Plan

1. Run the failing check once to force a blocked/capped goal state.
2. Repair `check_resume.py` in the smoke harness.
3. Run `orch goal resume G003 --until done` and verify evidence is recorded.
MD
orch goal gate G003 approve
orch goal work G003 --until done --max-steps 1 --timeout 600
cat > check_resume.py <<'PY'
raise SystemExit(0)
PY
orch goal resume G003 --until done --max-steps 2 --timeout 600
orch goal show G003

orch goal start "Cancellation smoke" --text "Exercise goal cancellation."
orch goal list
orch goal cancel G004 --reason "manual smoke cancellation drill"
orch goal show G004
orch goal list
````

Pass criteria:

- `orch goal start` creates `.orch/goals/G001/{source.md,acceptance.md,plan.md,goal.yaml,history.jsonl}`.
- `orch goal list` shows G001 with current status and gates.
- `orch goal review` shows source, AC, plan, coverage, and no uncovered warning.
- `orch goal approve G001 ac` and `orch goal approve G001 plan` move the goal to ready.
- `orch goal work G001 --until done` dispatches a worker task, verifies `python3 check_goal.py`, records evidence, and marks the goal `done`.
- Worker edits only `orch_task.py` and/or `tests/test_orch_task.py` for the goal task.
- `orch goal show G001` shows evidence for AC-1.
- `trial`/`trials` records and lists the real smoke trial.
- `audit` writes `.orch/goals/G001/audit.md` and does not change a done goal into a false state.
- Subjective goal pauses for signoff, then `signoff` marks it done.
- Resume recovery goal first stops in a blocked/capped state, then `orch goal resume G003 --until done` reruns the goal loop, records passing evidence, and marks G003 done after the check is repaired.
- `orch goal cancel G004 --reason ...` marks G004 cancelled, clears any active task, records cancellation history, and `goal list` shows the terminal cancelled status.

Extended derivation path, required when derivation prompts/parsing changed:

```bash
orch goal start "Derived smoke goal" --prd goal-prd.md --derive --timeout 600
orch goal review G005
```

Pass criteria for derivation:

- Worker derivation writes non-vague `acceptance.md`, `plan.md`, and optional `coverage.md`.
- The artifacts mention concrete `--name=value` behavior, not generic "work on everything" language.
- If artifacts are vague, reject the gate and record the failure.

## 8B. Compaction: native Pi compact and auto phase compact

Goal: prove there is no separate Orchlink manual compact command, normal Pi `/compact` preserves Orchlink state, and auto-compaction runs only after the lead reconciles a review/phase boundary.

Manual native Pi compaction:

1. In the visible lead Pi chat, run:

   ```text
   /compact manual smoke compact; preserve current project, task IDs, goal IDs, and next step
   ```

2. After compaction completes, ask lead a simple state question in the Pi chat:

   ```text
   What Orchlink project are we in, and what was the latest goal ID used in the smoke test? Answer from compacted context or durable .orch files.
   ```

Pass criteria:

- Pi's native `/compact` runs without using any Orchlink-specific compact command.
- The post-compact lead response can recover the project and goal context, either from the compaction summary or by reading `.orch/goals/`.
- No docs or skills tell the user to run an Orchlink-specific compact command.

Auto review-phase compaction:

```bash
orch ask work --wait -t TASK-AUTO-COMPACT-001 -m "Please review only orch_task.py and tests/test_orch_task.py for the smoke compaction drill. Do not edit. You may run python3 -m pytest tests/test_orch_task.py -v. Return verdict, risks, files inspected, and tests run."
orch idle
```

After the review result appears in the visible lead Pi chat, prompt lead in that same chat:

```text
Reconcile the review in one short response. Start exactly with: Review reconciled:
Include decision, tests, and next step. Do not run tools.
```

Pass criteria:

- The lead response starts with `Review reconciled:`.
- Orchlink starts auto phase compaction after that response.
- The Pi UI shows an `Orchlink auto phase compaction started` or completed notification.
- Auto-compaction does not trigger before the lead reconciliation response.
- Auto-compaction does not trigger if `orch idle` reports active work.

Disable-path check, required for any compaction/extension release and extended otherwise:

```bash
# Restart the lead Pi session with auto review compaction disabled.
ORCHLINK_AUTO_COMPACT_PHASES=off orch lead --new
```

Repeat the review/reconcile drill. Pass criteria: no auto compaction starts while `ORCHLINK_AUTO_COMPACT_PHASES=off`; native `/compact` still works.

## 9. Talk Mode follow-up, disagreement, max-turn discipline, and close

Goal: prove Talk Mode is conversational, not a one-shot task.

Start a conversation and copy the printed conversation ID, for example `C001`:

```bash
orch talk work -m "Ask me one direct clarifying question before recommending how parse_flags should handle unknown flags." -r 2
```

After the worker reply appears in the lead Pi chat:

```bash
orch say C001 -m "This is a teaching toy parser, not production CLI behavior. Prefer simple, explicit errors."
orch close C001 -m "Decision: reject unknown flags explicitly. Rationale: easier teaching/debugging. Dissent/risk accepted: less permissive than argparse. Next step: implement only if requested. Owner: lead. Human approval needed: no"
orch get C001
```

Disagreement path:

```bash
orch talk work -m "I think duplicate flags should be accepted silently in parse_flags. Push back if that is risky, and recommend a better behavior." -r 3
# Continue with orch say C002 -m "..." only if the discussion adds value.
orch close C002 -m "Decision: reject duplicate flags. Rationale: clear beginner feedback. Dissent/risk accepted: stricter than permissive parsers. Next step: no code unless requested. Owner: lead. Human approval needed: no"
```

Local validation and conversation-state errors:

```bash
orch talk work -m "" || true
orch say C001 -m "" || true
orch say C001 -m "This should fail because C001 is already closed." || true
orch say C999 -m "This should fail because the conversation does not exist." || true

orch talk work -m "Reply once; I will intentionally exceed the max turn count after your reply." -r 1
# Replace C003 with the printed ID from the previous command after the worker reply arrives.
orch say C003 -m "This should fail because the conversation reached max turns." || true
```

Pass criteria:

- Worker replies arrive in the lead Pi chat.
- `say` uses new context instead of repeating the first answer.
- Worker names at least one risk/tradeoff in the disagreement path.
- `close` records a decision and the conversation becomes terminal.
- Empty Talk/Say messages are rejected locally.
- `say` against closed, missing, or max-turn conversations exits non-zero with a useful message.
- `orch get C###` prints conversation summary/guidance.

## 10. Jobs, filters, idle, and stale activity handling

Goal: prove `jobs` is the main browser and `idle` is the safety gate.

```bash
orch jobs
orch jobs --active
orch jobs --status DONE
orch jobs --status BLOCKED
orch jobs --status CANCELLED
orch jobs --kind task
orch jobs --kind talk
orch jobs --id TASK-EDIT-001
orch jobs --json
orch jobs --kind bad || true
orch idle
```

Active-work drill:

```bash
orch send work -t TASK-IDLE-ACTIVE-001 -m "Idle drill. Do not edit files. Wait about 15 seconds, then reply that TASK-IDLE-ACTIVE-001 completed."
orch idle || true
orch jobs --active
orch wait TASK-IDLE-ACTIVE-001 --timeout 60
orch idle
```

Extended worker-lane locking and hard-timeout drills:

```bash
orch send work -t TASK-LANE-LOCK-001 -m "Worker lane locking drill. Do not edit files. Wait about 20 seconds, then reply that TASK-LANE-LOCK-001 completed."
orch send work -t TASK-LANE-LOCK-002 -m "This should be rejected while TASK-LANE-LOCK-001 owns the worker lane." || true
orch wait TASK-LANE-LOCK-001 --timeout 60

orch talk work -m "Keep this talk open until I close it; I will verify open Talk blocks new worker tasks." -r 3
# Replace C004 with the printed conversation ID.
orch send work -t TASK-TALK-BLOCKED-001 -m "This should be rejected while Talk is open." || true
orch close C004 -m "Decision: open Talk blocks worker tasks. Rationale: single-flight lane. Dissent/risk accepted: none. Next step: continue smoke. Owner: lead. Human approval needed: no"

orch send work --timeout 2 -t TASK-HARD-TIMEOUT-001 -m "Hard timeout drill. Do not edit files. Wait about 30 seconds, then reply that TASK-HARD-TIMEOUT-001 completed."
sleep 5
orch jobs --id TASK-HARD-TIMEOUT-001
orch idle
```

Extended lease-expiry and reclaim drill (M3):

```bash
# Dispatch a long task and capture the lease epoch from the delivered job.
orch send work -t TASK-LEASE-001 -m "Lease drill. Do not edit files. Wait about 120 seconds, then reply that TASK-LEASE-001 completed."
orch jobs --id TASK-LEASE-001   # note lease.epoch and lease.holder

# Stop the worker so heartbeats stop and the lease expires.
orch stop || true
# Wait past the lease TTL (default ~90s), then reclaim as a recovered holder.
curl -s -H 'X-API-Key: change-me' 'http://127.0.0.1:8787/v1/jobs/TASK-LEASE-001/reclaim' \
  -H 'X-Orchlink-Project-ID: smoke-full' -H 'content-type: application/json' \
  -d '{"holder":"smoke.recovered","project_id":"smoke-full"}' | python3 -m json.tool
# Expect reclaimed=true and a new epoch (incremented).

# Restart the worker and confirm the OLD holder cannot renew or reply (409).
orch work
```

```

Pass criteria:

- Default `jobs` shows recent terminal and active rows for the current project.
- `--active` shows only pending/running/open work.
- `--status`, `--kind`, `--id`, and `--json` filter correctly.
- Unknown `--kind` exits non-zero with a useful message.
- `idle` exits non-zero while active work exists and exits zero afterward.
- Terminal jobs do not display stale heartbeat activity as proof of active work.
- Extended lease drill: after the lease TTL expires with no heartbeat, `POST /v1/jobs/{id}/reclaim` reassigns the holder and increments `epoch`; a stale-holder `POST /v1/jobs/{id}/heartbeat` or a reply with `X-Orchlink-Lease-Epoch`/`X-Orchlink-Lease-Holder` from the old holder returns 409 with no state change. Reclaim by the current holder while still valid is idempotent (`reclaimed=false`).
- Extended: second worker task is rejected while one task is active.
- Extended: open Talk blocks new worker tasks until closed.
- Extended: hard timeout frees the worker lane and reports terminal state.

## 11. Cancellation drills

Goal: document the real cancellation boundary without claiming stronger interruption than measured.

Task cancellation:

```bash
orch send work -t TASK-CANCEL-001 -m "Cancellation drill. Do not edit files. Wait about 30 seconds, then reply that TASK-CANCEL-001 completed."
orch jobs --active
orch cancel TASK-CANCEL-001 -m "manual cancellation drill"
orch jobs --id TASK-CANCEL-001
orch idle
```

Shell-command cancellation, if you want to measure tool-abort behavior:

```bash
orch send work -t TASK-CANCEL-SHELL-001 -m "Shell cancellation drill. Do not edit files. Run python3 -c 'import time; time.sleep(30)' and then reply that TASK-CANCEL-SHELL-001 completed."
orch peek TASK-CANCEL-SHELL-001
orch cancel TASK-CANCEL-SHELL-001 -m "manual shell cancellation drill"
orch jobs --id TASK-CANCEL-SHELL-001
orch idle
```

Conversation cancellation:

```bash
orch talk work -m "Hold this conversation open briefly so I can cancel it." -r 3
orch cancel C003 -m "manual talk cancellation drill"
orch jobs --id C003
orch idle
```

Completed-task cancellation:

```bash
orch cancel TASK-BLOCKING-001 -m "completed task cancellation check" || true
orch jobs --id TASK-BLOCKING-001
```

Pass criteria:

- Active task/conversation broker state becomes `CANCELLED` quickly.
- A steering cancellation message is delivered to Pi if the work reached the worker.
- Future tool calls are blocked after cancellation.
- Already-running shell commands are recorded as best-effort, not guaranteed immediate stop.
- Cancelling completed work does not resurrect or corrupt terminal state.

## 12. Raw debug commands: status, task, watch, peek

Goal: prove debug commands work but remain secondary to `jobs`/`idle` for normal coordination.

```bash
orch status --limit 20
orch status --task TASK-EDIT-001 --limit 20
orch status --since-id 0 --limit 5
orch status --all-projects --limit 5
orch task TASK-EDIT-001
orch task DOES-NOT-EXIST || true
orch peek TASK-EDIT-001 || true
orch watch --iterations 1 --limit 5
# Audit journal (M1): append-only transition log, observability-only.
curl -s -H 'X-API-Key: change-me' 'http://127.0.0.1:8787/v1/journal?project_id=smoke-full&limit=20'
curl -s -H 'X-API-Key: change-me' 'http://127.0.0.1:8787/v1/journal?project_id=smoke-full&since=0&limit=5' | python3 -m json.tool
```

Pass criteria:

- `status` prints raw JSON and respects task/project filters.
- `task` reports known task status and handles missing IDs cleanly.
- `peek` is useful for long-running work and harmless for short completed work.
- `watch --iterations 1` exits after one poll.
- `GET /v1/journal?project_id=&since=&limit=` returns ordered transition entries scoped to the project; goal transitions (`goal.started`, `goal.gated`, `goal.worked`, `goal.done`, `goal.cancelled`, `goal.signedoff`) and broker transitions (`job.created`, `job.dispatched`, `job.replied`, `session.registered`, etc.) both appear.

## 13. Project scoping and same-ID safety

Goal: prove current-project filtering prevents cross-project confusion.

Create a second project in another directory:

```bash
export ORCH_SMOKE_ROOT_B="$(mktemp -d /tmp/orchlink-smoke-b-XXXXXX)"
cd "$ORCH_SMOKE_ROOT_B"
orch init --project-id smoke-full-b
orch jobs
orch status --limit 20
```

From the original project:

```bash
cd "$ORCH_SMOKE_ROOT"
orch jobs
orch status --limit 20
orch status --all-projects --limit 20
```

If you have a visible lead/work pair for the second project, also submit the same task ID in both projects and verify each `orch get TASK-SAME-ID-001` returns only the local project result.

Pass criteria:

- Current-project `jobs`/`status` output does not show other project jobs.
- `--all-projects` is explicitly required for cross-project debug visibility.
- Same task IDs in different projects do not collide.

## 14. JSONL persistence check

Extended for storage changes; required when changing broker persistence.

Goal: prove non-memory broker storage can restore terminal results.

Use a separate terminal so the foreground broker can be stopped and restarted.

```bash
cd "$ORCH_SMOKE_ROOT"
orch stop || true
orch broker run --store-backend jsonl --store-path .orch/run/smoke-journal.jsonl
```

In another terminal, with lead/work sessions attached to this broker:

```bash
cd "$ORCH_SMOKE_ROOT"
orch ask work --wait -t TASK-JSONL-001 -m "JSONL smoke. Inspect no files and make no edits. Reply with a short acknowledgement for TASK-JSONL-001. I will restart the broker after this."
orch get TASK-JSONL-001
```

Stop and restart the foreground broker, then run:

```bash
cd "$ORCH_SMOKE_ROOT"
orch get TASK-JSONL-001
orch jobs --id TASK-JSONL-001
```

Pass criteria:

- Completed task result is still readable after broker restart.
- JSONL store path is created under `.orch/run/`.

## 14A. Ungraceful broker crash and stale-session recovery

Extended for normal feature work; required for broker/session/persistence changes and before broad production-readiness claims.

Goal: prove a result survives an ungraceful broker death, normal commands restart the broker, and stale session/activity state does not masquerade as active work.

Use a throwaway foreground broker so you can kill only the smoke broker process:

```bash
cd "$ORCH_SMOKE_ROOT"
orch idle
orch stop || true
orch broker run --store-backend jsonl --store-path .orch/run/crash-journal.jsonl
```

In another terminal, with lead/work sessions attached to this foreground broker:

```bash
cd "$ORCH_SMOKE_ROOT"
orch ask work --wait -t TASK-CRASH-PERSIST-001 -m "Crash recovery smoke. Inspect no files and make no edits. Reply with a short acknowledgement for TASK-CRASH-PERSIST-001."
orch get TASK-CRASH-PERSIST-001
```

Kill the foreground broker terminal ungracefully, for example by sending `SIGKILL` to that broker process. Do not use a broad `pkill` on a machine running other Orchlink projects. Then run:

```bash
cd "$ORCH_SMOKE_ROOT"
orch jobs
orch get TASK-CRASH-PERSIST-001
orch sessions
orch jobs --active
orch idle
```

Optional stale-worker drill:

```bash
orch send work -t TASK-STALE-WORKER-001 -m "Stale worker drill. Do not edit files. Wait about 30 seconds, then reply."
# Hard-close the visible worker Pi terminal before it replies.
# Wait longer than the session lease/heartbeat timeout configured for this broker, then:
orch sessions --all
orch jobs --id TASK-STALE-WORKER-001
orch idle || true
```

Pass criteria:

- A normal command restarts the broker after the ungraceful death.
- `orch get TASK-CRASH-PERSIST-001` still returns the completed result from JSONL storage.
- `sessions`/`jobs` make stale or released sessions explicit instead of silently showing healthy active work.
- `jobs --active` and `idle` reflect the real terminal state; stale heartbeats are not treated as proof of active work.
- Optional stale-worker drill either cancels/times out the orphaned task or leaves an explicit terminal/recoverable state; it must not block the worker lane forever.

## 15. Stop/restart and session release

Goal: prove broker stop and visible session restart behavior.

```bash
cd "$ORCH_SMOKE_ROOT"
orch idle
orch sessions
orch stop
orch doctor
orch jobs
orch sessions
```

Then restart visible sessions with fresh sessions, stop them, and reopen saved sessions without `--new`:

```bash
orch lead --new
orch work --new
orch sessions
# Close the visible Pi terminals, then reopen saved sessions:
orch lead
orch work
orch sessions
```

Pass criteria:

- `orch stop` stops the project broker process when managed by Orchlink.
- A later command restarts the broker.
- Sessions can be re-registered after restart.
- `lead`/`work` without `--new` reopen saved sessions.
- `idle` is clean before final conclusions.

## 16. Peer-offline guard

Extended for session-lease changes; required when changing `ORCHLINK_REQUIRE_PEER_SESSIONS` behavior.

Goal: prove a broker configured to require peer sessions rejects work when the peer is offline.

```bash
cd "$ORCH_SMOKE_ROOT"
orch stop || true
ORCHLINK_REQUIRE_PEER_SESSIONS=true orch broker run
```

In another terminal, before starting `orch work`:

```bash
cd "$ORCH_SMOKE_ROOT"
orch ask work --wait -t TASK-PEER-OFFLINE-001 -m "This should be rejected because the worker session is offline." || true
orch work --new
# In a third terminal after the worker registers:
orch ask work --wait -t TASK-PEER-ONLINE-001 -m "Peer-online smoke. Inspect no files and make no edits. Reply with a short acknowledgement for TASK-PEER-ONLINE-001."
```

Pass criteria:

- With peer sessions required, task submission is rejected while worker is offline.
- After the worker registers, task submission succeeds.

## 17. Final release checks

Run from the Orchlink repository again:

```bash
cd /home/debian/projects/orchlink
python3 -m compileall src/orchlink
python3 -c "import pytest, sys; sys.exit(pytest.main(['tests', '-v']))"
```

Run from the smoke project:

```bash
cd "$ORCH_SMOKE_ROOT"
orch jobs
orch jobs --active
orch idle
orch sessions --all
```

Pass criteria:

- No pending/active jobs.
- No open Talk conversations.
- No cross-project jobs appear in normal project-scoped views.
- Automated checks remain green after the manual smoke.

## Coverage map

| Area | Covered by | Tier |
| --- | --- | --- |
| Package import/compile | Section 0 | Required |
| Unit-level protocol/storage/broker/CLI edge cases | Section 0 | Required |
| Command registration/help | Section 1 | Required |
| `init`, `doctor`, skill/reference refresh | Section 2 | Required |
| `update`, installer | Section 3 | Extended unless update/install changed |
| `broker run`, health, auth, `stop` | Sections 4, 15 | Required; foreground flag drill extended |
| `lead`, `work`, sessions, reopen without `--new` | Sections 5, 15 | Required |
| `ask`, `send`, `wait`, `get` | Sections 6-8 | Required |
| Missing/failed result display | Section 6 | Required for CLI display changes |
| REVIEW gate safety | Section 7 | Required |
| Worker edits and tests | Section 8 | Required |
| Goal Mode source, gate, work loop, evidence, audit, trial, signoff | Section 8A | Required for Goal Mode releases |
| Native Pi compaction, auto review-phase compaction, and auto-compaction disable path | Section 8B | Required for compaction/extension changes |
| `talk`, `say`, `close`, closed/missing/max-turn errors | Section 9 | Required |
| `jobs`, `idle`, worker-lane locks, hard timeout | Section 10 | Core required; lock/timeout drills extended |
| `cancel` | Section 11 | Required; shell abort timing extended |
| `task`, `peek`, `watch`, `status` | Section 12 | Required for CLI/debug changes |
| Project scoping and same-ID safety | Section 13 | Required for scoping changes |
| JSONL persistence | Section 14 | Extended unless storage changed |
| Ungraceful broker crash and stale-session recovery | Section 14A | Extended unless broker/session/persistence changed; required before production-readiness claims |
| Peer-offline session guard | Section 16 | Extended unless session leases changed |
| Final no-active-work gate | Section 17 | Required |

## Failure record template

For each failure, record:

```text
Date/time:
Orchlink version:
Project path:
Command:
Task/conversation ID:
Expected:
Actual:
Broker health output:
Relevant orch jobs --id / orch get / orch status output:
Relevant lead/work Pi chat excerpt:
Files changed unexpectedly:
```

Do not mark the full smoke green until failures are either fixed or explicitly accepted with a release note.
