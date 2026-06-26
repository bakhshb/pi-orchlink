# Orchlink full manual smoke test

Use this plan when validating a real visible lead/work Pi pair before a release or after changing coordination behavior. It is intentionally broader than a quick smoke: it exercises every documented `orch` command surface, the main broker/session/storage paths, and the real Pi handoff paths that unit tests cannot prove.

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
  cancel update watch stop status doctor; do
  orch "$cmd" --help >/tmp/orch-help-$cmd.txt || exit 1
done
```

Pass criteria:

- Every command prints help and exits successfully.
- Help text distinguishes human commands from debug/agent coordination commands where relevant.
- `orch wait --help` documents `--timeout`, `--progress/--no-progress`, and `--poll-seconds`.
- `orch jobs --help` documents `--active`, `--status`, `--kind`, `--id`, and `--json`.

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

- `.orch/project.yaml`, `.orch/skills/lead.md`, and `.orch/skills/work.md` exist.
- `orch doctor` reports the project config and generated skills.
- Broker compatibility is current. Expected health includes version `0.4.3` or newer and capabilities including:

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
orch doctor
orch init --refresh-skills
orch doctor
orch init --force --project-id smoke-full
orch doctor
```

Pass criteria:

- `doctor` detects the stale skill.
- `init --refresh-skills` repairs skills without changing the project identity.
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

Before starting sessions, deliberately stale one generated skill:

```bash
printf 'stale\n' > .orch/skills/work.md
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
- Starting `lead` or `work` refreshes stale/missing generated skills.
- `orch sessions` shows active lead/work registrations for `smoke-full`.
- `orch sessions --all` includes released history if any exists.
- `orch sessions --json` is machine-readable.

## 6. Blocking task, async task, and wait/get paths

Goal: prove `ask`, `send`, `wait`, `get`, progress polling, and exact task IDs.

Blocking ask:

```bash
orch ask work --wait -t TASK-BLOCKING-001 -m "MODE: PLAN. TASK_ID: TASK-BLOCKING-001. Current context: smoke test. Exact scope: inspect no files. Forbidden scope: do not edit. Permission: inspect only. Tests/checks: none. Desired reply shape: RESULT in one sentence. I will wait."
orch get TASK-BLOCKING-001
```

Async send and wait:

```bash
orch send work -t TASK-ASYNC-001 -m "MODE: PLAN. TASK_ID: TASK-ASYNC-001. Current context: smoke test. Exact scope: inspect no files. Forbidden scope: do not edit. Permission: inspect only. Tests/checks: none. Desired reply shape: RESULT after a short acknowledgement. I will wait by exact task ID."
orch jobs --id TASK-ASYNC-001
orch wait TASK-ASYNC-001 --timeout 300 --poll-seconds 1
orch get TASK-ASYNC-001
```

Ask without waiting:

```bash
orch ask work --no-wait -t TASK-ASK-NOWAIT-001 -m "MODE: PLAN. TASK_ID: TASK-ASK-NOWAIT-001. Current context: smoke test for ask --no-wait. Exact scope: inspect no files. Forbidden scope: do not edit. Permission: inspect only. Tests/checks: none. Desired reply shape: RESULT. I will wait later."
orch wait TASK-ASK-NOWAIT-001 --timeout 300 --no-progress
```

Wait timeout does not cancel:

```bash
orch send work -t TASK-WAIT-TIMEOUT-001 -m "MODE: DO. TASK_ID: TASK-WAIT-TIMEOUT-001. Current context: smoke test. Exact scope: wait 10 seconds, then reply RESULT. Forbidden scope: do not edit. Permission: no edits. Tests/checks: none. Desired reply shape: RESULT."
orch wait TASK-WAIT-TIMEOUT-001 --timeout 2 --poll-seconds 1
orch jobs --active
orch wait TASK-WAIT-TIMEOUT-001 --timeout 60
```

Missing-result and failed-result display:

```bash
orch get TASK-DOES-NOT-EXIST || true
orch wait TASK-DOES-NOT-EXIST --timeout 1 --no-progress || true

