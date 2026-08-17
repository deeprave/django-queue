## MODIFIED Requirements

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
