---
name: tla-model
description: Create or update a TLA+ protocol model from an approved OpenSpec modelling brief. Use after tlc-gather scope approval, or when a resolved requirement changes a covered concurrent state transition, ownership rule, recovery path, or invariant.
---

# Create a TLA+ model

Require an approved modelling brief from `tlc-gather`. Implement only the
requirements it selects. Place artefacts under `formal/tla/`:

```text
<protocol>.tla
<protocol>.cfg
<protocol>.coverage.md
```

Keep the first model small and finite, normally two entries, two workers, and
one failure/recovery cycle. Model atomic protocol actions, ownership, durable
state, and explicit failure transitions. Do not encode implementation details
unless the OpenSpec contract requires them.

In the coverage document, map every action and invariant to an exact resolved
OpenSpec requirement heading. List exclusions and assumptions. Do not describe
a model as complete merely because it parses or its properties look plausible.

Pass the model to `tlc-run`, which invokes the repository wrapper:

```sh
formal/run-tlc -config formal/tla/<protocol>.cfg formal/tla/<protocol>.tla
```

Repair syntax, typing, or configuration errors as needed. Treat
counterexamples as inputs to `tlc-triage`, not as permission to weaken the
model or its invariants.