orch ask work --wait -t TASK-FAILED-STDERR-001 -m "MODE: DO. TASK_ID: TASK-FAILED-STDERR-001. Current context: failed-result display smoke. Exact scope: run python3 -c 'import sys; sys.stderr.write(\"smoke-stderr\\n\"); sys.exit(3)' and report the command failure. Forbidden scope: do not edit. Permission: command only. Tests/checks: the failing command itself. Desired reply shape: FAILED with stderr included if your harness exposes it. I will wait."
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
orch ask work --wait -t TASK-BLOCKER-001 -m "MODE: DO. TASK_ID: TASK-BLOCKER-001. Improve the parser broadly. No specific files, behavior, or acceptance criteria are provided. Return BLOCKER if this is too unclear."
```

Review rejection through async send:

```bash
orch send work -t TASK-REVIEW-REJECT-001 -m "MODE: REVIEW. TASK_ID: TASK-REVIEW-REJECT-001. Review the previous change. Do not edit."
```

Explicit async review, only for non-gating checks:

```bash
orch send work --allow-async-review -t TASK-REVIEW-ASYNC-001 -m "MODE: REVIEW. TASK_ID: TASK-REVIEW-ASYNC-001. Current context: smoke test. Exact scope: inspect no files. Forbidden scope: do not edit. Permission: inspect only. Tests/checks: none. Desired reply shape: RESULT with no findings. This review is not a gate."
orch wait TASK-REVIEW-ASYNC-001 --timeout 300
```

Blocking review gate:

```bash
orch ask work --wait -t TASK-REVIEW-GATE-001 -m "MODE: REVIEW. TASK_ID: TASK-REVIEW-GATE-001. Current context: smoke test. Exact scope: review only orch_task.py and tests/test_orch_task.py. Forbidden scope: do not edit. Permission: inspect only. Tests/checks: none. Desired reply shape: RESULT with verdict, risks, files inspected, and whether lead can proceed. I will wait."
```

Pass criteria:

- BLOCKER result asks one concrete clarifying question and edits no files.
- `orch send` rejects REVIEW by default with guidance to use blocking `ask --wait`.
- `--allow-async-review` works only when explicitly requested.
- Blocking review completes before lead proceeds.

## 8. Edit-producing task plus real test run

Goal: prove worker can make a scoped edit and report verification.

```bash
orch send work -t TASK-EDIT-001 -m "MODE: DO. TASK_ID: TASK-EDIT-001. Current context: throwaway parser project. Exact scope: add one tiny parser behavior and one focused test. Only edit orch_task.py and tests/test_orch_task.py. Forbidden scope: do not edit .orch, docs, install files, or unrelated tests. Permission: implementation allowed only in the two allowed files. Tests/checks: run python3 -m pytest tests/test_orch_task.py -v. Desired reply shape: RESULT with files changed, tests run, and remaining risks. I will inspect status while you work."
orch jobs --active
orch jobs --id TASK-EDIT-001
orch task TASK-EDIT-001
orch peek TASK-EDIT-001 || true
orch wait TASK-EDIT-001 --timeout 300
python3 -m pytest tests/test_orch_task.py -v
```

Then gate the change:

```bash
orch ask work --wait -t TASK-EDIT-REVIEW-001 -m "MODE: REVIEW. TASK_ID: TASK-EDIT-REVIEW-001. Current context: review TASK-EDIT-001. Exact scope: review only orch_task.py and tests/test_orch_task.py. Forbidden scope: do not edit. Permission: inspect only. Tests/checks: you may run python3 -m pytest tests/test_orch_task.py -v. Desired reply shape: RESULT with findings, risks, files inspected, tests run, and whether lead can proceed. I will wait."
```

Pass criteria:

- Worker edits only the allowed files.
- Focused test passes for worker and lead.
- `orch task` reports route/status/activity while the task exists.
- `peek` reports activity if activity was recorded; no activity is acceptable for very short tasks.
- Review result arrives before the lead treats the edit as accepted.

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
orch send work -t TASK-IDLE-ACTIVE-001 -m "MODE: DO. TASK_ID: TASK-IDLE-ACTIVE-001. Current context: idle drill. Exact scope: wait 15 seconds, then reply RESULT. Forbidden scope: do not edit. Permission: no edits. Tests/checks: none. Desired reply shape: RESULT."
orch idle || true
orch jobs --active
orch wait TASK-IDLE-ACTIVE-001 --timeout 60
orch idle
```

Extended worker-lane locking and hard-timeout drills:

```bash
orch send work -t TASK-LANE-LOCK-001 -m "MODE: DO. TASK_ID: TASK-LANE-LOCK-001. Current context: worker lane locking drill. Exact scope: wait 20 seconds, then reply RESULT. Forbidden scope: do not edit. Permission: no edits. Tests/checks: none. Desired reply shape: RESULT."
orch send work -t TASK-LANE-LOCK-002 -m "MODE: PLAN. TASK_ID: TASK-LANE-LOCK-002. This should be rejected while TASK-LANE-LOCK-001 owns the worker lane." || true
orch wait TASK-LANE-LOCK-001 --timeout 60

orch talk work -m "Keep this talk open until I close it; I will verify open Talk blocks new worker tasks." -r 3
# Replace C004 with the printed conversation ID.
orch send work -t TASK-TALK-BLOCKED-001 -m "MODE: PLAN. TASK_ID: TASK-TALK-BLOCKED-001. This should be rejected while Talk is open." || true
orch close C004 -m "Decision: open Talk blocks worker tasks. Rationale: single-flight lane. Dissent/risk accepted: none. Next step: continue smoke. Owner: lead. Human approval needed: no"

orch send work --timeout 2 -t TASK-HARD-TIMEOUT-001 -m "MODE: DO. TASK_ID: TASK-HARD-TIMEOUT-001. Current context: hard timeout drill. Exact scope: wait 30 seconds, then reply RESULT. Forbidden scope: do not edit. Permission: no edits. Tests/checks: none. Desired reply shape: RESULT."
sleep 5
orch jobs --id TASK-HARD-TIMEOUT-001
orch idle
```

