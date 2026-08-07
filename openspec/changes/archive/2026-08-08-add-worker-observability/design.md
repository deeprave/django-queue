## Context

The worker has lifecycle state but no stable identity or operational snapshot.
It also logs handler and persistence failures, but does not emit a consistent
set of operational lifecycle records.

## Goals / Non-Goals

**Goals:** Supply a generated worker ID, in-process metrics, immutable health
snapshots, and structured log records for worker lifecycle and dispatch-state
changes.

**Non-Goals:** Metrics exporters, a dashboard, distributed liveness consensus,
Django signal delivery, or cross-process event propagation.

## Decisions

### Snapshot is the single local observability model

Each worker gets a UUIDv7 identifier when constructed. It exposes a frozen
`WorkerSnapshot` containing its ID, `running` state, run start time, active
entry ID, active queue name, registered queue aliases, dispatch count, and
terminal-outcome counts for succeeded, failed, and cancelled entries. The
snapshot is local to the worker process and is safe to inspect without mutating
worker state.

Counters advance only after the worker has confirmed persistence of a terminal
outcome. An unrecoverable terminal-persistence failure therefore does not claim
an outcome that the backend did not store. The active entry ID is set after
successful dequeue and cleared after the dispatch reaches a confirmed terminal
outcome or the worker stops.

The run start time is local UTC process metadata. It is not a queue-entry
timestamp: a generic worker can serve multiple queues with independent clocks.
If cancellation interrupts acknowledgement of an in-flight terminal write, the
backend may persist it after the worker stops; the final snapshot intentionally
counts only outcomes that this worker observed before stopping.

### Structured logs are emitted from snapshots

The worker logs at INFO when it starts, stops, begins dispatching an entry, and
records a terminal outcome. Each record carries structured `extra` fields
derived from the same snapshot, including worker ID and the current active
entry, queue identity, and counters. Existing error logs remain diagnostic logs
and are not replaced.

Using snapshot-derived fields avoids separate, drifting logging state. Django
signals were considered but rejected: they are process-local interceptors,
whereas this change is operational observation and must also work when the
worker runs in the standalone `runqueues` process.

## Risks / Trade-offs

- [Snapshot is process-local] → Document that a monitoring process must collect
  or forward snapshots/logs externally.
- [Logging fields can become inconsistent with counters] → Build all lifecycle
  log extras from the current immutable snapshot.
- [A persistence failure leaves an entry running] → Do not increment a terminal
  outcome counter unless the worker confirms a stored terminal entry.
- [Cancellation can interrupt terminal persistence acknowledgement] → Document
  that the final local snapshot can undercount a backend update completed after
  worker shutdown.

## Migration Plan

This is additive and requires no configuration or migration. Existing log
collection continues to work; operators can incrementally index the new
structured fields. Removing the feature removes local observation only and
does not affect queue delivery.
