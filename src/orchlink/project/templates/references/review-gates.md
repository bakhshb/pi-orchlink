# Orchlink review gates

Use this reference when a worker review can change the lead's next action.

## Review gates

Treat review requests as a gate when they can change your next action.

Use:

```bash
orch send work --wait -t TREV001 -m "Please review ..."
```

Do not start dependent full tests, final summaries, packaging, release notes, or cleanup until the review result arrives.

After review returns:

1. Stop unrelated work and read the exact task result.
2. Reconcile the worker's verdict with your own inspection.
3. Name any risk, disagreement, or assumption.
4. Decide whether to proceed, fix, ask a follow-up, or block.
5. Only then run dependent expensive steps or make final claims.

If the answer is risky, blocked, or unclear, ask a follow-up or use Talk Mode.

## Compaction

Orchlink does not trigger, customize, or hook Pi compaction. Use Pi's native `/compact` command or Pi's own auto-compaction behavior when you need context cleanup.

Do not compact:

- while worker work is active
- while a blocker is unresolved
- before important state is written to files
- before the exact review result has been reconciled

After any Pi compaction, reload durable Orchlink state before making claims about project status:

```bash
orch resume
orch goal show <id>  # when a goal is active
```
