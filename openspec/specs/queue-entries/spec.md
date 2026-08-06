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
