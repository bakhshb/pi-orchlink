# Orchlink review gates and phase compaction

Use this reference when a worker review can change the lead's next action, or when a phase is ready to compact.

## Review gates

Treat review requests as a gate when they can change your next action.

Use:

```bash
orch ask work --wait -t TREV001 -m "Please review ..."
```

Do not start dependent full tests, final summaries, packaging, release notes, or cleanup until the review result arrives.

After review returns:

1. Stop unrelated work and read the exact task result.
2. Reconcile the worker's verdict with your own inspection.
3. Name any risk, disagreement, or assumption.
4. Decide whether to proceed, fix, ask a follow-up, or block.
5. Only then run dependent expensive steps or make final claims.

If the answer is risky, blocked, or unclear, ask a follow-up or use Talk Mode.

## Compaction after review

Do not compact automatically after review gates. The human should see the worker result and the lead's reconciliation in the live conversation.

If a long session truly needs compaction, use Pi's native `/compact` command manually after the conclusion. Orchlink's `session_before_compact` hook makes normal Pi compaction preserve Orchlink state pointers, current task/goal context, and durable `.orch/` paths. After compaction, start with `orch resume` before making claims about state.

Automatic review compaction is opt-in only. If an operator explicitly starts the lead with `ORCHLINK_AUTO_COMPACT_PHASES=review`, a reconciliation line beginning with `Review reconciled:`, `Decision:`, or `Blocked:` may trigger compaction when no project work is active. Do not rely on this path by default.

Do not compact:

- while worker work is active
- while a blocker is unresolved
- before important state is written to files
- before the exact review result has been reconciled

The compaction summary should be short and operational: decision, tests/evidence, and next step.
