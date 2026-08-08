## MODIFIED Requirements

### Requirement: Expose immutable entry records
The system SHALL represent entries in Python as frozen value objects and SHALL
serialise them as JSON objects for durable storage. Entry records MUST contain
`id`, `queue`, `status`, `queued_at`, `dispatched_at`, `finished_at`, `payload`,
`result`, `error`, and `timeout` fields. The `timeout` field carries that entry's
execution budget in seconds, or nothing when the entry was enqueued without one.

Lifecycle timestamps SHALL be held as `ClockTime` values and stored as a float
count of seconds since the Unix epoch. The durable representation MUST NOT
encode a timezone or require string parsing to read, and restoring an entry MUST
yield timestamps equal to those it was stored with. An execution budget is a
duration, not an instant, and SHALL remain a plain count of seconds.

#### Scenario: Retrieve a queued entry
- **WHEN** a caller retrieves an entry by an identifier returned from enqueue
- **THEN** the system returns an immutable entry record with the original
  payload and required lifecycle fields

#### Scenario: Store a lifecycle timestamp
- **WHEN** an entry carrying lifecycle timestamps is written to its durable
  representation
- **THEN** each timestamp appears as a float count of seconds since the Unix
  epoch

#### Scenario: Round-trip an entry without losing its instant
- **WHEN** an entry is stored and restored
- **THEN** its restored lifecycle timestamps equal the values it was created
  with, on every backend

#### Scenario: Retrieve an entry enqueued with a budget
- **WHEN** a caller retrieves an entry that was enqueued with an execution
  budget
- **THEN** the returned record and its durable representation both carry that
  budget

### Requirement: Record entry lifecycle outcomes
The system SHALL represent lifecycle status with a string enum and SHALL
transition an entry only from `queued` to `running`, then from `running` to
exactly one terminal status of `succeeded`, `failed`, `cancelled`, or `timeout`.
Terminal statuses SHALL have no valid successor. The system MUST set
`dispatched_at` when it marks an entry running and MUST set `finished_at` when
it records a terminal outcome. The system SHALL reject any status value outside
this set when restoring an entry from its durable representation.

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
- **WHEN** a worker stops an active handler that complies with cancellation
- **THEN** the entry is stored with status `cancelled` and a non-null
  `finished_at` timestamp

#### Scenario: Record a timed-out handling
- **WHEN** a worker abandons a handler that exceeded its execution budget
- **THEN** the entry is stored with status `timeout` and a non-null
  `finished_at` timestamp, and no further transition is permitted
