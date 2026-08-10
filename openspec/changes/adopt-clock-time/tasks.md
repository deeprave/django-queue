## 1. Clocks report instants

- [x] 1.1 Add failing tests that a clock reports a `ClockTime`, for both the
  local and the Redis-aligned clock, and that the Redis clock builds it from
  `TIME`'s second and microsecond integers without an intermediate datetime or
  string.
- [x] 1.2 Change the clock protocol and both implementations to return
  `ClockTime`, replacing the datetime and timedelta calibration arithmetic with
  instant and duration operations, leaving the refresh interval, drift tolerance
  and failure behaviour unchanged.
- [x] 1.3 Change the `FixedClock` test helper to take and return a `ClockTime`
  rather than a `datetime`, and add a shared constant for its default beside
  `FIXED_UUID7`.

## 2. Entries hold and store instants

- [x] 2.1 Add failing tests that entry lifecycle timestamps are `ClockTime` in
  memory, are stored as a float count of seconds, and round-trip to an equal
  value on every backend.
- [x] 2.2 Change the entry record's lifecycle fields to `ClockTime`, point the
  three `_WIRE_DECODERS` entries at `ClockTime.from_timestamp`, and replace
  `_encode_wire_value`'s `isoformat()` fallthrough with `to_timestamp()`.
- [x] 2.3 Change `QueueEntry.create`'s `queued_at` parameter and its
  `datetime.now(UTC)` fallback, which is the one instant the entry mints
  without going through a queue clock.
- [x] 2.4 Update the tests that assert lifecycle timestamps against a
  `datetime`, in `test_entries.py` and `test_entry_queue.py`.

## 3. A worker times itself on its queue's clock

- [x] 3.1 Add a failing test that the worker snapshot and its structured log
  records report the run start time as a `ClockTime`, then change
  `WorkerSnapshot.started_at`, its assignment in `run`, and the
  `queue_worker_started_at` extra that currently calls `isoformat()`.
- [x] 3.2 Add a failing test that a queue exposes the clock it timestamps entries
  with, then add a public `clock` accessor to `BaseQueue`. Give `BaseQueue` a
  `_clock` default as it does for `_queue_name`, since the attribute is set by
  each entry-capable backend rather than by the base, and a backend that never
  sets one must not raise on access.
- [x] 3.3 Add a failing test that a worker created by a queue whose clock is
  offset from local UTC records a run start time on that clock, then accept an
  optional clock on `AsyncQueueWorker` defaulting to `LocalQueueClock` and pass
  the queue's clock from `create_worker`.
- [x] 3.4 Add a failing test that a worker's run start time never follows the
  `dispatched_at` of an entry it dispatched, on a queue with a skewed clock.

## 4. Review follow-ups

Raised by the collated review of this change and taken before merge.

- [x] 4.1 Add failing tests that a lifecycle timestamp which is not a
  `ClockTime` — a datetime, a bare float, or a null restored from a record — is
  rejected at construction, then validate the three fields in `__post_init__`.
- [x] 4.2 Document that a configured `WORKER` subclass overriding `__init__`
  must accept a `clock` keyword, in the README and on `create_worker`.
- [x] 4.3 Correct the worker-observability delta, which said the run start time
  is reported as a `ClockTime` in structured log records where a record must be
  serialisable; carry the same correction into `add-timeout-governance`.
- [x] 4.4 Assert the `queue_worker_started_at` log extra is a number, drive the
  skew scenario through `create_worker` as its requirement states, and assert
  the default worker clock's type rather than only that it reports an instant.
- [x] 4.5 Assert Redis lifecycle timestamps are `ClockTime` and round-trip
  through the durable form.
- [x] 4.6 Assign the calibration pair by name, and share one fallback clock in
  `entries.py` as `base.py` does.
- [x] 4.7 Correct the Redis clock test's docstring, which claimed to detect an
  intermediate datetime it cannot.

## 5. Report the elapsed time one basis makes meaningful

- [x] 5.1 Add failing tests that an entry reports how long it waited and how
  long it ran, that each is absent until the instants describing it exist, and
  that neither appears in the durable record while surviving a round trip.
- [x] 5.2 Add `queued_for` and `ran_for` to `QueueEntry` as derived properties.
- [x] 5.3 Add a failing test that a snapshot reports how long the worker has
  been running, and that a stopped worker reports how long it ran rather than a
  duration that keeps growing; then add `running_for` to `WorkerSnapshot`,
  measured on the worker's clock, and record a stop instant when the loop exits.
- [x] 5.4 Add a failing test that structured records carry the worker duration
  and that a terminal-outcome record carries the entry's durations, then add
  the `queue_worker_` extras.

## 6. Second review round

Raised by the collated review after the elapsed-time work and taken before merge.

- [x] 6.1 Add a failing test that a background calibration failing on a
  malformed reply still retries at the next interval, then clear `_refreshing`
  in a `finally` and catch broadly, so a dead refresh thread cannot stop the
  clock refreshing for good. Expose `refreshing` so tests stop reading a private
  attribute.
- [x] 6.2 Add failing tests that an entry and a worker report no duration when
  their instants contradict, then route every duration through one helper that
  reports an unanswerable elapsed time as absent; record the decision, and the
  rejection of clamping to zero, in the design and both deltas.
- [x] 6.3 Set the run start before clearing the stop instant, so a restarted
  worker never reports a duration measured from its previous run.
- [x] 6.4 Type-check `id` and `queue` alongside the fields already guarded.
- [x] 6.5 Share one fallback clock in `worker.py`, as `base.py` and
  `entries.py` do.
- [x] 6.6 Document in the README that reading a running worker's snapshot
  samples the queue clock.
- [x] 6.7 Correct the proposal: structured logs carry a count of seconds, and
  Capabilities and Impact name the duration surface the change adds.
- [x] 6.8 Use an async handler in the stopped-worker test.

- [x] 6.9 Normalise `from_dict` decoding failures: catch both error classes the
  decoders raise and re-raise one naming the field, chained to its cause, as
  `validate_json_value` already does for `json.dumps`.

## 7. Third review round

- [x] 7.1 Move the guarded subtraction to `clock.py` and rename it
  `elapsed_time`: it is `ClockTime` arithmetic the worker needs as much as an
  entry does, and the old name read as a whole number of seconds when it has
  always carried microseconds.
- [x] 7.2 Add tests pinning sub-second durations, which every existing duration
  test missed by using whole or half seconds.
- [x] 7.3 Finish the `from_dict` normalisation the spec claims: a missing field
  and a value the record rejects now fail the same way as a value that will not
  decode, naming the field and chaining the cause.
- [x] 7.4 Consolidate the three module-level fallback clocks into one
  `DEFAULT_CLOCK` in `clock.py`, and use it in the two memory backends that
  still allocated their own.
- [x] 7.5 Say in `refreshing`'s docstring that it is advisory and read without
  the calibration lock.
- [x] 7.6 Anchor the malformed-record test's match so a message that stops
  naming the field fails it.

## 8. Documentation and validation

- [x] 8.1 Sweep for any remaining instant that is still a datetime or an ISO
  string, and confirm durations — the grace period, refresh interval and drift
  tolerance — were left as plain second counts.
- [x] 8.2 Update the README for the new instant type on the clock protocol, the
  entry lifecycle fields and the worker snapshot, the durable float form, and the
  queue `clock` accessor; correct its statement that the run start time is local
  UTC process metadata.
- [x] 8.3 Run Ruff, ty, the full pytest suite, and strict OpenSpec validation.
