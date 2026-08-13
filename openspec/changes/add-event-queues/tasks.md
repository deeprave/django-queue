## 1. Queue semantic foundations

- [ ] 1.1 Write failing tests distinguishing retained task outcomes from removed
  event outcomes and resolving event lifetime as entry override, queue default,
  then 60 seconds.
- [ ] 1.2 Extend the shared `AsyncQueue` and `EventQueue` bases; keep existing
  Redis and memory public imports AsyncQueue-compatible.
- [ ] 1.3 Implement Redis and memory event queues with ownership-aware remove,
  fixed-delay release, and expiry pruning.
- [ ] 1.4 Run focused backend and timeout tests.

## 2. Listener worker and runtime

- [ ] 2.1 Write failing tests for decorator registration, optional filters,
  sync bridge, async invocation, cursor rotation, and all outcomes.
- [ ] 2.2 Implement `queue_listener` and the process-local listener registry.
- [ ] 2.3 Add `BaseQueueWorker`; preserve `AsyncQueueWorker` task behaviour;
  implement `EventQueueWorker` dispatch, settlement, delay, expiry, and logs.
- [ ] 2.4 Run focused listener and worker tests.

## 3. Django integration and verification

- [ ] 3.1 Write failing tests for automatic startup, one shared loop, one task
  per configured event queue, no task-worker startup, and invalid metadata.
- [ ] 3.2 Implement process-wide `EventRuntime` and connect it to AppConfig
  and configured event queues.
- [ ] 3.3 Add memory and Redis end-to-end tests for claims, expiry, all-pass
  delay, rejection, exception logs, and local-memory scope.
- [ ] 3.4 Document outcomes, lifetime, backend scope, and ordering limits; run
  full tests, `ruff format`, `ruff check`, `ty check`, and OpenSpec validation.
