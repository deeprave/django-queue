## ADDED Requirements

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
