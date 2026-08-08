## MODIFIED Requirements

### Requirement: Use queue-authoritative lifecycle time
Redis-backed queues SHALL source lifecycle timestamps from a Redis-aligned UTC
clock. The clock MUST obtain an initial Redis time calibration, calculate interim
timestamps from local UTC plus the cached Redis-to-local offset, and start no
more than one background refresh every 600 seconds. Redis unavailability and a
Redis/local UTC drift greater than 180 seconds MUST make initial calibration
fail clearly. After an initial calibration, a failed background refresh SHALL
retain the last good offset and retry no earlier than the next refresh interval.
Non-Redis queues SHALL document their use of local UTC fallback time.

A clock SHALL report the current instant as a `ClockTime`, and a Redis-aligned
clock SHALL build it from the whole second and microsecond counts the server
reports, without constructing an intermediate datetime or string. Its
calibration offset SHALL be a count of seconds. A queue SHALL expose its clock,
so a component recording times alongside that queue's entries can use the same
basis rather than local time.

#### Scenario: Timestamp entries without repeated Redis time calls
- **WHEN** a Redis queue creates two lifecycle timestamps within the configured
  refresh interval
- **THEN** it derives the later timestamp from its existing Redis calibration
  without issuing a second Redis `TIME` command

#### Scenario: Refresh Redis time without delaying timestamping
- **WHEN** a Redis calibration reaches its refresh interval
- **THEN** the queue returns timestamps using its current offset while one
  background refresh obtains the next Redis calibration

#### Scenario: Read a Redis-aligned instant without an intermediate form
- **WHEN** a Redis-aligned clock reports the current instant
- **THEN** it builds that instant from the second and microsecond integers the
  server reports, without constructing a datetime or a string on the way

#### Scenario: Share a queue's clock with a component it creates
- **WHEN** a component asks a queue for its clock
- **THEN** it receives the clock that queue timestamps its entries with

### Requirement: Expose immutable entry records
The system SHALL represent entries in Python as frozen value objects and SHALL
serialise them as JSON objects for durable storage. Entry records MUST contain
`id`, `queue`, `status`, `queued_at`, `dispatched_at`, `finished_at`, `payload`,
`result`, and `error` fields.

Lifecycle timestamps SHALL be held as `ClockTime` values and stored as a float
count of seconds since the Unix epoch. The durable representation MUST NOT
encode a timezone or require string parsing to read, and restoring an entry MUST
yield timestamps equal to those it was stored with.

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
