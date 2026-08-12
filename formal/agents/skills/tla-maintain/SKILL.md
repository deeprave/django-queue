---
name: tla-maintain
description: Keep repository TLA+ models aligned with resolved OpenSpec requirements. Use when a covered OpenSpec capability changes, before a related change is archived, or when formal coverage may be stale.
---

# Maintain formal coverage

Compare every `formal/tla/*.coverage.md` mapping with the current resolved
requirements in `openspec/specs/`, relevant ADRs, and any change being
archived. Flag a model as stale when a covered state transition, ownership
rule, recovery path, invariant, or explicit assumption changes.

Do not infer broad new semantics. For a stale model, identify the exact
requirement change, decide whether it is in or out of the model's declared
scope, and either invoke `tlc-gather` for renewed scope approval or update the
coverage document to record the exclusion. Rerun TLC whenever model semantics
or finite bounds change.
