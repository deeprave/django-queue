---
name: tlc-triage
description: Classify a TLC counterexample before any code, specification, or model change. Use whenever tlc-run reports an invariant, deadlock, or liveness failure.
---

# Triage a TLC counterexample

Read the raw TLC trace, the model coverage mapping, and the linked resolved
OpenSpec requirements. Reconstruct the trace in plain language and classify
exactly one primary cause:

- implementation violates an unambiguous requirement;
- resolved requirement is missing, contradictory, or ambiguous;
- model omitted intended behaviour or is otherwise incorrect;
- finite bounds/configuration do not exercise the intended case.

Do not alter a model, invariant, requirement, or implementation merely to
remove a counterexample. Record the classification, evidence, affected
requirements, and the smallest next action. Route requirement gaps to an
OpenSpec change and implementation defects to the normal implementation
workflow.
