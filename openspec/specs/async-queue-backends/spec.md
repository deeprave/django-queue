# Async Queue Backends

## Purpose

Define the asynchronous backend contract and its synchronous compatibility
surface.

## Requirements

### Requirement: Present an asynchronous backend contract
Queue backends SHALL expose every operation that performs storage work as an
awaitable method named with an `a` prefix: `aenqueue`, `aget_entry`,
`adequeue_entry`, `ahas_pending_entries`, `amark_running`, `amark_succeeded`,
`amark_failed`, `amark_cancelled`, `amark_timed_out`, `aprune_entry`, and
`aclose`. These
SHALL be the implementations, not wrappers. A backend supporting identified
entry dispatch MUST implement all of them.

#### Scenario: Await an entry operation
- **WHEN** a caller awaits an entry operation on any configured backend
- **THEN** the operation completes without dispatching work to a worker thread

#### Scenario: Implement a custom backend
- **WHEN** an application supplies a queue backend implementing the asynchronous
  methods
- **THEN** a worker dispatches through it without the backend defining any
  synchronous entry method

### Requirement: Keep the synchronous names working for synchronous callers
Each asynchronous operation SHALL have a synchronous counterpart under the name
that operation has today -- `enqueue`, `get_entry`, `close`, and the rest --
delegating to the asynchronous implementation through the framework's
synchronous-to-asynchronous bridge. A synchronous caller SHALL observe the same
behaviour, return value, and exceptions as before this change.

#### Scenario: Enqueue from a synchronous Django view
- **WHEN** synchronous application code calls `enqueue` with a payload and an
  optional budget
- **THEN** it receives the entry identifier, exactly as it did when the backend
  was synchronous

#### Scenario: Refuse a synchronous call from a running event loop
- **WHEN** code already running on an event loop calls a synchronous wrapper
- **THEN** the call raises, directing the caller to await the asynchronous
  method instead of blocking the loop it is running on

### Requirement: Cross between synchronous and asynchronous code through the framework
The package SHALL perform every crossing between synchronous and asynchronous
execution using the bridges the framework provides, and SHALL NOT dispatch to a
thread directly or implement an adaptor of its own.

#### Scenario: Bridge a blocking operation
- **WHEN** an operation blocks by design and must be awaited
- **THEN** it is bridged with the framework's asynchronous adaptor rather than
  by direct thread dispatch

### Requirement: Bind connection resources to the event loop that uses them
A backend holding loop-affine connection resources SHALL acquire them for the
running event loop rather than at construction, so resources created on one
loop are never used from another. Disposal SHALL release the resources
belonging to the loop that disposes them.

#### Scenario: Use one queue from a worker loop and a synchronous caller
- **WHEN** the same configured queue is used by a worker on its event loop and
  by a synchronous caller whose bridge runs on a different loop
- **THEN** each obtains connection resources belonging to its own loop and
  neither observes the other's

#### Scenario: Dispose a queue
- **WHEN** a caller closes a queue
- **THEN** the connection resources for that loop are released and the queue
  can acquire fresh resources if used again

### Requirement: List retained entry snapshots
Each AsyncQueue backend SHALL provide synchronous and asynchronous operations
that return its currently retained immutable QueueEntry snapshots for observer
bootstrap. The operations SHALL return queued, running, and terminal entries.

#### Scenario: List an AsyncQueue's retained entries
- **WHEN** an observer runtime requests the snapshots for an AsyncQueue
- **THEN** it receives every retained entry snapshot in that queue

### Requirement: Prune a retained AsyncQueue entry
`AsyncQueue` SHALL expose `aprune_entry(entry_id)` and its synchronous
counterpart `prune_entry(entry_id)` for removing one retained terminal entry.
`BaseQueue` and `EventQueue` SHALL NOT expose these entry-retention operations.
Scheduled cleanup and explicit pruning SHALL use the same removal behavior.

#### Scenario: Prune from synchronous application code
- **WHEN** synchronous application code prunes one terminal AsyncQueue entry
- **THEN** it observes the same removal and exception behavior as
  `aprune_entry`

### Requirement: Report an identified AsyncQueue entry that does not exist
AsyncQueue entry lookup and explicit pruning SHALL raise
`QueueEntryNotFoundError` when the requested retained entry ID has no durable
record. `QueueEmptyException` SHALL remain reserved for queue-dequeue
operations, and `QueueEntryMissingError` SHALL remain specific to
reliable-delivery claim settlement.

#### Scenario: Look up an absent entry
- **WHEN** a caller retrieves an AsyncQueue entry ID whose retained record does
  not exist
- **THEN** the backend raises `QueueEntryNotFoundError`
