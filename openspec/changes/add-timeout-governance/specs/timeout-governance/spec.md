## ADDED Requirements

### Requirement: Resolve a per-entry execution budget
The system SHALL resolve one execution budget for every dispatched entry, in
precedence order: the worker's configured override, then the budget carried on
the entry, then the queue's configured default, then 600 seconds. A resolved
budget MUST be a positive number of seconds; the system SHALL reject a
non-positive or non-numeric budget with an alias-specific configuration error
before it is applied. There SHALL be no value meaning "unbounded".

#### Scenario: Fall back to the built-in default
- **WHEN** a worker dispatches an entry whose queue, entry, and worker all
  specify no budget
- **THEN** it enforces a budget of 600 seconds

#### Scenario: Prefer the entry's budget over the queue default
- **WHEN** a queue defines a default budget and an entry was enqueued with its
  own budget
- **THEN** the worker enforces the entry's budget

#### Scenario: Prefer the worker override over the entry
- **WHEN** a worker defines a budget override and dispatches an entry that
  carries its own budget
- **THEN** the worker enforces its override

#### Scenario: Reject an invalid budget
- **WHEN** a queue, entry, or worker specifies a budget that is not a positive
  number of seconds
- **THEN** the system raises a configuration error identifying the queue alias

### Requirement: Accept a budget when enqueueing an entry
The entry-oriented enqueue operation SHALL accept an optional `timeout` keyword
naming that entry's execution budget in seconds, and MUST persist it on the
entry record. The item-oriented `add` operation creates no entry and SHALL NOT
accept a budget.

#### Scenario: Enqueue with an explicit budget
- **WHEN** a caller enqueues a payload with a `timeout` keyword
- **THEN** the returned entry records that budget in its durable representation

#### Scenario: Enqueue without a budget
- **WHEN** a caller enqueues a payload with no `timeout` keyword
- **THEN** the entry records no budget and resolution falls to the queue default

### Requirement: Abandon a handler that exceeds its budget
A worker SHALL bound each handler with the resolved budget, measured from the
point the entry is marked running. When the budget expires the worker MUST
cancel the handler, record the entry as `timeout`, and continue dispatching
subsequent entries on that queue.

#### Scenario: Time out a hung handler
- **WHEN** a handler neither returns nor extends its budget before the budget
  expires
- **THEN** the worker cancels it, stores the entry with status `timeout` and a
  non-null `finished_at`, and dispatches the next entry

#### Scenario: Leave a handler within its budget untouched
- **WHEN** a handler returns before its budget expires
- **THEN** the worker records the handler's own outcome and never marks the
  entry `timeout`

### Requirement: Extend a budget from a live handler
The system SHALL provide a heartbeat call that a handler invokes to assert
progress, and which restarts that entry's budget from the moment of the call.
The call MUST be usable from the handler's own coroutine and from a worker
thread the handler delegates to. It MUST raise when invoked outside an active
dispatch.

#### Scenario: Extend a long-running handler
- **WHEN** a handler calls the heartbeat before its budget expires and then
  continues working
- **THEN** the worker grants a fresh full budget from that call and does not
  time the handler out

#### Scenario: Heartbeat outside a dispatch
- **WHEN** the heartbeat is called where no entry is being dispatched
- **THEN** it raises an error rather than silently succeeding

### Requirement: Count timeout outcomes separately
A worker SHALL count entries it abandoned on budget expiry separately from
succeeded, failed, and cancelled outcomes, expose that count on its snapshot,
and include it in its structured lifecycle records.

#### Scenario: Report a timeout in the worker snapshot
- **WHEN** a worker has abandoned one entry on budget expiry
- **THEN** its snapshot reports a timeout count of one, leaves its cancelled
  count unchanged, and its terminal-outcome record carries the updated count
