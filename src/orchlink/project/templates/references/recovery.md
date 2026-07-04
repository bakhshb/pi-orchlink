# Orchlink recovery, cancellation, and debug reference

Use this file when normal coordination output is stale, contradictory, cross-project, or hard to interpret.

## Cancellation

Cancel stale or no-longer-needed work before assigning more work:

```bash
orch cancel T002 -m "reason"
```

Cancellation marks broker work `CANCELLED` and asks Pi to abort the current turn. Future tool calls should be blocked. Already-running shell commands are best-effort and may only stop if Pi's abort reaches them.

After cancellation:

```bash
orch jobs --id T002
orch idle
```

Do not assume cancelled shell commands stopped instantly.

## Debug and recovery

Use readable checks first:

```bash
orch doctor
orch sessions
orch jobs --active
orch idle
```

Use help when unsure:

```bash
orch --help
orch jobs --help
```

Use broker health only for deeper debugging:

```bash
curl -s http://127.0.0.1:8787/health
```

Healthy broker output should include capabilities such as:

```text
project_header_scope
task_activity_endpoint
scoped_task_results
status_filters
session_leases
session_readiness
session_lease_fencing
```

If Orchlink reports stale broker, missing capabilities, cross-project results, or confusing state, restart cleanly:

```bash
orch stop --all
orch lead --new
orch work --new
```

Raw debug commands:

```bash
orch status --task T002 --limit 20
orch watch --iterations 1 --limit 20
orch task T002
orch broker run --host 127.0.0.1 --port 8787
```

Do not use raw debug output for normal coordination or session checks. Use `orch sessions` for sessions and `orch jobs`/`idle` for work state.
