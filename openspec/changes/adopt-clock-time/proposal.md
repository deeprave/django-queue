## Why

An operator asks how long work waited and how long it took. The queue records
the instants to answer that and answers it nowhere, and until the timestamps
share one basis the answer would not be trustworthy anyway.

`add-clock-time` defines an instant but nothing uses it. Entries still persist
ISO strings and hold `datetime`, clocks still report `datetime`, and a worker
still times itself on local UTC while its entries are timestamped by the queue's
clock — so elapsed time spanning the two is unsound within the 180 seconds of
skew the Redis clock tolerates.

## What Changes

- **BREAKING** Report and hold every instant as a `ClockTime`: the clock
  protocol returns one, entry lifecycle fields hold one, and a worker snapshot
  reports one. A structured log record carries its count of seconds, since a
  record must be serialisable.
- **BREAKING** Store instants as a float count of seconds since the epoch,
  replacing the ISO strings entries persist today.
- Build a Redis-aligned instant from the second and microsecond integers the
  server reports, without an intermediate datetime or string, and express its
  calibration offset as a count of seconds.
- Expose a queue's clock, and give a worker a clock that defaults to local time
  and is supplied by the queue that creates it, so a worker's run start time and
  the entries it dispatches share one basis.
- Report the elapsed time that basis exists to make meaningful: how long an
  entry waited before dispatch, how long its handler ran, and how long a worker
  has been running — derived from the instants already held, and carried in
  snapshots and structured log records.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `queue-entries`: Hold lifecycle timestamps as instants stored as float epoch
  seconds, reject one that is not an instant at construction, report clock time
  as an instant derived without an intermediate form, expose a queue's clock,
  and report how long an entry waited and ran.
- `worker-observability`: Report the run start time as an instant taken from the
  worker's clock, which its queue supplies, and report how long the worker has
  been running.

## Impact

Changes the type of the clock protocol's return, of three entry lifecycle
fields, and of the worker snapshot's run start time; changes the durable form of
those fields from a string to a number; and adds a public `clock` accessor to
the backend contract and an optional clock to the worker.

Grows the public surface by `QueueEntry.queued_for` and `ran_for`, a
`running_for` field on `WorkerSnapshot`, and three `queue_worker_` log extras
carrying those durations. All are derived from instants already held, so none
enters the durable record. A configured `WORKER` subclass that overrides
`__init__` must now accept a `clock` keyword.

This change assumes `add-clock-time` archives before it, since it adopts the
type that change defines, and `add-worker-observability` before that, since it
modifies the run start time that change specifies.

This package has no released consumers and no stored entries to preserve. There
is no legacy behaviour, no migration, no rolling-upgrade ordering, and no wire
compatibility to maintain.
