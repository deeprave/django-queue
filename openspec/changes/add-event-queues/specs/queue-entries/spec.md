## MODIFIED Requirements

### Requirement: Record entry lifecycle outcomes

AsyncQueue lifecycle transitions are worker-internal operations. A worker SHALL
record `running`, terminal, and recovery outcomes without exposing public queue
mutation methods for those transitions. `cancelled` remains a valid reserved
terminal status, but no current worker path produces it.

#### Scenario: Record successful handling
- **WHEN** a worker handler returns a result for a running entry
- **THEN** the entry is stored with status `succeeded`, its `result` value, and a
  non-null `finished_at` timestamp

#### Scenario: Record failed handling
- **WHEN** a worker handler raises an exception
- **THEN** the entry is stored with status `failed`, a structured error value
  containing only a safe exception class and message, and a non-null
  `finished_at` timestamp

#### Scenario: Record a failure before handler dispatch
- **WHEN** queue processing detects a validation, transport, or other
  pre-dispatch failure for a queued entry
- **THEN** the entry is stored with status `failed`, a structured error value,
  a non-null `finished_at` timestamp, and no `dispatched_at` timestamp

#### Scenario: Reserve cancelled handling
- **WHEN** a worker-internal lifecycle operation records a running entry as
  cancelled
- **THEN** the entry is stored with status `cancelled` and a non-null
  `finished_at` timestamp

#### Scenario: Record a timed-out handling
- **WHEN** a worker abandons a handler that exceeded its execution budget
- **THEN** the entry is stored with status `timeout` and a non-null
  `finished_at` timestamp, and no further transition is permitted

#### Scenario: Recover an abandoned running entry
- **WHEN** reliable-delivery recovery reclaims an expired running entry
- **THEN** it resets the entry to `queued` and clears its execution timestamps
- **AND** its next worker attempt records a new `dispatched_at` timestamp

## ADDED Requirements

### Requirement: Remove expired event entries
For an event queue, `timeout_seconds` SHALL mean the event's positive lifetime
while it is unclaimed. A consumed, rejected, or expired unclaimed event SHALL
be removed without a task terminal result. Redis and memory backends SHALL
prune expired unclaimed events while receiving and during idle cleanup.

#### Scenario: Expire an unconsumed event
- **WHEN** an unclaimed event remains available for its resolved lifetime
- **THEN** the backend logs and removes it without a terminal entry record

#### Scenario: Reach expiry while claiming
- **WHEN** an unclaimed event reaches its resolved lifetime immediately before a worker claims it
- **THEN** the claim atomically removes the event and does not dispatch it
