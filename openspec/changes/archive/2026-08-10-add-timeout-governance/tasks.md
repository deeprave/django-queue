## 1. Entry lifecycle and durable record

- [x] 1.1 Add failing tests for the `timeout` status: `running` to `timeout` is
  permitted, `timeout` has no successor, and restoring an entry with an
  unrecognised status is rejected.
- [x] 1.2 Add `TIMEOUT` to `QueueEntryStatus` and its transition table.
- [x] 1.3 Add failing tests for a `timeout_seconds` field on the entry record and
  its JSON round trip, with and without a budget set.
- [x] 1.4 Add the `timeout_seconds` field to `QueueEntry` and to
  `to_dict`/`from_dict`, validating it as a number or nothing alongside the
  guards already on the other fields.

## 2. Backend contract

- [x] 2.1 Add failing tests for `mark_timed_out` on the memory and Redis
  entry-oriented backends, covering the transition and `finished_at`.
- [x] 2.2 Add `mark_timed_out` to `BaseQueue` and implement it on both backends,
  including the class-level borrow list `MemoryPriorityQueue` uses to take its
  entry methods from `MemoryQueue`.
- [x] 2.3 Add failing tests for the `timeout_seconds` keyword on `enqueue`
  persisting the budget, and for `add` rejecting it.
- [x] 2.4 Accept and persist the `timeout_seconds` keyword on `enqueue` for both
  backends.

## 3. Budget resolution

- [x] 3.1 Add failing tests for the resolution order — worker override, entry
  budget, queue default, 600 seconds — and for rejecting a non-positive or
  non-numeric budget with an alias-specific error.
- [x] 3.2 Add the queue `TIMEOUT` setting to the configured queue registry:
  validate it in `configure_settings` beside the existing `ENTRY_CLASS` check,
  so a bad budget fails at settings initialisation rather than at first
  dispatch, and pop it in `create_connection` as `WORKER` and `HANDLER` are, so
  it never reaches a backend constructor.
- [x] 3.3 Add the worker-level budget override and implement resolution as
  `resolve_budget`, exposing the queue's configured budget as a public
  `timeout_seconds` attribute set by the registry, as `entry_class` and
  `worker_class` already are.

## 4. Enforcement

- [x] 4.1 Add failing tests for a hung handler being abandoned as `timeout` while
  the worker continues to the next entry, and for a handler within its budget
  being left alone.
- [x] 4.2 Bound handler dispatch with `asyncio.timeout` using the resolved
  budget, cancelling the handler and recording `timeout` on expiry. The budget
  wraps the `asyncio.shield(handler_task)` await in `_dispatch`, and its expiry
  must be distinguishable from the outer cancellation that path already
  handles, which currently re-raises into `_finish_cancellation`.
  The heartbeat tasks that were 4.3 and 4.4 are deliberately not listed here as
  pending, because they are not pending here. A heartbeat extends the loop
  deadline through `Timeout.reschedule`, which mutates a `TimerHandle` and is
  not thread-safe, so it cannot be done across the `to_thread` hops the
  synchronous backends require. The work, its requirement, and its design
  reasoning moved to `refactor-redis-async`, which removes those hops — see its
  tasks 6.1 and 6.2. Leaving them here permanently unchecked would report this
  change as incomplete for work it does not own.
- [x] 4.5 Add a failing test that shutdown grace-period expiry now records
  `timeout`, and change `_finish_cancellation`'s `TimeoutError` branch from
  `queue.mark_cancelled` to `queue.mark_timed_out`. Expiry was `cancelled`'s
  only producer, and a handler that stops when asked has always recorded its own
  outcome rather than `cancelled` — so this leaves the status with no worker
  path. Assert that outcome explicitly in the grace-period test, and state in
  the spec and design that `cancelled` is reserved for a deliberate
  cancellation the queue does not yet offer rather than reachable today.

## 5. Observability

- [x] 5.1 Add failing tests for a `timed_out_count` on the worker snapshot and in
  the structured terminal record, and that a timeout leaves `cancelled_count`
  unchanged.
- [x] 5.2 Add the counter to the worker, `WorkerSnapshot`, and the
  `queue_worker_` log extras, and extend `_record_terminal_outcome`'s match on
  entry status so `TIMEOUT` increments it rather than falling through the
  wildcard that currently returns without counting.

## 6. Confirm the clocks stay separate

- [x] 6.1 Add a test that a timed-out entry's `ran_for`, measured on the queue's
  wall clock, and the budget that expired on the loop's monotonic clock are
  independent — the entry records what happened, the budget decided when to
  stop, and neither is derived from the other.

## 7. Documentation and validation

- [x] 7.1 Document the budget, its resolution order, the `TIMEOUT` setting, the
  `timeout_seconds` enqueue keyword, and the `mark_timed_out` backend contract
  addition in the README. The heartbeat is not documented here: it ships with
  4.3/4.4 in `refactor-redis-async`, and documenting a call that does not
  exist would be worse
  than not mentioning it.
- [x] 7.3 Address independent review. One shared `validate_budget` in
  `entries.py` is now the single definition of a budget, called from the entry
  record, the alias `TIMEOUT` setting, and the worker override, rejecting
  non-numbers with `TypeError` and non-finite or non-positive values with
  `ValueError`. Retain the `asyncio.timeout` context and consult `expired()`, so
  a handler raising `TimeoutError` of its own is recorded `failed` with its error
  rather than `timeout` with none -- at both the budget and the grace period,
  which now uses `asyncio.timeout` rather than `wait_for` for that reason.
  Enforce end to end from the entry-carried and queue-configured budgets, not
  only the worker override. Rename `_abandon_over_budget` to
  `_abandon_unresponsive_handler`, since its second caller has no budget.
  Parametrise the memory entry-lifecycle tests over `MemoryPriorityQueue` so its
  borrowed methods are exercised rather than merely present.
- [x] 7.2 Run Ruff, ty, the full pytest suite repeatedly to confirm the new
  timing paths are not flaky, and strict OpenSpec validation.
