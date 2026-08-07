## 1. Completion notification transports

- [ ] 1.1 Add immutable completion-notification values, intrinsic Redis key and
  channel helpers, Redis-backed publication, and in-memory broker publication
  with bounded backend-local TTL retention.
- [ ] 1.2 Add focused tests for Redis and memory terminal publication,
  retained-record catch-up, and publication failure isolation.

## 2. Django-process listener runtime

- [ ] 2.1 Implement lazy, idempotent local runtime startup, in-memory broker
  dispatch, and endpoint discovery for configured Redis-backed queues.
- [ ] 2.2 Implement listener registration, unsubscribe, callback dispatch, and
  callback-failure logging with focused tests.

## 3. Worker integration and documentation

- [ ] 3.1 Publish only confirmed terminal outcomes from `AsyncQueueWorker`
  without changing existing terminal-persistence semantics.
- [ ] 3.2 Add end-to-end Redis and memory tests from queue through worker,
  listener, and callback; update public documentation with backend scope,
  best-effort, and threading limitations.
- [ ] 3.3 Run the full test suite and OpenSpec validation.
