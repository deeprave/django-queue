## ADDED Requirements

### Requirement: List retained entry snapshots
Each AsyncQueue backend SHALL provide synchronous and asynchronous operations
that return its currently retained immutable QueueEntry snapshots for observer
bootstrap. The operations SHALL return queued, running, and terminal entries.

#### Scenario: List an AsyncQueue's retained entries
- **WHEN** an observer runtime requests the snapshots for an AsyncQueue
- **THEN** it receives every retained entry snapshot in that queue
