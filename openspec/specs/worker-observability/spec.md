# Worker Observability

## Purpose

Identify live workers and expose local worker health, counters, and structured
lifecycle logging without coupling generic queues to a monitoring vendor.

## Requirements

### Requirement: Expose worker identity and health
The worker SHALL generate a stable UUIDv7 ID at construction and expose an
immutable snapshot of its running state, run start time, active entry ID, active
queue name, registered queue aliases, dispatch count, and terminal outcome counters for
succeeded, failed, and cancelled entries. A terminal-outcome counter MUST
advance only after the worker has confirmed the corresponding entry state was
persisted.

#### Scenario: Inspect a running worker
- **WHEN** an operator reads a worker snapshot during dispatch
- **THEN** it includes the worker ID, running state, run start time, active
  entry ID, active queue name, registered queue aliases, dispatch count, and terminal
  outcome counters

#### Scenario: Persist a terminal outcome
- **WHEN** a worker confirms an entry's terminal outcome was recorded
- **THEN** its next snapshot clears the active entry ID and increments exactly
  the corresponding terminal-outcome counter

#### Scenario: Terminal outcome cannot be confirmed
- **WHEN** a worker cannot confirm either the requested terminal outcome or its
  safe persistence-failure outcome
- **THEN** it does not increment a terminal-outcome counter for that entry

### Requirement: Emit structured worker state-change logs
The worker SHALL emit INFO log records when it starts, stops, begins entry
dispatch, and records a terminal outcome. Each record MUST include structured
fields derived from the worker's current immutable snapshot, including the
worker ID, running state, active entry ID, active queue name, registered queue aliases,
dispatch count, and terminal-outcome counters.

#### Scenario: Start a worker
- **WHEN** a worker begins its dispatch loop
- **THEN** it emits a structured start record identifying the worker and its
  initial snapshot values

#### Scenario: Finish an entry
- **WHEN** a worker confirms an entry's terminal outcome
- **THEN** it emits a structured terminal-outcome record containing the updated
  snapshot values

#### Scenario: Stop a worker
- **WHEN** a worker exits its dispatch loop through cancellation or failure
- **THEN** it emits a structured stop record containing its final snapshot
  values
