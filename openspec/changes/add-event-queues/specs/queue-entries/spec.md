## ADDED Requirements

### Requirement: Remove expired event entries
For an event queue, `timeout_seconds` SHALL mean the event's positive
unconsumed lifetime. A consumed, rejected, or expired event SHALL be removed
without a task terminal result. Redis and memory backends SHALL prune expired
events while receiving and during idle cleanup.

#### Scenario: Expire an unconsumed event
- **WHEN** an event remains unconsumed for its resolved lifetime
- **THEN** the backend logs and removes it without a terminal entry record
