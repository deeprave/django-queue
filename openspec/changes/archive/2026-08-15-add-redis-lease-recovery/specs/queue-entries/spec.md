## MODIFIED Requirements

### Requirement: Record entry lifecycle outcomes
The system SHALL represent lifecycle status with a string enum and SHALL
transition an entry from `queued` to `running`, then from `running` to exactly
one terminal status of `succeeded`, `failed`, `cancelled`, or `timeout`.
Reliable-delivery recovery MAY return an abandoned `running` entry to `queued`
before retrying it. Terminal statuses SHALL have no valid successor. The system MUST set
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

#### Scenario: Recover an abandoned running entry
- **WHEN** reliable-delivery recovery reclaims an expired running entry
- **THEN** it resets the entry to `queued` and clears its execution timestamps
- **AND** its next worker attempt records a new `dispatched_at` timestamp
