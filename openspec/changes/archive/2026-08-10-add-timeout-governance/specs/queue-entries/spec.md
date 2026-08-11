## MODIFIED Requirements

### Requirement: Expose immutable entry records
The system SHALL represent entries in Python as frozen value objects and SHALL
serialise them as JSON objects for durable storage. Entry records MUST contain
`id`, `queue`, `status`, `queued_at`, `dispatched_at`, `finished_at`, `payload`,
`result`, `error`, and `timeout_seconds` fields. The `timeout_seconds` field
carries that entry's execution budget, or nothing when the entry was enqueued
without one. It is named for what it holds: a duration, not the instant at which
the entry expires, which is the one confusion the lifecycle instants beside it
make easy.

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

#### Scenario: Reject a lifecycle timestamp that is not an instant
- **WHEN** an entry is constructed with a lifecycle timestamp that is not a
  `ClockTime`, including a null where one is required
- **THEN** construction fails

#### Scenario: Reject a malformed durable record
- **WHEN** a record is restored whose stored identifier, status or lifecycle
  timestamp cannot be read back, whether because its type or its value is wrong
- **THEN** restoration fails with a single error naming the field, chained to
  the cause

#### Scenario: Reject a record that omits a required field
- **WHEN** a record is restored that has no value at all for a required field
- **THEN** restoration fails the same way, naming the field that is absent

#### Scenario: Report a duration shorter than a second
- **WHEN** an entry's handler ran for a fraction of a second
- **THEN** the reported duration carries that fraction rather than truncating to
  zero

#### Scenario: Report how long an entry waited and ran
- **WHEN** an entry that was dispatched and finished is read
- **THEN** it reports the seconds between being queued and dispatched, and the
  seconds between being dispatched and finishing

#### Scenario: Report no duration before the instants exist
- **WHEN** an entry that has not been dispatched, or has been dispatched but not
  finished, is read
- **THEN** the durations its instants cannot yet describe are absent

#### Scenario: Report no duration when the instants contradict
- **WHEN** an entry holds a later lifecycle instant that precedes an earlier one
- **THEN** the duration between them is absent rather than negative

#### Scenario: Keep durations out of the durable record
- **WHEN** an entry is written to its durable representation
- **THEN** that representation carries only the instants, and a restored entry
  reports the same durations as the entry it was restored from

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
- **WHEN** a caller marks a running entry cancelled through the backend contract
- **THEN** the entry is stored with status `cancelled` and a non-null
  `finished_at` timestamp
- **AND** no worker path produces this status: a handler that finishes during
  shutdown records its own outcome and one that overruns records `timeout`, so
  `cancelled` is reserved for a deliberate cancellation the queue does not yet
  offer

#### Scenario: Record a timed-out handling
- **WHEN** a worker abandons a handler that exceeded its execution budget
- **THEN** the entry is stored with status `timeout` and a non-null
  `finished_at` timestamp, and no further transition is permitted
