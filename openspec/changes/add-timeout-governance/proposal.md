## Why

A handler that never returns stalls its worker permanently, because dispatch is
sequential per alias and nothing bounds handler execution. The one existing
timeout, the shutdown grace period, records its expiry as `cancelled`, so a
handler that ignored cancellation is indistinguishable from an orderly shutdown.
There is no way to say how long a piece of work may take, and no way for work
that is still making progress to say so.

## What Changes

- Add a `timeout` terminal entry status, distinct from `cancelled`, for work the
  worker abandoned because it exceeded its time budget.
- Route shutdown grace-period expiry to `timeout` rather than `cancelled`, so
  `cancelled` means only a deliberate, orderly stop.
- Bound normal dispatch with a per-entry execution budget, resolved from the
  worker override, then the entry's own budget, then the queue default, then
  600 seconds.
- Accept an optional `timeout_seconds` keyword on `enqueue`, carried on the
  entry and persisted with it.
- Provide a heartbeat call a handler makes to extend its budget while it is
  still making progress, so long but live work is not killed.
- Count `timeout` outcomes separately in worker snapshots and structured logs.

## Capabilities

### New Capabilities

- `timeout-governance`: Resolve, enforce, and extend a per-entry execution
  budget, and record its expiry as a distinct terminal outcome.

### Modified Capabilities

- `queue-entries`: Add the `timeout` terminal status and an optional
  `timeout_seconds` execution budget to the entry record and its durable
  representation.
- `async-queue-workers`: Bound handler execution by the resolved budget, and
  record grace-period expiry as `timeout` rather than `cancelled`.
- `worker-observability`: Count timeout outcomes separately.

## Impact

Adds a status to the entry lifecycle, a field to the entry record and its wire
format, `mark_timed_out` to the backend contract, a `timeout_seconds` keyword to
`enqueue`, a `TIMEOUT` queue setting, a worker-level override, and a public
heartbeat call.

This change assumes `adopt-clock-time` archives before it, since the entry and
worker requirements it modifies are the ones that change carries forward, and
`add-worker-observability` before that.

This package has no released consumers and no stored entries to preserve. There
is no legacy behaviour, no migration, no rolling-upgrade ordering, and no wire
compatibility to maintain: the entry format, the status set, and the backend
contract are all changed outright.

Durations introduced here — the execution budget, the queue default, the grace
period — are plain counts of seconds, not instants.
