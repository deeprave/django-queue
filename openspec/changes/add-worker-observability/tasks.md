## 1. Worker observability

- [x] 1.1 Add focused failing tests for UUIDv7 worker identity, immutable
  snapshots, lifecycle transitions, and terminal counters advancing only after
  confirmed persistence.
- [x] 1.2 Introduce the frozen worker-snapshot value and update
  `AsyncQueueWorker` state at run start, dequeue, confirmed terminal outcome,
  and stop without changing delivery behaviour.
- [x] 1.3 Add focused logging tests that assert snapshot-derived structured
  fields for worker start, dispatch, terminal outcome, and stop.
- [x] 1.4 Emit structured lifecycle/dispatch INFO logs from current snapshots;
  preserve existing diagnostic error logs.
- [x] 1.5 Document the process-local scope, counter semantics, and logging
  fields; run OpenSpec validation and the full test suite.
