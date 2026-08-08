## 1. Clocks report instants

- [ ] 1.1 Add failing tests that a clock reports a `ClockTime`, for both the
  local and the Redis-aligned clock, and that the Redis clock builds it from
  `TIME`'s second and microsecond integers without an intermediate datetime or
  string.
- [ ] 1.2 Change the clock protocol and both implementations to return
  `ClockTime`, replacing the datetime and timedelta calibration arithmetic with
  instant and duration operations, leaving the refresh interval, drift tolerance
  and failure behaviour unchanged.
- [ ] 1.3 Give `FixedClock` a fixed `ClockTime`, and add a shared constant for it
  beside `FIXED_UUID7`.

## 2. Entries hold and store instants

- [ ] 2.1 Add failing tests that entry lifecycle timestamps are `ClockTime` in
  memory, are stored as a float count of seconds, and round-trip to an equal
  value on every backend.
- [ ] 2.2 Change the entry record's lifecycle fields to `ClockTime` and convert
  explicitly in `to_dict` and `from_dict`, removing the ISO string form.
- [ ] 2.3 Update the tests that assert lifecycle timestamps against a `datetime`.

## 3. A worker times itself on its queue's clock

- [ ] 3.1 Add a failing test that the worker snapshot and its structured log
  records report the run start time as a `ClockTime`, then change them.
- [ ] 3.2 Add a failing test that a queue exposes the clock it timestamps entries
  with, then add a public `clock` accessor to `BaseQueue` over the private
  attribute both backends already hold.
- [ ] 3.3 Add a failing test that a worker created by a queue whose clock is
  offset from local UTC records a run start time on that clock, then accept an
  optional clock on `AsyncQueueWorker` defaulting to `LocalQueueClock` and pass
  the queue's clock from `create_worker`.
- [ ] 3.4 Add a failing test that a worker's run start time never follows the
  `dispatched_at` of an entry it dispatched, on a queue with a skewed clock.

## 4. Documentation and validation

- [ ] 4.1 Sweep for any remaining instant that is still a datetime or an ISO
  string, and confirm durations — the grace period, refresh interval and drift
  tolerance — were left as plain second counts.
- [ ] 4.2 Update the README for the new instant type on the clock protocol, the
  entry lifecycle fields and the worker snapshot, the durable float form, and the
  queue `clock` accessor; correct its statement that the run start time is local
  UTC process metadata.
- [ ] 4.3 Run Ruff, ty, the full pytest suite, and strict OpenSpec validation.
