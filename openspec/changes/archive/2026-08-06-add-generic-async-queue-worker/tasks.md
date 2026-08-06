## 1. Package baseline and public contracts

- [x] 1.1 Update package metadata, classifiers, documentation, and CI support to
  require Python 3.14 or later.
- [x] 1.2 Define and export the entry-oriented public API without removing the
  existing raw queue methods.
- [x] 1.3 Add test fixtures and helpers for deterministic UUID, clock, and
  Redis-time behavior.

## 2. Queue entry and clock foundation

- [x] 2.1 Write failing unit tests for immutable `QueueEntry` construction,
  JSON round trips, required fields, and invalid payload rejection.
- [x] 2.2 Implement the frozen, slotted `QueueEntry` model, UUIDv7 ID
  generation, JSON conversion, and JSON-serialisability validation.
- [x] 2.3 Write failing unit tests for local UTC fallback time and the
  Redis-time/monotonic-clock cache refresh interval.
- [x] 2.4 Implement the queue-clock abstraction and Redis-aligned cached clock
  with a maximum 600-second refresh interval.

## 3. Entry-oriented backend operations

- [x] 3.1 Write failing backend contract tests for enqueue, ID lookup,
  non-blocking dequeue, and queued/running/succeeded/failed/cancelled lifecycle
  updates.
- [x] 3.2 Extend `BaseQueue` with entry-oriented abstract operations and
  preserve the current raw-value methods for compatibility.
- [x] 3.3 Implement and test in-memory entry storage, pending order, and
  lifecycle transitions.
- [x] 3.4 Implement and test Redis entry records, pending-item keys, atomic
  removal from the pending structure, and Redis-aligned lifecycle timestamps.
- [x] 3.5 Verify entry-oriented behavior for Redis queue variants that inherit
  queue behavior, including JSON, stack, and priority variants; document any
  intentionally unsupported variant.

## 4. Asynchronous worker

- [x] 4.1 Write failing async tests for successful dispatch, idle waiting,
  handler failure, event-loop responsiveness, and cancellation.
- [x] 4.2 Implement handler registration and `AsyncQueueWorker` sequential
  dispatch using `asyncio.to_thread` for synchronous queue operations.
- [x] 4.3 Implement lifecycle recording for handler results and structured
  failures, including validation of JSON-serialisable handler results.
- [x] 4.4 Implement cooperative cancellation so the worker stops taking new
  entries, allows an active handler its configured grace period before
  cancellation, clears its `running` state in `finally`, and propagates
  cancellation.
- [x] 4.5 Document the best-effort delivery guarantee and the loss window after
  dequeue but before terminal outcome persistence.

## 5. Verification and handoff

- [x] 5.1 Run the complete test suite on Python 3.14 and fix regressions while
  retaining the existing raw queue API behavior.
- [x] 5.2 Add API usage examples for enqueueing a JSON-serialisable value,
  looking up its `QueueEntry`, and running/cancelling a worker.
- [x] 5.3 Review the implementation against the `queue-entries` and
  `async-queue-workers` OpenSpec scenarios and record deferred observability
  decisions for a follow-up change.
