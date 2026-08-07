## Why

Time is handled inconsistently across the queue. A handler that never returns
stalls its worker permanently, because dispatch is sequential per alias and
nothing bounds handler execution. The one existing timeout, the shutdown grace
period, records its expiry as `cancelled`, so a handler that ignored
cancellation is indistinguishable from an orderly shutdown. A worker times
itself on local UTC while its entries are timestamped by the queue's clock, so
elapsed time spanning the two is unsound. And lifecycle timestamps are stored as
ISO strings even though the authoritative source, Redis `TIME`, already returns
epoch integers, so every read and write converts through a datetime and a string
that carry no information the integer did not.

## What Changes

- Add a `timeout` terminal entry status, distinct from `cancelled`, for work the
  worker abandoned because it exceeded its time budget.
- Route shutdown grace-period expiry to `timeout` rather than `cancelled`, so
  `cancelled` means only a deliberate, orderly stop.
- Bound normal dispatch with a per-entry execution budget, resolved from the
  worker override, then the entry's own budget, then the queue default, then
  600 seconds.
- Accept an optional `timeout` keyword on `enqueue`, carried on the entry and
  persisted with it.
- Provide a heartbeat call a handler makes to extend its budget while it is
  still making progress, so long but live work is not killed.
- Count `timeout` outcomes separately in worker snapshots and structured logs.
- Source a worker's run start time from its queue's clock instead of local UTC,
  so worker and entry timestamps share one basis and elapsed time across them is
  meaningful.
- **BREAKING** Represent every instant as a float count of seconds since the Unix
  epoch — the form `time.time()` returns — consistently across the clock
  protocol, entry records, the durable representation, worker snapshots, and
  structured logs, replacing ISO strings on the wire and `datetime` in memory.
  Callers convert at the edge when they want a `datetime` or a string.
- Name that representation as a `ClockTime` type and annotate every instant with
  it, keeping durations such as the execution budget and the grace period as
  plain second counts.

## Capabilities

### New Capabilities

- `timeout-governance`: Resolve, enforce, and extend a per-entry execution
  budget, and record its expiry as a distinct terminal outcome.

### Modified Capabilities

- `queue-entries`: Add the `timeout` terminal status and an optional per-entry
  execution budget to the entry record and its durable representation, represent
  lifecycle timestamps as float epoch seconds in memory and in storage, and
  expose a queue's clock so components timestamping alongside its entries can
  share that basis.
- `async-queue-workers`: Bound handler execution by the resolved budget, and
  record grace-period expiry as `timeout` rather than `cancelled`.
- `worker-observability`: Source the worker's run start time from its queue's
  clock, and count timeout outcomes separately.

## Impact

Adds a status to the entry lifecycle, a field to the entry record and its wire
format, `mark_timed_out` and a public `clock` accessor to the backend contract, a
budget keyword to `enqueue`, a `TIMEOUT` queue setting, a worker-level override,
a worker clock, and a public heartbeat call.

This change assumes `add-worker-observability` archives before it, so the
worker's run start time is already a specified snapshot field by the time this
change modifies where that time comes from.

This package has no released consumers and no stored entries to preserve. There
is no legacy behaviour, no migration, no rolling-upgrade ordering, and no wire
compatibility to maintain: the entry format, the status set, and the backend
contract are all changed outright.
