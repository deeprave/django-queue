## 1. Entry lifecycle and durable record

- [ ] 1.1 Add failing tests for the `timeout` status: `running` to `timeout` is
  permitted, `timeout` has no successor, and restoring an entry with an
  unrecognised status is rejected.
- [ ] 1.2 Add `TIMEOUT` to `QueueEntryStatus` and its transition table.
- [ ] 1.3 Add failing tests for a `timeout` field on the entry record and its
  JSON round trip, with and without a budget set.
- [ ] 1.4 Add the `timeout` field to `QueueEntry` and to `to_dict`/`from_dict`.

## 2. Backend contract

- [ ] 2.1 Add failing tests for `mark_timed_out` on the memory and Redis
  entry-oriented backends, covering the transition and `finished_at`.
- [ ] 2.2 Add `mark_timed_out` to `BaseQueue` and implement it on both backends.
- [ ] 2.3 Add failing tests for the `timeout` keyword on `enqueue` persisting the
  budget, and for `add` rejecting it.
- [ ] 2.4 Accept and persist the `timeout` keyword on `enqueue` for both
  backends.

## 3. Budget resolution

- [ ] 3.1 Add failing tests for the resolution order — worker override, entry
  budget, queue default, 600 seconds — and for rejecting a non-positive or
  non-numeric budget with an alias-specific error.
- [ ] 3.2 Add the queue `TIMEOUT` setting to the configured queue registry,
  validated at settings initialisation alongside the existing extension keys.
- [ ] 3.3 Add the worker-level budget override and implement resolution.

## 4. Enforcement and heartbeat

- [ ] 4.1 Add failing tests for a hung handler being abandoned as `timeout` while
  the worker continues to the next entry, and for a handler within its budget
  being left alone.
- [ ] 4.2 Bound handler dispatch with `asyncio.timeout` using the resolved
  budget, cancelling the handler and recording `timeout` on expiry.
- [ ] 4.3 Add failing tests for the heartbeat: extending from the handler
  coroutine, extending from a delegated worker thread, and raising outside a
  dispatch.
- [ ] 4.4 Publish the active dispatch's extension callback in a `ContextVar` and
  implement the public `heartbeat()` call, rescheduling through the loop.
- [ ] 4.5 Add a failing test that shutdown grace-period expiry now records
  `timeout`, and route `_finish_cancellation` there.

## 5. Observability

- [ ] 5.1 Add failing tests for a `timed_out_count` on the worker snapshot and in
  the structured terminal record, and that a timeout leaves `cancelled_count`
  unchanged.
- [ ] 5.2 Add the counter to the worker, `WorkerSnapshot`, and the
  `queue_worker_` log extras.

## 6. Documentation and validation

- [ ] 6.1 Document the budget, its resolution order, the `TIMEOUT` setting, the
  `timeout` enqueue keyword, and the heartbeat call in the README, including the
  backend contract addition.
- [ ] 6.2 Run Ruff, the full pytest suite repeatedly to confirm the new timing
  paths are not flaky, and strict OpenSpec validation.
