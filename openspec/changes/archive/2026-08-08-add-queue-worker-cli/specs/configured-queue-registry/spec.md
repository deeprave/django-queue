## MODIFIED Requirements

### Requirement: Initialise configured queues

The Django app SHALL validate and initialise every `QUEUES` alias idempotently
during application setup without starting a worker. An optional `HANDLER`
dotted import path is command metadata for `runqueues` and MUST NOT be passed
to the queue backend constructor.

#### Scenario: Configure a queue handler

- **WHEN** a valid queue definition includes `HANDLER`
- **THEN** application setup initialises the queue normally and `runqueues` can
  use the handler path to create that queue's worker
