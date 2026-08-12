---
name: tlc-run
description: Run TLC against a repository TLA+ model and preserve an auditable result. Use after tla-model, when asked to check a formal model, or to rerun a model after a bounded configuration or toolchain update.
---

# Run TLC

Use the repository wrapper so TLC runs with the Java runtime and
`tla2tools.jar` bundled in TLA+ Toolbox:

```sh
formal/run-tlc -config formal/tla/<protocol>.cfg formal/tla/<protocol>.tla
```

Set `TLA_TOOLBOX_APP` only when Toolbox is not installed at
`/Applications/TLA+ Toolbox.app`. Preserve raw output in an ignored run-output
location or CI artifact; never edit the model merely to obtain a pass.

Report:

- exact command, TLC and Java versions;
- model and configuration paths;
- pass/fail status, invariant, deadlock, and liveness outcome;
- state-space counts and elapsed time;
- counterexample trace location and the first violated property.

A passing finite run means no checked property failed within the selected
model bounds. It does not prove the production implementation. Send failures
to `tlc-triage`; return syntax, toolchain, and configuration failures to
`tla-model` with the raw diagnostic.
