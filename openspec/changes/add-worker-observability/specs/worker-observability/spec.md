## ADDED Requirements

### Requirement: Expose worker identity and health
The worker SHALL expose a stable generated ID and an immutable snapshot of its
running state, active entry, and dispatch outcome counters.

#### Scenario: Inspect a running worker
- **WHEN** an operator reads a worker snapshot during dispatch
- **THEN** it includes the worker ID, running state, and active entry ID
