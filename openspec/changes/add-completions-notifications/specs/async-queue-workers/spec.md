## ADDED Requirements

### Requirement: Publish terminal completion observations
The asynchronous worker SHALL publish a best-effort completion observation
after it confirms that a queue entry has been recorded with a terminal status.
It MUST NOT publish an observation before the terminal outcome is stored, and
it MUST NOT change its queue-delivery or terminal-persistence behaviour because
completion publication fails.

#### Scenario: Publish after recording a terminal outcome
- **WHEN** a worker successfully records an entry as `succeeded`, `failed`, or
  `cancelled`
- **THEN** it publishes a completion observation for that recorded outcome

#### Scenario: Publication failure after terminal persistence
- **WHEN** a worker cannot publish a completion observation after recording an
  entry's terminal outcome
- **THEN** it logs the publication failure and continues according to its
  existing worker failure policy
