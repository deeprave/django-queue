# Queue Entries

## Purpose

Define identified queue entries, their lifecycle, and their timekeeping
contract across generic queue backends.

## Requirements

### Requirement: Enqueue identified JSON-serialisable entries
The system SHALL provide an entry-oriented enqueue operation that accepts any
JSON-serialisable payload value, generates a UUID version 7 identifier, records
the queue namespace and `queued_at` timestamp, persists a `queued` entry, and
returns the generated identifier. The operation MUST reject a payload that does
not survive JSON serialisation before it persists a pending entry.

#### Scenario: Enqueue a JSON value
- **WHEN** a caller enqueues a JSON-serialisable payload on a named queue
- **THEN** the system returns a UUIDv7 identifier and persists an entry with
  `status` equal to `queued` and a non-null `queued_at` timestamp

#### Scenario: Reject a non-JSON payload
- **WHEN** a caller enqueues a value that cannot be JSON serialised
- **THEN** the system raises a serialisation error and does not create a pending
  queue entry

### Requirement: Expose immutable entry records
The system SHALL represent entries in Python as frozen value objects and SHALL
serialise them as JSON objects for durable storage. Entry records MUST contain
`id`, `queue`, `status`, `queued_at`, `dispatched_at`, `finished_at`, `payload`,
`result`, and `error` fields.

#### Scenario: Retrieve a queued entry
- **WHEN** a caller retrieves an entry by an identifier returned from enqueue
- **THEN** the system returns an immutable entry record with the original
  payload and required lifecycle fields

### Requirement: Record entry lifecycle outcomes
The system SHALL represent lifecycle status with a string enum and SHALL
transition an entry only from `queued` to `running`, then from `running` to
exactly one terminal status of `succeeded`, `failed`, or `cancelled`. Terminal
statuses SHALL have no valid successor. The system MUST set `dispatched_at` when
it marks an entry running and MUST set `finished_at` when it records a terminal
outcome.

#### Scenario: Record successful handling
- **WHEN** a worker completes an entry handler with a JSON-serialisable value
- **THEN** the entry is stored with status `succeeded`, its `result` value, and a
  non-null `finished_at` timestamp

#### Scenario: Record failed handling
- **WHEN** a worker handler raises an exception
- **THEN** the entry is stored with status `failed`, a structured error value
  containing only a safe exception class and message, and a non-null
  `finished_at` timestamp

#### Scenario: Record cancelled handling
- **WHEN** a worker cancels an active handler after its grace period
- **THEN** the entry is stored with status `cancelled` and a non-null
  `finished_at` timestamp

### Requirement: Use queue-authoritative lifecycle time
Redis-backed queues SHALL source lifecycle timestamps from a Redis-aligned UTC
clock. The clock MUST obtain an initial Redis time calibration, calculate interim
timestamps from local UTC plus the cached Redis-to-local offset, and start no
more than one background refresh every 600 seconds. Redis unavailability and a
Redis/local UTC drift greater than 180 seconds MUST make initial calibration
fail clearly. After an initial calibration, a failed background refresh SHALL
retain the last good offset and retry no earlier than the next refresh interval.
Non-Redis queues SHALL document their use of local UTC fallback time.

#### Scenario: Timestamp entries without repeated Redis time calls
- **WHEN** a Redis queue creates two lifecycle timestamps within the configured
  refresh interval
- **THEN** it derives the later timestamp from its existing Redis calibration
  without issuing a second Redis `TIME` command

#### Scenario: Refresh Redis time without delaying timestamping
- **WHEN** a Redis calibration reaches its refresh interval
- **THEN** the queue returns timestamps using its current offset while one
  background refresh obtains the next Redis calibration

### Requirement: Construct configured entry subclasses
Each queue backend SHALL create and restore entries with its alias's resolved
`ENTRY_CLASS`. The class MUST extend `QueueEntry`; the resulting value MUST
retain all base entry fields, immutable lifecycle behaviour, and JSON durable
representation. Fields the subclass declares MUST be persisted and restored
alongside the base fields without the subclass overriding any conversion
method. A backend MUST NOT instantiate an entry during settings or queue
construction; it SHALL do so only for enqueue, restore, or lifecycle operations
that require an entry value.

#### Scenario: Enqueue with a custom entry class
- **WHEN** a queue defines a valid `ENTRY_CLASS` subclass and a caller enqueues
  a JSON-serialisable payload
- **THEN** the backend stores and returns that entry subclass with the standard
  queued lifecycle fields

#### Scenario: Persist a field the subclass declares
- **WHEN** a queue's `ENTRY_CLASS` declares a JSON-serialisable field beyond the
  base entry's and an entry is stored and read back
- **THEN** the restored entry carries that field's value, with no conversion
  method overridden on the subclass

#### Scenario: Construct an idle configured queue
- **WHEN** Django initialises a queue with a valid custom `ENTRY_CLASS` but no
  entry operation occurs
- **THEN** the queue is constructed without creating an entry instance

#### Scenario: Restore a custom entry after a lifecycle transition
- **WHEN** a backend retrieves or updates an entry written with its configured
  entry subclass
- **THEN** it restores the configured subclass and preserves its standard
  lifecycle transition semantics