Pass criteria:

- Default `jobs` shows recent terminal and active rows for the current project.
- `--active` shows only pending/running/open work.
- `--status`, `--kind`, `--id`, and `--json` filter correctly.
- Unknown `--kind` exits non-zero with a useful message.
- `idle` exits non-zero while active work exists and exits zero afterward.
- Terminal jobs do not display stale heartbeat activity as proof of active work.
- Extended: second worker task is rejected while one task is active.
- Extended: open Talk blocks new worker tasks until closed.
- Extended: hard timeout frees the worker lane and reports terminal state.

## 11. Cancellation drills

Goal: document the real cancellation boundary without claiming stronger interruption than measured.

Task cancellation:

```bash
orch send work -t TASK-CANCEL-001 -m "MODE: DO. TASK_ID: TASK-CANCEL-001. Current context: cancellation drill. Exact scope: wait 30 seconds, then reply RESULT. Forbidden scope: do not edit. Permission: no edits. Tests/checks: none. Desired reply shape: RESULT."
orch jobs --active
orch cancel TASK-CANCEL-001 -m "manual cancellation drill"
orch jobs --id TASK-CANCEL-001
orch idle
```

Shell-command cancellation, if you want to measure tool-abort behavior:

```bash
orch send work -t TASK-CANCEL-SHELL-001 -m "MODE: DO. TASK_ID: TASK-CANCEL-SHELL-001. Current context: shell cancellation drill. Exact scope: run python3 -c 'import time; time.sleep(30)' and then reply RESULT. Forbidden scope: do not edit. Permission: command only. Tests/checks: none. Desired reply shape: RESULT."
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
```

Pass criteria:

- `status` prints raw JSON and respects task/project filters.
- `task` reports known task status and handles missing IDs cleanly.
- `peek` is useful for long-running work and harmless for short completed work.
- `watch --iterations 1` exits after one poll.

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
orch ask work --wait -t TASK-JSONL-001 -m "MODE: PLAN. TASK_ID: TASK-JSONL-001. Current context: jsonl smoke. Exact scope: inspect no files. Forbidden scope: do not edit. Permission: inspect only. Tests/checks: none. Desired reply shape: RESULT. I will restart the broker after this."
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
orch ask work --wait -t TASK-PEER-OFFLINE-001 -m "MODE: PLAN. TASK_ID: TASK-PEER-OFFLINE-001. This should be rejected because the worker session is offline." || true
orch work --new
# In a third terminal after the worker registers:
orch ask work --wait -t TASK-PEER-ONLINE-001 -m "MODE: PLAN. TASK_ID: TASK-PEER-ONLINE-001. Current context: peer-online smoke. Exact scope: inspect no files. Forbidden scope: do not edit. Permission: inspect only. Tests/checks: none. Desired reply shape: RESULT. I will wait."
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
| `init`, `doctor`, skill refresh | Section 2 | Required |
| `update`, installer | Section 3 | Extended unless update/install changed |
| `broker run`, health, auth, `stop` | Sections 4, 15 | Required; foreground flag drill extended |
| `lead`, `work`, sessions, reopen without `--new` | Sections 5, 15 | Required |
| `ask`, `send`, `wait`, `get` | Sections 6-8 | Required |
| Missing/failed result display | Section 6 | Required for CLI display changes |
| REVIEW gate safety | Section 7 | Required |
| Worker edits and tests | Section 8 | Required |
| `talk`, `say`, `close`, closed/missing/max-turn errors | Section 9 | Required |
| `jobs`, `idle`, worker-lane locks, hard timeout | Section 10 | Core required; lock/timeout drills extended |
| `cancel` | Section 11 | Required; shell abort timing extended |
| `task`, `peek`, `watch`, `status` | Section 12 | Required for CLI/debug changes |
| Project scoping and same-ID safety | Section 13 | Required for scoping changes |
| JSONL persistence | Section 14 | Extended unless storage changed |
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
