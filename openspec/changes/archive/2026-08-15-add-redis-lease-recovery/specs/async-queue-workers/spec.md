## MODIFIED Requirements

### Requirement: Provide delivery semantics by backend capability
The worker SHALL remove a pending entry before invoking its handler. A backend
without claim-lease support SHALL provide best-effort delivery and document
that a process failure after removal can lose that entry.

Redis queues with claim-lease support SHALL provide at-least-once delivery: a
worker claims an entry before dispatch, expired unacknowledged claims become
pending again, and a recovered entry can execute more than once. The system
MUST document that handlers which make external changes need idempotent
effects.

#### Scenario: Dispatch on a best-effort backend
- **WHEN** a worker obtains an entry from a backend without claim-lease support
- **THEN** the entry is no longer available as pending work before its handler
  starts
- **AND** a process failure after removal can lose that entry

#### Scenario: Dispatch on a Redis reliable-delivery backend
- **WHEN** a worker serves a Redis queue with claim-lease support
- **THEN** it claims an entry before dispatching it
- **AND** recovery can return an expired unacknowledged claim to pending work
- **AND** the entry may execute more than once

### Requirement: Continue after terminal outcome persistence failures
The worker SHALL log an infrastructure failure to persist a terminal outcome
and continue dispatching later entries. If it can still retrieve the affected
entry in the `running` state, it SHALL make one best-effort attempt to store a
safe `QueuePersistenceError` failure outcome. If it cannot confirm either
terminal outcome, it SHALL stop and raise `QueuePersistenceError` rather than
dispatch another entry.

Loss of an owned reliable-delivery claim is not an infrastructure persistence
failure: the worker SHALL stop handling that entry without recording an
outcome, and MAY continue dispatching later entries. Recovery or the worker
that acquired the claim owns the retry and its terminal outcome.

#### Scenario: Continue after an outcome persistence failure
- **WHEN** a backend rejects a terminal outcome for an entry that remains
  `running`
- **THEN** the worker logs the failure, records a safe `QueuePersistenceError`
  outcome when possible, and continues dispatching later entries

#### Scenario: Stop after an unrecoverable outcome persistence failure
- **WHEN** neither the requested terminal outcome nor a safe persistence-failure
  outcome can be recorded
- **THEN** the worker raises `QueuePersistenceError` and does not dispatch
  another entry

#### Scenario: Hand off a lost claim
- **WHEN** a reliable-delivery worker loses ownership before it can settle an
  entry's outcome
- **THEN** it stops handling that entry without recording a terminal outcome
- **AND** it may continue dispatching later entries while recovery or the new
  owner handles the retry
