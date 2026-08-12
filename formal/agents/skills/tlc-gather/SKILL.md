---
name: tlc-gather
description: Gather and bound the OpenSpec context required for a TLA+ and TLC assessment. Use when asked to formally assess an OpenSpec capability or change, prepare a model, or determine whether a concurrent protocol is suitable for TLC.
---

# Gather a formal modelling brief

Read the resolved requirements in `openspec/specs/` before reading individual
change artefacts. Read relevant ADRs, active delta specifications and design
documents, then behavioural tests only as implementation evidence.

Produce a modelling brief; do not create a model until its scope is approved.
Include:

- exact capability and requirement headings to cover;
- actors, durable state, atomic actions, and failure actions;
- intended safety properties and any liveness properties;
- a smallest useful finite model configuration;
- explicit assumptions, exclusions, and ambiguities requiring an operator
  decision;
- a requirement-to-model coverage table.

Model protocols and state transitions, not Python, Redis command syntax, JSON
encoding, or handler business logic. State that a successful finite TLC run is
not a proof of the full application.
