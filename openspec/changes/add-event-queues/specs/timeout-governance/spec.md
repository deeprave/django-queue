## ADDED Requirements

### Requirement: Resolve an event lifetime
For an event queue, `timeout_seconds` SHALL resolve as an explicit entry
value, then queue `TIMEOUT`, then 60 seconds. Each resolved lifetime MUST be
finite and strictly positive. It governs an event only while it is unclaimed;
an active listener is governed by its claim lease instead. An event queue SHALL
NOT accept a task-worker execution-budget override.

#### Scenario: Use the event lifetime default
- **WHEN** an event queue enqueues without an explicit lifetime or queue `TIMEOUT`
- **THEN** the event expires after 60 seconds if it remains unconsumed

#### Scenario: Prefer the explicit lifetime
- **WHEN** an event queue enqueue supplies a positive `timeout_seconds`
- **THEN** expiry uses that value instead of the queue default
