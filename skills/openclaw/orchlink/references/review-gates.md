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

## Phase compaction after review

After a full review is reconciled and you have stated the decision/next step, compact the phase when running inside Pi:

```text
/orch compact-phase "Review reconciled: <decision>. Tests: <summary>. Next: <next step>."
```

Do this after the conclusion, not before.

Do not compact:

- while worker work is active
- while a blocker is unresolved
- before important state is written to files
- before the exact review result has been reconciled

The compaction summary should be short and operational: decision, tests/evidence, and next step.
