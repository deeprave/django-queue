## 1. Naming inventory and contracts

- [x] 1.1 Inventory public and private callable, type, exception, and
  configuration names; agree each canonical replacement and the few qualifiers
  required to avoid a raw-value/lifecycle-record collision.
- [x] 1.2 Update API naming and affected backend/registry OpenSpec contracts
  with the approved canonical names and removal of superseded spellings.
- [x] 1.3 Add focused contract tests covering concise names, required
  disambiguation, and the absence of deprecated aliases.

## 2. Implementation cutover

- [x] 2.1 Rename queue and lifecycle operations across base classes, memory and
  Redis backends, providers, workers, observers, and runtime code.
- [x] 2.2 Rename or remove redundant worker, provider, registry, and
  transport-specific identifiers from the agreed inventory without exposing
  ownership internals on public queue APIs.
- [x] 2.3 Update custom-backend protocols, imports, type annotations, and all
  in-repository consumers; remove every old spelling rather than adding aliases.

## 3. Documentation and verification

- [x] 3.1 Update README, demo, examples, and OpenSpec references to the
  canonical vocabulary.
- [x] 3.2 Run focused naming and custom-backend tests, then the full test,
  ruff, format, type, Django-demo, and strict OpenSpec validation suites.
