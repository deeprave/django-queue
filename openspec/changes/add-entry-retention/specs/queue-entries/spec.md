## MODIFIED Requirements

### Requirement: Record entry lifecycle outcomes
The system SHALL represent lifecycle status with a string enum and SHALL
transition an entry only from `queued` to `running` or `failed`. A `running`
entry MAY transition back to `queued`, or transition to exactly one completed
status of `succeeded`, `failed`, `cancelled`, or `timeout`. Each completed
status SHALL transition only to `terminated`, and
`terminated` SHALL have no valid successor. The system MUST set `dispatched_at`
when it marks an entry running and MUST set `finished_at` when it records a
completed outcome. A direct `queued` to `failed` transition MUST leave
`dispatched_at` absent. The system SHALL reject any status value outside this
set when restoring an entry from its durable representation.

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
