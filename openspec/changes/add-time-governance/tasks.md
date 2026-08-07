## 1. One time representation everywhere

- [ ] 1.1 Declare a `ClockTime` alias for a float count of seconds since the Unix
  epoch in `django_queue.clock`, documenting the unit and that it names an
  instant rather than a duration.
- [ ] 1.2 Add failing tests that a clock reports a `ClockTime`, for both the
  local and the Redis-aligned clock, and that the Redis clock derives it from
  `TIME`'s seconds and microseconds by arithmetic without an intermediate date
  or string.
- [ ] 1.3 Change the clock protocol and both implementations to return
  `ClockTime`, replacing the datetime and timedelta offset arithmetic.
- [ ] 1.4 Add failing tests that entry lifecycle timestamps are `ClockTime` in
  memory, are stored as the identical value, and round-trip exactly.
- [ ] 1.5 Change the entry record's lifecycle fields to `ClockTime` and drop
  their wire conversion, since stored and in-memory forms are now the same.
- [ ] 1.6 Add a failing test that the worker snapshot and its structured log
  records report the run start time as `ClockTime`, then change them.
- [ ] 1.7 Give `FixedClock` a fixed `ClockTime`, add a shared constant for it
  beside `FIXED_UUID7`, and update the tests that assert lifecycle timestamps
  against a `datetime`.
- [ ] 1.8 Sweep for any remaining instant that is still a datetime or an ISO
  string, annotate every instant with `ClockTime`, and confirm durations — the
  execution budget, grace period, refresh interval and drift tolerance — were
  left as plain second counts.

## 2. Entry lifecycle and durable record

- [ ] 2.1 Add failing tests for the `timeout` status: `running` to `timeout` is
  permitted, `timeout` has no successor, and restoring an entry with an
  unrecognised status is rejected.
- [ ] 2.2 Add `TIMEOUT` to `QueueEntryStatus` and its transition table.
- [ ] 2.3 Add failing tests for a `timeout` field on the entry record and its
  JSON round trip, with and without a budget set.
- [ ] 2.4 Add the `timeout` field to `QueueEntry` and to `to_dict`/`from_dict`.

## 3. Backend contract

- [ ] 3.1 Add failing tests for `mark_timed_out` on the memory and Redis
  entry-oriented backends, covering the transition and `finished_at`.
- [ ] 3.2 Add `mark_timed_out` to `BaseQueue` and implement it on both backends.
- [ ] 3.3 Add failing tests for the `timeout` keyword on `enqueue` persisting the
  budget, and for `add` rejecting it.
- [ ] 3.4 Accept and persist the `timeout` keyword on `enqueue` for both
  backends.

## 4. Budget resolution

- [ ] 4.1 Add failing tests for the resolution order — worker override, entry
  budget, queue default, 600 seconds — and for rejecting a non-positive or
  non-numeric budget with an alias-specific error.
- [ ] 4.2 Add the queue `TIMEOUT` setting to the configured queue registry,
  validated at settings initialisation alongside the existing extension keys.
- [ ] 4.3 Add the worker-level budget override and implement resolution.

## 5. Enforcement and heartbeat

- [ ] 5.1 Add failing tests for a hung handler being abandoned as `timeout` while
  the worker continues to the next entry, and for a handler within its budget
  being left alone.
- [ ] 5.2 Bound handler dispatch with `asyncio.timeout` using the resolved
  budget, cancelling the handler and recording `timeout` on expiry.
- [ ] 5.3 Add failing tests for the heartbeat: extending from the handler
  coroutine, extending from a delegated worker thread, and raising outside a
  dispatch.
- [ ] 5.4 Publish the active dispatch's extension callback in a `ContextVar` and
  implement the public `heartbeat()` call, rescheduling through the loop.
- [ ] 5.5 Add a failing test that shutdown grace-period expiry now records
  `timeout`, and route `_finish_cancellation` there.

## 6. Observability

- [ ] 6.1 Add failing tests for a `timed_out_count` on the worker snapshot and in
  the structured terminal record, and that a timeout leaves `cancelled_count`
  unchanged.
- [ ] 6.2 Add the counter to the worker, `WorkerSnapshot`, and the
  `queue_worker_` log extras.

## 7. Shared time basis

- [ ] 7.1 Add failing tests that a queue exposes the clock it timestamps entries
  with, and that a worker created by a queue whose clock is offset from local UTC
  records a run start time on that clock rather than local UTC.
- [ ] 7.2 Add a public `clock` accessor to `BaseQueue` over the private attribute
  both backends already hold.
- [ ] 7.3 Accept an optional clock on `AsyncQueueWorker`, defaulting to
  `LocalQueueClock`, use it for the run start time, and pass the queue's clock
  from `create_worker`.
- [ ] 7.4 Add a failing test that a worker's run start time never follows the
  `dispatched_at` of an entry it dispatched, on a queue with a skewed clock.

## 8. Documentation and validation

- [ ] 8.1 Document the budget, its resolution order, the `TIMEOUT` setting, the
  `timeout` enqueue keyword, the float epoch-seconds time representation and how
  to convert it at the edge, and the
  heartbeat call in the README, including the backend contract additions, and
  correct the README's statement that the run start time is local UTC process
  metadata.
- [ ] 8.2 Run Ruff, the full pytest suite repeatedly to confirm the new timing
  paths are not flaky, and strict OpenSpec validation.
