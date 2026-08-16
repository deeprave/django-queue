## 1. Queue semantic foundations

- [x] 1.1 Write failing tests distinguishing retained task outcomes from removed
  event outcomes and resolving event lifetime as entry override, queue default,
  then 60 seconds.
- [x] 1.2 Add `QueueProvider`, `QueueProviderMemory`, and
  `QueueProviderRedis`; adapt shared `AsyncQueue` and `EventQueue` bases while
  keeping existing Redis and memory public imports AsyncQueue-compatible.
- [x] 1.3 Implement Redis and memory event queues with ownership-aware remove,
  fixed-delay release, and expiry pruning.
- [x] 1.4 Run focused backend and timeout tests.

## 2. Listener worker and runtime

- [x] 2.1 Write failing tests for decorator registration, optional filters,
  sync bridge, async invocation, cursor rotation, and all outcomes.
- [x] 2.2 Implement `queue_listener` and the process-local listener registry.
- [x] 2.3 Add `BaseQueueWorker`; preserve `AsyncQueueWorker` task behaviour;
  implement `EventQueueWorker` dispatch, settlement, delay, expiry, and logs.
- [x] 2.4 Run focused listener and worker tests.

## 3. Django integration and verification

- [x] 3.1 Write failing tests for automatic startup, one shared loop, one task
  per configured event queue, no task-worker startup, and invalid metadata.
- [x] 3.2 Implement process-wide `EventRuntime` and connect it to Django's
first-request signal for configured event queues.
- [x] 3.3 Add memory and Redis end-to-end tests for claims, expiry, all-pass
  delay, rejection, exception logs, and local-memory scope.
- [x] 3.4 Document outcomes, lifetime, backend scope, and ordering limits; run
  full tests, `ruff format`, `ruff check`, `ty check`, and OpenSpec validation.
- [x] 3.5 Keep event-worker ownership stable across runtime recovery and keep
  direct-dequeue ownership private.

## 4. Follow-up: provider composition and transport-aware delivery

- [x] 4.1 Replace the universal provider inheritance hierarchy with a minimal
  `QueueProvider` lifecycle contract containing only asynchronous closure;
  remove universal Redis-style ownership and clock operations and their queue
  forwarding hooks while retaining queue-owned provider methods for transport
  workers.
- [x] 4.2 Make `AsyncQueue` and `EventQueue` concrete semantic facades that
  compose a provider, retaining only producer, reader, and administration APIs.
- [x] 4.3 Introduce backend-specific default worker classes and provider
  delivery sessions; move Redis claim, renewal, acknowledgement, settlement,
  recovery, retention, and observer transport behaviour into Redis workers and
  provider internals.
- [x] 4.4 Adapt memory backends and workers to the composition boundary without
  requiring Redis delivery semantics.
- [x] 4.5 Update configured-queue construction, Django runtime startup,
  imports, documentation, and tests for backend-selected worker overrides.
- [x] 4.6 Add focused behavioural tests for the public queue surface,
  transport-specific worker ownership, Redis delivery, and a minimal
  non-Redis-provider contract; run the full validation suite and a fresh review
  cycle.
- [x] 4.7 Make common async and event workers provider-agnostic orchestration
  layers; add memory-specific default workers and move all memory and Redis
  delivery access into their respective worker implementations.
