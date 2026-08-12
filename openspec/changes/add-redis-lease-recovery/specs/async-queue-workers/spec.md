## MODIFIED Requirements

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
