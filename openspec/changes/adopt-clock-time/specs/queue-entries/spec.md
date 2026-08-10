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
yield timestamps equal to those it was stored with. A lifecycle timestamp that
is not a `ClockTime` MUST be rejected when the record is constructed, rather
than on use. Restoring a record whose stored value cannot be read back MUST fail
with one error identifying the field at fault, whichever way that value is
wrong, and MUST preserve the underlying cause.

An entry SHALL report how long it waited before dispatch and how long its
handler ran, each as a count of seconds derived from the instants the entry
already holds, carrying the microseconds those instants hold rather than a whole
number of seconds, since work often completes in well under one. A duration that
its instants cannot describe SHALL be reported as
absent — both before the instants exist, and when they contradict, since a clock
that has moved backwards can leave the later instant before the earlier one and
a negative elapsed time has no meaning. These durations SHALL NOT be stored, so
a record cannot disagree with itself and any entry gains them without being
rewritten.

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
