## 1. Queue variants and lifecycle-observer transports

- [x] 1.1 Add failing tests for `BaseQueue` semantic variants and reject
  `queue_observer` registration for an EventQueue.
- [x] 1.2 Add `AsyncQueue` and `EventQueue` semantic bases below `BaseQueue`;
  make existing memory and Redis queues AsyncQueue variants without breaking
  public imports.
- [x] 1.3 Add synchronous and asynchronous retained-entry listing, immutable
  lifecycle snapshots, queue-scoped Redis channel helpers, Redis-backed
  publication, and in-memory broker publication.
- [x] 1.4 Add focused tests for Redis and memory queued/running/terminal
  publication, initial retained snapshot collections, and publication failure
  isolation.

## 2. Django-process listener runtime

- [x] 2.1 Implement `django_queue.observers` with lazy, idempotent local
  runtime startup, in-memory broker dispatch, and endpoint discovery for
  configured Redis-backed queues.
- [x] 2.2 Implement `queue_observer` registration with optional entry ID
  filter and returned subscription object with `unsubscribe()`, subscription-before-bootstrap
  sequencing, sequential sync/async callback dispatch, lifecycle ordering, and
  callback-failure logging.

## 3. Worker integration and documentation

- [x] 3.1 Publish the persisted queued state when `AsyncQueueWorker` receives
  an entry, then confirmed running and terminal states, without changing task
  semantics.
- [x] 3.2 Add end-to-end Redis and memory tests from queue through worker,
  observer, and dashboard-style snapshot projection; document backend scope,
  best-effort, passive-observer boundaries, and threading limitations.
- [x] 3.3 Run the full test suite and OpenSpec validation.
