# Async Queue Workers

## Purpose

Define the generic asynchronous worker contract for dispatching identified
entries from named queues.
## Requirements
### Requirement: Dispatch registered queue handlers asynchronously
The system SHALL provide an asynchronous worker that dispatches entries from
named queues to registered asynchronous handlers. The worker MUST mark an entry
running before awaiting its handler and MUST record the handler's result or
failure as the entry's terminal outcome.

#### Scenario: Dispatch a queued entry
- **WHEN** a worker has a registered handler and the associated queue contains
  an entry
- **THEN** it invokes the handler with that entry and stores the handler result
  as a successful terminal outcome

### Requirement: Keep queue I/O from blocking the event loop
The asynchronous worker SHALL await queue operations directly and SHALL await
handlers directly. It MUST NOT dispatch queue operations to a worker thread:
keeping the event loop free is the backend's responsibility, discharged by the
backend being asynchronous.

#### Scenario: Run an asynchronous queue backend
- **WHEN** a worker dequeues or updates an entry
- **THEN** it awaits the backend operation, and other tasks on the worker's
  event loop continue to run while that operation is outstanding

#### Scenario: Dispatch without a worker thread
- **WHEN** a worker completes a full dispatch, from dequeue through the terminal
  outcome
- **THEN** no part of that dispatch is executed on a thread other than the one
  running the event loop

### Requirement: Stop workers cooperatively on cancellation
The asynchronous worker SHALL continue dispatching until cancelled. On
`asyncio.CancelledError`, it MUST stop accepting new entries, allow an active
handler its configured grace period, then cancel it if necessary, set its
`running` state to `False`, and propagate cancellation to its caller. A handler
that completes within its grace period SHALL have its entry recorded by its own
outcome, `succeeded` or `failed`, since it finished and its result is real; a
handler that must be cancelled because the grace period expired SHALL have its
entry recorded as `timeout`.

#### Scenario: Cancel an idle worker
- **WHEN** a caller cancels a running worker while no entry is being handled
- **THEN** the worker completes cancellation, reports `running` as `False`, and
  does not dispatch another entry

#### Scenario: Cancel a worker whose handler finishes in time
- **WHEN** a caller cancels a worker and its active handler returns within the
  configured grace period
- **THEN** the worker records that entry as `succeeded` with its result, rather
  than discarding the result because a shutdown was in progress

#### Scenario: Cancel an active handler after its grace period
- **WHEN** a caller cancels a worker while a handler is active beyond its
  configured grace period
- **THEN** the worker cancels that handler, records its entry as `timeout`,
  reports `running` as `False`, and does not dispatch another entry

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
- **THEN** the entry is no longer available as a pending item before its handler
  starts
- **AND** a process failure after removal can lose that entry

#### Scenario: Dispatch on a Redis reliable-delivery backend
- **WHEN** a worker serves a Redis queue with claim-lease support
- **THEN** it claims an entry before dispatching it
- **AND** recovery can return an expired unacknowledged claim to pending work
- **AND** the entry may execute more than once

### Requirement: Continue after terminal outcome persistence failures
The worker SHALL log a failure to persist a terminal outcome and continue
dispatching later entries. If it can still retrieve the affected entry in the
`running` state, it SHALL make one best-effort attempt to store a safe
`QueuePersistenceError` failure outcome. If it cannot confirm either terminal
outcome, it SHALL stop and raise `QueuePersistenceError` rather than dispatch
another entry.

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

### Requirement: Activate configured worker types per queue
The `runqueues` command SHALL ask each configured queue to resolve its `WORKER`
at startup, without constructing it. A queue without `WORKER` SHALL use its
concrete backend's selected compatible default worker. When the command
observes pending work for an alias, it SHALL request one worker from that queue
and start its dispatch loop. An activated worker retains its normal lifecycle
until cancellation or failure.

#### Scenario: Run a specialised configured worker
- **WHEN** a handler-configured queue declares a valid specialised worker class
  and its queue receives pending work
- **THEN** `runqueues` starts that class for the queue alias

#### Scenario: Leave an idle command queue without a worker
- **WHEN** `runqueues` starts with a handler-configured queue that has no
  pending work
- **THEN** it resolves the queue's worker class without constructing it

#### Scenario: Reject an invalid configured worker
- **WHEN** a handler-configured queue declares an invalid worker extension
- **THEN** `runqueues` exits non-zero before starting any configured worker

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
