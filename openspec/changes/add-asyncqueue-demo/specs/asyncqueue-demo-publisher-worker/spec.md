## ADDED Requirements

### Requirement: Generate sample demo tasks
The demo project SHALL provide a management command that independently
publishes several tasks to the Redis AsyncQueue named `demo`. Each generated
task SHALL contain additional metadata and one random message selected from
`man -k .` output.

#### Scenario: Publish sample tasks
- **WHEN** a developer runs the demo publisher command
- **THEN** it creates several `demo` queue entries whose payloads include a
  generated message and additional demo metadata

### Requirement: Start each demo run with an empty queue
Before publishing its random batch, the management command SHALL remove the
existing retained-entry, pending, and claim state for the `demo` queue. It
SHALL NOT attempt to clear Redis Pub/Sub messages, which are transient.

#### Scenario: Start a new demo run
- **WHEN** a developer runs the demo publisher command after a previous
  demo run
- **THEN** its dashboard observer bootstrap contains only entries published by
  the new run

### Requirement: Avoid error simulation
The publisher command and configured Django queue worker SHALL exercise normal
processing and a controlled sample of failed entries. They SHALL NOT simulate
retries, cancellations, or broker failures.

#### Scenario: Run the normal demo cycle
- **WHEN** the publisher command and `runqueues` worker run under ordinary
  Redis availability
- **THEN** it produces and processes demo tasks with one or two deliberately
  failed entries, without retries, cancellations, or broker failures

### Requirement: Show delayed lifecycle transitions
The demo worker SHALL leave each entry queued for a random 10–30 seconds before
marking it running, then leave it running for a random 30–60 seconds before
recording a success or controlled failure.

#### Scenario: Observe a demo task lifecycle
- **WHEN** a generated task is processed
- **THEN** its queued, running, and terminal snapshots are visibly separated
  by the configured random delays

### Requirement: Record interrupted demo handlers as failed
When the configured demo worker stops, it SHALL stop active handler tasks and
record their queue entries with the `failed` terminal state and a termination
error.

#### Scenario: Stop the demo worker during active processing
- **WHEN** a developer stops `manage.py runqueues` while demo handlers are
  running
- **THEN** the interrupted entries appear as `failed` rather than remaining in
  the running state
