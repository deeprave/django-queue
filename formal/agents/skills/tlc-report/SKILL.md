---
name: tlc-report
description: Interpret and document TLC results, including bounded coverage and counterexamples. Use after tlc-run and tlc-triage to provide an operator-facing formal-assessment result.
---

# Report a formal assessment

Write a concise result for the operator. Include:

- resolved OpenSpec requirements covered, with linked coverage artefact;
- model bounds, assumptions, and explicit exclusions;
- TLC command, tool versions, and state-space result;
- checked invariants and liveness properties;
- a plain-English counterexample timeline when present;
- triage classification and recommended OpenSpec or implementation follow-up.

Never say that TLC proved the application. Say precisely what finite model and
configuration were checked. When a design gap is found, propose a small
OpenSpec change; do not silently revise the specification.
