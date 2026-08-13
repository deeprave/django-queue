## ADDED Requirements

### Requirement: Start configured event queues automatically
During Django application setup, the registry SHALL start or reuse the
process-wide event runtime for every configured event queue and add one
dispatcher task per event queue. Task queues SHALL retain existing startup
behaviour and SHALL NOT start a worker during application setup.

#### Scenario: Initialise an event queue
- **WHEN** Django setup completes with a valid event queue alias
- **THEN** the process-wide runtime has one dispatcher task for that queue

#### Scenario: Initialise task queues only
- **WHEN** Django setup completes with no event queues
- **THEN** it starts no event dispatcher
