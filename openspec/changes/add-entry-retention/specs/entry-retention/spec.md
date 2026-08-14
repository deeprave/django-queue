## ADDED Requirements

### Requirement: Expire terminal entries
The system SHALL support terminal-entry retention through `RETENTION_TIMEOUT`.
It SHALL default to 600 seconds, and explicit `None` SHALL disable automatic
retention. A running worker SHALL remove entries after their terminal timestamp
has aged past that duration. The policy MUST NOT expire queued or running
entries. Failed entries SHALL be eligible whether they failed before or after
entering the running state.

#### Scenario: Expire a completed entry
- **WHEN** a terminal entry exceeds its configured retention duration
- **THEN** lookup reports that the entry does not exist

#### Scenario: Expire a pre-dispatch failure
- **WHEN** an entry fails directly from queued state and exceeds its configured
  retention duration
- **THEN** lookup reports that the entry does not exist

### Requirement: Observe AsyncQueue terminal-record removal
The system SHALL publish an immutable observer-only copy of an AsyncQueue
terminal entry with state `terminated` when scheduled retention cleanup or
explicit pruning deletes its durable record. The `terminated` state SHALL be
the final queue-entry lifecycle state, but its pruning snapshot SHALL NOT be
stored durably.

#### Scenario: Prune a retained AsyncQueue entry
- **WHEN** retention cleanup prunes a succeeded or failed AsyncQueue entry
- **THEN** its observers receive a `terminated` copy of the entry and later
  lookup reports that the entry does not exist

#### Scenario: Explicitly prune a terminal AsyncQueue entry
- **WHEN** a caller invokes `prune_entry` or `aprune_entry` for a terminal
  AsyncQueue entry ID
- **THEN** its observers receive a `terminated` copy and the durable record is
  removed

#### Scenario: Refuse to prune a non-terminal entry
- **WHEN** a caller prunes a queued or running entry by ID
- **THEN** the operation rejects the request and leaves the durable record
  unchanged

#### Scenario: Prune an absent entry
- **WHEN** a caller prunes an entry ID whose retained record does not exist
- **THEN** the operation raises `QueueEntryNotFoundError`

#### Scenario: Observe a retained entry before pruning
- **WHEN** an AsyncQueue observer receives normal queued, running, succeeded,
  or failed snapshots
- **THEN** each snapshot remains a durable `QueueEntry`; `terminated` is
  published only as the final snapshot during pruning
