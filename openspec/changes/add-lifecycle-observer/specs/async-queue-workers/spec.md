## ADDED Requirements

### Requirement: Publish lifecycle observations
The asynchronous worker SHALL publish a best-effort lifecycle observation after
it receives an already persisted queued entry and after it confirms running or
terminal states have been recorded. It MUST NOT publish an observation before
the relevant state is stored, and it MUST NOT change queue delivery or
terminal-persistence behaviour because publication fails.

#### Scenario: Publish queued, then recorded transitions
- **WHEN** a worker receives a persisted queued entry and successfully records
  it as running and then terminal
- **THEN** it publishes queued, running, and terminal lifecycle observations in
  that order

#### Scenario: Publication failure after terminal persistence
- **WHEN** a worker cannot publish a completion observation after recording an
  entry's terminal outcome
- **THEN** it logs the publication failure and continues according to its
  existing worker failure policy
