## ADDED Requirements

### Requirement: Dispatch tracked entries in priority order on a priority backend
A backend declared as a priority variant SHALL dispatch retained, entry-tracked
work in descending priority order — the highest-priority queued entry SHALL be
the next one an entry-tracked dequeue operation returns, ahead of any
lower-priority entry regardless of enqueue order. Among entries sharing the same
priority, the backend SHALL preserve that variant's existing ordering guarantee
(FIFO for a plain priority queue). This ordering guarantee applies to the
entry-tracked enqueue/dequeue operations that produce and consume `QueueEntry`
records; it does not alter the separate untracked value-only API a caller may
use directly against the same backend.

A non-priority backend SHALL continue to dispatch entries in that backend's
existing order (FIFO or LIFO) and MUST NOT consult an entry's `priority` field.

#### Scenario: Dispatch the higher-priority entry first
- **WHEN** two entries are enqueued on a priority backend through the
  entry-tracked enqueue operation, the lower-priority entry first and the
  higher-priority entry second
- **THEN** an entry-tracked dequeue returns the higher-priority entry before
  the lower-priority one

#### Scenario: Preserve arrival order within equal priority
- **WHEN** two entries of equal priority are enqueued on a priority backend
  through the entry-tracked enqueue operation, in a given order
- **THEN** entry-tracked dequeues return them in that same order

#### Scenario: Track a dispatched priority entry
- **WHEN** an entry enqueued with a priority on a priority backend is
  dequeued through the entry-tracked dequeue operation
- **THEN** the returned entry is a full `QueueEntry` that can be found by its
  identifier and carries its lifecycle transitions, the same as on a
  non-priority backend

#### Scenario: Ignore priority on a non-priority backend
- **WHEN** an entry is enqueued with a non-zero priority on a backend that is
  not a priority variant
- **THEN** the backend dispatches it in that backend's existing FIFO or LIFO
  order, unaffected by its priority value
