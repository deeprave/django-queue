## 0. Demo lifecycle alignment

- [x] 0.1 Keep every published demo entry on the same `queued` → `running` →
  terminal lifecycle and use an independent one-in-eight failure selection;
  do not use the library's future direct queued-failure transition in the demo.

## 1. Terminal entry retention

- [x] 1.1 Write failing tests for direct `queued` to `failed` transition,
  terminal expiry, explicit pruning, missing-entry errors, non-terminal
  preservation, and observer delivery of an immutable `terminated` copy after
  durable removal.
- [x] 1.2 Permit direct queued failures in every AsyncQueue backend, setting a
  terminal timestamp without a dispatch timestamp. Add `prune_entry` and
  `aprune_entry` to `AsyncQueue`, `QueueEntryNotFoundError`, retention
  configuration, and one shared cleanup behavior, including the AsyncQueue
  observer-only termination event when the durable record is deleted.
- [x] 1.3 Document status lookup lifetime, missing-entry errors, scheduled and
  explicit pruning, and observer removal semantics, then run the full suite.

## 2. Termination lifecycle state

- [x] 2.1 Model `terminated` as the final successor of each completed status,
  keep it non-persisted during pruning, and order it during observer bootstrap.
