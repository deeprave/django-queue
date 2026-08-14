## ADDED Requirements

### Requirement: Expire terminal entries
The system SHALL support an optional retention duration for succeeded and failed
entries and MUST NOT expire queued or running entries under that policy.

#### Scenario: Expire a completed entry
- **WHEN** a terminal entry exceeds its configured retention duration
- **THEN** lookup reports that the entry does not exist

### Requirement: Observe AsyncQueue terminal-record removal
When retention cleanup removes an AsyncQueue terminal entry, the system SHALL
publish an immutable observer-only copy of that entry with state `terminated`
before deleting the durable record. The `terminated` state SHALL NOT be stored
in a `QueueEntry` or added to the durable queue-entry lifecycle.

#### Scenario: Prune a retained AsyncQueue entry
- **WHEN** retention cleanup prunes a succeeded or failed AsyncQueue entry
- **THEN** its observers receive a `terminated` copy of the entry before later
  lookup reports that the entry does not exist

#### Scenario: Observe a retained entry before pruning
- **WHEN** an AsyncQueue observer receives normal queued, running, succeeded,
  or failed snapshots
- **THEN** each snapshot remains a durable `QueueEntry` with no `terminated`
  lifecycle state
