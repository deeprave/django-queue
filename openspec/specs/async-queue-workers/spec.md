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
The asynchronous worker SHALL execute synchronous queue operations outside the
event loop and SHALL await handlers directly.

#### Scenario: Run a synchronous queue backend
- **WHEN** a worker dequeues or updates an entry through a synchronous backend
- **THEN** the backend operation does not block other tasks on the worker's
  event loop

### Requirement: Stop workers cooperatively on cancellation
The asynchronous worker SHALL continue dispatching until cancelled. On
`asyncio.CancelledError`, it MUST stop accepting new entries, allow an active
handler its configured grace period, then cancel it if necessary, set its
`running` state to `False`, and propagate cancellation to its caller.

#### Scenario: Cancel an idle worker
- **WHEN** a caller cancels a running worker while no entry is being handled
- **THEN** the worker completes cancellation, reports `running` as `False`, and
  does not dispatch another entry

#### Scenario: Cancel an active handler after its grace period
- **WHEN** a caller cancels a worker while a handler is active beyond its
  configured grace period
- **THEN** the worker cancels that handler, records its entry as `cancelled`,
  reports `running` as `False`, and does not dispatch another entry

### Requirement: Provide explicit best-effort delivery semantics
The worker SHALL remove a pending entry before invoking its handler and SHALL
document that a process failure after removal can lose that entry. It MUST NOT
claim at-least-once delivery in this change.

#### Scenario: Entry is removed before dispatch
- **WHEN** a worker obtains an entry for dispatch
- **THEN** the entry is no longer available as a pending item before its handler
  starts

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

### Requirement: Activate configured worker types per queue
The `runqueues` command SHALL ask each configured queue to resolve its `WORKER`
at startup, without constructing it. A queue without `WORKER` SHALL inherit
`AsyncQueueWorker` from `BaseQueue`. When the command observes pending work for
an alias, it SHALL request one worker from that queue and start its dispatch
loop. An activated worker retains its normal lifecycle until cancellation or
failure.

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

### Requirement: Start ASGI workers lazily per local queue activity
The ASGI worker wrapper SHALL observe enqueue activity for its configured
aliases and construct at most one resolved worker per alias for its lifespan.
It MUST NOT construct workers for idle aliases at lifespan startup. The local
enqueue observation SHALL NOT claim to wake an ASGI worker for work added by a
different process.

#### Scenario: Leave an idle ASGI queue without a worker
- **WHEN** the ASGI lifespan starts and a configured queue receives no local
  enqueue activity
- **THEN** the wrapper does not construct a worker for that alias

#### Scenario: Start a worker after local enqueue
- **WHEN** a configured queue receives its first local enqueue during an ASGI
  lifespan
- **THEN** the wrapper starts that alias's resolved worker and does not create a
  second worker for later local enqueues
