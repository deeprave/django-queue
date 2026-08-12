# TLA+ agent workflow

These are deliberately unregistered skill sources. They support optional
formal assessment of the resolved OpenSpec contract in `openspec/specs/`.

## Operator narrative

1. Ask for a formal assessment of a capability or change. `tlc-gather` reads
   resolved requirements first, then relevant ADRs, active deltas, and tests.
   It produces a modelling brief: scope, state, actors, failures, properties,
   finite bounds, exclusions, and requirement coverage.
2. Confirm that brief. In particular, confirm the intended failure semantics
   and every non-goal. Do not let an agent silently choose an interpretation
   where the specification is ambiguous.
3. `tla-model` creates or changes a small TLA+ model, TLC configuration, and
   coverage mapping. Begin with the smallest useful finite domain.
4. `tlc-run` runs the pinned TLC toolchain and preserves the raw output.
5. If TLC finds a counterexample, `tlc-triage` classifies it before any model,
   specification, or implementation change: implementation defect, missing or
   contradictory requirement, model defect, or insufficient bounds.
6. `tlc-report` explains the run, its assumptions and coverage, and records
   any recommended OpenSpec change. `tla-maintain` is used whenever resolved
   requirements covered by a model change or when a change is archived.

## Tool roles

| Tool | Role | Main output |
| --- | --- | --- |
| TLA+ | Model language | `.tla` protocol model |
| TLC | Finite explicit-state model checker | state-space result or counterexample trace |
| Apalache (optional) | Symbolic bounded checker | bounded result or counterexample |

TLC success establishes only that the selected finite model satisfied the
checked properties. It does not prove the complete Python application.

## Suggested repository layout

```text
formal/
  tla/
    <protocol>.tla
    <protocol>.cfg
    <protocol>.coverage.md
  agents/
    skills/
```

Each coverage document maps formal actions and invariants to exact resolved
OpenSpec requirement headings, and explicitly identifies requirements that are
out of scope. Keep generated TLC state directories out of version control.

## Toolchain expectation

Use a project-pinned TLC distribution and Java runtime in CI. The TLA+
Toolbox and editor extensions are local conveniences only. Use Apalache only
when its symbolic or inductive checks add value; it is not required for the
initial workflow.
